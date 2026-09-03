"""릴레이 상태머신 회귀 테스트 — 직렬화·웨지 방지 불변식을 고정한다.
가짜 KnpsClient/Telegram을 주입해 네트워크 없이 상태 전이만 검증."""
from config import Config, Target
from knps import Cell, KnpsError, ReservationResult, Reservation
from watch import Event, Slot
from monitor import Daemon


def _cfg():
    return Config(knps_id="u", knps_pw="p", telegram_token="t",
                  telegram_chat_id="1", poll_sec=180)


def _cell(fclt, dt, tp="W", rsvt=1):
    return Cell(fclt, dt, tp, rsvt, 5, 30000, "SB0310060100" + dt[-1],
                "B031006", "월", "설악산", "설악산")


class FakeKnps:
    def __init__(self, captcha_error=False, submit=None, reservations=None, cancel_error=False):
        self.captcha_error = captcha_error
        self.submit = submit
        self.captcha_calls = 0
        self.submit_calls = 0
        self.submit_args = []          # (fclt, qty, captcha)
        self.reservations = list(reservations or [])   # 마이페이지 활성 예약
        self.cancel_error = cancel_error
        self.cancelled = []

    def my_reservations(self, use_dt=None):
        return [r for r in self.reservations if use_dt is None or r.use_dt == use_dt]

    def cancel_reservation(self, res):
        if self.cancel_error:
            raise KnpsError("cancel not applied")
        self.cancelled.append(res.rsvt_id)
        self.reservations = [r for r in self.reservations if r.rsvt_id != res.rsvt_id]

    def is_authenticated(self):
        return True

    def login(self, i, p):
        pass

    def get_captcha(self):
        self.captcha_calls += 1
        if self.captcha_error:
            raise KnpsError("network")
        return b"PNGBYTES"

    def submit_reservation(self, cell, qty, captcha):
        self.submit_calls += 1
        self.submit_args.append((cell.fclt_nm, qty, captcha))
        return self.submit


class FakeTg:
    def __init__(self):
        self.sent, self.photos = [], []

    def send(self, text):
        self.sent.append(text)

    def send_photo(self, png, caption):
        self.photos.append(caption)

    def set_chat_id(self, c):
        pass


def _daemon(knps):
    d = Daemon(_cfg(), [])
    d.knps = knps
    d.tg = FakeTg()
    return d


OK = ReservationResult(True, "W", "양폭대피소", None, "대기신청 완료")
FAIL = ReservationResult(False, "W", "양폭대피소", None, "이미 마감")


def test_relay_start_sets_active_and_sends_captcha():
    d = _daemon(FakeKnps())
    t = Target("설악산", "B03", "양폭대피소", "20261026", 5, "auto")
    d.start_relay(t, _cell("양폭대피소", "20261026"))
    assert d.active is not None
    assert d.active["target"].key() == t.key()
    assert len(d.tg.photos) == 1


def test_captcha_error_does_not_wedge():
    """로그인/캡차 네트워크 오류 시 active가 영구히 물리지 않는다(웨지 방지)."""
    d = _daemon(FakeKnps(captcha_error=True))
    t = Target("설악산", "B03", "양폭대피소", "20261026", 5, "auto")
    d.start_relay(t, _cell("양폭대피소", "20261026"))
    assert d.active is None          # 큐가 비면 active 해제
    assert d.queue == []
    assert d.state[t.key()] == Slot("none", 0)  # 다음 폴링 재감지되도록 리셋


def test_reply_full_booking_marks_done():
    """요청 인원 전부 선점되면 done 처리(더 감시 안 함)."""
    d = _daemon(FakeKnps(submit=OK))
    t = Target("설악산", "B03", "양폭대피소", "20261026", 5, "auto")
    d.start_relay(t, _cell("양폭대피소", "20261026", rsvt=5))  # 5석 열림, 5명 요청 → 전량
    d.handle_captcha_reply("1234")
    assert d.active is None
    assert t.key() in d.done


def test_partial_booking_keeps_monitoring():
    """일부만 선점되면 done이 아니라 party(총 희망 인원)는 그대로 두고 계속 감시한다."""
    d = _daemon(FakeKnps(submit=OK))
    t = Target("설악산", "B03", "소청대피소", "20261015", 5, "auto")
    d.targets = [t]
    d.start_relay(t, _cell("소청대피소", "20261015", rsvt=1))  # 1석만 열림
    d.handle_captcha_reply("1")                                # 1자리 선점
    assert d.active is None
    assert t.key() not in d.done          # 계속 감시
    assert t.party == 5                    # 총 희망 인원 유지(다음엔 취소 후 n+1 재예약)
    assert t.key() not in d.state          # 잔여분 재감지되도록 상태 초기화
    assert d.knps.submit_args == [("소청대피소", 1, "1")]


def _held(shelter, dt, qty, kind="R"):
    return Reservation("SB0310020100220260902106743", shelter, dt, "예약(미결제)", kind, qty)


def test_existing_reservation_cancelled_then_rebooked_merged():
    """같은 날 같은 대피소에 이미 1명 예약이 있고 1자리가 더 나면: 기존 취소 → 2명으로 재예약."""
    k = FakeKnps(submit=OK, reservations=[_held("소청대피소", "20261015", 1)])
    d = _daemon(k)
    t = Target("설악산", "B03", "소청대피소", "20261015", 5, "auto")
    d.targets = [t]
    d.start_relay(t, _cell("소청대피소", "20261015", tp="R", rsvt=1))
    assert "1명(예약) 취소 후 2명" in d.tg.photos[0]   # 캡션이 취소·재예약 계획을 알려준다
    d.handle_captcha_reply("1234")
    assert k.cancelled == ["SB0310020100220260902106743"]
    assert k.submit_args == [("소청대피소", 2, "1234")]
    assert t.key() not in d.done                     # 2/5 → 계속 감시
    assert d.active is None


def test_merge_reaching_party_marks_done():
    k = FakeKnps(submit=OK, reservations=[_held("소청대피소", "20261015", 4)])
    d = _daemon(k)
    t = Target("설악산", "B03", "소청대피소", "20261015", 5, "auto")
    d.targets = [t]
    d.start_relay(t, _cell("소청대피소", "20261015", tp="R", rsvt=3))
    d.handle_captcha_reply("1234")
    assert k.submit_args == [("소청대피소", 5, "1234")]   # 4+3 중 희망 5명까지만
    assert t.key() in d.done


def test_cancel_failure_skips_submit_and_keeps_active():
    k = FakeKnps(submit=OK, reservations=[_held("소청대피소", "20261015", 1)], cancel_error=True)
    d = _daemon(k)
    t = Target("설악산", "B03", "소청대피소", "20261015", 5, "auto")
    d.targets = [t]
    d.start_relay(t, _cell("소청대피소", "20261015", tp="R", rsvt=1))
    d.handle_captcha_reply("1234")
    assert k.submit_calls == 0             # 취소 안 됐으면 제출해봐야 거절 — 제출 안 함
    assert d.active is not None            # 새 캡차로 재시도 가능
    assert t.key() not in d.done


def test_submit_failure_after_cancel_retries_with_freed_seats():
    """취소는 됐는데 제출(캡차 오답)이 실패하면 풀린 자리까지 포함해 즉시 재시도한다."""
    k = FakeKnps(submit=FAIL, reservations=[_held("소청대피소", "20261015", 1)])
    d = _daemon(k)
    t = Target("설악산", "B03", "소청대피소", "20261015", 5, "auto")
    d.targets = [t]
    d.start_relay(t, _cell("소청대피소", "20261015", tp="R", rsvt=1))
    d.handle_captcha_reply("0000")
    assert k.cancelled == ["SB0310020100220260902106743"]
    assert k.submit_args == [("소청대피소", 2, "0000")]
    assert d.active is not None
    k.submit = OK
    d.handle_captcha_reply("1234")         # 기존 예약은 이미 취소됨 → 다시 취소하지 않고 2명 제출
    assert k.cancelled == ["SB0310020100220260902106743"]
    assert k.submit_args[-1] == ("소청대피소", 2, "1234")
    assert d.active is None


def test_pass_cancels_active():
    d = _daemon(FakeKnps(submit=OK))
    t = Target("설악산", "B03", "양폭대피소", "20261026", 5, "auto")
    d.start_relay(t, _cell("양폭대피소", "20261026"))
    d.handle_captcha_reply("pass")
    assert d.active is None
    assert t.key() not in d.done


def test_soldout_while_active_cancels_relay():
    d = _daemon(FakeKnps(submit=OK))
    t = Target("설악산", "B03", "양폭대피소", "20261026", 5, "auto")
    d.start_relay(t, _cell("양폭대피소", "20261026"))
    ev = Event(t, "became_soldout", Slot("W", 1), Slot("none", 0), None)
    d.handle_event(ev, {})
    assert d.active is None


def test_concurrent_relays_are_serialized():
    """두 자리가 동시에 열려도 캡차는 한 번에 하나, 응답 후 다음으로."""
    d = _daemon(FakeKnps(submit=OK))
    t1 = Target("설악산", "B03", "소청대피소", "20261015", 5, "auto")
    t2 = Target("설악산", "B03", "양폭대피소", "20261016", 5, "auto")
    d.start_relay(t1, _cell("소청대피소", "20261015", rsvt=5))  # 5석 → 5명 전량 선점
    d.start_relay(t2, _cell("양폭대피소", "20261016"))
    assert d.active["target"].key() == t1.key()   # 첫 번째만 active
    assert len(d.queue) == 1                       # 두 번째는 대기열
    d.handle_captcha_reply("1111")                 # t1 해결 → t2로 전진
    assert d.active["target"].key() == t2.key()
    assert t1.key() in d.done


def test_submit_failure_keeps_active_for_retry():
    d = _daemon(FakeKnps(submit=FAIL))
    t = Target("설악산", "B03", "양폭대피소", "20261026", 5, "auto")
    d.start_relay(t, _cell("양폭대피소", "20261026"))
    d.handle_captcha_reply("9999")
    assert d.active is not None          # 실패 시 재시도 위해 active 유지
    assert t.key() not in d.done
