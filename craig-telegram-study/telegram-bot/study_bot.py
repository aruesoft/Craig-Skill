#!/usr/bin/env python3
"""
Craig_Telegram_Study_bot — 텔레그램으로 받은 링크/텍스트/이미지를 학습용으로 정리해 Obsidian(StudyVault)에 저장.

입력별 처리:
  · 텍스트           → 그대로 정리
  · 웹 링크          → 본문 추출(trafilatura)
  · 유튜브 링크      → 자막(yt-dlp) → 없으면 오디오 음성인식(faster-whisper)
  · 인스타/틱톡 등   → 캡션(yt-dlp, 쿠키 있으면) → 없으면 오디오 음성인식(쿠키 필요)
  · 이미지(사진)     → Claude 비전으로 텍스트화(손글씨/책 캡처) 후 정리
그 뒤 Claude 로 정리(요약·상세·인과관계·태그·기존 노트 [[링크]]) → StudyVault 기록 → 텔레그램 회신.

메시지 지시어:
  · #태그          → 사용자가 지정한 태그를 노트에 반드시 반영
  · [주제] 내용     → '주제' 노트에 이어서(append) 저장. 없으면 새로 생성.

실행:
  python study_bot.py --listen                 # 상시 long-poll(즉시 응답)
  python study_bot.py --once                   # 밀린 메시지 1회(cron)
  python study_bot.py --check "URL/텍스트"      # 텔레그램 없이 로컬 처리(볼트 기록·미리보기)
"""

import os
import re
import sys
import json
import time
import glob
import base64
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
TOPIC_RE = re.compile(r"^\s*\[([^\]\n]{1,60})\]\s*")
HASHTAG_RE = re.compile(r"(?:^|\s)#([^\s#]{1,40})")
# 로그인/접근 벽으로 URL만으론 본문 추출이 어려운 소셜 플랫폼(쿠키 설정 시 가능)
SOCIAL_RE = re.compile(r"(instagram\.com|tiktok\.com|facebook\.com|fb\.watch|threads\.net|x\.com|twitter\.com)")


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
    cfg.setdefault("transcribe_enabled", True)     # 자막 없을 때 오디오 음성인식
    cfg.setdefault("whisper_model", "small")       # faster-whisper 모델(base/small/medium/large-v3)
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
    for i in range(0, len(text), 3800):
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat_id, "text": text[i:i + 3800], "disable_web_page_preview": True},
                          timeout=20)
        except Exception as e:
            log(f"tg_send 오류: {e}")


def tg_download(cfg, file_id):
    """텔레그램 파일(사진 등)을 내려받아 (bytes, media_type) 반환."""
    token = cfg["telegram_bot_token"]
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getFile",
                         params={"file_id": file_id}, timeout=20).json()
        if not r.get("ok"):
            return None, None
        path = r["result"]["file_path"]
        fr = requests.get(f"https://api.telegram.org/file/bot{token}/{path}", timeout=60)
        if fr.status_code != 200:
            return None, None
        media = "image/jpeg"
        pl = path.lower()
        if pl.endswith(".png"):
            media = "image/png"
        elif pl.endswith(".webp"):
            media = "image/webp"
        elif pl.endswith(".gif"):
            media = "image/gif"
        return fr.content, media
    except Exception as e:
        log(f"tg_download 오류: {e}")
        return None, None


def _authorized(cfg, chat_id):
    want = str(cfg.get("telegram_chat_id", ""))
    return (not want) or str(chat_id) == want


# ─────────────────────────── 메시지 지시어 파싱 ───────────────────────────
def parse_directives(text):
    """메시지에서 [주제]와 #태그 지시어를 뽑고 본문에서 제거.
    → (topic|None, user_tags[list], cleaned_text)
    """
    text = text or ""
    topic = None
    m = TOPIC_RE.match(text)
    if m:
        topic = m.group(1).strip()
        text = text[m.end():]
    user_tags = [t.strip() for t in HASHTAG_RE.findall(text)]
    cleaned = HASHTAG_RE.sub(" ", text).strip()
    return topic, user_tags, cleaned


# ─────────────────────────── 콘텐츠 추출 ───────────────────────────
def _platform_name(url):
    for pat, name in [(r"instagram\.com", "인스타그램"), (r"tiktok\.com", "틱톡"),
                      (r"(facebook\.com|fb\.watch)", "페이스북"), (r"threads\.net", "스레드"),
                      (r"(x\.com|twitter\.com)", "X(트위터)")]:
        if re.search(pat, url):
            return name
    return "이 링크"


def extract_content(text, cfg, debug=False):
    """텍스트 → (출처, 본문|None, url|None, extracted_ok)."""
    m = URL_RE.search(text.strip())
    if not m:
        return "text", text.strip(), None, True
    url = m.group(0).rstrip(").,。")
    user_note = URL_RE.sub("", text).strip()

    body = None
    is_video = False
    if re.search(r"(youtube\.com|youtu\.be)", url):
        is_video = True
        body = _youtube_transcript(url, cfg, debug)
    elif SOCIAL_RE.search(url):
        is_video = True
        body = _ytdlp_caption(url, cfg, debug)
    else:
        body = _web_extract(url, debug)

    if not body and is_video and cfg.get("transcribe_enabled", True):
        body = transcribe_url(url, cfg, debug)  # 자막/캡션 없음 → 오디오 음성인식

    if body:
        content = f"[사용자 메모] {user_note}\n\n{body}" if user_note else body
        return url, content, url, True
    if len(user_note) >= 20:  # 추출 실패해도 사용자가 붙인 텍스트가 있으면 그걸로
        return url, f"[출처] {url}\n\n{user_note}", url, True
    return url, None, url, False


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


def _ytdlp_cookie_args(cfg):
    args = []
    ck = cfg.get("ytdlp_cookies")
    if ck and os.path.exists(os.path.expanduser(ck)):
        args += ["--cookies", os.path.expanduser(ck)]
    cb = cfg.get("ytdlp_cookies_from_browser")
    if cb:
        args += ["--cookies-from-browser", cb]
    return args


def _ytdlp_base(cfg):
    exe = shutil.which("yt-dlp")
    return ([exe] if exe else [sys.executable, "-m", "yt_dlp"]) + _ytdlp_cookie_args(cfg)


def _ytdlp_caption(url, cfg, debug=False):
    """인스타/틱톡 등 제목+캡션(description) 추출. 로그인 벽이면 None."""
    try:
        r = subprocess.run(_ytdlp_base(cfg) + ["--no-warnings", "--skip-download",
                           "--print", "%(title)s\n%(description)s", url],
                           capture_output=True, text=True, timeout=90)
    except Exception as e:
        if debug:
            log(f"yt-dlp caption 실패: {e}")
        return None
    out = (r.stdout or "").strip()
    if r.returncode != 0 or len(out) < 20:
        if debug:
            log(f"yt-dlp caption 실패/빈응답: {(r.stderr or '').strip()[:140]}")
        return None
    return out


def _youtube_transcript(url, cfg, debug=False):
    with tempfile.TemporaryDirectory() as td:
        try:
            subprocess.run(
                _ytdlp_base(cfg) + ["--skip-download", "--write-subs", "--write-auto-subs",
                                    "--sub-langs", "ko,en,ko-orig,en-orig,-live_chat", "--sub-format", "vtt/best",
                                    "--retries", "3", "--no-warnings", "--quiet",
                                    "-o", os.path.join(td, "%(id)s.%(ext)s"), url],
                check=False, timeout=90, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            if debug:
                log(f"yt-dlp 자막 실패: {e}")
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


# ─────────────────────────── 음성인식 (faster-whisper) ───────────────────────────
def transcribe_url(url, cfg, debug=False):
    """자막/캡션이 없는 영상 → 오디오 다운로드 후 음성인식. (IG 등은 쿠키 필요)"""
    log("자막/캡션 없음 → 오디오 다운로드 후 음성인식(whisper)…")
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "audio.%(ext)s")
        try:
            subprocess.run(_ytdlp_base(cfg) + ["-x", "--audio-format", "mp3", "-o", out,
                           "--no-warnings", "--quiet", url],
                           check=False, timeout=300, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            if debug:
                log(f"오디오 다운로드 실패: {e}")
            return None
        files = glob.glob(os.path.join(td, "audio.*"))
        if not files:
            log("오디오 확보 실패(비공개/쿠키 필요 가능)")
            return None
        return _whisper(files[0], cfg, debug)


def _whisper(audio_path, cfg, debug=False):
    """음성인식: faster-whisper(있으면) → openai-whisper 순으로 시도."""
    model_name = cfg.get("whisper_model", "small")
    # 1) faster-whisper (빠름, 설치 가능한 환경에서)
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segs, _ = model.transcribe(audio_path)
        text = " ".join(s.text.strip() for s in segs).strip()
        if text:
            log(f"음성인식 완료(faster-whisper, {len(text)}자)")
        return text or None
    except ImportError:
        pass
    except Exception as e:
        log(f"faster-whisper 오류(→openai-whisper 시도): {e}")
    # 2) openai-whisper (torch 기반, ffmpeg CLI 사용)
    try:
        import whisper
        valid = {"tiny", "base", "small", "medium", "large", "large-v2", "large-v3"}
        m = whisper.load_model(model_name if model_name in valid else "small")
        r = m.transcribe(audio_path)
        text = (r.get("text") or "").strip()
        if text:
            log(f"음성인식 완료(whisper, {len(text)}자)")
        return text or None
    except ImportError:
        log("whisper 미설치 → pip install openai-whisper (또는 faster-whisper)")
        return None
    except Exception as e:
        log(f"whisper 오류: {e}")
        return None


# ─────────────────────────── 볼트 인덱스 ───────────────────────────
def vault_index(vault_dir):
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
SYSTEM = (
    "너는 개인 학습 지식베이스(옵시디언)의 큐레이터다. 사용자가 보낸 학습 자료(텍스트·웹·영상 자막·"
    "이미지 속 글)를 학습·복습에 최적화된 노트로 정리한다. 원자적이고 자기설명적으로 쓰고, 인과관계를 "
    "명시하며, 기존 노트와 연결하고, 계층형 태그를 붙인다. 반드시 지정한 JSON만 출력한다(설명·코드펜스 금지).")


def _extract_json(raw):
    s = raw.strip()
    i, j = s.find("{"), s.rfind("}")
    if i == -1 or j == -1 or j < i:
        raise json.JSONDecodeError("no json object", s, 0)
    return json.loads(s[i:j + 1])


def _build_prompt(source, existing, content_or_hint, user_tags, is_image):
    ut = (" ".join("#" + t for t in user_tags)) if user_tags else "(없음)"
    src_section = (
        f"# 자료 출처\n{source}\n\n"
        f"# 사용자 지정 태그 (반드시 tags 에 포함)\n{ut}\n\n"
        f"# 기존 노트/개념 (링크 대상 후보 — 이 목록에 실제 있는 제목만 links 로)\n{existing}\n\n")
    if is_image:
        body_section = (f"# 학습 자료\n첨부한 이미지(사용자의 손글씨 노트 또는 책/자료 캡처)를 읽어 "
                        f"텍스트로 옮긴 뒤 정리하라. 이미지 속 글이 흐리면 최대한 판독하고 불확실한 부분은 그대로 두라.\n"
                        f"사용자 메모: {content_or_hint or '(없음)'}\n\n")
    else:
        body_section = f"# 학습 자료 원문\n{content_or_hint[:100000]}\n\n"
    schema = (
        "# 출력 JSON 스키마\n"
        "{\n"
        '  "title": "간결하고 검색 가능한 노트 제목(특수문자 금지)",\n'
        '  "one_line": "핵심 한 줄(텔레그램 회신용)",\n'
        '  "tags": ["분야/하위", ...],\n'
        '  "summary_md": "마크다운 본문",\n'
        '  "links": ["기존 목록에 실제 있는 제목만"],\n'
        '  "new_concepts": ["본문에서 [[..]]로 참조했으나 아직 없는 개념 제목"]\n'
        "}\n\n"
        "# 규칙\n"
        "- 한국어. 자료에 근거해 정리하고 지어내지 않는다.\n"
        "- tags: 3~6개, 계층형(예: 경제/금리), 소문자, 공백은 하이픈. 사용자 지정 태그를 반드시 포함.\n"
        "- summary_md 섹션: '## 핵심 요약' → '## 상세 정리' → '## 인과관계'(A → B) → '## 왜 중요한가/응용'. "
        "본문의 중요한 개념은 [[개념]] 위키링크로.\n"
        "- links 는 기존 목록에 실제 존재하는 제목만(없으면 빈 배열).")
    return src_section + body_section + schema


def organize(source, existing, cfg, content=None, image=None, image_media="image/jpeg",
             user_tags=None, debug=False):
    import anthropic
    key = cfg["anthropic_api_key"]
    if not key:
        log("anthropic 키 없음")
        return None
    existing_txt = "\n".join(
        f"- [[{i['title']}]] ({i['kind']}) {' '.join('#' + t for t in i['tags'])}"
        for i in existing[:400]) or "(아직 없음)"
    prompt = _build_prompt(source, existing_txt, content, user_tags, is_image=bool(image))

    user_content = []
    if image:
        user_content.append({"type": "image",
                             "source": {"type": "base64", "media_type": image_media,
                                        "data": base64.b64encode(image).decode()}})
    user_content.append({"type": "text", "text": prompt})

    raw = ""
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(model=cfg["claude_model"], max_tokens=4096,
                                       system=SYSTEM, messages=[{"role": "user", "content": user_content}])
        raw = "".join(b.text for b in resp.content if b.type == "text").strip()
        data = _extract_json(raw)
    except json.JSONDecodeError:
        log("Claude JSON 파싱 실패 — 원문 저장")
        data = {"title": f"학습노트 {datetime.now():%Y%m%d-%H%M}", "one_line": "자동정리(형식 파싱 실패)",
                "tags": ["inbox"], "summary_md": raw or "(내용 없음)", "links": [], "new_concepts": []}
    except Exception as e:
        log(f"Claude 오류: {e}")
        return None

    # 사용자 지정 태그 강제 병합(맨 앞)
    tags = [t for t in (user_tags or [])]
    for t in data.get("tags", []) or []:
        if t not in tags:
            tags.append(t)
    data["tags"] = tags
    # 링크/개념 문자열에서 [[ ]] 제거(중복 방지)
    def _clean(x):
        return re.sub(r"^\[+|\]+$", "", str(x).strip()).strip()
    data["links"] = [_clean(l) for l in (data.get("links") or []) if _clean(l)]
    data["new_concepts"] = [_clean(c) for c in (data.get("new_concepts") or []) if _clean(c)]
    return data


# ─────────────────────────── 볼트 쓰기 ───────────────────────────
def slugify(title):
    s = re.sub(r'[\\/:*?"<>|#\[\]]', " ", title or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s[:80] or "noname"


def _frontmatter(title, tags, source, date, extra=None):
    lines = ["---", "type: study-note", f'title: "{str(title).replace(chr(34), "")}"',
             "tags: [" + ", ".join(tags) + "]", f'source: "{source}"', f"created: {date}"]
    if extra:
        lines += extra
    lines += ["---", ""]
    return lines


def _make_concepts(vault, new_concepts, date):
    created = []
    concepts = Path(vault) / "Concepts"
    concepts.mkdir(parents=True, exist_ok=True)
    for c in new_concepts or []:
        cpath = concepts / f"{slugify(c)}.md"
        if not cpath.exists():
            cpath.write_text("\n".join([
                "---", "type: concept", "tags: [concept]", f"created: {date}", "---", "",
                f"# {c}", "", "> 개념 허브. 이 개념을 다루는 노트가 여기로 연결된다(백링크 참조).", "",
                "## 관련 노트", ""]), encoding="utf-8")
            created.append(c)
    return created


def write_note(vault_dir, data, source_url, debug=False):
    vault = Path(vault_dir)
    notes = vault / "Notes"
    notes.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    title = data.get("title") or f"학습노트 {date}"
    slug = slugify(title)
    fpath = notes / f"{date} {slug}.md"
    n = 2
    while fpath.exists():
        fpath = notes / f"{date} {slug} ({n}).md"
        n += 1
    body = (data.get("summary_md") or "").strip()
    lines = _frontmatter(title, data.get("tags", []), source_url or "text", date) + [f"# {title}", "", body, ""]
    if data.get("links"):
        lines += ["", "## 관련 (인과·연관)"] + [f"- [[{l}]]" for l in data["links"]]
    fpath.write_text("\n".join(lines), encoding="utf-8")
    created = _make_concepts(vault, data.get("new_concepts"), date)
    return fpath, created, False  # created_flag False = 새 노트(topic append 아님)


def append_to_topic(vault_dir, topic, data, source_url, debug=False):
    """[주제] 노트에 이어서 저장. 없으면 새로 생성. 태그는 병합."""
    vault = Path(vault_dir)
    notes = vault / "Notes"
    notes.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    fpath = notes / f"{slugify(topic)}.md"  # 주제 노트는 안정 파일명(날짜 접두사 없음)
    body = (data.get("summary_md") or "").strip()
    new_tags = data.get("tags", []) or []
    entry_head = f"## {date} — {data.get('one_line', '') or (source_url or '추가')}".rstrip(" —")
    entry = [entry_head, "", body]
    if data.get("links"):
        entry += ["", "**관련:** " + " · ".join(f"[[{l}]]" for l in data["links"])]

    existed = fpath.exists()
    if not existed:
        lines = _frontmatter(topic, new_tags, source_url or "text", date, extra=[f"updated: {date}"]) + \
            [f"# {topic}", "", "> 주제 노트 — 관련 학습을 시간순으로 누적한다.", ""] + entry + [""]
        fpath.write_text("\n".join(lines), encoding="utf-8")
    else:
        raw = fpath.read_text(encoding="utf-8")
        # 태그 병합
        mt = re.search(r"^tags:\s*\[([^\]]*)\]\s*$", raw, flags=re.MULTILINE)
        if mt:
            cur = [t.strip() for t in mt.group(1).split(",") if t.strip()]
            for t in new_tags:
                if t not in cur:
                    cur.append(t)
            raw = raw[:mt.start()] + "tags: [" + ", ".join(cur) + "]" + raw[mt.end():]
        # updated 갱신(있으면)
        raw = re.sub(r"^updated:.*$", f"updated: {date}", raw, count=1, flags=re.MULTILINE)
        if not raw.endswith("\n"):
            raw += "\n"
        raw += "\n" + "\n".join(entry) + "\n"
        fpath.write_text(raw, encoding="utf-8")

    created = _make_concepts(vault, data.get("new_concepts"), date)
    return fpath, created, existed  # existed=True → 이어붙임, False → 새 주제노트


# ─────────────────────────── 파이프라인 ───────────────────────────
def _finish(cfg, data, source_url, topic, debug):
    vault = cfg["study_vault_dir"]
    if topic:
        fpath, new_concepts, appended = append_to_topic(vault, topic, data, source_url, debug)
        head = f"➕ [{topic}] 에 이어붙였어요" if appended else f"🆕 [{topic}] 주제 노트를 새로 만들었어요"
    else:
        fpath, new_concepts, _ = write_note(vault, data, source_url, debug)
        head = "✅ 정리 완료"
    rel = fpath.relative_to(Path(vault))
    reply = (f"{head}\n📝 {data.get('title')}\n"
             f"🏷️ {' '.join('#' + t for t in data.get('tags', []))}\n"
             f"💡 {data.get('one_line', '')}\n📁 {rel}")
    if data.get("links"):
        reply += "\n🔗 연결: " + ", ".join(data["links"][:6])
    if new_concepts:
        reply += "\n🆕 개념: " + ", ".join(new_concepts)
    return fpath, reply


def process(text, cfg, debug=False):
    topic, user_tags, cleaned = parse_directives(text)
    source, content, url, ok = extract_content(cleaned, cfg, debug)
    if not ok:
        plat = _platform_name(url or "")
        return None, (
            f"⚠️ {plat} 링크는 로그인/접근 제한으로 내용을 자동으로 가져오지 못했어요.\n"
            f"• 캡션·자막·핵심 텍스트를 링크와 함께 붙여넣기\n"
            f"• (관리자) config 의 ytdlp_cookies 에 로그인 쿠키 지정 시 자동 추출/음성인식\n"
            f"※ 내용이 없어 노트는 만들지 않았어요(정크 노트 방지).")
    if not content or len(content.strip()) < 5:
        return None, "내용이 비어 있어 정리하지 못했어요. 링크나 학습 텍스트를 보내주세요."
    log(f"콘텐츠 추출: {source} ({len(content)}자){' [주제:'+topic+']' if topic else ''}")
    data = organize(source, vault_index(cfg["study_vault_dir"]), cfg,
                    content=content, user_tags=user_tags, debug=debug)
    if not data:
        return None, "Claude 정리에 실패했어요 (anthropic 키·네트워크 확인)."
    return _finish(cfg, data, url, topic, debug)


def process_image(image, media, caption, cfg, debug=False):
    topic, user_tags, cleaned = parse_directives(caption or "")
    log(f"이미지 수신({len(image)}바이트){' [주제:'+topic+']' if topic else ''} → 비전 정리")
    data = organize("image", vault_index(cfg["study_vault_dir"]), cfg,
                    content=cleaned, image=image, image_media=media, user_tags=user_tags, debug=debug)
    if not data:
        return None, "이미지 정리에 실패했어요 (anthropic 키·네트워크 확인)."
    return _finish(cfg, data, "image", topic, debug)


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
        chat_id = msg.get("chat", {}).get("id")
        if not _authorized(cfg, chat_id):
            continue
        photos = msg.get("photo")
        doc = msg.get("document")
        text = (msg.get("text") or "").strip()
        caption = (msg.get("caption") or "").strip()

        try:
            if photos:  # 사진(이미지) — 가장 큰 해상도
                tg_send(cfg, chat_id, "🖼️ 이미지 읽는 중…")
                img, media = tg_download(cfg, photos[-1]["file_id"])
                if not img:
                    tg_send(cfg, chat_id, "이미지를 받지 못했어요. 다시 보내주세요.")
                    continue
                _, reply = process_image(img, media, caption, cfg, debug)
            elif doc and str(doc.get("mime_type", "")).startswith("image/"):  # 이미지 파일
                tg_send(cfg, chat_id, "🖼️ 이미지 읽는 중…")
                img, media = tg_download(cfg, doc["file_id"])
                _, reply = process_image(img, media or doc.get("mime_type"), caption, cfg, debug) \
                    if img else (None, "이미지를 받지 못했어요.")
            elif text:
                if text in ("/start", "/help"):
                    tg_send(cfg, chat_id,
                            "📚 학습봇입니다. 아래를 보내면 옵시디언에 정리해 드려요.\n"
                            "• 웹/유튜브/인스타 링크, 학습 텍스트, 노트·책 사진(이미지)\n"
                            "• #태그 를 넣으면 그 태그를 반영\n"
                            "• [주제] 로 시작하면 해당 주제 노트에 이어서 저장(없으면 새로)\n"
                            "예) [금리] #경제 https://... / (책 사진) #독서")
                    continue
                log(f"수신: {text[:60]}")
                tg_send(cfg, chat_id, "🧠 정리 중…")
                _, reply = process(text, cfg, debug)
            else:
                continue
        except Exception as e:
            reply = f"❌ 처리 오류: {e}"
            log(reply)
        tg_send(cfg, chat_id, reply)
    if last is not None:
        st["telegram_update_offset"] = last + 1
        save_state(st)


def listen(cfg, debug=False):
    log("학습봇 리스너 시작 (long-poll). 링크/텍스트/이미지를 보내세요. Ctrl+C 로 종료.")
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
    ap.add_argument("--listen", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--check", metavar="TEXT", help="텔레그램 없이 로컬 처리(텍스트/URL)")
    ap.add_argument("--image", metavar="PATH", help="로컬 이미지 파일로 비전 정리 테스트")
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    if a.image:
        data = open(a.image, "rb").read()
        media = "image/png" if a.image.lower().endswith(".png") else "image/jpeg"
        _, reply = process_image(data, media, a.check or "", cfg, a.debug)
        print("\n" + reply + "\n")
    elif a.check:
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
