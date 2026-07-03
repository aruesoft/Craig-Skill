#!/usr/bin/env python3
"""
Craig_Telegram_Study_bot — 텔레그램으로 받은 링크/텍스트를 학습용으로 정리해 Obsidian(StudyVault)에 저장.

흐름:
  텔레그램 메시지(URL | 텍스트)
   → 본문 추출 (웹=trafilatura, 유튜브=yt-dlp 자막, 텍스트=그대로)
   → Claude 로 학습 정리 (핵심요약·상세·인과관계·계층 태그·기존 노트 [[링크]] 제안)
   → StudyVault/Notes 에 노트 작성 + 새 개념은 Concepts/ 스텁 생성 (백링크로 연결)
   → 텔레그램으로 결과 회신

설정 (우선순위: 환경변수 > config.json):
  ~/.config/craig-telegram-study/config.json
  {"telegram_bot_token": "...", "telegram_chat_id": "", "anthropic_api_key": "...",
   "claude_model": "claude-sonnet-5", "study_vault_dir": "/path/to/StudyVault"}

실행:
  python study_bot.py --listen                 # 상시 long-poll (즉시 응답)
  python study_bot.py --once                   # 밀린 메시지 1회 (cron)
  python study_bot.py --check "URL 또는 텍스트"  # 텔레그램 없이 로컬 처리(볼트에 기록·미리보기)
"""

import os
import re
import sys
import json
import time
import glob
import shutil
import argparse
import tempfile
import subprocess
from datetime import datetime
from pathlib import Path

import requests

CONFIG_DIR = Path.home() / ".config" / "craig-telegram-study"
STATE_FILE = CONFIG_DIR / "state.json"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL = "claude-sonnet-5"
URL_RE = re.compile(r"https?://[^\s]+")


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ─────────────────────────── 설정 / 상태 ───────────────────────────
def load_config():
    cfg = {}
    for p in (SCRIPT_DIR / "config.json", CONFIG_DIR / "config.json"):
        if p.exists():
            try:
                cfg.update(json.load(open(p)))
            except Exception as e:
                log(f"config 파싱 실패({p}): {e}")
    cfg["telegram_bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get("telegram_bot_token", "")
    cfg["anthropic_api_key"] = os.environ.get("ANTHROPIC_API_KEY") or cfg.get("anthropic_api_key", "")
    cfg.setdefault("claude_model", DEFAULT_MODEL)
    cfg.setdefault("telegram_chat_id", "")
    vault = cfg.get("study_vault_dir") or str(Path.home() / "StudyVault")
    cfg["study_vault_dir"] = os.path.expanduser(vault)
    return cfg


def load_state():
    if STATE_FILE.exists():
        try:
            return json.load(open(STATE_FILE))
        except Exception:
            pass
    return {"telegram_update_offset": None}


def save_state(s):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    json.dump(s, open(tmp, "w"), ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


# ─────────────────────────── 텔레그램 ───────────────────────────
def tg_send(cfg, chat_id, text):
    token = cfg["telegram_bot_token"]
    if not token:
        return
    for i in range(0, len(text), 3800):  # 4096자 제한 → 분할
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": text[i:i + 3800], "disable_web_page_preview": True},
                timeout=20)
        except Exception as e:
            log(f"tg_send 오류: {e}")


def _authorized(cfg, chat_id):
    want = str(cfg.get("telegram_chat_id", ""))
    return (not want) or str(chat_id) == want  # 비우면 아무 채팅이나(개인봇)


# ─────────────────────────── 콘텐츠 추출 ───────────────────────────
def extract_content(text, debug=False):
    """메시지 → (출처라벨, 본문텍스트, 원본URL|None)."""
    m = URL_RE.search(text.strip())
    if not m:
        return "text", text.strip(), None
    url = m.group(0).rstrip(").,。")
    if re.search(r"(youtube\.com|youtu\.be)", url):
        body = _youtube_transcript(url, debug)
        if body:
            return url, body, url
    body = _web_extract(url, debug)
    if body:
        note = URL_RE.sub("", text).strip()  # URL 외 사용자 메모 보존
        if note:
            body = f"[사용자 메모] {note}\n\n{body}"
        return url, body, url
    return url, text.strip(), url  # 추출 실패 → 링크/원문만


def _web_extract(url, debug=False):
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None
        return trafilatura.extract(downloaded, include_comments=False, include_tables=True)
    except Exception as e:
        if debug:
            log(f"web_extract 실패: {e}")
        return None


def _youtube_transcript(url, debug=False):
    exe = shutil.which("yt-dlp")
    cmd = [exe] if exe else [sys.executable, "-m", "yt_dlp"]
    with tempfile.TemporaryDirectory() as td:
        try:
            subprocess.run(
                cmd + ["--skip-download", "--write-subs", "--write-auto-subs",
                       "--sub-langs", "ko,en,ko-orig,en-orig,-live_chat", "--sub-format", "vtt/best",
                       "--retries", "3", "--no-warnings", "--quiet",
                       "-o", os.path.join(td, "%(id)s.%(ext)s"), url],
                check=False, timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            if debug:
                log(f"yt-dlp 실패: {e}")
            return None
        vtts = glob.glob(os.path.join(td, "*.vtt"))
        return _vtt_to_text(vtts[0]) if vtts else None


def _vtt_to_text(path):
    try:
        raw = open(path, encoding="utf-8", errors="ignore").read()
    except Exception:
        return None
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if (not line or line == "WEBVTT" or "-->" in line
                or re.fullmatch(r"\d+", line) or line.startswith(("Kind:", "Language:", "NOTE"))):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if not line or (out and out[-1] == line):
            continue
        out.append(line)
    return " ".join(out).strip() or None


# ─────────────────────────── 볼트 인덱스 ───────────────────────────
def vault_index(vault_dir):
    """기존 Notes/Concepts 의 제목+태그 목록 (Claude 가 인과 링크 제안에 사용)."""
    items = []
    for sub in ("Notes", "Concepts"):
        d = Path(vault_dir) / sub
        if not d.exists():
            continue
        for f in sorted(d.glob("*.md")):
            tags = []
            try:
                head = "".join(open(f, encoding="utf-8").readlines()[:12])
                mt = re.search(r"tags:\s*\[([^\]]*)\]", head)
                if mt:
                    tags = [t.strip() for t in mt.group(1).split(",") if t.strip()]
            except Exception:
                pass
            items.append({"title": f.stem, "tags": tags, "kind": sub})
    return items


# ─────────────────────────── Claude 정리 ───────────────────────────
def _extract_json(raw):
    """응답에서 JSON 오브젝트만 뽑아 파싱 (코드펜스·군더더기 제거)."""
    s = raw.strip()
    i, j = s.find("{"), s.rfind("}")
    if i == -1 or j == -1 or j < i:
        raise json.JSONDecodeError("no json object", s, 0)
    return json.loads(s[i:j + 1])


def organize(content, source, existing, cfg, debug=False):
    import anthropic
    key = cfg["anthropic_api_key"]
    if not key:
        log("anthropic 키 없음 — config.anthropic_api_key 또는 ANTHROPIC_API_KEY 설정")
        return None

    existing_txt = "\n".join(
        f"- [[{i['title']}]] ({i['kind']}) {' '.join('#' + t for t in i['tags'])}"
        for i in existing[:400]) or "(아직 없음)"
    content = content[:100000]

    system = (
        "너는 개인 학습 지식베이스(옵시디언)의 큐레이터다. 사용자가 보낸 학습 자료를 "
        "학습·복습에 최적화된 노트로 정리한다. 원자적이고 자기설명적으로 쓰고, 인과관계를 명시하며, "
        "기존 노트와 연결하고, 계층형 태그를 붙인다. 반드시 지정한 JSON만 출력한다(설명·코드펜스 금지).")
    prompt = (
        f"# 자료 출처\n{source}\n\n"
        f"# 기존 노트/개념 (링크 대상 후보 — 이 목록에 실제 있는 제목만 links 로)\n{existing_txt}\n\n"
        f"# 학습 자료 원문\n{content}\n\n"
        "# 출력 JSON 스키마\n"
        "{\n"
        '  "title": "간결하고 검색 가능한 노트 제목(파일명이 됨, 특수문자 금지)",\n'
        '  "one_line": "핵심을 담은 한 줄(텔레그램 회신용)",\n'
        '  "tags": ["분야/하위", ...],\n'
        '  "summary_md": "마크다운 본문",\n'
        '  "links": ["기존 목록에 실제로 있는 제목만"],\n'
        '  "new_concepts": ["본문에서 [[..]]로 참조했으나 아직 없는 개념 제목"]\n'
        "}\n\n"
        "# 규칙\n"
        "- 한국어. 자료에 근거해 정리하고 지어내지 않는다.\n"
        "- tags: 3~6개, 계층형(예: 경제/금리, cs/알고리즘), 소문자, 공백은 하이픈.\n"
        "- summary_md 섹션 구성: '## 핵심 요약'(2~4문장) → '## 상세 정리'(불릿) → "
        "'## 인과관계'(A → B 형태로 원인·결과·메커니즘) → '## 왜 중요한가/응용'. "
        "본문 안에서 중요한 개념은 [[개념]] 위키링크로 표기한다.\n"
        "- links 는 위 기존 목록에 실제 존재하는 제목만(없으면 빈 배열). 새 개념은 new_concepts 로.\n"
        "- 마크다운 제목 기호 외 과한 장식 금지.")
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=cfg["claude_model"], max_tokens=4096,
            system=system, messages=[{"role": "user", "content": prompt}])
        raw = "".join(b.text for b in resp.content if b.type == "text").strip()
        return _extract_json(raw)
    except json.JSONDecodeError as e:
        log(f"Claude JSON 파싱 실패: {e} — 원문을 그대로 저장")
        return {"title": f"학습노트 {datetime.now():%Y%m%d-%H%M}", "one_line": "자동정리(형식 파싱 실패)",
                "tags": ["inbox"], "summary_md": raw if 'raw' in dir() else content[:2000],
                "links": [], "new_concepts": []}
    except Exception as e:
        log(f"Claude 오류: {e}")
        return None


# ─────────────────────────── 볼트 쓰기 ───────────────────────────
def slugify(title):
    s = re.sub(r'[\\/:*?"<>|#\[\]]', " ", title or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s[:80] or "noname"


def write_note(vault_dir, data, source_url, debug=False):
    vault = Path(vault_dir)
    notes, concepts = vault / "Notes", vault / "Concepts"
    notes.mkdir(parents=True, exist_ok=True)
    concepts.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    title = data.get("title") or f"학습노트 {date}"
    slug = slugify(title)

    fpath = notes / f"{date} {slug}.md"
    n = 2
    while fpath.exists():
        fpath = notes / f"{date} {slug} ({n}).md"
        n += 1

    tags = data.get("tags", []) or []
    links = data.get("links", []) or []
    body = (data.get("summary_md") or "").strip()
    fm = ["---", "type: study-note", f'title: "{str(title).replace(chr(34), "")}"',
          "tags: [" + ", ".join(tags) + "]", f'source: "{source_url or "text"}"',
          f"created: {date}", "---", ""]
    lines = fm + [f"# {title}", "", body, ""]
    if links:
        lines += ["", "## 관련 (인과·연관)"] + [f"- [[{l}]]" for l in links]
    fpath.write_text("\n".join(lines), encoding="utf-8")

    created = []
    for c in data.get("new_concepts", []) or []:
        cpath = concepts / f"{slugify(c)}.md"
        if not cpath.exists():
            cpath.write_text("\n".join([
                "---", "type: concept", "tags: [concept]", f"created: {date}", "---", "",
                f"# {c}", "", "> 개념 허브. 이 개념을 다루는 노트가 여기로 연결된다(백링크 참조).", "",
                "## 관련 노트", ""]), encoding="utf-8")
            created.append(c)
    return fpath, created


# ─────────────────────────── 파이프라인 ───────────────────────────
def process(text, cfg, debug=False):
    source, content, url = extract_content(text, debug)
    if not content or len(content.strip()) < 5:
        return None, "내용이 비어 있어 정리하지 못했어요. 링크나 학습 텍스트를 보내주세요."
    log(f"콘텐츠 추출: {source} ({len(content)}자)")
    existing = vault_index(cfg["study_vault_dir"])
    data = organize(content, source, existing, cfg, debug)
    if not data:
        return None, "Claude 정리에 실패했어요 (anthropic 키·네트워크 확인)."
    fpath, new_concepts = write_note(cfg["study_vault_dir"], data, url, debug)
    rel = fpath.relative_to(Path(cfg["study_vault_dir"]))
    reply = (f"✅ 정리 완료\n📝 {data.get('title')}\n"
             f"🏷️ {' '.join('#' + t for t in data.get('tags', []))}\n"
             f"💡 {data.get('one_line', '')}\n"
             f"📁 {rel}")
    if data.get("links"):
        reply += "\n🔗 연결: " + ", ".join(data["links"][:6])
    if new_concepts:
        reply += "\n🆕 개념: " + ", ".join(new_concepts)
    return fpath, reply


# ─────────────────────────── 텔레그램 루프 ───────────────────────────
def poll_once(cfg, long_poll=False, debug=False):
    token = cfg["telegram_bot_token"]
    if not token:
        log("텔레그램 토큰 없음")
        return
    st = load_state()
    offset = st.get("telegram_update_offset")
    params = {"timeout": 50 if long_poll else 0}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                         params=params, timeout=(55 if long_poll else 20)).json()
    except Exception as e:
        log(f"getUpdates 오류: {e}")
        return
    if not r.get("ok"):
        log(f"getUpdates 실패: {r.get('description')}")
        return
    last = None
    for upd in r.get("result", []):
        last = upd["update_id"]
        msg = upd.get("message") or upd.get("channel_post") or {}
        text = (msg.get("text") or "").strip()
        chat_id = msg.get("chat", {}).get("id")
        if not text or not _authorized(cfg, chat_id):
            continue
        if text in ("/start", "/help"):
            tg_send(cfg, chat_id,
                    "📚 학습봇입니다. 링크나 학습 내용을 보내면 옵시디언에 정리해 드려요.\n"
                    "- URL(웹/유튜브) 또는 텍스트 전송\n"
                    "- 요약·상세정리·인과관계·태그·기존 노트 연결까지 자동")
            continue
        log(f"수신: {text[:60]}")
        tg_send(cfg, chat_id, "🧠 정리 중…")
        try:
            _, reply = process(text, cfg, debug)
        except Exception as e:
            reply = f"❌ 처리 오류: {e}"
            log(reply)
        tg_send(cfg, chat_id, reply)
    if last is not None:
        st["telegram_update_offset"] = last + 1
        save_state(st)


def listen(cfg, debug=False):
    log("학습봇 리스너 시작 (long-poll). 링크/텍스트를 보내세요. Ctrl+C 로 종료.")
    while True:
        try:
            poll_once(cfg, long_poll=True, debug=debug)
        except KeyboardInterrupt:
            log("종료")
            break
        except Exception as e:
            log(f"리스너 오류(계속 실행): {e}")
            time.sleep(5)


def main():
    ap = argparse.ArgumentParser(description="Craig 텔레그램 학습봇")
    ap.add_argument("--listen", action="store_true", help="long-poll 상시 대기")
    ap.add_argument("--once", action="store_true", help="밀린 메시지 1회 처리 (cron)")
    ap.add_argument("--check", metavar="TEXT", help="텔레그램 없이 로컬 처리(볼트 기록·미리보기)")
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    if a.check:
        fpath, reply = process(a.check, cfg, a.debug)
        print("\n" + reply + "\n")
        if fpath:
            print("작성 파일:", fpath)
    elif a.listen:
        listen(cfg, a.debug)
    elif a.once:
        poll_once(cfg, long_poll=False, debug=a.debug)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
