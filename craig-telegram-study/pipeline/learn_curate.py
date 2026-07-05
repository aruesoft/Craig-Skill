#!/usr/bin/env python3
"""
learn-curate — ② 가치평가 → 승인 → 병합. (설계안 §4-②)

- --run: 인박스 raw 노트를 평가해 텔레그램 승인 카드(✅승인/📁보관/🗑버림) 발송(스케줄/명령)
- --queue: route=curate 큐 처리 (/curate 명령 → 제안, 콜백 cur:ok|arc|del → 실행)
- 승격: 기존 주제노트에 **병합 우선**, 없으면 신규 생성 + 복습 스케줄 시작 + MOC 갱신.
- 원본 인박스는 status: promoted 마킹(삭제 안 함).
"""
import os
import re
import sys
import json
import glob
import hashlib
import argparse
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from learn_ingest import load_config, claude_json, slugify  # 재사용

USER_CFG = Path.home() / ".config" / "craig-telegram-study" / "config.json"
RELAY_STATE = Path.home() / ".config" / "craig-telegram-study" / "relay_state.json"


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def read_note(p):
    raw = open(p, encoding="utf-8").read()
    fm, body = {}, raw
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", raw, re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            mm = re.match(r"^(\w+):\s*(.*)$", line)
            if mm:
                fm[mm.group(1)] = mm.group(2).strip().strip('"')
        body = m.group(2)
    return fm, body


def inbox_raw(vault):
    d = Path(vault) / "00_Inbox"
    out = []
    if d.exists():
        for f in sorted(d.glob("*.md")):
            if f.name.startswith("_"):
                continue
            fm, _ = read_note(f)
            if fm.get("status", "raw") == "raw":
                out.append(f)
    return out


def area_topics(vault, area):
    titles = []
    for base in (Path(vault) / "02_Areas" / area, Path(vault) / "03_Resources"):
        if base.exists():
            for f in base.glob("*.md"):
                if not f.name.startswith("_MOC"):
                    titles.append(f.stem)
    return titles


def get_chat(cfg, fallback=None):
    if fallback:
        return fallback
    uc = json.load(open(USER_CFG)) if USER_CFG.exists() else {}
    c = str(uc.get("telegram_chat_id") or "")
    if not c and RELAY_STATE.exists():
        try:
            c = str(json.load(open(RELAY_STATE)).get("last_chat", ""))
        except Exception:
            pass
    return c


def outgoing(cfg, chat_id, text, buttons=None, edit_mid=None):
    if not chat_id:
        return
    q = Path(cfg["vault"]) / "_System" / "Queue" / "outgoing"
    q.mkdir(parents=True, exist_ok=True)
    fn = f"{datetime.now():%Y%m%d%H%M%S%f}.json"
    (q / fn).write_text(json.dumps({"chat_id": chat_id, "text": text, "buttons": buttons, "edit_mid": edit_mid},
                        ensure_ascii=False), encoding="utf-8")


def nid(path):
    return hashlib.sha1(str(path).encode()).hexdigest()[:10]


def _pending_path(vault):
    return Path(vault) / "_System" / "curate_pending.json"


def pending_load(vault):
    p = _pending_path(vault)
    if p.exists():
        try:
            return json.load(open(p))
        except Exception:
            pass
    return {}


def pending_save(vault, d):
    p = _pending_path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    json.dump(d, open(tmp, "w"), ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def refresh_moc(cfg, area):
    d = Path(cfg["vault"]) / "02_Areas" / area
    if not d.exists():
        return
    topics = sorted(f.stem for f in d.glob("*.md") if not f.name.startswith("_MOC"))
    (d / f"_MOC_{area}.md").write_text("\n".join(
        ["---", "type: moc", f"area: {area}", f"updated: {datetime.now():%Y-%m-%d}", "---", "",
         f"# 🗺️ {area} — Map of Content", "", "## 주제 노트"] +
        [f"- [[{t}]]" for t in topics] + [""]), encoding="utf-8")


# ───────── 제안 ─────────
def propose(cfg, chat_id):
    notes = inbox_raw(cfg["vault"])
    if not notes:
        outgoing(cfg, chat_id, "📭 인박스에 정리할 raw 항목이 없어요.")
        return
    pend = pending_load(cfg["vault"])
    sent = 0
    for f in notes[:10]:
        fm, body = read_note(f)
        area = fm.get("suggested_area", "unsorted")
        if area == "unsorted" or area not in cfg["categories"]:
            area = cfg["categories"][0]
        existing = area_topics(cfg["vault"], area)
        prompt = (f"인박스 노트를 학습 노트로 승격할지 평가.\n제목: {fm.get('title', f.stem)}\n영역: {area}\n"
                  f"기존 주제노트: {existing}\n내용: {body[:4000]}\n\n"
                  'JSON: {"value":"high|mid|low","reason":"한 줄","topic":"병합할 기존 제목 또는 새 제목","is_new":true}')
        plan = claude_json(cfg, prompt) or {}
        i = nid(f)
        pend[i] = {"path": str(f), "area": area}
        act = "새 주제노트 생성" if plan.get("is_new", True) else f"[[{plan.get('topic')}]] 에 병합"
        card = (f"📥 {fm.get('title', f.stem)}\n"
                f"가치: {plan.get('value', '?')} · {plan.get('reason', '')}\n"
                f"제안: {area} · {act}")
        buttons = [[{"text": "✅ 승인", "callback_data": f"cur:ok:{i}"},
                    {"text": "📁 보관", "callback_data": f"cur:arc:{i}"}],
                   [{"text": "🗑 버림", "callback_data": f"cur:del:{i}"}]]
        outgoing(cfg, chat_id, card, buttons)
        sent += 1
    pending_save(cfg["vault"], pend)
    outgoing(cfg, chat_id, f"🧩 {sent}건 검토 요청. 버튼으로 승인/보관/버림 해줘.")
    log(f"propose {sent}건")


# ───────── 승격(병합/신규) ─────────
def approve(cfg, i):
    pend = pending_load(cfg["vault"])
    info = pend.get(i)
    if not info or not os.path.exists(info["path"]):
        return "이미 처리됐거나 원본을 못 찾음"
    f = Path(info["path"])
    fm, body = read_note(f)
    area = info["area"]
    existing = area_topics(cfg["vault"], area)
    prompt = (f"인박스 노트를 '{area}' 영역의 주제 노트로 통합한다. 기존 주제노트: {existing}.\n"
              f"인박스 제목: {fm.get('title')}\n내용: {body[:6000]}\n\n"
              'JSON: {"topic_title":"주제노트 제목(관련 기존이 있으면 그 제목)","is_new":true,'
              '"section_md":"주제노트에 넣을 마크다운(## 핵심 요지/상세/열린 질문, 내 언어로 통합)"}')
    d = claude_json(cfg, prompt) or {}
    title = (d.get("topic_title") or fm.get("title") or f.stem).strip()
    area_dir = Path(cfg["vault"]) / "02_Areas" / area
    area_dir.mkdir(parents=True, exist_ok=True)
    tp = area_dir / f"{slugify(title)}.md"
    date = datetime.now().strftime("%Y-%m-%d")
    merged = tp.exists() and not d.get("is_new", True)
    if merged:
        raw = tp.read_text(encoding="utf-8")
        raw = re.sub(r"^updated:.*$", f"updated: {date}", raw, count=1, flags=re.M)
        raw = re.sub(r"^sources:\s*(\d+)", lambda m: f"sources: {int(m.group(1)) + 1}", raw, count=1, flags=re.M)
        if not raw.endswith("\n"):
            raw += "\n"
        raw += f"\n## {date} 통합\n\n{d.get('section_md', '')}\n\n> 출처: [[{f.stem}]]\n"
        tp.write_text(raw, encoding="utf-8")
    else:
        iv = int((cfg.get("srs") or {}).get("intervals", [1])[0])
        nxt = (datetime.now() + timedelta(days=iv)).strftime("%Y-%m-%d")
        tp.write_text("\n".join([
            "---", "type: topic", f"area: {area}", f"created: {date}", f"updated: {date}",
            "maturity: seed", "review:", f"  interval: {iv}", f"  next_review: {nxt}",
            "  reps: 0", "  lapses: 0", "sources: 1", "---", "",
            f"# {title}", "", d.get("section_md", ""), "", "## 출처", f"- [[{f.stem}]]", ""]), encoding="utf-8")
    refresh_moc(cfg, area)
    raw = f.read_text(encoding="utf-8")
    raw = re.sub(r"^status:.*$", "status: promoted", raw, count=1, flags=re.M)
    raw = re.sub(r"^promoted_to:.*$", f'promoted_to: "[[{title}]]"', raw, count=1, flags=re.M)
    f.write_text(raw, encoding="utf-8")
    pend.pop(i, None)
    pending_save(cfg["vault"], pend)
    return f"✅ 승격: [[{title}]] ({'병합' if merged else '신규 주제노트'})"


def archive(cfg, i, trash=False):
    pend = pending_load(cfg["vault"])
    info = pend.get(i)
    if not info:
        return "이미 처리됨"
    f = Path(info["path"])
    stem = f.stem
    if f.exists():
        dest = Path(cfg["vault"]) / "04_Archive" / ("trash" if trash else "inbox")
        dest.mkdir(parents=True, exist_ok=True)
        f.rename(dest / f.name)
    pend.pop(i, None)
    pending_save(cfg["vault"], pend)
    return ("🗑 버림" if trash else "📁 보관") + f": {stem}"


# ───────── 큐 ─────────
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
        if j.get("route") != "curate":
            continue
        chat = get_chat(cfg, j.get("chat_id"))
        if j.get("type") == "command":
            propose(cfg, chat)
        elif j.get("type") == "callback":
            parts = j.get("data", "").split(":")
            if len(parts) == 3:
                act, i = parts[1], parts[2]
                if act == "ok":
                    msg = approve(cfg, i)
                elif act == "arc":
                    msg = archive(cfg, i)
                elif act == "del":
                    msg = archive(cfg, i, trash=True)
                else:
                    msg = "✏️ 다른 위치 지정은 준비 중 — #ai/#biz 태그로 다시 보내줘"
                outgoing(cfg, chat, msg)
        f.rename(qdone / f.name)
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="인박스 평가→승인 카드 발송(스케줄)")
    ap.add_argument("--queue", action="store_true", help="curate 큐 처리")
    a = ap.parse_args()
    cfg = load_config()
    if a.run:
        propose(cfg, get_chat(cfg))
    elif a.queue:
        print(f"{process_queue(cfg)}건 처리")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
