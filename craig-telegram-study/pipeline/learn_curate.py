#!/usr/bin/env python3
"""
learn-curate — ② 가치평가 → 승인 → 병합. (설계안 §4-② v2)

- --run: 인박스 raw 노트를 평가해 텔레그램 승인 카드 발송(스케줄). 쿨다운 내 재발송 안 함.
- --queue: route=curate 큐 처리 (/curate 명령 → 제안(강제), 콜백 실행)
- 콜백: cur:ok|arc|del|snz:<id> (승인/보관/버림/미루기) · cur:okall|arcall:<batch> (일괄)
  처리 결과는 해당 카드를 editMessageText 로 제자리 갱신.
- 승격: 기존 주제노트에 **병합 우선**, 없으면 신규 생성 + 복습 스케줄 시작 + MOC 갱신.
- 원본 인박스는 status: promoted 마킹(삭제 안 함). 가치평가는 curate_pending.json에 캐시.
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


VALUE_RANK = {"high": 0, "mid": 1, "low": 2}


def curcfg(cfg):
    c = cfg.get("curate") or {}
    return (int(c.get("batch", 5)), int(c.get("cooldown_days", 3)), int(c.get("snooze_days", 3)))


def summary_of(body):
    """노트의 '## 요약' 섹션 앞부분 — 카드만 보고 결정할 수 있게."""
    m = re.search(r"##\s*요약\s*\n+(.*?)(?=\n##|\Z)", body, re.DOTALL)
    if not m:
        return ""
    txt = " ".join(l.strip() for l in m.group(1).strip().splitlines() if l.strip())
    return (txt[:180] + "…") if len(txt) > 180 else txt


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


# ───────── 제안 (v2: 쿨다운·미루기·가치순·요약카드·일괄버튼·평가캐시) ─────────
def propose(cfg, chat_id, force=False):
    batch, cooldown, snooze_days = curcfg(cfg)
    vault = cfg["vault"]
    notes = inbox_raw(vault)
    if not notes:
        outgoing(cfg, chat_id, "📭 인박스에 정리할 raw 항목이 없어요.")
        return
    pend = pending_load(vault)
    today = datetime.now().date()

    items = [(f,) + read_note(f) for f in notes]
    items.sort(key=lambda x: x[0].name, reverse=True)                       # 최신 먼저
    items.sort(key=lambda x: VALUE_RANK.get(x[1].get("value", "mid"), 1))   # 가치 높은 순(안정 정렬)

    sel, waiting, snoozed = [], 0, 0
    for f, fm, body in items:
        i = nid(f)
        e = pend.get(i) or {}
        if not force:
            if (e.get("snooze_until") or "") > str(today):
                snoozed += 1
                continue
            pa = e.get("proposed_at") or ""
            try:
                if pa and (today - datetime.strptime(pa, "%Y-%m-%d").date()).days < cooldown:
                    waiting += 1
                    continue
            except ValueError:
                pass
        if len(sel) < batch:
            sel.append((f, fm, body, i, e))

    for f, fm, body, i, e in sel:
        area = fm.get("suggested_area", "unsorted")
        if area == "unsorted" or area not in cfg["categories"]:
            area = cfg["categories"][0]
        plan = e.get("eval")
        if not plan:
            existing = area_topics(vault, area)
            prompt = (f"인박스 노트를 학습 노트로 승격할지 평가.\n제목: {fm.get('title', f.stem)}\n영역: {area}\n"
                      f"기존 주제노트: {existing}\n내용: {body[:4000]}\n\n"
                      'JSON: {"value":"high|mid|low","reason":"한 줄","topic":"병합할 기존 제목 또는 새 제목","is_new":true}')
            plan = claude_json(cfg, prompt) or {}
        pend[i] = {"path": str(f), "area": area, "eval": plan, "proposed_at": str(today)}
        act = "새 주제노트 생성" if plan.get("is_new", True) else f"[[{plan.get('topic')}]] 에 병합"
        cap = (fm.get("captured") or "")[:10]
        summ = summary_of(body)
        card = (f"📥 {fm.get('title', f.stem)}\n"
                + (f"🗓 {cap} · " if cap else "") + f"가치 {plan.get('value', '?')} — {plan.get('reason', '')}\n"
                + (f"“{summ}”\n" if summ else "")
                + f"제안: {area} · {act}")
        buttons = [[{"text": "✅ 승인", "callback_data": f"cur:ok:{i}"},
                    {"text": "📁 보관", "callback_data": f"cur:arc:{i}"}],
                   [{"text": "🗑 버림", "callback_data": f"cur:del:{i}"},
                    {"text": f"⏰ {snooze_days}일 뒤", "callback_data": f"cur:snz:{i}"}]]
        outgoing(cfg, chat_id, card, buttons)

    if sel:
        bkey = f"{datetime.now():%m%d%H%M%S%f}"   # µs까지 — 연속 /curate 시 배치 덮어쓰기 방지
        batches = pend.setdefault("_batches", {})
        batches[bkey] = [i for _, _, _, i, _ in sel]
        for k in sorted(batches)[:-5]:   # 오래된 묶음 정리
            batches.pop(k, None)
        tail = (f"🧩 새 카드 {len(sel)}건"
                + (f" · 응답 대기 {waiting}건" if waiting else "")
                + f" · 인박스 raw {len(items)}건")
        bt = None
        if len(sel) > 1:
            bt = [[{"text": f"✅ 이번 {len(sel)}건 모두 승인", "callback_data": f"cur:okall:{bkey}"}],
                  [{"text": "📁 모두 보관", "callback_data": f"cur:arcall:{bkey}"}]]
        outgoing(cfg, chat_id, tail, bt)
    elif waiting or snoozed:
        outgoing(cfg, chat_id,
                 f"🧩 응답 대기 카드 {waiting}건" + (f" · 미루기 {snoozed}건" if snoozed else "")
                 + " — 이전 카드의 버튼을 그대로 누르면 처리돼요. 새 카드를 받으려면 /curate")
    pending_save(vault, pend)
    log(f"propose {len(sel)}건 (대기 {waiting} · 미루기 {snoozed} · raw {len(items)})")


# ───────── 승격(병합/신규) ─────────
def approve(cfg, i):
    pend = pending_load(cfg["vault"])
    info = pend.get(i)
    if not info or not os.path.exists(info["path"]):
        return "✔️ 이미 처리된 카드예요."
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
        return "✔️ 이미 처리된 카드예요."
    f = Path(info["path"])
    stem = f.stem
    if f.exists():
        dest = Path(cfg["vault"]) / "04_Archive" / ("trash" if trash else "inbox")
        dest.mkdir(parents=True, exist_ok=True)
        f.rename(dest / f.name)
    pend.pop(i, None)
    pending_save(cfg["vault"], pend)
    return ("🗑 버림" if trash else "📁 보관") + f": {stem}"


def snooze(cfg, i):
    _, _, days = curcfg(cfg)
    pend = pending_load(cfg["vault"])
    info = pend.get(i)
    if not info:
        return "✔️ 이미 처리된 카드예요."
    until = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    info["snooze_until"] = until
    pending_save(cfg["vault"], pend)
    return f"⏰ {until}까지 미룸: {Path(info['path']).stem}"


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
            propose(cfg, chat, force=True)   # /curate 는 쿨다운 무시하고 즉시 카드
        elif j.get("type") == "callback":
            parts = j.get("data", "").split(":")
            if len(parts) == 3:
                act, arg = parts[1], parts[2]
                mid = j.get("msg_id")        # 누른 카드를 제자리 갱신(editMessageText)
                if act in ("okall", "arcall"):
                    ids = (pending_load(cfg["vault"]).get("_batches") or {}).get(arg, [])
                    res = []
                    for i in ids:
                        r = approve(cfg, i) if act == "okall" else archive(cfg, i)
                        if not r.startswith("✔️"):
                            res.append(r)
                    msg = ("📦 일괄 처리 결과\n" + "\n".join(res)) if res else "✔️ 남은 카드가 없었어요."
                else:
                    if act == "ok":
                        msg = approve(cfg, arg)
                    elif act == "arc":
                        msg = archive(cfg, arg)
                    elif act == "del":
                        msg = archive(cfg, arg, trash=True)
                    elif act == "snz":
                        msg = snooze(cfg, arg)
                    else:
                        msg = "✏️ 다른 위치 지정은 준비 중 — #ai/#biz 태그로 다시 보내줘"
                outgoing(cfg, chat, msg, edit_mid=mid)
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
