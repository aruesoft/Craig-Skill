#!/usr/bin/env python3
"""
learn-ingest — 파이프라인 ① 수집 → 1차 정리. (설계안 §4-①, ①-a)

입력: _System/Queue/incoming/*.json  또는  CLI(--text/--url/--image)
처리: URL 타입 판별 → 콘텐츠 추출(동영상은 타임스탬프 전사) → 중복검사
      → 00_Inbox 노트 생성(원본+요약+핵심포인트+카테고리 제안+연결 후보)
      → _System/Queue/outgoing 에 텔레그램 확인 메시지 적재
출력: 확인 메시지 dict (봇이 발신)

설계 원칙: 봇은 큐만, 지능은 여기. 원본 불변. 비밀값은 ~/.config/craig-telegram-study/config.json.
"""
import os
import re
import sys
import json
import glob
import shutil
import hashlib
import argparse
import tempfile
import subprocess
from datetime import datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
USER_CFG = Path.home() / ".config" / "craig-telegram-study" / "config.json"
URL_RE = re.compile(r"https?://[^\s]+")
YT_RE = re.compile(r"(youtube\.com|youtu\.be)")
SOCIAL_RE = re.compile(r"(instagram\.com|tiktok\.com|facebook\.com|fb\.watch|threads\.net|x\.com|twitter\.com)")
HASHTAG_RE = re.compile(r"(?:^|\s)#([^\s#]{1,40})")
CAT_HINT = {"ai": "AI-ML", "ml": "AI-ML", "llm": "AI-ML",
            "biz": "Business-Investing", "invest": "Business-Investing", "econ": "Business-Investing"}


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def load_config():
    cfg = {}
    p = HERE / "pipeline.config.json"
    if p.exists():
        cfg.update(json.load(open(p)))
    uc = {}
    if USER_CFG.exists():
        try:
            uc = json.load(open(USER_CFG))
        except Exception:
            pass
    cfg["anthropic_api_key"] = os.environ.get("ANTHROPIC_API_KEY") or uc.get("anthropic_api_key", "")
    cfg["ytdlp_cookies"] = uc.get("ytdlp_cookies", "")
    cfg.setdefault("claude_model", "claude-sonnet-5")
    cfg.setdefault("categories", ["AI-ML", "Business-Investing"])
    cfg.setdefault("video", {"max_minutes": 30, "whisper_model": "small", "langs": ["ko", "en"]})
    cfg["vault"] = os.path.expanduser(cfg.get("vault", str(Path.home() / "StudyVault")))
    return cfg


# ───────── yt-dlp 공통 ─────────
def _ytdlp_base(cfg):
    exe = shutil.which("yt-dlp")
    base = [exe] if exe else [sys.executable, "-m", "yt_dlp"]
    ck = cfg.get("ytdlp_cookies")
    if ck and os.path.exists(os.path.expanduser(ck)):
        base += ["--cookies", os.path.expanduser(ck)]
    return base


def video_meta(url, cfg):
    try:
        r = subprocess.run(_ytdlp_base(cfg) + ["--no-warnings", "--skip-download",
                           "--print", "%(title)s\t%(channel|uploader)s\t%(duration)s\t%(upload_date)s", url],
                           capture_output=True, text=True, timeout=90)
        parts = (r.stdout or "").strip().split("\t")
        if len(parts) >= 4:
            return {"title": parts[0], "channel": parts[1], "duration": parts[2], "date": parts[3]}
    except Exception:
        pass
    return {"title": "", "channel": "", "duration": "", "date": ""}


def _fmt_ts(sec):
    try:
        s = int(float(sec))
        return f"{s // 60:02d}:{s % 60:02d}" if s < 3600 else f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    except Exception:
        return "00:00"


# ───────── 전사(타임스탬프 포함) ─────────
def transcript_segments(url, cfg):
    """(segments[{t_sec, text}], 방식) 반환. 유튜브=자막API → 실패시 오디오+whisper."""
    if YT_RE.search(url):
        segs = _yt_transcript_api(url)
        if segs:
            return segs, "youtube-자막"
    segs = _yt_vtt(url, cfg)
    if segs:
        return segs, "yt-dlp-자막"
    segs = _whisper_segments(url, cfg)
    if segs:
        return segs, "whisper-전사"
    return None, None


def _yt_transcript_api(url):
    m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([\w-]{11})", url)
    if not m:
        return None
    vid = m.group(1)
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        for langs in (["ko"], ["en"], None):
            try:
                api = YouTubeTranscriptApi()
                fetched = api.fetch(vid, languages=langs) if langs else api.fetch(vid)
                out = []
                for s in fetched:
                    t = getattr(s, "start", None) if not isinstance(s, dict) else s.get("start")
                    txt = getattr(s, "text", None) if not isinstance(s, dict) else s.get("text")
                    if txt:
                        out.append({"t": t or 0, "text": txt.strip()})
                if out:
                    return out
            except Exception:
                continue
    except ImportError:
        pass
    return None


def _yt_vtt(url, cfg):
    with tempfile.TemporaryDirectory() as td:
        try:
            subprocess.run(_ytdlp_base(cfg) + ["--skip-download", "--write-subs", "--write-auto-subs",
                           "--sub-langs", "ko,en,ko-orig,en-orig,-live_chat", "--sub-format", "vtt/best",
                           "--retries", "3", "--no-warnings", "--quiet",
                           "-o", os.path.join(td, "%(id)s.%(ext)s"), url],
                           check=False, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return None
        vtts = glob.glob(os.path.join(td, "*.vtt"))
        if not vtts:
            return None
        out, last = [], None
        for line in open(vtts[0], encoding="utf-8", errors="ignore"):
            m = re.match(r"(\d{2}):(\d{2}):(\d{2})[.,]\d+\s*-->", line)
            if m:
                last = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
                continue
            t = re.sub(r"<[^>]+>", "", line).strip()
            if t and last is not None and (not out or out[-1]["text"] != t):
                out.append({"t": last, "text": t})
        return out or None


def _whisper_segments(url, cfg):
    dur = cfg.get("_duration_min", 0)
    if dur and dur > cfg["video"].get("max_minutes", 30):
        return None  # 길이 초과는 상위에서 확인 질문
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "a.%(ext)s")
        try:
            subprocess.run(_ytdlp_base(cfg) + ["-x", "--audio-format", "mp3", "-o", out,
                           "--no-warnings", "--quiet", url],
                           check=False, timeout=600, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            return None
        files = glob.glob(os.path.join(td, "a.*"))
        if not files:
            return None
        try:
            import whisper
            model = whisper.load_model(cfg["video"].get("whisper_model", "small"))
            r = model.transcribe(files[0])
            return [{"t": s.get("start", 0), "text": s.get("text", "").strip()} for s in r.get("segments", []) if s.get("text")]
        except Exception as e:
            log(f"whisper 실패: {e}")
            return None


def segments_to_transcript(segs):
    return "\n".join(f"[{_fmt_ts(s['t'])}] {s['text']}" for s in segs)


# ───────── 웹/텍스트/이미지 ─────────
def web_extract(url):
    try:
        import trafilatura
        d = trafilatura.fetch_url(url)
        return trafilatura.extract(d, include_comments=False, include_tables=True) if d else None
    except Exception:
        return None


# ───────── Claude 정리 ─────────
def claude_json(cfg, prompt, image=None, media="image/jpeg"):
    import anthropic
    key = cfg["anthropic_api_key"]
    if not key:
        return None
    content = []
    if image:
        import base64
        content.append({"type": "image", "source": {"type": "base64", "media_type": media,
                       "data": base64.b64encode(image).decode()}})
    content.append({"type": "text", "text": prompt})
    try:
        c = anthropic.Anthropic(api_key=key)
        resp = c.messages.create(model=cfg["claude_model"], max_tokens=2000,
                                 messages=[{"role": "user", "content": content}])
        raw = "".join(b.text for b in resp.content if b.type == "text").strip()
        i, j = raw.find("{"), raw.rfind("}")
        return json.loads(raw[i:j + 1])
    except Exception as e:
        log(f"claude 오류: {e}")
        return None


def summarize(cfg, body, source_type, cats, image=None, media="image/jpeg"):
    catlist = " / ".join(cats)
    hint = ("첨부 이미지를 읽어 텍스트로 옮긴 뒤 정리하라.\n" if image else f"# 자료 원문\n{(body or '')[:80000]}\n\n")
    prompt = (f"학습 자료를 옵시디언 인박스 노트로 1차 정리한다(한국어).\n{hint}"
              "아래 JSON만 출력:\n{\n"
              '  "title": "간결한 제목(특수문자 금지)",\n'
              '  "summary": "3~5줄 요약",\n'
              '  "points": ["핵심 포인트", ...],\n'
              f'  "suggested_area": "{catlist} 중 하나 또는 unsorted",\n'
              '  "value": "high|mid|low",\n'
              '  "links": ["연결하면 좋을 주제 키워드", ...]\n}')
    return claude_json(cfg, prompt, image=image, media=media)


# ───────── 중복 검사 ─────────
def norm_url(url):
    u = re.sub(r"[?#].*$", "", url.strip().rstrip("/"))
    return hashlib.sha1(u.encode()).hexdigest()[:12]


def seen_index(vault):
    p = Path(vault) / "_System" / "seen_urls.json"
    if p.exists():
        try:
            return json.load(open(p))
        except Exception:
            pass
    return {}


def save_seen(vault, idx):
    p = Path(vault) / "_System" / "seen_urls.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    json.dump(idx, open(tmp, "w"), ensure_ascii=False, indent=2)
    os.replace(tmp, p)


# ───────── 인박스 노트 작성 ─────────
def slugify(t):
    s = re.sub(r'[\\/:*?"<>|#\[\]]', " ", t or "").strip()
    return re.sub(r"\s+", " ", s)[:80] or "noname"


def write_inbox(cfg, data, source_type, url, original, extra_section=""):
    vault = Path(cfg["vault"])
    inbox = vault / "00_Inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    title = data.get("title") or f"수집 {date}"
    fp = inbox / f"{date}_{slugify(title)}.md"
    n = 2
    while fp.exists():
        fp = inbox / f"{date}_{slugify(title)} ({n}).md"
        n += 1
    fm = ["---", "type: inbox", f"captured: {datetime.now():%Y-%m-%dT%H:%M}",
          "source: telegram", f"source_type: {source_type}", f"source_url: {url or ''}",
          "status: raw", f"suggested_area: {data.get('suggested_area', 'unsorted')}",
          f"value: {data.get('value', 'mid')}", "promoted_to: ", "---", ""]
    lines = fm + ["## 원본", "", (original or "").strip(), extra_section, "",
                  "## 요약", "", (data.get("summary") or "").strip(), "",
                  "## 핵심 포인트"] + [f"- {p}" for p in (data.get("points") or [])] + \
        ["", "## 연결 후보"] + [f"- {l}" for l in (data.get("links") or [])] + [""]
    fp.write_text("\n".join(lines), encoding="utf-8")
    return fp


# ───────── 파이프라인 ─────────
def ingest_one(cfg, text, tags=None, image=None, image_media="image/jpeg"):
    tags = tags or []
    cats = cfg["categories"]
    # 카테고리 힌트
    for t in tags + HASHTAG_RE.findall(text or ""):
        if t.lower() in CAT_HINT:
            cats = [CAT_HINT[t.lower()]] + [c for c in cats if c != CAT_HINT[t.lower()]]
    text_clean = HASHTAG_RE.sub(" ", text or "").strip()

    m = URL_RE.search(text_clean)
    url = m.group(0).rstrip(").,。") if m else None
    source_type, original, extra = "text", text_clean, ""

    if image:
        source_type = "image"
        original = "(이미지 첨부 — 아래 요약 참조)"
    elif url and (YT_RE.search(url) or SOCIAL_RE.search(url)):
        source_type = "youtube" if YT_RE.search(url) else "video"
        meta = video_meta(url, cfg)
        try:
            cfg["_duration_min"] = int(float(meta.get("duration") or 0)) // 60
        except Exception:
            cfg["_duration_min"] = 0
        log(f"동영상 전사: {meta.get('title') or url} ({cfg['_duration_min']}분)")
        segs, how = transcript_segments(url, cfg)
        if segs:
            transcript = segments_to_transcript(segs)
            original = f"- 출처: [{meta.get('title') or '동영상'}]({url}) · {meta.get('channel', '')} · {meta.get('date', '')}"
            extra = f"\n\n## 스크립트 (전문 · {how})\n\n{transcript}"
            body_for_claude = transcript
        else:
            original = f"- 출처: {url} (전사 실패)"
            extra = "\n\n> ⚠️ transcript: failed — 자막/오디오 확보 실패. 링크만 저장."
            body_for_claude = f"동영상 제목: {meta.get('title')}\n(전사 실패, 제목·링크만)"
        data = summarize(cfg, body_for_claude, source_type, cats)
    elif url:
        source_type = "web"
        body = web_extract(url)
        original = (body or f"(본문 추출 실패) {url}")
        data = summarize(cfg, body or text_clean, source_type, cats)
    else:
        data = summarize(cfg, text_clean, "text", cats)

    if image:
        data = summarize(cfg, "", "image", cats, image=image, media=image_media)

    if not data:
        return None, "정리 실패(anthropic 키·네트워크 확인)"

    # 중복 검사
    if url:
        idx = seen_index(cfg["vault"])
        h = norm_url(url)
        if h in idx and os.path.exists(idx[h]):
            note = Path(idx[h])
            note.write_text(note.read_text(encoding="utf-8") +
                            f"\n> 🔁 재수집 {datetime.now():%Y-%m-%d %H:%M}\n", encoding="utf-8")
            return note, f"🔁 이미 있는 자료라 기존 노트에 재수집 메모 추가: {note.stem}"

    fp = write_inbox(cfg, data, source_type, url, original, extra)
    if url:
        idx = seen_index(cfg["vault"])
        idx[norm_url(url)] = str(fp)
        save_seen(cfg["vault"], idx)

    n_script = f" · 스크립트 {len(extra)}자 전사" if extra and "스크립트" in extra else ""
    msg = (f"✅ 저장됨: \"{data.get('title')}\"{n_script}\n"
           f"→ 제안: {data.get('suggested_area')} · 가치 {data.get('value')} · 연결후보 {len(data.get('links', []))}건")
    return fp, msg


def process_queue(cfg):
    qin = Path(cfg["vault"]) / "_System" / "Queue" / "incoming"
    qout = Path(cfg["vault"]) / "_System" / "Queue" / "outgoing"
    qdone = Path(cfg["vault"]) / "_System" / "Queue" / "processed"
    for d in (qin, qout, qdone):
        d.mkdir(parents=True, exist_ok=True)
    files = sorted(qin.glob("*.json"))
    if not files:
        log("incoming 큐 비어있음")
        return 0
    n = 0
    for f in files:
        try:
            j = json.load(open(f))
        except Exception:
            continue
        fp, msg = ingest_one(cfg, j.get("text", ""), tags=j.get("tags", []))
        out = {"chat_id": j.get("chat_id"), "text": msg, "reply_to": j.get("msg_id")}
        (qout / f"{f.stem}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        shutil.move(str(f), str(qdone / f.name))
        n += 1
        log(f"처리: {f.name} → {msg[:50]}")
    return n


def main():
    ap = argparse.ArgumentParser(description="learn-ingest")
    ap.add_argument("--text", help="텍스트/URL 직접 수집")
    ap.add_argument("--image", help="이미지 파일 수집")
    ap.add_argument("--queue", action="store_true", help="incoming 큐 처리")
    a = ap.parse_args()
    cfg = load_config()
    if a.image:
        img = open(a.image, "rb").read()
        media = "image/png" if a.image.lower().endswith(".png") else "image/jpeg"
        fp, msg = ingest_one(cfg, a.text or "", image=img, image_media=media)
        print(msg, "\n→", fp)
    elif a.text:
        fp, msg = ingest_one(cfg, a.text)
        print(msg, "\n→", fp)
    elif a.queue:
        print(f"{process_queue(cfg)}건 처리")
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
