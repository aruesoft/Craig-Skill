#!/usr/bin/env python3
"""
Craig-Skill 헬스체크 대시보드 — 봇·launchd·배포·시스템 상태를 한 페이지로.

의존성 없음(stdlib). launchd 상시 구동. LAN 에서 http://<서버>:8788 로 조회.
  GET /        → HTML 대시보드(30초 자동 새로고침)
  GET /health  → JSON(프로그램 점검용)

실행: python3 dashboard.py [--port 8788]
비밀값(토큰 등)은 노출하지 않는다(설정에서 경로·개수 등 비민감 정보만 읽음).
"""

import os
import re
import json
import time
import html
import argparse
import subprocess
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = "/Users/craigpark/Github/Craig-Skill"
BOTS = [
    {"key": "mountainbot", "name": "등산봇", "label": "com.craig.skill.mountainbot",
     "proc": "korean-mountain-hiking/telegram-bot/bot.py"},
    {"key": "youtube", "name": "유튜브봇", "label": "com.craig.skill.youtube",
     "proc": "youtube-telegram-summary/monitor.py"},
    {"key": "studybot", "name": "학습봇", "label": "com.craig.skill.studybot",
     "proc": "craig-telegram-study/telegram-bot/study_bot.py"},
    {"key": "dashboard", "name": "대시보드", "label": "com.craig.skill.dashboard",
     "proc": "deploy/dashboard.py"},
]


def sh(*args, timeout=10):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def read_json(path):
    try:
        return json.load(open(os.path.expanduser(path)))
    except Exception:
        return {}


def launchd_map():
    m = {}
    for line in sh("launchctl", "list").splitlines():
        p = line.split("\t")
        if len(p) >= 3 and p[2].startswith("com.craig.skill."):
            m[p[2]] = {"pid": p[0], "exit": p[1]}
    return m


def proc_map():
    m = {}
    for line in sh("ps", "-eo", "pid,etime,pcpu,command").splitlines():
        for b in BOTS:
            if b["proc"] in line and "grep" not in line:
                parts = line.split(None, 3)
                if len(parts) >= 3:
                    m[b["key"]] = {"pid": parts[0], "etime": parts[1], "cpu": parts[2]}
    return m


def log_tail(name, n=1):
    try:
        lines = open(f"{ROOT}/logs/{name}.out.log", encoding="utf-8", errors="ignore").read().splitlines()
        return lines[-1] if lines else ""
    except Exception:
        return ""


def studybot_metrics():
    cfg = read_json("~/.config/craig-telegram-study/config.json")
    vault = cfg.get("study_vault_dir", "")
    d = {"vault": vault, "cards": 0, "due": 0, "inbox": 0, "reviews_today": 0}
    if not vault:
        return d
    sched = read_json(f"{vault}/_srs/schedule.json")
    today = datetime.now().strftime("%Y-%m-%d")
    d["cards"] = len(sched)
    d["due"] = sum(1 for c in sched.values() if (c.get("due") or "9999") <= today)
    ib = os.path.join(vault, "0_Inbox")
    if os.path.isdir(ib):
        d["inbox"] = sum(1 for f in os.listdir(ib) if f.endswith(".md") and not f.startswith("_"))
    rp = os.path.join(vault, "_srs", "reviews.jsonl")
    if os.path.exists(rp):
        try:
            d["reviews_today"] = sum(1 for l in open(rp, encoding="utf-8") if today in l[:30])
        except Exception:
            pass
    return d


def youtube_metrics():
    cfg = read_json("~/.config/youtube-telegram-summary/config.json")
    st = read_json("~/.config/youtube-telegram-summary/state.json")
    return {"channels": len(cfg.get("youtube_channels", [])),
            "seen": len(st.get("seen_videos", [])),
            "last_checked": st.get("last_checked") or "-",
            "vault_log": cfg.get("obsidian_daily_dir", "")}


def mountain_metrics():
    m = read_json(f"{ROOT}/korean-mountain-hiking/references/mountains.json")
    return {"mountains": len(m.get("mountains", []))}


def git_status():
    local = sh("git", "-C", ROOT, "rev-parse", "--short", "HEAD").strip()
    remote = sh("git", "-C", ROOT, "rev-parse", "--short", "origin/master").strip()
    subj = sh("git", "-C", ROOT, "log", "-1", "--format=%s").strip()
    return {"local": local, "remote": remote, "synced": bool(local) and local == remote, "subject": subj}


def deploy_log():
    try:
        lines = open(f"{ROOT}/logs/auto_deploy.log", encoding="utf-8", errors="ignore").read().splitlines()
        for l in reversed(lines):
            if "배포 완료" in l or "경보" in l or "diverge" in l:
                return l
    except Exception:
        pass
    return "(최근 배포 이벤트 없음 — 최신 상태)"


def system_metrics():
    up = sh("uptime").strip()
    pm = sh("pmset", "-g")
    sleep_ok = bool(re.search(r"\bsleep\s+0\b", pm))
    disk = ""
    for l in sh("df", "-h", "/").splitlines()[1:]:
        parts = l.split()
        if len(parts) >= 5:
            disk = f"{parts[3]} 남음 / {parts[1]} ({parts[4]} 사용)"
    return {"uptime": up, "sleep_prevented": sleep_ok, "disk": disk}


def gather():
    ld = launchd_map()
    pr = proc_map()
    bots = []
    for b in BOTS:
        ld_i = ld.get(b["label"], {})
        pr_i = pr.get(b["key"], {})
        running = bool(pr_i.get("pid")) or (ld_i.get("pid", "-") not in ("-", ""))
        bots.append({
            "name": b["name"], "key": b["key"], "label": b["label"],
            "running": running, "pid": pr_i.get("pid") or ld_i.get("pid", "-"),
            "etime": pr_i.get("etime", "-"), "cpu": pr_i.get("cpu", "-"),
            "exit": ld_i.get("exit", "-"), "last_log": log_tail(b["key"]),
        })
    return {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "bots": bots, "git": git_status(), "deploy": deploy_log(),
        "study": studybot_metrics(), "youtube": youtube_metrics(),
        "mountain": mountain_metrics(), "system": system_metrics(),
    }


def render(d):
    def esc(x):
        return html.escape(str(x))
    rows = ""
    for b in d["bots"]:
        badge = "🟢 실행중" if b["running"] else "🔴 중단"
        cls = "ok" if b["running"] else "bad"
        rows += (f"<tr class='{cls}'><td><b>{esc(b['name'])}</b><div class='sub'>{esc(b['label'])}</div></td>"
                 f"<td>{badge}</td><td>{esc(b['pid'])}</td><td>{esc(b['etime'])}</td>"
                 f"<td>{esc(b['cpu'])}%</td><td>exit {esc(b['exit'])}</td>"
                 f"<td class='log'>{esc(b['last_log'][:80])}</td></tr>")
    g = d["git"]
    git_badge = "🟢 최신" if g["synced"] else "🟡 뒤처짐"
    sy = d["study"]; yt = d["youtube"]; mt = d["mountain"]; sysm = d["system"]
    sleep_badge = "🟢 절전 차단" if sysm["sleep_prevented"] else "🔴 절전 허용(위험)"
    return f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content=30>
<title>Craig-Skill 헬스체크</title>
<style>
*{{box-sizing:border-box}}body{{font-family:-apple-system,system-ui,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}}
.wrap{{max-width:960px;margin:0 auto;padding:20px}}
h1{{font-size:20px;margin:0 0 4px}}.ts{{color:#8b949e;font-size:12px;margin-bottom:16px}}
.card{{background:#171a21;border:1px solid #262b36;border-radius:12px;padding:16px;margin-bottom:16px}}
.card h2{{font-size:14px;margin:0 0 12px;color:#9ecbff}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
td,th{{text-align:left;padding:8px 6px;border-bottom:1px solid #21262d;vertical-align:top}}
tr.bad td{{background:#2a1618}}.sub{{color:#8b949e;font-size:11px}}.log{{color:#8b949e;font-size:11px;font-family:ui-monospace,monospace}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}}
.kv{{background:#0f1115;border:1px solid #21262d;border-radius:8px;padding:10px}}
.kv .k{{color:#8b949e;font-size:11px}}.kv .v{{font-size:16px;font-weight:600;margin-top:2px}}
.badge{{font-size:12px}}
</style></head><body><div class=wrap>
<h1>🩺 Craig-Skill 헬스체크</h1><div class=ts>업데이트 {esc(d['ts'])} · 30초마다 자동 새로고침</div>

<div class=card><h2>서비스 (launchd)</h2>
<table><tr><th>서비스</th><th>상태</th><th>PID</th><th>가동</th><th>CPU</th><th>exit</th><th>최근 로그</th></tr>
{rows}</table></div>

<div class=card><h2>배포</h2>
<div class=grid>
<div class=kv><div class=k>git 동기화</div><div class=v>{git_badge}</div></div>
<div class=kv><div class=k>로컬 / 원격</div><div class=v style='font-size:13px'>{esc(g['local'])} / {esc(g['remote'])}</div></div>
<div class=kv><div class=k>최신 커밋</div><div class=v style='font-size:12px'>{esc(g['subject'][:40])}</div></div>
</div><div class=log style='margin-top:10px'>auto_deploy: {esc(d['deploy'])}</div></div>

<div class=card><h2>봇별 지표</h2>
<div class=grid>
<div class=kv><div class=k>🏔️ 등산 데이터</div><div class=v>{mt['mountains']}곳</div></div>
<div class=kv><div class=k>📺 유튜브 채널</div><div class=v>{yt['channels']}개</div></div>
<div class=kv><div class=k>📺 본 영상</div><div class=v>{yt['seen']}</div></div>
<div class=kv><div class=k>📚 학습 카드</div><div class=v>{sy['cards']}장</div></div>
<div class=kv><div class=k>🧠 복습 대기</div><div class=v>{sy['due']}장</div></div>
<div class=kv><div class=k>📥 인박스</div><div class=v>{sy['inbox']}개</div></div>
<div class=kv><div class=k>✅ 오늘 복습</div><div class=v>{sy['reviews_today']}장</div></div>
</div></div>

<div class=card><h2>시스템</h2>
<div class=grid>
<div class=kv><div class=k>절전</div><div class=v style='font-size:13px'>{sleep_badge}</div></div>
<div class=kv><div class=k>디스크</div><div class=v style='font-size:13px'>{esc(sysm['disk'])}</div></div>
</div><div class=log style='margin-top:10px'>{esc(sysm['uptime'])}</div></div>

</div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        try:
            data = gather()
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"error: {e}".encode())
            return
        if self.path.startswith("/health"):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            ctype = "application/json; charset=utf-8"
        else:
            body = render(data).encode("utf-8")
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args()
    print(f"[{datetime.now():%H:%M:%S}] 대시보드 시작 http://{a.host}:{a.port}", flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
