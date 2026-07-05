#!/usr/bin/env python3
"""
learn-weekly — ④ 주간 리트로 리포트. (설계안 §4-④)

일요일 20:00. 이번 주 수집·승격·복습 통계, 약한 주제, 다음 주 복습 예정, Claude 코멘트.
출력: _System/Review/YYYY-Www_retro.md + 텔레그램 요약. --run / --queue(/weekly).
"""
import os
import re
import sys
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from learn_ingest import load_config, claude_json
from learn_curate import read_note, get_chat, outgoing
from learn_retro import topic_files, review_fields


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def gather(cfg):
    vault = cfg["vault"]
    now = datetime.now()
    since = now - timedelta(days=7)
    topics = topic_files(vault)
    new_week, weak, maturity = [], [], {}
    upcoming = []
    for f in topics:
        raw = f.read_text(encoding="utf-8")
        fm, _ = read_note(f)
        maturity[fm.get("maturity", "seed")] = maturity.get(fm.get("maturity", "seed"), 0) + 1
        try:
            created = fm.get("created", "")
            if created and created >= since.strftime("%Y-%m-%d"):
                new_week.append(f.stem)
        except Exception:
            pass
        rv = review_fields(raw)
        if rv["lapses"] >= 1:
            weak.append((f.stem, rv["lapses"]))
        if now.strftime("%Y-%m-%d") <= rv["next"] <= (now + timedelta(days=7)).strftime("%Y-%m-%d"):
            upcoming.append((f.stem, rv["next"]))
    inbox = Path(vault) / "00_Inbox"
    pending = sum(1 for f in inbox.glob("*.md") if not f.name.startswith("_")) if inbox.exists() else 0
    weak.sort(key=lambda x: -x[1])
    upcoming.sort(key=lambda x: x[1])
    return {"total": len(topics), "new": new_week, "weak": weak[:8],
            "upcoming": upcoming[:10], "maturity": maturity, "inbox_pending": pending}


def build(cfg, chat_id):
    g = gather(cfg)
    now = datetime.now()
    wk = now.strftime("%Y-W%V")
    ctx = (f"# 이번 주 통계\n- 주제 노트: {g['total']}개 (신규 {len(g['new'])}: {g['new']})\n"
           f"- maturity: {g['maturity']}\n- 인박스 대기: {g['inbox_pending']}\n"
           f"- 약한 주제(lapses): {g['weak']}\n- 다음 주 복습 예정: {g['upcoming']}")
    # 규칙 기반 본문 + 코멘트만 Claude
    comment = ""
    if cfg.get("anthropic_api_key"):
        cj = claude_json(cfg, "아래 주간 학습 데이터를 보고 격려+패턴 관찰 코멘트를 한두 줄로.\n"
                              f"{ctx}\n\nJSON: {{\"comment\":\"...\"}}") or {}
        comment = cj.get("comment", "")
    lines = [f"## 이번 주 요약",
             f"- 주제 노트 {g['total']}개 · 신규 {len(g['new'])}개 · 인박스 대기 {g['inbox_pending']}개",
             f"- maturity: " + ", ".join(f"{k} {v}" for k, v in g['maturity'].items()),
             "", "## 약한 주제 (집중 복습)"]
    lines += [f"- [[{t}]] (실패 {n})" for t, n in g['weak']] or ["- 없음 👍"]
    lines += ["", "## 다음 주 복습 예정"]
    lines += [f"- [[{t}]] — {d}" for t, d in g['upcoming']] or ["- 없음"]
    if comment:
        lines += ["", "## 코멘트", comment]
    body = "\n".join(lines)
    rev = Path(cfg["vault"]) / "_System" / "Review"
    rev.mkdir(parents=True, exist_ok=True)
    fp = rev / f"{wk}_retro.md"
    fp.write_text(f"---\ntype: retro\nweek: {wk}\ncreated: {now:%Y-%m-%d}\n---\n\n# 주간 리트로 {wk}\n\n{body}\n",
                  encoding="utf-8")
    if chat_id:
        outgoing(cfg, chat_id, f"🗓️ 주간 리트로 ({wk})\n\n{body[:2500]}\n\n📁 _System/Review/{wk}_retro.md")
    log(f"주간 리포트 생성: {fp.name}")


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
        if j.get("route") != "weekly":
            continue
        build(cfg, get_chat(cfg, j.get("chat_id")))
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
        build(cfg, get_chat(cfg))
    elif a.queue:
        print(f"{process_queue(cfg)}건 처리")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
