#!/usr/bin/env python3
"""
Claude 기반 요약 폴백 — secondb.ai가 실패할 때 사용.

흐름: 유튜브 자막(youtube-transcript-api, 무료) 확보 → Claude(anthropic SDK)로 요약.
자막이 있는 영상만 가능. Anthropic API 키 필요(유료, Haiku 기준 매우 저렴).
"""

import os
import re
from datetime import datetime

# 비용 효율 모델 (입력 $1 / 출력 $5 per 1M tokens). config로 변경 가능.
DEFAULT_CLAUDE_MODEL = "claude-haiku-4-5"


def _log(msg, debug=False, is_debug=False):
    if is_debug and not debug:
        return
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def claude_enabled(config):
    """Anthropic API 키가 있으면 폴백 사용 가능"""
    return bool(config.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY"))


def extract_video_id(url):
    """유튜브 URL에서 video id 추출 (watch?v=, youtu.be/, shorts/)"""
    for pat in (r"[?&]v=([\w-]{11})", r"youtu\.be/([\w-]{11})",
                r"/shorts/([\w-]{11})", r"/embed/([\w-]{11})"):
        m = re.search(pat, url)
        if m:
            return m.group(1)
    # 마지막 경로 조각이 11자 id면 사용
    tail = url.rstrip("/").split("/")[-1]
    return tail if re.fullmatch(r"[\w-]{11}", tail) else None


def _join_snippets(fetched):
    parts = []
    for s in fetched:
        t = getattr(s, "text", None)
        if t is None and isinstance(s, dict):
            t = s.get("text")
        if t:
            parts.append(t)
    return " ".join(parts).strip()


def get_transcript(video_id, languages=("ko", "en"), debug=False):
    """유튜브 자막 텍스트 확보 (youtube-transcript-api). 신/구 버전 API 모두 대응."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        print("youtube-transcript-api 미설치:  pip install youtube-transcript-api")
        return None

    langs = list(languages)

    # 1) 신규 인스턴스 API (v1.0+)
    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=langs)
        text = _join_snippets(fetched)
        if text:
            return text
    except Exception as e:
        _log(f"자막 fetch(신규 API) 실패: {e}", debug, is_debug=True)

    # 2) 구버전 classmethod API
    try:
        data = YouTubeTranscriptApi.get_transcript(video_id, languages=langs)
        text = _join_snippets(data)
        if text:
            return text
    except Exception as e:
        _log(f"자막 get_transcript(구 API) 실패: {e}", debug, is_debug=True)

    # 3) 언어 제약 없이 아무 자막이나 (자동 생성 포함)
    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id)
        text = _join_snippets(fetched)
        if text:
            return text
    except Exception as e:
        _log(f"자막(언어 무제약) 실패: {e}", debug, is_debug=True)

    return None


def summarize_with_claude(transcript, video, language, config, debug=False):
    """자막을 Claude로 요약 (anthropic SDK)."""
    try:
        import anthropic
    except ImportError:
        print("anthropic 미설치:  pip install anthropic")
        return None

    key = config.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        _log("Anthropic API 키가 없습니다 (config.anthropic_api_key 또는 ANTHROPIC_API_KEY)")
        return None

    model = config.get("claude_model", DEFAULT_CLAUDE_MODEL)
    lang_name = "한국어" if language == "kr" else language

    # Haiku 컨텍스트는 200K 토큰. 매우 긴 영상도 안전하도록 자막 길이 제한.
    transcript = transcript[:100000]

    prompt = (
        f"다음은 유튜브 영상 \"{video.get('title', '')}\" (채널: {video.get('channel', '')})의 "
        f"자막입니다. 핵심 내용을 {lang_name}로 6~8문장으로 간결하게 요약해 주세요. "
        f"인사말·군더더기 없이 요점만, 자연스러운 문단으로 작성하세요.\n\n"
        f"=== 자막 ===\n{transcript}"
    )

    try:
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        if resp.stop_reason == "refusal":
            _log("Claude가 요약을 거부했습니다(refusal)")
            return None
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return text or None
    except anthropic.APIError as e:
        _log(f"Claude API 오류: {getattr(e, 'message', e)}")
        return None
    except Exception as e:
        _log(f"Claude 요약 오류: {e}")
        return None


def claude_fallback_summary(video, language, config, debug=False):
    """secondb 실패 시 호출: 자막 확보 → Claude 요약. 실패 시 None."""
    vid = extract_video_id(video.get("url", ""))
    if not vid:
        _log("video id를 찾지 못해 Claude 폴백 불가")
        return None

    _log("Claude 폴백 시도: 자막 확보 중...", debug)
    transcript = get_transcript(vid, debug=debug)
    if not transcript:
        _log("자막이 없어 Claude 폴백 불가 (자막 없는 영상)")
        return None

    _log(f"자막 확보({len(transcript)}자) → Claude 요약 중...", debug)
    return summarize_with_claude(transcript, video, language, config, debug)
