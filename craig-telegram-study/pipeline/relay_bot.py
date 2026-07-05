#!/usr/bin/env python3
"""
relay_bot — 텔레그램 ↔ 큐 릴레이 (설계안 §5). 봇은 지능 없음:
  수신(메시지/사진/버튼) → _System/Queue/incoming/*.json 저장 (+ 트리거)
  발신 → _System/Queue/outgoing/*.json 폴링 → sendMessage/editMessageText → processed/

지능(요약·분류·복습)은 learn_* 처리기가 큐를 읽어 수행. 봇은 단순해서 안 죽는다.
실행: python relay_bot.py --listen | --once
"""
import os
import re
import sys
import json
import time
import glob
import argparse
import subprocess
from datetime import datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
USER_CFG = Path.home() / ".config" / "craig-telegram-study" / "config.json"
STATE = Path.home() / ".config" / "craig-telegram-study" / "relay_state.json"
PROC = {"ingest": "learn_ingest.py", "curate": "learn_curate.py",
        "garden": "learn_garden.py", "retro": "learn_retro.py", "weekly": "learn_weekly.py"}


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def load_config():
    cfg = json.load(open(HERE / "pipeline.config.json"))
    uc = json.load(open(USER_CFG)) if USER_CFG.exists() else {}
    cfg["token"] = os.environ.get("TELEGRAM_BOT_TOKEN") or uc.get("telegram_bot_token", "")
    cfg["chat_id"] = str(uc.get("telegram_chat_id", ""))
    cfg["vault"] = os.path.expanduser(cfg.get("vault"))
    cfg["qin"] = Path(cfg["vault"]) / "_System" / "Queue" / "incoming"
    cfg["qout"] = Path(cfg["vault"]) / "_System" / "Queue" / "outgoing"
    cfg["qdone"] = Path(cfg["vault"]) / "_System" / "Queue" / "processed"
    cfg["media"] = Path(cfg["vault"]) / "00_Inbox" / "_attachments" / "media"
    for d in (cfg["qin"], cfg["qout"], cfg["qdone"], cfg["media"]):
        d.mkdir(parents=True, exist_ok=True)
    return cfg


def load_state():
    if STATE.exists():
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    return {}


def save_state(s):
    tmp = STATE.with_suffix(".tmp")
    json.dump(s, open(tmp, "w"), ensure_ascii=False, indent=2)
    os.replace(tmp, STATE)


def authorized(cfg, chat_id):
    return (not cfg["chat_id"]) or str(chat_id) == cfg["chat_id"]


# ───────── 텔레그램 ─────────
def api(cfg, method, _http_timeout=20, **data):
    try:
        return requests.post(f"https://api.telegram.org/bot{cfg['token']}/{method}",
                             data=data, timeout=_http_timeout).json()
    except Exception as e:
        log(f"{method} 오류: {e}")
        return {}


def send(cfg, chat_id, text, buttons=None, edit_mid=None):
    d = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
    if buttons:
        d["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    if edit_mid:
        d["message_id"] = edit_mid
        return api(cfg, "editMessageText", **d)
    return api(cfg, "sendMessage", **d)


def download_photo(cfg, file_id, msg_id):
    r = api(cfg, "getFile", file_id=file_id)
    if not r.get("ok"):
        return None
    path = r["result"]["file_path"]
    try:
        fr = requests.get(f"https://api.telegram.org/file/bot{cfg['token']}/{path}", timeout=60)
        if fr.status_code != 200:
            return None
        ext = ".png" if path.lower().endswith(".png") else ".jpg"
        dest = cfg["media"] / f"{datetime.now():%Y%m%d}_{msg_id}{ext}"
        dest.write_bytes(fr.content)
        return str(dest)
    except Exception:
        return None


# ───────── 큐 ─────────
def enqueue(cfg, item, route):
    item["route"] = route
    item["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fn = f"{datetime.now():%Y%m%d%H%M%S}_{item.get('msg_id', 0)}_{route}.json"
    (cfg["qin"] / fn).write_text(json.dumps(item, ensure_ascii=False), encoding="utf-8")


def trigger(cfg, route):
    """해당 처리기를 백그라운드로 즉시 실행(응답성). launchd 스케줄과 병행."""
    script = PROC.get(route)
    if not script or not (HERE / script).exists():
        return
    try:
        subprocess.Popen([sys.executable, str(HERE / script), "--queue"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log(f"trigger {route} 오류: {e}")


def flush_outgoing(cfg):
    for f in sorted(cfg["qout"].glob("*.json")):
        try:
            m = json.load(open(f))
            send(cfg, m["chat_id"], m.get("text", ""), m.get("buttons"), m.get("edit_mid"))
        except Exception as e:
            log(f"발신 오류({f.name}): {e}")
        try:
            f.rename(cfg["qdone"] / f.name)
        except Exception:
            f.unlink(missing_ok=True)


# ───────── 라우팅 ─────────
HELP = ("📚 학습 파이프라인 봇\n"
        "① 링크(웹/유튜브/인스타)·텍스트·사진을 보내면 인박스에 수집(동영상은 전사)\n"
        "② /curate → 승인 버튼으로 주제 노트로 승격·병합\n"
        "③ /review → 오늘 복습 카드(👍👌👎)\n"
        "• #ai #biz 카테고리 힌트\n"
        "명령: /status /find 키워드 /curate /garden /review /weekly")


def route_text(text):
    low = text.strip().lower()
    if low.startswith("/weekly"):
        return "weekly"
    if low.startswith("/garden"):
        return "garden"
    if low.startswith("/curate"):
        return "curate"
    if low.startswith(("/review", "/quiz", "/복습")):
        return "retro"
    return "ingest"


def handle_update(cfg, upd):
    cq = upd.get("callback_query")
    if cq:
        ch = cq.get("message", {}).get("chat", {}).get("id")
        api(cfg, "answerCallbackQuery", callback_query_id=cq.get("id"))
        if not authorized(cfg, ch):
            return
        data = cq.get("data", "")
        route = "curate" if data.startswith("cur:") else ("retro" if data.startswith("rev:") else "ingest")
        enqueue(cfg, {"type": "callback", "data": data, "chat_id": ch,
                      "msg_id": cq.get("message", {}).get("message_id")}, route)
        trigger(cfg, route)
        return

    msg = upd.get("message") or upd.get("channel_post") or {}
    ch = msg.get("chat", {}).get("id")
    if not authorized(cfg, ch):
        return
    if ch:  # 처리기들이 스케줄 발신 대상으로 쓰도록 마지막 채팅 저장
        st = load_state()
        if str(st.get("last_chat")) != str(ch):
            st["last_chat"] = ch
            save_state(st)
    text = (msg.get("text") or "").strip()
    caption = (msg.get("caption") or "").strip()
    photos = msg.get("photo")
    doc = msg.get("document")

    if photos or (doc and str(doc.get("mime_type", "")).startswith("image/")):
        send(cfg, ch, "🖼️ 이미지 수집 중…")
        fid = photos[-1]["file_id"] if photos else doc["file_id"]
        path = download_photo(cfg, fid, msg.get("message_id", 0))
        enqueue(cfg, {"type": "message", "text": caption, "attachments": [path] if path else [],
                      "chat_id": ch, "msg_id": msg.get("message_id")}, "ingest")
        trigger(cfg, "ingest")
        return
    if not text:
        return
    if text in ("/start", "/help"):
        send(cfg, ch, HELP)
        return
    if text.startswith("/status"):
        send(cfg, ch, status_text(cfg))
        return
    if text.startswith("/find"):
        q = text[5:].strip()
        send(cfg, ch, find_text(cfg, q))
        return
    route = route_text(text)
    if route == "ingest":
        send(cfg, ch, "🧠 수집 중…")
    enqueue(cfg, {"type": ("command" if text.startswith("/") else "message"),
                  "text": text, "chat_id": ch, "msg_id": msg.get("message_id")}, route)
    trigger(cfg, route)


# ───────── 빠른 조회(지능 없음) ─────────
def _count_inbox(cfg):
    d = Path(cfg["vault"]) / "00_Inbox"
    return sum(1 for f in d.glob("*.md") if not f.name.startswith("_")) if d.exists() else 0


def status_text(cfg):
    srs = {}
    p = Path(cfg["vault"]) / "_srs" / "schedule.json"
    if p.exists():
        try:
            srs = json.load(open(p))
        except Exception:
            pass
    today = datetime.now().strftime("%Y-%m-%d")
    due = sum(1 for c in srs.values() if (c.get("due") or "9999") <= today)
    return (f"📊 현황\n• 인박스 대기: {_count_inbox(cfg)}개\n"
            f"• 복습 대기: {due}장 / 전체 {len(srs)}장\n"
            f"/curate 정리 · /review 복습")


def find_text(cfg, q):
    if not q:
        return "사용법: /find 키워드"
    hits = []
    for sub in ("02_Areas", "03_Resources", "00_Inbox"):
        for f in (Path(cfg["vault"]) / sub).rglob("*.md"):
            try:
                if q.lower() in f.stem.lower() or q.lower() in f.read_text(encoding="utf-8").lower():
                    hits.append(f"- {f.stem}")
            except Exception:
                pass
            if len(hits) >= 10:
                break
    return (f"🔎 '{q}' 검색:\n" + "\n".join(hits[:10])) if hits else f"'{q}' 결과 없음"


# ───────── 루프 ─────────
def poll_once(cfg, long_poll=False):
    flush_outgoing(cfg)
    st = load_state()
    params = {"timeout": 50 if long_poll else 0}
    off = st.get("offset")
    if off is not None:
        params["offset"] = off
    r = api(cfg, "getUpdates", _http_timeout=(55 if long_poll else 20), **params)
    if not r.get("ok"):
        return
    last = None
    for upd in r.get("result", []):
        last = upd["update_id"]
        try:
            handle_update(cfg, upd)
        except Exception as e:
            log(f"handle 오류: {e}")
    if last is not None:
        st["offset"] = last + 1
        save_state(st)


def listen(cfg):
    log("relay_bot 시작 (큐 릴레이). 링크/텍스트/사진을 보내세요.")
    while True:
        try:
            poll_once(cfg, long_poll=True)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log(f"루프 오류(계속): {e}")
            time.sleep(5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", action="store_true")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    if not cfg["token"]:
        log("텔레그램 토큰 없음")
        return
    if a.listen:
        listen(cfg)
    else:
        poll_once(cfg, long_poll=False)


if __name__ == "__main__":
    main()
