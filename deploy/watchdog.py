#!/usr/bin/env python3
"""
Craig-Skill 워치독 — 봇/서비스 중단·오류를 감지해 자동 재시작하고 텔레그램으로 올림보고.

launchd 로 5분마다 실행(StartInterval 300). launchd KeepAlive 가 1차 방어(크래시 즉시 재시작)이고,
이 워치독은 2차 방어 — 서비스가 booted-out 되거나 오류가 누적될 때 재시작·알림한다.

알림 대상: 학습봇 config 의 토큰 + (alert_chat_id | telegram_chat_id | 학습봇 state.last_chat).
의존성 없음(stdlib urllib). 비밀값은 출력하지 않는다.
"""
import os
import re
import json
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime

ROOT = "/Users/craigpark/Github/Craig-Skill"
STATE = f"{ROOT}/logs/watchdog_state.json"
SERVICES = [("mountainbot", "등산봇"), ("youtube", "유튜브봇"),
            ("studybot", "학습봇"), ("dashboard", "대시보드")]
ERR_RE = re.compile(r"(Traceback|Exception|CRITICAL|❌|오류)", re.I)
WARN_RE = re.compile(r"(Warning|warn|FP16|semaphore)", re.I)


def sh(*a, timeout=20):
    try:
        return subprocess.run(a, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def read_json(path, default=None):
    try:
        return json.load(open(os.path.expanduser(path)))
    except Exception:
        return {} if default is None else default


def tg(text):
    cfg = read_json("~/.config/craig-telegram-study/config.json")
    token = cfg.get("telegram_bot_token", "")
    chat = str(cfg.get("alert_chat_id") or cfg.get("telegram_chat_id") or "")
    if not chat:
        chat = str(read_json("~/.config/craig-telegram-study/state.json").get("last_chat", ""))
    if not token or not chat:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=15)
    except Exception:
        pass


def launchd_pid(label):
    for line in sh("launchctl", "list").splitlines():
        p = line.split("\t")
        if len(p) >= 3 and p[2] == label:
            return p[0]  # "pid" 또는 "-"
    return None  # 미로드


def err_count(key):
    n = 0
    for suf in ("err", "out"):
        try:
            for l in open(f"{ROOT}/logs/{key}.{suf}.log", encoding="utf-8", errors="ignore").read().splitlines()[-500:]:
                if ERR_RE.search(l) and not WARN_RE.search(l):
                    n += 1
        except Exception:
            pass
    return n


def kickstart(key, loaded):
    uid = os.getuid()
    la = os.path.expanduser(f"~/Library/LaunchAgents/com.craig.skill.{key}.plist")
    if loaded:
        sh("launchctl", "kickstart", "-k", f"gui/{uid}/com.craig.skill.{key}")
    elif os.path.exists(la):
        sh("launchctl", "bootstrap", f"gui/{uid}", la)


def main():
    first_run = not os.path.exists(STATE)
    st = read_json(STATE, {})
    down_prev = set(st.get("down", []))
    errs_prev = st.get("errs", {})
    down_now, errs_now = [], {}
    for key, name in SERVICES:
        pid = launchd_pid(f"com.craig.skill.{key}")
        loaded = pid is not None
        running = loaded and pid not in ("-", "0", "")
        if not running:
            down_now.append(key)
            if key not in down_prev:  # 새로 중단 → 재시작 + 올림보고
                kickstart(key, loaded)
                tg(f"🔴 {name}({key}) 중단 감지 → 재시작했어요.\n{datetime.now():%m-%d %H:%M}")
        elif key in down_prev:        # 복구
            tg(f"✅ {name}({key}) 복구됐어요.\n{datetime.now():%m-%d %H:%M}")

        ec = err_count(key)
        errs_now[key] = ec
        if not first_run and running and ec > errs_prev.get(key, 0):
            tg(f"⚠️ {name}({key}) 새 오류 {ec - errs_prev.get(key, 0)}건 감지(누적 {ec}). 로그 확인 필요.")

    st = {"down": down_now, "errs": errs_now, "last": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    try:
        json.dump(st, open(STATE, "w"), ensure_ascii=False, indent=2)
    except Exception:
        pass


if __name__ == "__main__":
    main()
