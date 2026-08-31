"""릴레이 상태머신 회귀 테스트 — 직렬화·웨지 방지 불변식을 고정한다.
가짜 KnpsClient/Telegram을 주입해 네트워크 없이 상태 전이만 검증."""
from config import Config, Target
from knps import Cell, KnpsError, ReservationResult
from watch import Event, Slot
from monitor import Daemon


def _cfg():
    return Config(knps_id="u", knps_pw="p", telegram_token="t",
                  telegram_chat_id="1", poll_sec=180)


def _cell(fclt, dt, tp="W", rsvt=1):
    return Cell(fclt, dt, tp, rsvt, 5, 30000, "SB0310060100" + dt[-1],
                "B031006", "월", "설악산", "설악산")


class FakeKnps:
    def __init__(self, captcha_error=False, submit=None):
        self.captcha_error = captcha_error
        self.submit = submit
        self.captcha_calls = 0
        self.submit_calls = 0

    def is_authenticated(self):
        return True

    def login(self, i, p):
        pass

    def get_captcha(self):
        self.captcha_calls += 1
        if self.captcha_error:
            raise KnpsError("network")
        return b"PNGBYTES"

    def submit_reservation(self, cell, party, captcha):
        self.submit_calls += 1
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


def test_reply_success_clears_active_and_marks_done():
    d = _daemon(FakeKnps(submit=OK))
    t = Target("설악산", "B03", "양폭대피소", "20261026", 5, "auto")
    d.start_relay(t, _cell("양폭대피소", "20261026"))
    d.handle_captcha_reply("1234")
    assert d.active is None
    assert t.key() in d.done


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
    d.start_relay(t1, _cell("소청대피소", "20261015"))
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
