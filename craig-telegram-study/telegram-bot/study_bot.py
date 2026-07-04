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
import hashlib
import argparse
import tempfile
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import requests

CONFIG_DIR = Path.home() / ".config" / "craig-telegram-study"
STATE_FILE = CONFIG_DIR / "state.json"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL = "claude-sonnet-5"

URL_RE = re.compile(r"https?://[^\s]+")
TOPIC_RE = re.compile(r"^\s*\[([^\]\n]{1,60})\]\s*")
HASHTAG_RE = re.compile(r"(?:^|\s)#([^\s#]{1,40})")
PROMOTE_RE = re.compile(r"(?:^|\s)!(?:학습|study|s)\b")  # 즉시 학습 노트로 승격
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
    """메시지에서 [주제]·#태그·!학습 지시어를 뽑고 본문에서 제거.
    → (topic|None, user_tags[list], promote[bool], cleaned_text)
    """
    text = text or ""
    topic = None
    m = TOPIC_RE.match(text)
    if m:
        topic = m.group(1).strip()
        text = text[m.end():]
    promote = bool(PROMOTE_RE.search(text))
    text = PROMOTE_RE.sub(" ", text)
    user_tags = [t.strip() for t in HASHTAG_RE.findall(text)]
    cleaned = HASHTAG_RE.sub(" ", text).strip()
    return topic, user_tags, promote, cleaned


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


def _build_prompt(source, existing, content_or_hint, user_tags, is_image, mode="full"):
    ut = (" ".join("#" + t for t in user_tags)) if user_tags else "(없음)"
    if mode == "inbox":
        # 경량 수집 — 요약·태그만(카드·상세·링크 없음)
        body = (f"# 학습 자료\n첨부 이미지를 읽어 " if is_image else "# 학습 자료 원문\n") + \
               (content_or_hint[:60000] if not is_image else f"(이미지) 메모: {content_or_hint or '(없음)'}") + "\n\n"
        schema = (
            "# 출력 JSON 스키마 (경량 수집용)\n{\n"
            '  "title": "간결한 제목(특수문자 금지)",\n'
            '  "one_line": "핵심 한 줄",\n'
            '  "tags": ["분야/하위", ...],\n'
            '  "summary_md": "핵심만 3~5줄(불릿 가능). 상세·카드 없이 가볍게."\n}\n\n'
            "# 규칙\n- 한국어. 자료 근거. tags 3~5개 계층형+사용자태그 포함. 나중에 선별할 수 있게 요점만.")
        return (f"# 자료 출처\n{source}\n\n# 사용자 지정 태그(반드시 포함)\n{ut}\n\n") + body + schema
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
        '  "new_concepts": ["본문에서 [[..]]로 참조했으나 아직 없는 개념 제목"],\n'
        '  "cards": [{"type": "basic", "q": "질문", "a": "정답"}, '
        '{"type": "cloze", "q": "핵심어를 ==이렇게== 표시한 완성 문장", "a": "가려진 핵심어"}]\n'
        "}\n\n"
        "# 규칙\n"
        "- 한국어. 자료에 근거해 정리하고 지어내지 않는다.\n"
        "- tags: 3~6개, 계층형(예: 경제/금리), 소문자, 공백은 하이픈. 사용자 지정 태그를 반드시 포함.\n"
        "- summary_md 섹션: '## 핵심 요약' → '## 상세 정리' → '## 인과관계'(A → B) → '## 왜 중요한가/응용'. "
        "본문의 중요한 개념은 [[개념]] 위키링크로.\n"
        "- links 는 기존 목록에 실제 존재하는 제목만(없으면 빈 배열).\n"
        "- cards: 능동 인출용 복습 카드 3~5개. 핵심 개념·인과관계를 묻는다(단순 암기 지양, '왜/어떻게' 위주). "
        "basic 은 질문/정답, cloze 는 문장에서 핵심어 하나를 ==표시==(q에 완성 문장, a에 그 핵심어). "
        "자료로 답할 수 있는 것만.\n"
        "- 기억을 돕는 니모닉·비유가 자연스럽게 있으면 summary_md 끝에 '## 기억법(니모닉)' 섹션을 짧게 추가(억지스러우면 생략).")
    return src_section + body_section + schema


def organize(source, existing, cfg, content=None, image=None, image_media="image/jpeg",
             user_tags=None, mode="full", debug=False):
    import anthropic
    key = cfg["anthropic_api_key"]
    if not key:
        log("anthropic 키 없음")
        return None
    existing_txt = "\n".join(
        f"- [[{i['title']}]] ({i['kind']}) {' '.join('#' + t for t in i['tags'])}"
        for i in existing[:400]) or "(아직 없음)"
    prompt = _build_prompt(source, existing_txt, content, user_tags, is_image=bool(image), mode=mode)

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
    # 카드 정규화(질문 있는 것만)
    cards = []
    for c in (data.get("cards") or []):
        if isinstance(c, dict) and str(c.get("q", "")).strip():
            cards.append({"type": c.get("type", "basic"),
                          "q": str(c["q"]).strip(),
                          "a": str(c.get("a", "")).strip()})
    data["cards"] = cards[:8]
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


# ── 복습 카드 (Phase 1: 능동 인출) — 노트에 SR-플러그인 포맷 + _srs 스케줄 등록 ──
def _srs_load(vault):
    p = Path(vault) / "_srs" / "schedule.json"
    if p.exists():
        try:
            return json.load(open(p))
        except Exception:
            pass
    return {}


def _srs_save(vault, data):
    d = Path(vault) / "_srs"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "schedule.json.tmp"
    json.dump(data, open(tmp, "w"), ensure_ascii=False, indent=2)
    os.replace(tmp, d / "schedule.json")


def _card_id(note_rel, q):
    return hashlib.sha1(f"{note_rel}::{q}".encode("utf-8")).hexdigest()[:12]


def render_cards(cards):
    """옵시디언 Spaced Repetition 플러그인 호환 카드 섹션 마크다운."""
    if not cards:
        return []
    lines = ["", "## 복습 카드 (능동 인출)", "", "#flashcard", ""]
    for c in cards:
        if c.get("type") == "cloze" and "==" in c.get("q", ""):
            lines.append(c["q"].replace("\n", " "))
        else:
            q = c["q"].replace("::", ":").replace("\n", " ")
            a = (c.get("a") or "").replace("\n", " ")
            lines.append(f"{q}::{a}")
        lines.append("")
    return lines


def register_cards(vault, note_rel, cards):
    """카드를 _srs/schedule.json 에 due=오늘로 등록(중복 id 스킵). 등록 수 반환."""
    if not cards:
        return 0
    sched = _srs_load(vault)
    today = datetime.now().strftime("%Y-%m-%d")
    n = 0
    for c in cards:
        cid = _card_id(note_rel, c["q"])
        if cid in sched:
            continue
        sched[cid] = {"note": note_rel, "type": c.get("type", "basic"),
                      "q": c["q"], "a": c.get("a", ""), "due": today,
                      "stability": None, "difficulty": None,
                      "reps": 0, "lapses": 0, "last": None, "created": today}
        n += 1
    _srs_save(vault, sched)
    return n


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
    lines += render_cards(data.get("cards"))
    fpath.write_text("\n".join(lines), encoding="utf-8")
    created = _make_concepts(vault, data.get("new_concepts"), date)
    note_rel = str(fpath.relative_to(vault))
    ncards = register_cards(vault, note_rel, data.get("cards"))
    return fpath, created, False, ncards  # False = 새 노트(topic append 아님)


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
    if data.get("cards"):
        entry += ["", "**복습 카드** #flashcard", ""]
        for c in data["cards"]:
            if c.get("type") == "cloze" and "==" in c.get("q", ""):
                entry.append(c["q"].replace("\n", " "))
            else:
                entry.append(f"{c['q'].replace('::', ':').replace(chr(10), ' ')}::{(c.get('a') or '').replace(chr(10), ' ')}")

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
    ncards = register_cards(vault, str(fpath.relative_to(vault)), data.get("cards"))
    return fpath, created, existed, ncards  # existed=True → 이어붙임


# ─────────────────────────── ① 수집(인박스) ───────────────────────────
def write_inbox(vault_dir, data, source_url):
    """경량 인박스 노트 저장(카드·복습 없음). → fpath"""
    vault = Path(vault_dir)
    inbox = vault / "0_Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    title = data.get("title") or f"인박스 {date}"
    fpath = inbox / f"{date} {slugify(title)}.md"
    n = 2
    while fpath.exists():
        fpath = inbox / f"{date} {slugify(title)} ({n}).md"
        n += 1
    body = (data.get("summary_md") or "").strip()
    lines = ["---", "type: inbox", "status: unread",
             f'title: "{str(title).replace(chr(34), "")}"',
             f'one_line: "{str(data.get("one_line", "")).replace(chr(34), "")}"',
             "tags: [" + ", ".join(data.get("tags", []) or []) + "]",
             f'source: "{source_url or "text"}"', f"created: {date}", "---", "",
             f"# {title}", "", body, "",
             "> 📥 인박스 — `/curate` 로 선별하거나 `!학습`/`[주제]` 로 학습 노트로 승격.", ""]
    fpath.write_text("\n".join(lines), encoding="utf-8")
    return fpath


# ─────────────────────────── ② 선별·재조합(큐레이션) ───────────────────────────
def _read_fm(path):
    """노트 프론트매터에서 title/one_line/tags/status 추출."""
    d = {"title": path.stem, "one_line": "", "tags": [], "status": "unread"}
    try:
        head = "".join(open(path, encoding="utf-8").readlines()[:15])
        for k in ("title", "one_line", "status"):
            m = re.search(rf'^{k}:\s*"?([^"\n]*)"?\s*$', head, flags=re.MULTILINE)
            if m:
                d[k] = m.group(1).strip()
        mt = re.search(r"tags:\s*\[([^\]]*)\]", head)
        if mt:
            d["tags"] = [t.strip() for t in mt.group(1).split(",") if t.strip()]
    except Exception:
        pass
    return d


def list_inbox(vault_dir):
    """인박스의 미승격(status!=promoted) 노트 목록."""
    items = []
    d = Path(vault_dir) / "0_Inbox"
    if not d.exists():
        return items
    for f in sorted(d.glob("*.md")):
        if f.name.startswith("_"):  # _안내 등 인프라 노트 제외
            continue
        fm = _read_fm(f)
        if fm.get("status") == "promoted":
            continue
        items.append({"file": f, "title": fm["title"], "one_line": fm["one_line"], "tags": fm["tags"]})
    return items


def curate(cfg, debug=False):
    """인박스를 주제별로 자동 클러스터링해 승격 후보를 제안. state 에 저장."""
    vault = cfg["study_vault_dir"]
    items = list_inbox(vault)
    if not items:
        return "📭 인박스가 비어 있어요. 링크·텍스트·이미지를 보내면 여기에 모입니다."
    listing = "\n".join(
        f"{i}. {it['title']} — {it['one_line']} {' '.join('#'+t for t in it['tags'])}"
        for i, it in enumerate(items))
    import anthropic
    key = cfg["anthropic_api_key"]
    if not key:
        return "anthropic 키가 없어 큐레이션을 못 해요."
    prompt = (
        "다음은 학습 인박스 항목들이다. 관련 있는 것끼리 묶어 '학습 노트로 승격할 그룹'을 제안하라.\n"
        "가치가 낮거나 스쳐가는 정보는 그룹에서 빼도 된다. 단독으로도 가치 있으면 1개짜리 그룹 가능.\n\n"
        f"{listing}\n\n"
        "아래 JSON만 출력:\n"
        '{ "groups": [ {"label": "묶음 주제", "members": [항목번호...], "reason": "왜 묶나(한 줄)"} ],'
        ' "skip": [가치 낮아 보류할 항목번호...] }')
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(model=cfg["claude_model"], max_tokens=2048,
                                       messages=[{"role": "user", "content": prompt}])
        raw = "".join(b.text for b in resp.content if b.type == "text")
        res = _extract_json(raw)
    except Exception as e:
        log(f"curate 오류: {e}")
        return "큐레이션 중 오류가 났어요."
    groups = res.get("groups", []) or []
    if not groups:
        return f"📥 인박스 {len(items)}개 — 아직 묶을 만한 게 뚜렷하지 않아요. 더 모은 뒤 다시 `/curate`."
    st = load_state()
    st["curate"] = {"files": [str(it["file"]) for it in items],
                    "groups": [{"label": g.get("label", ""), "members": g.get("members", [])} for g in groups]}
    save_state(st)
    lines = [f"🧩 인박스 {len(items)}개 → 승격 후보 {len(groups)}묶음:"]
    for gi, g in enumerate(groups, 1):
        mem = ", ".join(items[m]["title"] for m in g.get("members", []) if 0 <= m < len(items))
        lines.append(f"\n{gi}) [{g.get('label','')}]  ← {mem}\n   → {g.get('reason','')}")
    skip = res.get("skip", []) or []
    if skip:
        lines.append("\n⏭️ 보류: " + ", ".join(items[m]["title"] for m in skip if 0 <= m < len(items)))
    lines.append("\n\n승격: `/promote 1 3` (번호) 또는 `/promote all`")
    return "\n".join(lines)


def promote_groups(cfg, selection, debug=False):
    """/promote — 큐레이션 제안 중 선택 그룹을 학습 노트로 합성(재조합) + 카드 + 인박스 아카이브."""
    vault = cfg["study_vault_dir"]
    st = load_state()
    cur = st.get("curate")
    if not cur or not cur.get("groups"):
        return "먼저 `/curate` 로 후보를 만들어 주세요."
    files = [Path(p) for p in cur["files"]]
    groups = cur["groups"]
    sel = selection.strip().lower()
    idxs = list(range(len(groups))) if sel in ("all", "전체") else \
        [int(x) - 1 for x in re.findall(r"\d+", sel) if 0 < int(x) <= len(groups)]
    if not idxs:
        return "승격할 번호를 알려주세요. 예: `/promote 1 3` 또는 `/promote all`"
    done = []
    for gi in idxs:
        g = groups[gi]
        members = [files[m] for m in g.get("members", []) if 0 <= m < len(files) and files[m].exists()]
        if not members:
            continue
        # 멤버 본문을 합쳐 재조합 → 학습 노트(카드 포함)
        combined = f"[묶음 주제] {g.get('label','')}\n\n" + "\n\n---\n\n".join(
            f"[{p.stem}]\n{p.read_text(encoding='utf-8')}" for p in members)
        data = organize(f"인박스 재조합: {g.get('label','')}", vault_index(vault), cfg,
                        content=combined[:100000], mode="full", debug=debug)
        if not data:
            continue
        fpath, _, _, ncards = write_note(vault, data, "inbox-curate", debug)
        for p in members:  # 인박스 항목 아카이브
            _archive_inbox(p)
        done.append(f"📝 {data.get('title')} (카드 {ncards})")
    st.pop("curate", None)
    save_state(st)
    if not done:
        return "승격된 게 없어요(멤버 파일을 못 찾았을 수 있어요)."
    return "✅ 승격 완료:\n" + "\n".join(done)


def _archive_inbox(path):
    """처리된 인박스 노트를 status: promoted 로 표시 후 0_Inbox/_archive 로 이동."""
    try:
        raw = path.read_text(encoding="utf-8")
        raw = re.sub(r"^status:.*$", "status: promoted", raw, count=1, flags=re.MULTILINE)
        arc = path.parent / "_archive"
        arc.mkdir(exist_ok=True)
        (arc / path.name).write_text(raw, encoding="utf-8")
        path.unlink()
    except Exception as e:
        log(f"인박스 아카이브 실패({path.name}): {e}")


# ─────────────────────────── 옵시디언 직접 입력 스캔 ───────────────────────────
def _strip_frontmatter(raw):
    m = re.match(r"^---\n.*?\n---\n?", raw, flags=re.DOTALL)
    return raw[m.end():] if m else raw


def scan_vault(cfg, debug=False):
    """0_Inbox 를 스캔: 옵시디언에서 직접 만든 원시 노트를 인식·강화(또는 지시어 승격),
    `#승격` 태그 달린 인박스 노트는 학습 노트로 승격. (텍스트 기반 — 스캔은 가볍게)"""
    vault = cfg["study_vault_dir"]
    inbox = Path(vault) / "0_Inbox"
    if not inbox.exists():
        return
    for f in sorted(inbox.glob("*.md")):
        if f.name.startswith("_"):
            continue
        try:
            if time.time() - f.stat().st_mtime < 120:  # 편집 중 방지(2분)
                continue
            raw = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        is_ours = re.search(r"^type:\s*inbox\s*$", raw, flags=re.MULTILINE)
        try:
            if not is_ours:
                _ingest_raw(cfg, f, raw, debug)
            elif "#승격" in raw:
                _promote_note(cfg, f, raw, debug)
        except Exception as e:
            log(f"[scan] {f.name} 처리 오류: {e}")


def _ingest_raw(cfg, path, raw, debug):
    """옵시디언에서 직접 만든 원시 노트 → 지시어 처리 후 인박스 강화(또는 즉시 승격)."""
    vault = cfg["study_vault_dir"]
    body = _strip_frontmatter(raw).strip()
    if len(body) < 5:
        return
    topic, user_tags, promote, cleaned = parse_directives(body)
    if not cleaned or len(cleaned) < 5:
        return
    if topic or promote:  # !학습/[주제] → 학습 노트로 승격
        data = organize("obsidian", vault_index(vault), cfg, content=cleaned,
                        user_tags=user_tags, mode="full", debug=debug)
        if not data:
            return
        _finish(cfg, data, "obsidian", topic, debug)
        path.unlink()
        log(f"[scan] 원시노트 승격: {path.name}")
        return
    data = organize("obsidian", [], cfg, content=cleaned, user_tags=user_tags, mode="inbox", debug=debug)
    if not data:
        return
    title = data.get("title") or path.stem
    date = datetime.now().strftime("%Y-%m-%d")
    lines = ["---", "type: inbox", "status: unread",
             f'title: "{str(title).replace(chr(34), "")}"',
             f'one_line: "{str(data.get("one_line", "")).replace(chr(34), "")}"',
             "tags: [" + ", ".join(data.get("tags", []) or []) + "]",
             f'source: "obsidian"', f"created: {date}", "---", "",
             f"# {title}", "", body, "",
             "> 📥 인박스(옵시디언 입력) — 학습감이면 `#승격` 태그를 달거나 텔레그램 `/curate`.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    log(f"[scan] 인박스 강화: {path.name}")


def _promote_note(cfg, path, raw, debug):
    """#승격 태그가 달린 인박스 노트 → 학습 노트로 승격 + 카드."""
    vault = cfg["study_vault_dir"]
    body = _strip_frontmatter(raw).replace("#승격", " ").strip()
    topic, user_tags, _, cleaned = parse_directives(body)
    if not cleaned or len(cleaned) < 5:
        return
    data = organize("obsidian 승격", vault_index(vault), cfg, content=cleaned,
                    user_tags=user_tags, mode="full", debug=debug)
    if not data:
        return
    _finish(cfg, data, "obsidian", topic, debug)
    _archive_inbox(path)
    log(f"[scan] #승격 노트 승격: {path.name}")


# ─────────────────────────── 마무리(학습 노트) ───────────────────────────
def _finish(cfg, data, source_url, topic, debug):
    vault = cfg["study_vault_dir"]
    if topic:
        fpath, new_concepts, appended, ncards = append_to_topic(vault, topic, data, source_url, debug)
        head = f"➕ [{topic}] 에 이어붙였어요" if appended else f"🆕 [{topic}] 주제 노트를 새로 만들었어요"
    else:
        fpath, new_concepts, _, ncards = write_note(vault, data, source_url, debug)
        head = "✅ 정리 완료"
    rel = fpath.relative_to(Path(vault))
    reply = (f"{head}\n📝 {data.get('title')}\n"
             f"🏷️ {' '.join('#' + t for t in data.get('tags', []))}\n"
             f"💡 {data.get('one_line', '')}\n📁 {rel}")
    if data.get("links"):
        reply += "\n🔗 연결: " + ", ".join(data["links"][:6])
    if new_concepts:
        reply += "\n🆕 개념: " + ", ".join(new_concepts)
    if ncards:
        reply += f"\n🃏 복습 카드 {ncards}개 (오늘부터 복습 대상)"
    return fpath, reply


def _inbox_reply(fpath, data, cfg):
    rel = fpath.relative_to(Path(cfg["study_vault_dir"]))
    return (f"📥 인박스 저장\n📝 {data.get('title')}\n"
            f"🏷️ {' '.join('#' + t for t in data.get('tags', []))}\n"
            f"💡 {data.get('one_line', '')}\n📁 {rel}\n"
            f"(가치 있으면 `/curate` 로 묶어 승격 · `!학습`/`[주제]` 로 즉시 학습 노트)")


def process(text, cfg, debug=False):
    topic, user_tags, promote, cleaned = parse_directives(text)
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
    to_study = bool(topic or promote)
    log(f"콘텐츠 추출: {source} ({len(content)}자) → {'학습노트' if to_study else '인박스'}"
        f"{' [주제:'+topic+']' if topic else ''}")
    data = organize(source, vault_index(cfg["study_vault_dir"]) if to_study else [], cfg,
                    content=content, user_tags=user_tags,
                    mode="full" if to_study else "inbox", debug=debug)
    if not data:
        return None, "Claude 정리에 실패했어요 (anthropic 키·네트워크 확인)."
    if to_study:
        return _finish(cfg, data, url, topic, debug)
    fpath = write_inbox(cfg["study_vault_dir"], data, url)
    return fpath, _inbox_reply(fpath, data, cfg)


def process_image(image, media, caption, cfg, debug=False):
    topic, user_tags, promote, cleaned = parse_directives(caption or "")
    to_study = bool(topic or promote)
    log(f"이미지 수신({len(image)}바이트) → {'학습노트' if to_study else '인박스'}"
        f"{' [주제:'+topic+']' if topic else ''}")
    data = organize("image", vault_index(cfg["study_vault_dir"]) if to_study else [], cfg,
                    content=cleaned, image=image, image_media=media, user_tags=user_tags,
                    mode="full" if to_study else "inbox", debug=debug)
    if not data:
        return None, "이미지 정리에 실패했어요 (anthropic 키·네트워크 확인)."
    if to_study:
        return _finish(cfg, data, "image", topic, debug)
    fpath = write_inbox(cfg["study_vault_dir"], data, "image")
    return fpath, _inbox_reply(fpath, data, cfg)


# ─────────────────────────── Phase 2: 간격 반복 복습(SM-2) ───────────────────────────
def _sm2(card, q):
    """SM-2 간격 반복. q: 0=다시 3=어려움 4=알맞음 5=쉬움. card 를 갱신하고 다음 간격(일) 반환."""
    ease = float(card.get("ease", 2.5))
    reps = int(card.get("reps", 0))
    interval = int(card.get("interval", 0))
    lapses = int(card.get("lapses", 0))
    if q < 3:
        reps, interval, lapses = 0, 1, lapses + 1
    else:
        if reps == 0:
            interval = 1
        elif reps == 1:
            interval = 6
        else:
            interval = max(1, round(interval * ease))
        if q == 3:
            interval = max(1, round(interval * 0.6))
        elif q == 5:
            interval = max(1, round(interval * 1.3))
        reps += 1
    ease = max(1.3, ease + (0.1 - (5 - q) * (0.08 + (5 - q) * 0.02)))
    now = datetime.now()
    card.update({"ease": round(ease, 2), "reps": reps, "interval": interval, "lapses": lapses,
                 "due": (now + timedelta(days=interval)).strftime("%Y-%m-%d"),
                 "last": now.strftime("%Y-%m-%d")})
    return interval


def _due_cards(vault):
    sched = _srs_load(vault)
    today = datetime.now().strftime("%Y-%m-%d")
    due = [(cid, c) for cid, c in sched.items() if (c.get("due") or "0000-00-00") <= today]
    due.sort(key=lambda x: (x[1].get("due") or "", -int(x[1].get("lapses", 0))))  # 오래된·약한 것 먼저
    return [cid for cid, _ in due]


def _cloze_hide(q):
    return re.sub(r"==(.+?)==", "＿＿＿", q)


def _cloze_answer(card):
    if card.get("a"):
        return card["a"]
    m = re.search(r"==(.+?)==", card.get("q", ""))
    return m.group(1) if m else "(정답)"


def _log_review(vault, cid, q):
    try:
        d = Path(vault) / "_srs"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "reviews.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().strftime("%Y-%m-%d %H:%M"), "card": cid, "q": q},
                               ensure_ascii=False) + "\n")
    except Exception:
        pass


# 인라인 키보드 텔레그램 헬퍼
def tg_send_kb(cfg, chat, text, buttons=None):
    token = cfg["telegram_bot_token"]
    data = {"chat_id": chat, "text": text}
    if buttons:
        data["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20).json()
        return r.get("result", {}).get("message_id")
    except Exception as e:
        log(f"send_kb 오류: {e}")
        return None


def tg_edit(cfg, chat, mid, text, buttons=None):
    token = cfg["telegram_bot_token"]
    data = {"chat_id": chat, "message_id": mid, "text": text}
    data["reply_markup"] = json.dumps({"inline_keyboard": buttons or []})
    try:
        requests.post(f"https://api.telegram.org/bot{token}/editMessageText", data=data, timeout=20)
    except Exception as e:
        log(f"edit 오류: {e}")


def tg_answer_cb(cfg, cb_id):
    token = cfg["telegram_bot_token"]
    try:
        requests.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                      data={"callback_query_id": cb_id}, timeout=15)
    except Exception:
        pass


# 복습 세션 (state["quiz"] = {chat, queue[], current, mid, total, done})
def start_quiz(cfg, chat):
    due = _due_cards(cfg["study_vault_dir"])
    if not due:
        tg_send(cfg, chat, "🎉 지금 복습할 카드가 없어요. 나중에 또 만나요!")
        return
    due = due[:int(cfg.get("quiz_max", 20))]
    st = load_state()
    st["quiz"] = {"chat": chat, "queue": due[1:], "current": due[0], "mid": None,
                  "total": len(due), "done": 0}
    save_state(st)
    _send_card(cfg)


def _send_card(cfg):
    st = load_state()
    qz = st.get("quiz")
    if not qz or not qz.get("current"):
        return _finish_quiz(cfg)
    card = _srs_load(cfg["study_vault_dir"]).get(qz["current"])
    if not card:
        return _advance(cfg)
    disp = _cloze_hide(card["q"]) if card.get("type") == "cloze" else card["q"]
    text = f"🧠 복습 {qz['done'] + 1}/{qz['total']}\n\n{disp}"
    mid = tg_send_kb(cfg, qz["chat"], text,
                     [[{"text": "💡 정답 보기", "callback_data": "show"}],
                      [{"text": "⏹ 그만", "callback_data": "stop"}]])
    qz["mid"] = mid
    st["quiz"] = qz
    save_state(st)


def _reveal(cfg):
    st = load_state()
    qz = st.get("quiz")
    if not qz:
        return
    card = _srs_load(cfg["study_vault_dir"]).get(qz.get("current"))
    if not card:
        return _advance(cfg)
    disp = _cloze_hide(card["q"]) if card.get("type") == "cloze" else card["q"]
    ans = _cloze_answer(card) if card.get("type") == "cloze" else (card.get("a") or "(정답 없음)")
    text = f"🧠 복습 {qz['done'] + 1}/{qz['total']}\n\n{disp}\n\n💡 {ans}"
    kb = [[{"text": "❌ 다시", "callback_data": "rate:0"}, {"text": "😓 어려움", "callback_data": "rate:3"}],
          [{"text": "🙂 알맞음", "callback_data": "rate:4"}, {"text": "😎 쉬움", "callback_data": "rate:5"}]]
    tg_edit(cfg, qz["chat"], qz["mid"], text, kb)


def _grade(cfg, quality):
    st = load_state()
    qz = st.get("quiz")
    if not qz:
        return
    vault = cfg["study_vault_dir"]
    sched = _srs_load(vault)
    cid = qz.get("current")
    card = sched.get(cid)
    if card:
        interval = _sm2(card, quality)
        sched[cid] = card
        _srs_save(vault, sched)
        _log_review(vault, cid, quality)
        label = {0: "다시", 3: "어려움", 4: "알맞음", 5: "쉬움"}.get(quality, "")
        nxt = "내일" if interval <= 1 else f"{interval}일 후"
        tg_edit(cfg, qz["chat"], qz["mid"], f"✓ {label} · 다음 복습 {nxt}", None)
    qz["done"] = qz.get("done", 0) + 1
    st["quiz"] = qz
    save_state(st)
    _advance(cfg)


def _advance(cfg):
    st = load_state()
    qz = st.get("quiz")
    if not qz:
        return
    if qz.get("queue"):
        qz["current"] = qz["queue"].pop(0)
        st["quiz"] = qz
        save_state(st)
        _send_card(cfg)
    else:
        _finish_quiz(cfg)


def _finish_quiz(cfg):
    st = load_state()
    qz = st.pop("quiz", None)
    save_state(st)
    if qz:
        tg_send(cfg, qz["chat"], f"🏁 복습 완료! {qz.get('done', 0)}장 했어요. 수고했어요 👏")


def handle_callback(cfg, chat, data):
    if data == "show":
        _reveal(cfg)
    elif data.startswith("rate:"):
        _grade(cfg, int(data.split(":")[1]))
    elif data == "stop":
        _finish_quiz(cfg)
    elif data == "start":
        start_quiz(cfg, chat)


def status_text(cfg):
    vault = cfg["study_vault_dir"]
    sched = _srs_load(vault)
    due = _due_cards(vault)
    today = datetime.now().strftime("%Y-%m-%d")
    rt = 0
    rp = Path(vault) / "_srs" / "reviews.jsonl"
    if rp.exists():
        try:
            rt = sum(1 for l in open(rp, encoding="utf-8") if today in l[:30])
        except Exception:
            pass
    stats, dates = _review_stats(vault)
    mastery = _mastery(stats)
    streak = _streak(dates)
    m = f"{mastery}%" if mastery is not None else "-"
    return (f"📊 학습 현황\n"
            f"• 복습 대기: {len(due)}장\n"
            f"• 전체 카드: {len(sched)}장\n"
            f"• 오늘 복습: {rt}장 · 연속 {streak}일 🔥\n"
            f"• 이해도(최근): {m}\n"
            f"• 인박스: {len(list_inbox(vault))}개\n\n"
            f"/quiz 복습 · /weak 약점 · /plan 계획 · /report 리포트")


def maybe_review_push(cfg):
    """정한 시각(quiz_times, 기본 8·21시) 지나면 슬롯당 1회 복습 알림(due 있을 때)."""
    times = sorted(int(x) for x in cfg.get("quiz_times", [8, 21]))
    now = datetime.now()
    passed = [h for h in times if now.hour >= h]
    if not passed:
        return
    slot = f"{now.strftime('%Y-%m-%d')}:{passed[-1]}"
    st = load_state()
    if st.get("last_review_push") == slot or st.get("quiz"):
        return
    st["last_review_push"] = slot
    save_state(st)
    due = _due_cards(cfg["study_vault_dir"])
    chat = str(cfg.get("telegram_chat_id") or st.get("last_chat") or "")
    if due and chat:
        tg_send_kb(cfg, chat, f"🧠 복습할 카드 {len(due)}장이 대기 중이에요.",
                   [[{"text": "지금 복습 시작 ▶", "callback_data": "start"}]])


# ─────────────────────────── Phase 3~5: 이해도·약점·리포트·계획 ───────────────────────────
def _review_stats(vault, days=60):
    """reviews.jsonl → (카드별 {n,fails}, 복습한 날짜 set)."""
    rp = Path(vault) / "_srs" / "reviews.jsonl"
    stats, dates = {}, set()
    if rp.exists():
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        for line in open(rp, encoding="utf-8"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            day = str(r.get("ts", ""))[:10]
            if day < cutoff:
                continue
            s = stats.setdefault(r.get("card"), {"n": 0, "fails": 0})
            s["n"] += 1
            s["fails"] += 1 if int(r.get("q", 4)) < 3 else 0
            dates.add(day)
    return stats, dates


def _streak(dates):
    if not dates:
        return 0
    d = datetime.now().date()
    if d.strftime("%Y-%m-%d") not in dates:
        d = d - timedelta(days=1)
    n = 0
    while d.strftime("%Y-%m-%d") in dates:
        n += 1
        d = d - timedelta(days=1)
    return n


def _mastery(stats):
    tot = sum(s["n"] for s in stats.values())
    fails = sum(s["fails"] for s in stats.values())
    return round(100 * (tot - fails) / tot) if tot else None


def weak_text(cfg):
    vault = cfg["study_vault_dir"]
    sched = _srs_load(vault)
    stats, _ = _review_stats(vault)
    weak = [(c, s) for c, s in stats.items() if s["fails"] > 0 and c in sched]
    weak.sort(key=lambda x: (-x[1]["fails"], -x[1]["fails"] / max(1, x[1]["n"])))
    if not weak:
        return "👍 최근 자주 틀린 카드가 없어요. 잘하고 있어요!"
    lines = ["🔴 약점 카드(자주 틀림):"]
    for c, s in weak[:10]:
        lines.append(f"- {sched[c]['q'][:50]} ({s['fails']}/{s['n']} 실패)")
    lines.append("\n/quiz 는 약점(실패 많은) 카드부터 보여줍니다.")
    return "\n".join(lines)


# Phase 3 — 파인만(설명) 모드
def explain_start(cfg, chat, topic):
    vault = cfg["study_vault_dir"]
    ref, found = "", None
    for sub in ("Notes", "Concepts"):
        d = Path(vault) / sub
        if not d.exists():
            continue
        for f in d.glob("*.md"):
            if topic in f.stem or slugify(topic).lower() in f.stem.lower():
                ref, found = f.read_text(encoding="utf-8")[:8000], f.stem
                break
        if ref:
            break
    st = load_state()
    st["explain"] = {"topic": topic, "ref": ref}
    save_state(st)
    hint = f"'{found}' 노트 기준으로 채점할게요." if ref else "(관련 노트가 없어 일반 지식 기준으로 봐요.)"
    tg_send(cfg, chat, f"🧑‍🏫 파인만 모드: '{topic}'을(를) 아는 대로 설명해보세요.\n{hint}\n(그만두려면 /stop)")


def explain_grade(cfg, chat, explanation):
    st = load_state()
    ex = st.pop("explain", None)
    save_state(st)
    if not ex:
        return
    import anthropic
    key = cfg["anthropic_api_key"]
    if not key:
        tg_send(cfg, chat, "anthropic 키가 없어 채점을 못 해요.")
        return
    prompt = (f"학습자가 '{ex['topic']}'를 자기 말로 설명했다. 참고자료 기준으로 이해도를 평가하라.\n"
              f"# 참고자료\n{ex['ref'] or '(없음 — 일반 지식으로 평가)'}\n\n# 학습자 설명\n{explanation}\n\n"
              "한국어로 간결히:\n✅ 잘 이해한 점:\n⚠️ 빠졌거나 부정확한 점:\n📌 보강 포인트:\n"
              "마지막 줄에 'SCORE: N'(0~100).")
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(model=cfg["claude_model"], max_tokens=1200,
                                       messages=[{"role": "user", "content": prompt}])
        txt = "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception as e:
        txt = f"채점 오류: {e}"
    tg_send(cfg, chat, "🧑‍🏫 피드백\n\n" + txt)


# Phase 4 — 주간 복습 리포트
def build_report(cfg, push_chat=None):
    vault = cfg["study_vault_dir"]
    now = datetime.now()
    wk = now.strftime("%Y-W%V")
    since = now - timedelta(days=7)
    notes = []
    nd = Path(vault) / "Notes"
    if nd.exists():
        for f in nd.glob("*.md"):
            try:
                if datetime.fromtimestamp(f.stat().st_mtime) >= since:
                    fm = _read_fm(f)
                    notes.append(fm["title"] + " " + " ".join("#" + t for t in fm["tags"]))
            except Exception:
                pass
    sched = _srs_load(vault)
    stats, _ = _review_stats(vault, days=8)
    reviewed = sum(s["n"] for s in stats.values())
    weak = [sched[c]["q"][:40] for c, s in stats.items() if s["fails"] > 0 and c in sched][:8]
    ctx = (f"# 이번 주 학습 노트({len(notes)})\n" + "\n".join("- " + n for n in notes[:40]) +
           f"\n\n# 복습 {reviewed}회 · 약점: {weak}\n# 인박스 대기: {len(list_inbox(vault))}개")
    body = "(요약 없음)"
    key = cfg["anthropic_api_key"]
    if key:
        import anthropic
        prompt = ("아래 한 주 학습 활동으로 '주간 복습 리포트'를 옵시디언 마크다운으로 작성하라. 한국어, 간결히.\n"
                  "섹션: ## 이번 주 배운 것 / ## 개념 간 연결 / ## 약점·복습 필요 / ## 다음 주 집중 제안.\n"
                  "본문만(프론트매터 없이).\n\n" + ctx)
        try:
            client = anthropic.Anthropic(api_key=key)
            resp = client.messages.create(model=cfg["claude_model"], max_tokens=2000,
                                           messages=[{"role": "user", "content": prompt}])
            body = "".join(b.text for b in resp.content if b.type == "text").strip()
        except Exception as e:
            body = f"(리포트 생성 오류: {e})"
    d = Path(vault) / "주간복습"
    d.mkdir(exist_ok=True)
    fp = d / f"{wk}.md"
    fp.write_text(f"---\ntype: weekly-review\ntags: [주간복습]\ncreated: {now.strftime('%Y-%m-%d')}\n---\n\n"
                  f"# 주간 복습 {wk}\n\n{body}\n", encoding="utf-8")
    if push_chat:
        tg_send(cfg, push_chat, f"🗓️ 주간 복습 리포트 ({wk})\n\n{body[:2500]}\n\n📁 주간복습/{wk}.md")
    return fp


def restart_service(arg):
    """/restart — launchd 서비스 재시작. studybot 은 마지막(자기 종료 전 나머지 먼저)."""
    order = [("mountainbot", "등산봇"), ("youtube", "유튜브봇"), ("dashboard", "대시보드"),
             ("watchdog", "워치독"), ("studybot", "학습봇")]
    nmap = dict(order)
    arg = (arg or "").strip()
    if not arg:
        return "사용법: /restart studybot|youtube|mountainbot|dashboard|watchdog|all", []
    tgs = [k for k, _ in order] if arg in ("all", "전체") else ([arg] if arg in nmap else [])
    if not tgs:
        return f"알 수 없는 서비스: {arg}", []
    return "🔄 재시작: " + ", ".join(nmap[t] for t in tgs), tgs


def do_restart(targets):
    uid = os.getuid()
    for t in targets:  # order 상 studybot 이 마지막
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/com.craig.skill.{t}"],
                       capture_output=True)


def maybe_report(cfg):
    day = int(cfg.get("report_day", 6))     # 6=일요일
    hour = int(cfg.get("report_hour", 20))
    now = datetime.now()
    if now.weekday() != day or now.hour < hour:
        return
    wk = now.strftime("%Y-W%V")
    st = load_state()
    if st.get("last_report") == wk:
        return
    st["last_report"] = wk
    save_state(st)
    chat = str(cfg.get("telegram_chat_id") or st.get("last_chat") or "")
    build_report(cfg, push_chat=chat or None)


# Phase 5 — 적응형 학습 계획
def build_plan(cfg):
    vault = cfg["study_vault_dir"]
    due = _due_cards(vault)
    sched = _srs_load(vault)
    stats, _ = _review_stats(vault)
    weak = [sched[c]["q"][:40] for c, s in stats.items() if s["fails"] > 0 and c in sched][:8]
    inbox = [it["title"] for it in list_inbox(vault)][:15]
    key = cfg["anthropic_api_key"]
    if not key:
        return "anthropic 키가 없어요."
    import anthropic
    prompt = ("학습 코치로서 이번 주 개인 학습 계획을 짧고 실천 가능하게 제안하라(한국어).\n"
              f"# 복습 대기: {len(due)}장\n# 약점: {weak}\n# 인박스 미선별: {inbox}\n\n"
              "형식: 🎯 이번 주 목표 / 📌 복습(약점 우선) / 🧩 인박스 승격 추천 / 🆕 새로 학습 추천.")
    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(model=cfg["claude_model"], max_tokens=1200,
                                       messages=[{"role": "user", "content": prompt}])
        return "🗺️ 학습 계획\n\n" + "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception as e:
        return f"계획 생성 오류: {e}"


# ─────────────────────────── 텔레그램 루프 ───────────────────────────
def _remember_chat(chat_id):
    st = load_state()
    if str(st.get("last_chat")) != str(chat_id):
        st["last_chat"] = chat_id
        save_state(st)


def maybe_remind(cfg):
    """상기(리마인드): 정한 시각 이후 하루 1회, 인박스에 쌓인 게 있으면 알림."""
    try:
        hour = int(cfg.get("remind_hour", 9))
    except Exception:
        hour = 9
    now = datetime.now()
    if now.hour < hour:
        return
    st = load_state()
    today = now.strftime("%Y-%m-%d")
    if st.get("last_remind") == today:
        return
    items = list_inbox(cfg["study_vault_dir"])
    if not items:
        return
    chat = str(cfg.get("telegram_chat_id") or st.get("last_chat") or "")
    if not chat:
        return
    oldest = min((it["file"].name[:10] for it in items), default="")
    tg_send(cfg, chat, f"📥 인박스에 {len(items)}개가 쌓여 있어요(가장 오래된 {oldest}).\n"
                       f"`/curate` 로 가치 있는 것들을 묶어 학습 노트로 승격해보세요.")
    st["last_remind"] = today
    save_state(st)


def maybe_scan(cfg):
    """옵시디언 직접 입력 반영: 15분마다 볼트 스캔(같은 프로세스라 state 경합 없음)."""
    st = load_state()
    if time.time() - float(st.get("last_scan", 0) or 0) < 900:
        return
    st["last_scan"] = time.time()
    save_state(st)
    try:
        scan_vault(cfg)
    except Exception as e:
        log(f"scan 오류: {e}")


def poll_once(cfg, long_poll=False, debug=False):
    token = cfg["telegram_bot_token"]
    if not token:
        log("텔레그램 토큰 없음")
        return
    maybe_scan(cfg)
    maybe_review_push(cfg)
    maybe_report(cfg)
    maybe_remind(cfg)
    offset = load_state().get("telegram_update_offset")
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
        cq = upd.get("callback_query")
        if cq:  # 인라인 버튼(복습 세션)
            ch = cq.get("message", {}).get("chat", {}).get("id")
            tg_answer_cb(cfg, cq.get("id"))
            if _authorized(cfg, ch):
                try:
                    handle_callback(cfg, ch, cq.get("data", ""))
                except Exception as e:
                    log(f"callback 오류: {e}")
            continue
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat_id = msg.get("chat", {}).get("id")
        if not _authorized(cfg, chat_id):
            continue
        _remember_chat(chat_id)
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
                low = text.strip()
                # 파인만 설명 대기 중이면 이 메시지를 설명으로 채점(명령 제외)
                if not low.startswith("/") and load_state().get("explain"):
                    explain_grade(cfg, chat_id, text)
                    continue
                if low in ("/stop", "/그만"):
                    s = load_state()
                    s.pop("explain", None)
                    s.pop("quiz", None)
                    save_state(s)
                    tg_send(cfg, chat_id, "중단했어요.")
                    continue
                if low.startswith("/explain") or low.startswith("/설명"):
                    parts = text.split(maxsplit=1)
                    if len(parts) > 1:
                        explain_start(cfg, chat_id, parts[1].strip())
                    else:
                        tg_send(cfg, chat_id, "사용법: /explain 복리")
                    continue
                if low.startswith("/weak") or low.startswith("/약점"):
                    tg_send(cfg, chat_id, weak_text(cfg))
                    continue
                if low.startswith("/report") or low.startswith("/리포트"):
                    tg_send(cfg, chat_id, "🗓️ 주간 리포트 생성 중…")
                    build_report(cfg, push_chat=chat_id)
                    continue
                if low.startswith("/plan") or low.startswith("/계획"):
                    tg_send(cfg, chat_id, "🗺️ 학습 계획 생성 중…")
                    tg_send(cfg, chat_id, build_plan(cfg))
                    continue
                if low.startswith("/restart") or low.startswith("/재시동"):
                    parts = text.split(maxsplit=1)
                    reply, tgs = restart_service(parts[1] if len(parts) > 1 else "")
                    tg_send(cfg, chat_id, reply)  # 자기 종료 전에 먼저 응답
                    do_restart(tgs)
                    continue
                if low in ("/start", "/help"):
                    tg_send(cfg, chat_id,
                            "📚 학습봇 — 수집 → 선별·재조합 → 학습·복습 파이프라인\n\n"
                            "① 수집: 링크(웹/유튜브/인스타)·텍스트·사진을 보내면 인박스에 모음\n"
                            "② 선별: /curate 로 봇이 주제별로 묶어 승격 제안 → /promote 1 3\n"
                            "   즉시 승격: `!학습` 또는 `[주제]` 로 보내면 바로 학습 노트+카드\n"
                            "③ 복습: 승격 카드 간격 반복(SM-2). 매일 알림 + /quiz\n\n"
                            "지시어: #태그 · [주제](이어쓰기) · !학습(즉시 승격)\n"
                            "수집·선별: /inbox /curate /promote\n"
                            "복습·이해: /quiz(복습) /explain 주제(설명채점) /weak(약점)\n"
                            "현황·계획: /status /plan(계획) /report(주간리포트)\n"
                            "운영: /restart <봇|all>(재시동)")
                    continue
                if low.startswith("/curate"):
                    tg_send(cfg, chat_id, "🧩 인박스 큐레이션 중…")
                    tg_send(cfg, chat_id, curate(cfg, debug))
                    continue
                if low.startswith("/promote"):
                    tg_send(cfg, chat_id, promote_groups(cfg, low[len("/promote"):], debug))
                    continue
                if low.startswith("/inbox"):
                    items = list_inbox(cfg["study_vault_dir"])
                    tg_send(cfg, chat_id, (f"📥 인박스 {len(items)}개:\n" +
                            "\n".join(f"- {it['title']}" for it in items[:30])) if items else "📭 인박스 비어있음")
                    continue
                if low.startswith("/quiz") or low.startswith("/복습"):
                    start_quiz(cfg, chat_id)
                    continue
                if low.startswith("/status") or low.startswith("/현황"):
                    tg_send(cfg, chat_id, status_text(cfg))
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
        st = load_state()          # 커맨드들이 중간에 state 를 바꿨을 수 있어 재로딩 후 offset 만 갱신
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
    ap.add_argument("--curate", action="store_true", help="인박스 큐레이션 제안(로컬)")
    ap.add_argument("--promote", metavar="SEL", help="큐레이션 후보 승격(예: '1 3' 또는 'all')")
    ap.add_argument("--scan", action="store_true", help="옵시디언 0_Inbox 직접 입력 스캔·반영")
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    if a.scan:
        scan_vault(cfg, a.debug)
        print("스캔 완료")
    elif a.curate:
        print(curate(cfg, a.debug))
    elif a.promote is not None:
        print(promote_groups(cfg, a.promote, a.debug))
    elif a.image:
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
