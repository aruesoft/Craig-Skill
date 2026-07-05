#!/usr/bin/env python3
"""
learn-garden — ②-a 볼트 정비(링크·태그·MOC). (설계안 §4-②a)

안전 원칙: 본문 의미는 안 건드림 — 평문으로 언급된 다른 주제노트 제목을 [[위키링크]]로 변환,
MOC 리프레시, 고아 노트 표시만. --run(스케줄/명령) / --queue(route=garden 명령).
"""
import os
import re
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from learn_ingest import load_config
from learn_curate import read_note, refresh_moc, get_chat, outgoing


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def topic_files(vault):
    out = []
    for base in [Path(vault) / "02_Areas"]:
        if base.exists():
            for f in base.rglob("*.md"):
                if not f.name.startswith("_MOC"):
                    out.append(f)
    r = Path(vault) / "03_Resources"
    if r.exists():
        out += [f for f in r.glob("*.md") if not f.name.startswith("_")]
    return out


def link_pass(files):
    """평문으로 언급된 다른 주제노트 제목 → [[링크]] 변환. 변환 수 반환."""
    titles = sorted({f.stem for f in files}, key=len, reverse=True)  # 긴 제목 우선
    total = 0
    for f in files:
        raw = f.read_text(encoding="utf-8")
        # frontmatter 보존
        m = re.match(r"^(---\n.*?\n---\n)?(.*)$", raw, re.DOTALL)
        head, body = (m.group(1) or ""), m.group(2)
        changed = 0
        for t in titles:
            if t == f.stem or len(t) < 3:
                continue
            # 이미 링크된/코드 아닌 평문만. 단어 경계.
            pat = re.compile(r"(?<!\[\[)(?<!\w)" + re.escape(t) + r"(?!\w)(?!\]\])")

            def _sub(mo):
                nonlocal changed
                changed += 1
                return f"[[{t}]]"
            body, cnt = pat.subn(_sub, body, count=3)  # 노트당 제목별 최대 3회
        if changed:
            f.write_text(head + body, encoding="utf-8")
            total += changed
    return total


def orphans(files):
    """들어오는 링크(다른 노트에서의 [[제목]])가 0인 노트."""
    referenced = set()
    for f in files:
        for m in re.findall(r"\[\[([^\]|#]+)", f.read_text(encoding="utf-8")):
            referenced.add(m.strip())
    return [f.stem for f in files if f.stem not in referenced]


def collect_tags(files):
    tags = {}
    for f in files:
        fm, _ = read_note(f)
        for t in re.findall(r"#([\w/\-]+)", fm.get("tags", "") if isinstance(fm.get("tags"), str) else ""):
            tags[t] = tags.get(t, 0) + 1
    return tags


def run_garden(cfg, chat_id=None):
    vault = cfg["vault"]
    files = topic_files(vault)
    if not files:
        if chat_id:
            outgoing(cfg, chat_id, "🌱 아직 주제 노트가 없어 정비할 게 없어요.")
        return
    n_links = link_pass(files)
    for area in cfg["categories"]:
        refresh_moc(cfg, area)
    orph = orphans(files)
    # 로그
    rev = Path(vault) / "_System" / "Review"
    rev.mkdir(parents=True, exist_ok=True)
    with open(rev / "garden-log.md", "a", encoding="utf-8") as lg:
        lg.write(f"- {datetime.now():%Y-%m-%d %H:%M} · 링크 +{n_links} · MOC 갱신 · 고아 {len(orph)}\n")
    msg = f"🌱 정비 완료: 링크 +{n_links} · MOC 갱신 · 고아 노트 {len(orph)}건"
    if orph:
        msg += "\n고아: " + ", ".join(orph[:8])
    if chat_id:
        outgoing(cfg, chat_id, msg)
    log(msg)


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
        if j.get("route") != "garden":
            continue
        run_garden(cfg, get_chat(cfg, j.get("chat_id")))
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
        run_garden(cfg, get_chat(cfg))
    elif a.queue:
        print(f"{process_queue(cfg)}건 처리")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
