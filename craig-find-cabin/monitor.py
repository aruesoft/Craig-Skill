import argparse
import sys
import time
import traceback

from config import (load_config, load_targets, save_targets, save_config_chat_id,
                    DEFAULT_CONFIG_PATH, DEFAULT_TARGETS_PATH, Target, ConfigError)
from knps import KnpsClient, KnpsError
from notify import Telegram
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
        self.knps = KnpsClient()
        self.state = {}
        self.done = set()          # 선점 완료된 target key
        self.queue = []            # FIFO: [{"cell","party","target"}, ...] 캡차 대기열
        self.active = None         # 현재 캡차 발송 후 응답 대기 중인 job (dict) 또는 None
        self.last_poll = 0.0
        self.last_heartbeat = time.time()
        self.tg_offset = None
        self.fail_streak = 0

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
                self.active = None
                self._send_next_captcha()
            return
        if ev.kind in ("became_available", "became_waiting") and ev.target.mode == "auto":
            self.start_relay(ev.target, ev.cell)

    def start_relay(self, target, cell):
        self.queue.append({"cell": cell, "party": target.party, "target": target})
        if self.active is None:
            self._send_next_captcha()

    def _send_next_captcha(self):
        if not self.queue:
            self.active = None
            return
        job = self.queue.pop(0)
        self.active = job
        target, cell = job["target"], job["cell"]
        try:
            if not self.knps.is_authenticated():
                self.knps.login(self.cfg.knps_id, self.cfg.knps_pw)
            png = self.knps.get_captcha()
        except KnpsError as e:
            self.tg.send(f"⚠️ 로그인/캡차 실패: {e}")
            self.state[target.key()] = Slot("none", 0)
            self.active = None
            self._send_next_captcha()
            return
        qty = min(target.party, cell.rsvt_cnt) if cell.rsvt_cnt else target.party
        self.tg.send_photo(png,
            f"{target.shelter} {_fmt_date(target.date)} {qty}명 예약. "
            f"캡차 숫자를 답장하세요. (인원변경: '2 1234', 취소: 'pass')")

    def handle_captcha_reply(self, text):
        if self.active is None:
            return
        text = text.strip()
        if text.lower() == "pass":
            job = self.active
            self.tg.send(f"릴레이를 취소했습니다: {job['target'].shelter} "
                         f"{_fmt_date(job['target'].date)}.")
            self.active = None
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
            self.active = None
            self._send_next_captcha()
            return
        try:
            res = self.knps.submit_reservation(job["cell"], party, captcha)
        except KnpsError as e:
            self.tg.send(f"⚠️ 제출 오류: {e}. 자리가 남아있으면 다시 캡차를 보냅니다.")
            self._resend_active_captcha()
            return
        if res.success:
            self.done.add(job["target"].key())
            if res.payment_deadline:
                dl = res.payment_deadline
                pretty = f"{dl[:4]}-{dl[4:6]}-{dl[6:8]} {dl[8:10]}:{dl[10:12]}"
                self.tg.send(f"✅ 선점 완료! {res.prd_nm}\n결제 만기: {pretty}\n"
                             f"미결제 시 자동취소 — 지금 결제하세요:\n"
                             f"https://reservation.knps.or.kr/mypage/dashBoard.do?prdDvcd=S")
            else:
                self.tg.send(f"✅ {res.message}: {res.prd_nm}")
            self.active = None
            self._send_next_captcha()
        else:
            self.tg.send(f"❌ {res.message}. 자리가 남아있으면 다시 캡차를 보냅니다.")
            self._resend_active_captcha()

    def _resend_active_captcha(self):
        """제출 실패/오류 후 현재 active job을 유지한 채 캡차를 다시 보낸다."""
        job = self.active
        target, cell = job["target"], job["cell"]
        try:
            if not self.knps.is_authenticated():
                self.knps.login(self.cfg.knps_id, self.cfg.knps_pw)
            png = self.knps.get_captcha()
        except KnpsError as e:
            self.tg.send(f"⚠️ 로그인/캡차 실패: {e}")
            self.state[target.key()] = Slot("none", 0)
            self.active = None
            self._send_next_captcha()
            return
        qty = min(target.party, cell.rsvt_cnt) if cell.rsvt_cnt else target.party
        self.tg.send_photo(png,
            f"{target.shelter} {_fmt_date(target.date)} {qty}명 예약. "
            f"캡차 숫자를 답장하세요. (인원변경: '2 1234', 취소: 'pass')")

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

    def maybe_heartbeat(self):
        if time.time() - self.last_heartbeat >= HEARTBEAT_SEC:
            lines = [f"{t.shelter} {_fmt_date(t.date)}" for t in self.active_targets()]
            self.tg.send("감시 중: " + ", ".join(lines) + " — 변화 없음\n"
                         f"마지막 확인 {_now()}")
            self.last_heartbeat = time.time()

    def run(self):
        self.tg.send(f"🏔️ 대피소 감시 시작 ({len(self.active_targets())}개 대상)")
        while True:
            try:
                if self.active_targets() and time.time() - self.last_poll >= self.cfg.poll_sec:
                    self.poll()
                    self.last_poll = time.time()
                self.pump_telegram()
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
