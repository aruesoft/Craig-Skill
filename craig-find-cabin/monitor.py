import argparse
import sys
import time
import traceback

from config import (load_config, load_targets, save_targets, save_config_chat_id,
                    targets_mtime, DEFAULT_CONFIG_PATH, DEFAULT_TARGETS_PATH,
                    Target, ConfigError)
from knps import KnpsClient, KnpsError
from notify import Telegram, Pushover, Caller
from watch import diff_targets, compute_state, Slot
import status_page

STATUS_DIR = "status"
HEARTBEAT_SEC = 2 * 3600
CAPTCHA_RE = None  # set in main via re


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_date(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def _event_message(ev):
    d = _fmt_date(ev.target.date)
    if ev.kind == "became_available":
        return f"🎉 예약가능! {ev.target.shelter} {d} — 잔여 {ev.curr.rsvt}자리"
    if ev.kind == "became_waiting":
        return f"🕓 대기가능! {ev.target.shelter} {d} — 대기 {ev.curr.rsvt}"
    if ev.kind == "rsvt_changed":
        return f"ℹ️ {ev.target.shelter} {d} 잔여 변화: {ev.prev.rsvt} → {ev.curr.rsvt}"
    if ev.kind == "became_soldout":
        return f"⚠️ {ev.target.shelter} {d} 자리 소진(매진 전환)."
    return ""


class Daemon:
    def __init__(self, cfg, targets, dry_run=False):
        self.cfg = cfg
        self.targets = targets
        self.dry_run = dry_run
        self.tg = Telegram(cfg.telegram_token, cfg.telegram_chat_id)
        self.pushover = Pushover(cfg.pushover_user, cfg.pushover_token)
        self.caller = Caller(cfg.twilio_sid, cfg.twilio_token,
                             cfg.twilio_from, cfg.twilio_to)
        self.knps = KnpsClient()
        self.state = {}
        self.done = set()          # 선점 완료된 target key
        self.queue = []            # FIFO: [{"cell","party","target"}, ...] 캡차 대기열
        self.active = None         # 현재 캡차 발송 후 응답 대기 중인 job (dict) 또는 None
        self.active_sent_at = 0.0  # 현재 캡차를 보낸 시각 (재발송 판단용)
        self.last_nudge = 0.0      # 마지막 텔레그램 반복 독촉 시각
        self.last_call = 0.0       # 마지막 전화 시각
        self.active_receipt = None # 현재 활성 릴레이의 Pushover 긴급 receipt (해결 시 취소)
        self.last_poll = 0.0
        self.last_heartbeat = time.time()
        self.tg_offset = None
        self.fail_streak = 0
        self.targets_sig = targets_mtime()  # 대상 파일 변경 감지(웹/텔레그램 편집)

    def active_targets(self):
        return [t for t in self.targets if t.key() not in self.done]

    def poll(self):
        depts = {(t.park, t.dept) for t in self.active_targets()}
        calendar = {}
        for park, dept in depts:
            calendar.update(self.knps.fetch_calendar(dept, park))
        events, self.state = diff_targets(self.active_targets(), calendar, self.state)
        for ev in events:
            self.handle_event(ev, calendar)
        status_page.write_status(STATUS_DIR, self.targets, self.state, _now(), True)
        self.fail_streak = 0

    def handle_event(self, ev, calendar):
        self.tg.send(_event_message(ev))
        k = ev.target.key()
        if ev.kind == "became_soldout":
            self.queue = [j for j in self.queue if j["target"].key() != k]
            if self.active is not None and self.active["target"].key() == k:
                self.tg.send(f"릴레이 취소: {ev.target.shelter} {_fmt_date(ev.target.date)} 소진.")
                self._clear_active()
                self._send_next_captcha()
            return
        if ev.kind in ("became_available", "became_waiting") and ev.target.mode == "auto":
            self.start_relay(ev.target, ev.cell)

    def start_relay(self, target, cell):
        self.queue.append({"cell": cell, "party": target.party, "target": target})
        if self.active is None:
            self._send_next_captcha()

    def _clear_active(self):
        """활성 릴레이 종료: Pushover 긴급 반복 취소 + active 해제."""
        if self.active_receipt:
            self.pushover.cancel(self.active_receipt)
            self.active_receipt = None
        self.active = None

    def _escalate(self, target, qty, kind):
        """자리 발생 시 큰 알림: Pushover 긴급 푸시(확인까지 반복) + 전화."""
        title = f"{kind} 자리! {target.shelter} {_fmt_date(target.date)}"
        body = (f"{target.shelter} {_fmt_date(target.date)} {qty}명 {kind} 가능. "
                f"텔레그램에서 캡차 숫자를 답장하세요.")
        self.active_receipt = self.pushover.emergency(title, body)
        self.caller.call(f"국립공원 대피소 {kind} 자리가 났습니다. 텔레그램을 확인하세요.")
        self.last_call = time.time()

    def _send_captcha(self, job, escalate):
        """job에 대한 캡차를 받아 텔레그램으로 보낸다. escalate=True면 긴급 알림도 발동.
        성공 True / 로그인·캡차 실패 시 active 해제 후 다음 job 진행하고 False."""
        target, cell = job["target"], job["cell"]
        try:
            if not self.knps.is_authenticated():
                self.knps.login(self.cfg.knps_id, self.cfg.knps_pw)
            png = self.knps.get_captcha()
        except KnpsError as e:
            self.tg.send(f"⚠️ 로그인/캡차 실패: {e}")
            self.state[target.key()] = Slot("none", 0)
            self._clear_active()
            self._send_next_captcha()
            return False
        qty = min(target.party, cell.rsvt_cnt) if cell.rsvt_cnt else target.party
        kind = "대기신청" if cell.reser_tp == "W" else "예약"
        self.tg.send_photo(png,
            f"{target.shelter} {_fmt_date(target.date)} {qty}명 {kind}. "
            f"캡차 숫자를 답장하세요. (인원변경: '2 1234', 취소: 'pass')")
        self.active_sent_at = time.time()
        self.last_nudge = time.time()
        if escalate:
            self._escalate(target, qty, kind)
        return True

    def _send_next_captcha(self):
        if not self.queue:
            self.active = None
            return
        job = self.queue.pop(0)
        self.active = job
        self._send_captcha(job, escalate=True)

    def handle_captcha_reply(self, text):
        if self.active is None:
            return
        text = text.strip()
        if text.lower() == "pass":
            job = self.active
            self.tg.send(f"릴레이를 취소했습니다: {job['target'].shelter} "
                         f"{_fmt_date(job['target'].date)}.")
            self._clear_active()
            self._send_next_captcha()
            return
        parts = text.split()
        qty_override = None
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            qty_override, captcha = int(parts[0]), parts[1]
        elif len(parts) == 1 and parts[0].isdigit():
            captcha = parts[0]
        else:
            return  # 캡차 형식 아님 — 명령으로 처리
        job = self.active
        party = qty_override or job["party"]
        if self.dry_run:
            self.tg.send(f"[dry-run] 제출 생략: {job['cell'].fclt_nm} "
                         f"{_fmt_date(job['target'].date)} {party}명 captcha={captcha}")
            self._clear_active()
            self._send_next_captcha()
            return
        try:
            res = self.knps.submit_reservation(job["cell"], party, captcha)
        except KnpsError as e:
            self.tg.send(f"⚠️ 제출 오류: {e}. 자리가 남아있으면 다시 캡차를 보냅니다.")
            self._resend_active_captcha()
            return
        if res.success:
            tgt, cell = job["target"], job["cell"]
            booked = min(party, cell.rsvt_cnt) if cell.rsvt_cnt else party
            remaining = max(0, tgt.party - booked)
            if remaining > 0:
                # 일부만 선점 — 나머지 인원만큼 계속 감시(취소표는 한 자리씩 떨어짐)
                tgt.party = remaining
                self.state.pop(tgt.key(), None)   # 잔여분 다시 감지하도록 상태 초기화
                save_targets(DEFAULT_TARGETS_PATH, self.targets)
                self.targets_sig = targets_mtime()  # 방금 쓴 변경으로 재리로드되지 않게
                keep = f"\n남은 {remaining}자리 계속 감시합니다."
            else:
                self.done.add(tgt.key())
                keep = ""
            if res.payment_deadline:
                dl = res.payment_deadline
                pretty = f"{dl[:4]}-{dl[4:6]}-{dl[6:8]} {dl[8:10]}:{dl[10:12]}"
                self.tg.send(f"✅ 선점 완료! {res.prd_nm} ({booked}명)\n결제 만기: {pretty}\n"
                             f"미결제 시 자동취소 — 지금 결제하세요:\n"
                             f"https://reservation.knps.or.kr/mypage/dashBoard.do?prdDvcd=S{keep}")
            else:
                self.tg.send(f"✅ {res.message}: {res.prd_nm} ({booked}명){keep}")
            self._clear_active()
            self._send_next_captcha()
        else:
            self.tg.send(f"❌ {res.message}. 자리가 남아있으면 다시 캡차를 보냅니다.")
            self._resend_active_captcha()

    def _resend_active_captcha(self):
        """제출 실패/오류 후 현재 active job을 유지한 채 캡차를 다시 보낸다(긴급알림 재발동 없음)."""
        self._send_captcha(self.active, escalate=False)

    def maybe_relay_upkeep(self):
        """활성 릴레이가 있으면 캡차를 신선하게 유지하고 반복 알림/전화로 재촉한다."""
        if self.active is None:
            return
        now = time.time()
        if now - self.active_sent_at >= self.cfg.captcha_refresh_sec:
            # 오래된 캡차는 만료될 수 있으니 새 캡차로 교체(같은 job 유지)
            self._send_captcha(self.active, escalate=False)
            return
        if now - self.last_nudge >= self.cfg.alert_repeat_sec:
            at = self.active["target"]
            self.tg.send(f"⏰ 아직 캡차 답장 대기 중: {at.shelter} "
                         f"{_fmt_date(at.date)} — 숫자를 답장하세요.")
            self.last_nudge = now
        if self.caller.enabled and now - self.last_call >= self.cfg.call_repeat_sec:
            at = self.active["target"]
            self.caller.call(f"국립공원 대피소 {at.shelter} 자리가 아직 대기 중입니다. "
                             f"텔레그램을 확인하세요.")
            self.last_call = now

    def handle_command(self, text, chat_id):
        if self.cfg.telegram_chat_id is None:
            self.cfg.telegram_chat_id = str(chat_id)
            self.tg.set_chat_id(chat_id)
            save_config_chat_id(DEFAULT_CONFIG_PATH, chat_id)
        cmd, *rest = text.strip().split()
        if cmd == "/start":
            self.tg.send("감시를 시작합니다. /status 로 현재 상태를 볼 수 있어요.")
        elif cmd == "/status":
            lines = [f"{t.shelter} {_fmt_date(t.date)}: "
                     f"{self.state.get(t.key(), Slot('none',0)).status}"
                     for t in self.active_targets()]
            msg = "감시 중\n" + "\n".join(lines) if lines else "감시 대상 없음"
            if self.active is not None:
                at = self.active["target"]
                msg += f"\n\n캡차 대기 중: {at.shelter} {_fmt_date(at.date)}"
            if self.queue:
                qlines = [f"{j['target'].shelter} {_fmt_date(j['target'].date)}" for j in self.queue]
                msg += "\n대기열: " + ", ".join(qlines)
            self.tg.send(msg)
        elif cmd == "/targets":
            self.tg.send("\n".join(f"{t.shelter} {_fmt_date(t.date)} {t.party}명 [{t.mode}]"
                                   for t in self.targets) or "없음")
        elif cmd == "/add" and len(rest) >= 2:
            shelter, date = rest[0], rest[1]
            if not shelter.endswith("대피소"):
                shelter = shelter + "대피소"
            party = int(rest[2]) if len(rest) > 2 and rest[2].isdigit() else 5
            self.targets.append(Target("설악산", "B03", shelter, date, party, "auto"))
            save_targets(DEFAULT_TARGETS_PATH, self.targets)
            self.tg.send(f"추가: {shelter} {_fmt_date(date)} {party}명")
        elif cmd == "/remove" and len(rest) >= 2:
            self.targets = [t for t in self.targets
                            if not (t.shelter == rest[0] and t.date == rest[1])]
            save_targets(DEFAULT_TARGETS_PATH, self.targets)
            self.tg.send(f"삭제: {rest[0]} {rest[1]}")
        else:
            self.handle_captcha_reply(text)

    def pump_telegram(self):
        for up in self.tg.get_updates(self.tg_offset, timeout=5):
            self.tg_offset = up["update_id"] + 1
            msg = up.get("message") or {}
            text = msg.get("text")
            chat_id = (msg.get("chat") or {}).get("id")
            if not text:
                continue
            if text.startswith("/"):
                self.handle_command(text, chat_id)
            else:
                self.handle_captcha_reply(text)

    def maybe_reload_targets(self):
        """대상 파일이 외부(웹/텔레그램)에서 바뀌면 self.targets를 다시 읽는다."""
        sig = targets_mtime()
        if sig == self.targets_sig:
            return
        self.targets_sig = sig
        try:
            new = load_targets()
        except (OSError, ValueError):
            return
        old_keys = {t.key() for t in self.targets}
        new_keys = {t.key() for t in new}
        self.targets = new
        removed = old_keys - new_keys
        for k in removed:
            self.state.pop(k, None)
            self.done.discard(k)
        self.queue = [j for j in self.queue if j["target"].key() not in removed]
        if self.active is not None and self.active["target"].key() in removed:
            self._clear_active()
            self._send_next_captcha()
        added = new_keys - old_keys
        if added or removed:
            self.tg.send(f"🛠️ 감시 대상 갱신 ({len(self.active_targets())}개)\n"
                         f"{self._target_lines()}")

    def maybe_heartbeat(self):
        if time.time() - self.last_heartbeat >= HEARTBEAT_SEC:
            self.tg.send(f"🔎 감시 중 — 변화 없음\n{self._target_lines()}\n"
                         f"마지막 확인 {_now()}")
            self.last_heartbeat = time.time()

    def _target_lines(self):
        return "\n".join(f"• {t.shelter} {_fmt_date(t.date)} ({t.party}명)"
                         for t in self.active_targets())

    def run(self):
        ch = ["텔레그램"]
        if self.pushover.enabled:
            ch.append("Pushover")
        if self.caller.enabled:
            ch.append("전화")
        self.tg.send(f"🏔️ 대피소 감시 시작 ({len(self.active_targets())}개 대상)\n"
                     f"{self._target_lines()}\n\n알림: {'·'.join(ch)}")
        while True:
            try:
                if self.active_targets() and time.time() - self.last_poll >= self.cfg.poll_sec:
                    self.poll()
                    self.last_poll = time.time()
                self.pump_telegram()
                self.maybe_reload_targets()
                self.maybe_relay_upkeep()
                self.maybe_heartbeat()
            except KnpsError as e:
                self.fail_streak += 1
                print(f"[{_now()}] poll fail #{self.fail_streak}: {e}")
                if self.fail_streak == 5:
                    self.tg.send(f"⚠️ 조회 연속 5회 실패: {e}")
                time.sleep(min(30 * self.fail_streak, 900))
            except Exception:
                traceback.print_exc()
                time.sleep(30)
            time.sleep(1)


def cmd_check(cfg, targets):
    knps = KnpsClient()
    depts = {(t.park, t.dept) for t in targets}
    calendar = {}
    for park, dept in depts:
        calendar.update(knps.fetch_calendar(dept, park))
    state = compute_state(targets, calendar)
    for t in targets:
        slot = state[t.key()]
        print(f"{t.shelter} {_fmt_date(t.date)}: {slot.status} (잔여 {slot.rsvt})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--listen", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        sys.exit(1)
    targets = load_targets()
    if args.check:
        cmd_check(cfg, targets)
    elif args.listen or args.dry_run:
        Daemon(cfg, targets, dry_run=args.dry_run).run()
    else:
        print("usage: monitor.py [--check | --listen | --dry-run]")


if __name__ == "__main__":
    main()
