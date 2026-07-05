#!/usr/bin/env python3
"""
learn-retro — ③ 일일 복습(간격 반복). (설계안 §4-③)

SM-2 라이트 간격: 1→3→7→21→60일.  👍쉬움(×1.3) / 👌보통 / 👎어려움(1일 리셋, lapses+1)
- --run: next_review ≤ 오늘인 주제노트(일 상한 5) → 회상 질문 카드 발송 + daily-queue.md
- --queue: rev:show(정답 공개) / rev:easy|ok|hard(채점→주제노트 review frontmatter 갱신)
복습 스케줄은 주제노트 frontmatter(review:)에 저장.
"""
import os
import re
import sys
import json
import glob
import argparse
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from learn_ingest import load_config, claude_json
from learn_curate import read_note, get_chat, outgoing, nid

INTERVALS = [1, 3, 7, 21, 60]


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def _pending(vault):
    return Path(vault) / "_System" / "retro_pending.json"


def pend_load(vault):
    p = _pending(vault)
    return json.load(open(p)) if p.exists() else {}


def pend_save(vault, d):
    p = _pending(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    json.dump(d, open(tmp, "w"), ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def topic_files(vault):
    out = []
    b = Path(vault) / "02_Areas"
    if b.exists():
        out += [f for f in b.rglob("*.md") if not f.name.startswith("_MOC")]
    return out


def review_fields(raw):
    """frontmatter review: 블록에서 next_review/interval/reps/lapses 추출."""
    def g(k, d):
        m = re.search(rf"^\s+{k}:\s*(.+)$", raw, re.M)
        return m.group(1).strip() if m else d
    return {"next": g("next_review", "1900-01-01"), "interval": int(g("interval", "1") or 1),
            "reps": int(g("reps", "0") or 0), "lapses": int(g("lapses", "0") or 0)}


def set_review(raw, interval, next_review, reps, lapses, date):
    raw = re.sub(r"^(\s+interval:).*$", rf"\g<1> {interval}", raw, count=1, flags=re.M)
    raw = re.sub(r"^(\s+next_review:).*$", rf"\g<1> {next_review}", raw, count=1, flags=re.M)
    raw = re.sub(r"^(\s+reps:).*$", rf"\g<1> {reps}", raw, count=1, flags=re.M)
    raw = re.sub(r"^(\s+lapses:).*$", rf"\g<1> {lapses}", raw, count=1, flags=re.M)
    raw = re.sub(r"^(updated:).*$", rf"\g<1> {date}", raw, count=1, flags=re.M)
    return raw


def due_notes(vault):
    today = datetime.now().strftime("%Y-%m-%d")
    out = []
    for f in topic_files(vault):
        rv = review_fields(f.read_text(encoding="utf-8"))
        if rv["next"] <= today:
            out.append((f, rv))
    out.sort(key=lambda x: (x[1]["next"], -x[1]["lapses"]))  # 오래된·약한 것 먼저
    return out


def sched(rv, rating, date):
    reps, lapses = rv["reps"], rv["lapses"]
    if rating == "hard":
        reps, iv, lapses = 0, 1, lapses + 1
    else:
        reps = min(reps + 1, len(INTERVALS) - 1)
        iv = INTERVALS[reps]
        if rating == "easy":
            iv = round(iv * 1.3)
    nxt = (datetime.now() + timedelta(days=iv)).strftime("%Y-%m-%d")
    return iv, nxt, reps, lapses


# ───────── 카드 발송 ─────────
def run(cfg, chat_id):
    if not chat_id:
        return
    due = due_notes(cfg["vault"])
    cap = int((cfg.get("srs") or {}).get("daily_cap", 5))
    if not due:
        outgoing(cfg, chat_id, "🎉 오늘 복습할 주제가 없어요.")
        return
    pend = pend_load(cfg["vault"])
    sent = 0
    daily = [f"# {datetime.now():%Y-%m-%d} 복습 큐", ""]
    for f, rv in due[:cap]:
        fm, body = read_note(f)
        qa = claude_json(cfg, f"아래 주제 노트로 능동 회상 복습 카드를 만든다.\n제목: {f.stem}\n내용: {body[:5000]}\n\n"
                              'JSON: {"question":"핵심을 떠올리게 하는 질문 1개","answer":"간결한 정답/요지"}') or {}
        q = qa.get("question") or f"'{f.stem}'의 핵심을 떠올려보세요."
        i = nid(f)
        pend[i] = {"path": str(f), "answer": qa.get("answer", "")}
        outgoing(cfg, chat_id, f"🧠 복습: {f.stem}\n\n{q}",
                 [[{"text": "💡 정답 보기", "callback_data": f"rev:show:{i}"}]])
        daily.append(f"- [[{f.stem}]] — {q}")
        sent += 1
    pend_save(cfg["vault"], pend)
    rev = Path(cfg["vault"]) / "_System" / "Review"
    rev.mkdir(parents=True, exist_ok=True)
    (rev / "daily-queue.md").write_text("\n".join(daily) + "\n", encoding="utf-8")
    log(f"복습 카드 {sent}장 발송")


# ───────── 콜백 처리 ─────────
def handle(cfg, data, chat_id, msg_id):
    parts = data.split(":")
    if len(parts) != 3:
        return
    _, act, i = parts
    pend = pend_load(cfg["vault"])
    info = pend.get(i)
    if not info:
        outgoing(cfg, chat_id, "이미 처리된 카드예요.")
        return
    f = Path(info["path"])
    if act == "show":
        outgoing(cfg, chat_id, f"🧠 {f.stem}\n\n💡 {info.get('answer', '')}\n\n기억한 정도는?",
                 [[{"text": "👍 쉬움", "callback_data": f"rev:easy:{i}"},
                   {"text": "👌 보통", "callback_data": f"rev:ok:{i}"},
                   {"text": "👎 어려움", "callback_data": f"rev:hard:{i}"}]], edit_mid=msg_id)
        return
    if act in ("easy", "ok", "hard") and f.exists():
        date = datetime.now().strftime("%Y-%m-%d")
        raw = f.read_text(encoding="utf-8")
        rv = review_fields(raw)
        iv, nxt, reps, lapses = sched(rv, act, date)
        f.write_text(set_review(raw, iv, nxt, reps, lapses, date), encoding="utf-8")
        pend.pop(i, None)
        pend_save(cfg["vault"], pend)
        lab = {"easy": "쉬움", "ok": "보통", "hard": "어려움"}[act]
        outgoing(cfg, chat_id, f"✓ {f.stem} · {lab} · 다음 복습 {'내일' if iv <= 1 else str(iv) + '일 후'}",
                 edit_mid=msg_id)


def process_queue(cfg):
    qin = Path(cfg["vault"]) / "_System" / "Queue" / "incoming"
    qdone = Path(cfg["vault"]) / "_System" / "Queue" / "processed"
    for d in (qin, qdone):
        d.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(qin.glob("*.json")):
        try:
            j = json.load(open(f))
        except Exception:
            continue
        if j.get("route") != "retro":
            continue
        chat = get_chat(cfg, j.get("chat_id"))
        if j.get("type") == "command":
            run(cfg, chat)
        elif j.get("type") == "callback":
            handle(cfg, j.get("data", ""), chat, j.get("msg_id"))
        f.rename(qdone / f.name)
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--queue", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    if a.run:
        run(cfg, get_chat(cfg))
    elif a.queue:
        print(f"{process_queue(cfg)}건 처리")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
