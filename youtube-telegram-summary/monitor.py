#!/usr/bin/env python3
"""
YouTube 채널 모니터 → secondb.ai 요약 → Telegram 전송

사용법:
  python monitor.py                      새 동영상 확인 후 요약·전송
  python monitor.py --debug              브라우저 표시 + 상세 로그
  python monitor.py --list-channels      등록된 채널 목록
  python monitor.py --add-channel @핸들  채널 추가 (@핸들 / URL / UC아이디 모두 가능)
  python monitor.py --remove-channel X   채널 삭제 (UC아이디 또는 목록 번호)
  python monitor.py --listen             텔레그램 명령 상시 대기(채널명 보내면 즉시 등록)

텔레그램에서 봇에게 보내는 명령:
  채널명/@핸들/URL/UC아이디 → 채널 추가   |   /list 목록   |   /remove N 삭제   |   /run 즉시 실행
"""

import json
import re
import sys
import time
import argparse
import requests
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "youtube-telegram-summary"
CONFIG_FILE = CONFIG_DIR / "config.json"
STATE_FILE = CONFIG_DIR / "state.json"
PROFILE_DIR = CONFIG_DIR / "browser_profile"  # secondb.ai 로그인 세션 영속화

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 로그아웃(데모) 상태에서만 나타나는 문구 — 이게 보이면 실제 요약이 아니라 데모임
DEMO_MARKERS = [
    "Create Your Own Summaries",
    "Sign in to summarize",
    "Sign in with Google",
    "Continue with Google",
]


def log(msg, debug_mode=False, is_debug=False):
    if is_debug and not debug_mode:
        return
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ──────────────────────────── 설정/상태 ────────────────────────────

def load_config():
    if not CONFIG_FILE.exists():
        print(f"설정 파일이 없습니다: {CONFIG_FILE}")
        print("setup.py를 먼저 실행해주세요.")
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return json.load(f)


def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"seen_videos": [], "last_checked": None}


def save_state(state):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ──────────────────────── 채널 ID 해석/관리 ────────────────────────

def resolve_channel_id(value):
    """@핸들 / 채널 URL / UC아이디 → UC채널ID 로 변환"""
    value = value.strip()

    # 이미 UC 아이디인 경우
    if re.fullmatch(r'UC[\w-]{22}', value):
        return value

    # /channel/UC... 형태 URL
    m = re.search(r'/channel/(UC[\w-]{22})', value)
    if m:
        return m.group(1)

    # 핸들/사용자명 → 페이지에서 channelId 추출
    if value.startswith('http'):
        url = value
    elif value.startswith('@'):
        url = f'https://www.youtube.com/{value}'
    else:
        url = f'https://www.youtube.com/@{value}'

    try:
        r = requests.get(url, timeout=15, headers={'User-Agent': UA})
        for pat in (r'"channelId":"(UC[\w-]{22})"',
                    r'channel/(UC[\w-]{22})',
                    r'"externalId":"(UC[\w-]{22})"'):
            m = re.search(pat, r.text)
            if m:
                return m.group(1)
    except requests.RequestException as e:
        log(f"채널 ID 조회 실패: {e}")
    return None


def search_channel_id(query):
    """플레인 채널명/키워드 → YouTube 검색으로 첫 '채널' 결과의 UC아이디.

    @핸들/URL/UC 로 안 잡히는 한글 채널명(예: '삼프로TV')을 텔레그램에서 바로
    등록할 수 있도록 채널 필터(sp) 검색 결과에서 channelId 를 뽑는다.
    """
    import urllib.parse
    q = urllib.parse.quote(query.strip())
    # sp=EgIQAg%3D%3D → 검색 유형을 '채널'로 필터 (영상 채널ID 오탐 감소)
    url = (f"https://www.youtube.com/results?search_query={q}"
           f"&sp=EgIQAg%253D%253D")
    try:
        r = requests.get(url, timeout=15, headers={'User-Agent': UA})
    except requests.RequestException as e:
        log(f"채널 검색 실패: {e}")
        return None
    # 채널 결과(channelRenderer)를 최우선, 없으면 일반 channelId 폴백
    for pat in (r'"channelRenderer":\{"channelId":"(UC[\w-]{22})"',
                r'"browseEndpoint":\{"browseId":"(UC[\w-]{22})"',
                r'"channelId":"(UC[\w-]{22})"'):
        m = re.search(pat, r.text)
        if m:
            return m.group(1)
    return None


def resolve_channel_query(value):
    """@핸들 / URL / UC아이디 → 해석. 실패하면 채널명 검색으로 폴백."""
    return resolve_channel_id(value) or search_channel_id(value)


def get_channel_name(channel_id):
    """RSS에서 채널 이름만 빠르게 조회"""
    try:
        r = requests.get(
            f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
            timeout=10)
        root = ET.fromstring(r.content)
        el = root.find('{http://www.w3.org/2005/Atom}title')
        return el.text if el is not None else channel_id
    except Exception:
        return channel_id


# 아래 *_core 함수는 (성공여부, 사람이 읽을 메시지) 를 반환한다.
# CLI(cmd_*) 와 텔레그램 명령 핸들러가 함께 재사용한다.

def list_channels_core(config):
    channels = config.get('youtube_channels', [])
    if not channels:
        return "등록된 채널이 없습니다. 채널명이나 @핸들을 보내면 추가됩니다."
    lines = [f"📺 등록된 채널 {len(channels)}개:"]
    for i, ch in enumerate(channels, 1):
        lines.append(f"{i}. {get_channel_name(ch)}  ({ch})")
    return "\n".join(lines)


def add_channel_core(config, value):
    channel_id = resolve_channel_query(value)
    if not channel_id:
        return False, (f"❌ 채널을 찾지 못했습니다: {value}\n"
                       f"@핸들, 채널 URL, UC아이디, 또는 정확한 채널명을 보내주세요.")
    channels = config.get('youtube_channels', [])
    name = get_channel_name(channel_id)
    if channel_id in channels:
        return False, f"ℹ️ 이미 등록된 채널입니다: {name} ({channel_id})"
    channels.append(channel_id)
    config['youtube_channels'] = channels
    save_config(config)
    return True, f"✅ 채널 추가됨: {name}\n({channel_id})\n지금부터 새 영상을 추적합니다."


def remove_channel_core(config, value):
    channels = config.get('youtube_channels', [])
    target = None
    # 목록 번호로 삭제
    if value.strip().isdigit():
        idx = int(value.strip()) - 1
        if 0 <= idx < len(channels):
            target = channels[idx]
    # UC 아이디 또는 핸들/URL/채널명로 삭제
    if target is None:
        rid = resolve_channel_query(value) or value.strip()
        if rid in channels:
            target = rid
    if target is None:
        return False, (f"❌ 해당 채널을 찾지 못했습니다: {value}\n"
                       f"/list 로 번호나 ID를 확인하세요.")
    name = get_channel_name(target)
    channels.remove(target)
    config['youtube_channels'] = channels
    save_config(config)
    return True, f"🗑 채널 삭제됨: {name} ({target})"


def cmd_list_channels(config):
    print("\n" + list_channels_core(config) + "\n")


def cmd_add_channel(config, value):
    ok, msg = add_channel_core(config, value)
    print(msg)
    if not ok and msg.startswith("❌"):
        sys.exit(1)


def cmd_remove_channel(config, value):
    ok, msg = remove_channel_core(config, value)
    print(msg)
    if not ok:
        sys.exit(1)


# ──────────────────────────── YouTube ────────────────────────────

def get_channel_videos(channel_id, debug=False):
    """YouTube RSS 피드에서 최신 동영상 목록 (API 키 불필요)"""
    # 혹시 핸들/URL이 들어와 있으면 해석
    if not re.fullmatch(r'UC[\w-]{22}', channel_id):
        resolved = resolve_channel_id(channel_id)
        if resolved:
            channel_id = resolved

    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        log(f"채널 {channel_id} RSS 오류: {e}")
        return []

    ns = {
        'atom': 'http://www.w3.org/2005/Atom',
        'yt': 'http://www.youtube.com/xml/schemas/2015',
    }
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as e:
        log(f"RSS 파싱 오류 ({channel_id}): {e}")
        return []

    name_el = root.find('atom:title', ns)
    channel_name = name_el.text if name_el is not None else channel_id

    videos = []
    for entry in root.findall('atom:entry', ns):
        vid_el = entry.find('yt:videoId', ns)
        if vid_el is None:
            continue
        title_el = entry.find('atom:title', ns)
        link_el = entry.find('atom:link', ns)
        pub_el = entry.find('atom:published', ns)
        vid = vid_el.text
        videos.append({
            'id': vid,
            'title': title_el.text if title_el is not None else '제목 없음',
            'url': link_el.get('href') if link_el is not None else f"https://www.youtube.com/watch?v={vid}",
            'published': pub_el.text if pub_el is not None else '',
            'channel': channel_name,
            'channel_id': channel_id,
        })

    log(f"{channel_name}: 최근 {len(videos)}개 동영상 확인", debug)
    return videos


# ──────────────────────────── secondb.ai ────────────────────────────
#
# secondb.ai 는 api.secondb.ai REST API 로 동작한다 (진단으로 확인):
#   GET  /api/v1/search_summary?url=<영상URL>   기존 요약 조회 (없으면 null)
#   POST /api/v1/summarize  {content_url, language}  요약 생성 시작 (비동기)
#   → quick_summary/summary 가 "processing..." 이 아니게 되면 완료
#
# 인증은 localStorage 토큰 기반(쿠키 없음)이라, 로그인된 브라우저로 secondb.ai 를
# 열면 앱이 보내는 Authorization 헤더를 그대로 관찰해 API 호출에 재사용한다.

SECONDB_API = "https://api.secondb.ai/api/v1"


@contextmanager
def secondb_session(debug=False):
    """로그인된 브라우저를 1회 열고 (api_request, headers) 를 제공.

    여러 영상을 처리할 때 매번 브라우저를 띄우지 않도록 세션을 재사용한다.
    로그인/실행 불가 시 None 을 내보낸다.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright가 없습니다:  pip install playwright && playwright install chromium")
        yield None
        return

    if not PROFILE_DIR.exists():
        print("secondb.ai 로그인 세션이 없습니다.")
        print("먼저 다음을 실행해 구글 로그인을 한 번 해주세요:")
        print("  python ~/youtube-telegram-summary/login.py")
        yield None
        return

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=not debug,
            user_agent=UA,
            viewport={'width': 1280, 'height': 900},
        )

        # 앱이 api.secondb.ai 로 보내는 Authorization 헤더를 관찰
        captured = {"auth": None}

        def on_request(req):
            if "api.secondb.ai" in req.url and not captured["auth"]:
                a = req.headers.get("authorization")
                if a:
                    captured["auth"] = a

        context.on("request", on_request)
        page = context.pages[0] if context.pages else context.new_page()

        try:
            page.goto("https://secondb.ai/", wait_until="networkidle", timeout=60000)

            if _needs_login(page):
                print("secondb.ai 로그인이 만료되었습니다. 다시 로그인해주세요:")
                print("  python ~/youtube-telegram-summary/login.py")
                yield None
                return

            # Authorization 헤더가 잡힐 때까지 잠시 대기 (앱의 초기 API 호출 대기)
            for _ in range(10):
                if captured["auth"]:
                    break
                page.wait_for_timeout(1000)

            headers = {"Accept": "application/json"}
            if captured["auth"]:
                headers["Authorization"] = captured["auth"]
                log("인증 토큰 확보", debug, is_debug=True)
            else:
                log("Authorization 헤더를 관찰하지 못했습니다 (쿠키 인증으로 시도)", debug)

            yield (context.request, headers)
        finally:
            context.close()


def get_secondb_summary(video_url, debug=False, language="kr"):
    """단일 영상 요약 (브라우저 1회 열어 처리) — 직접 호출/테스트용 편의 함수"""
    log(f"secondb.ai 요약 시작: {video_url}", debug)
    with secondb_session(debug) as session:
        if session is None:
            return None
        api, headers = session
        return _fetch_summary_via_api(api, video_url, headers, language, debug)


def _needs_login(page):
    """구글 로그인이 필요한(=데모만 보이는) 상태인지 확인"""
    url = page.url
    if "accounts.google.com" in url or "/login" in url or "/signin" in url:
        return True
    try:
        body = page.inner_text("body")
        if any(m in body for m in DEMO_MARKERS):
            return True
    except Exception:
        pass
    return False


def _summary_obj_done(obj):
    """API 응답 요약 객체가 생성 완료 상태인지"""
    if not obj or not isinstance(obj, dict):
        return False
    for key in ("quick_summary", "summary"):
        v = obj.get(key)
        if v and isinstance(v, str) and v.strip() and v != "processing...":
            return True
    return False


def _extract_text_from_obj(obj):
    """요약 객체에서 Telegram에 보낼 텍스트 추출 (quick_summary 우선, 없으면 summary)"""
    if not obj:
        return None
    for key in ("quick_summary", "summary"):
        v = obj.get(key)
        if v and isinstance(v, str) and v.strip() and v != "processing...":
            return v.strip()
    return None


# 폴링 설정: 긴 영상(예: 3시간짜리)은 요약에 수 분 걸릴 수 있다.
# summarize 는 URL 기준 멱등이라, 시간 내 못 끝내면 다음 실행에서 안전하게 이어받는다.
POLL_INTERVAL = 5      # 초
POLL_MAX = 48          # 최대 4분 (48 × 5초)


def _fetch_summary_via_api(api, video_url, headers, language, debug):
    """기존 요약 확인 → 없으면 생성 요청 → content_no 로 완료까지 폴링"""
    import urllib.parse
    enc = urllib.parse.quote(video_url, safe='')

    def get_json(url, post=None):
        try:
            if post is not None:
                r = api.post(url, headers={**headers, "Content-Type": "application/json"}, data=post)
            else:
                r = api.get(url, headers=headers)
            if r.ok:
                return r.json()
            log(f"HTTP {r.status}: {url}", debug, is_debug=True)
        except Exception as e:
            log(f"API 오류({url}): {e}", debug, is_debug=True)
        return None

    # 1) 기존 요약 조회
    obj = get_json(f"{SECONDB_API}/search_summary?url={enc}")

    # 2) 없으면 생성 요청 (URL 기준 멱등 — 중복 생성되지 않음)
    #    일시적 거부는 짧게 재시도하되, quota 초과(429)는 재시도 무의미하므로 즉시 중단
    if not obj:
        for attempt in range(3):
            log("새 요약 생성 요청..." + (f" (재시도 {attempt})" if attempt else ""), debug)
            try:
                r = api.post(
                    f"{SECONDB_API}/summarize",
                    headers={**headers, "Content-Type": "application/json"},
                    data={"content_url": video_url, "language": language},
                )
                if r.ok:
                    obj = r.json()
                    break
                if r.status == 429:
                    log("secondb.ai 요약 quota 초과(429) — 다음 실행에 자동 재시도합니다")
                    return None
                log(f"summarize 실패: HTTP {r.status}", debug, is_debug=True)
            except Exception as e:
                log(f"summarize 오류: {e}", debug, is_debug=True)
            time.sleep(8)

    if not obj:
        log("secondb API가 요약 객체를 반환하지 않았습니다 (다음 실행 시 재시도)")
        return None

    # 3) content_no 로 완료될 때까지 폴링
    content_no = obj.get("content_no")
    if content_no and not _summary_obj_done(obj):
        log("요약 생성 중...", debug)
        for _ in range(POLL_MAX):
            time.sleep(POLL_INTERVAL)
            latest = get_json(f"{SECONDB_API}/summaries/{content_no}")
            if latest:
                obj = latest
            if _summary_obj_done(obj):
                break

    text = _extract_text_from_obj(obj)
    if not text:
        log("요약이 시간 내에 완료되지 않았습니다 (다음 실행 시 자동 이어받음)")
    return text


# ──────────────────────────── Telegram ────────────────────────────

def send_telegram(text, config):
    token = config.get('telegram_bot_token', '')
    chat_id = config.get('telegram_chat_id', '')
    if not token or not chat_id:
        log("Telegram 설정이 없습니다 (config.json 확인)")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # HTML 모드는 <,>,& 만 이스케이프하면 되므로 Markdown보다 깨질 위험이 적다
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for i, chunk in enumerate(chunks):
        data = {'chat_id': chat_id, 'text': chunk,
                'parse_mode': 'HTML', 'disable_web_page_preview': False}
        try:
            result = requests.post(url, json=data, timeout=10).json()
            if not result.get('ok'):
                log(f"Telegram 오류: {result.get('description', result)}")
                return False
        except Exception as e:
            log(f"Telegram 전송 실패: {e}")
            return False
        if i < len(chunks) - 1:
            time.sleep(0.5)
    return True


def send_telegram_alert(config, text):
    """오류/상태 알림 전송 (요약 메시지와 구분되는 ⚠️ 포맷)"""
    import html
    msg = f"⚠️ <b>요약봇 알림</b>\n\n{html.escape(str(text))}"
    try:
        return send_telegram(msg, config)
    except Exception as e:
        log(f"알림 전송 실패: {e}")
        return False


def send_telegram_plain(config, text):
    """명령 응답용 평문 전송 (HTML 파싱 안 함 — 사용자 입력이 그대로 나가도 안전)."""
    token = config.get('telegram_bot_token', '')
    chat_id = config.get('telegram_chat_id', '')
    if not token or not chat_id:
        log("Telegram 설정이 없습니다 (config.json 확인)")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        result = requests.post(
            url,
            json={'chat_id': chat_id, 'text': text, 'disable_web_page_preview': True},
            timeout=10).json()
        if not result.get('ok'):
            log(f"Telegram 오류: {result.get('description', result)}")
            return False
        return True
    except Exception as e:
        log(f"Telegram 전송 실패: {e}")
        return False


# ─────────────────── 텔레그램 → 봇 (수신 명령 처리) ───────────────────
#
# 봇은 요약을 '보내기'만 하는 게 아니라, 사용자가 텔레그램에서 보낸 메시지를
# getUpdates 로 읽어 채널 추가/삭제/목록/실행 명령을 처리한다.
#  - 크론 실행마다 자동으로 밀린 명령을 소비 (인프라 0, 최대 주기만큼 지연)
#  - `python monitor.py --listen` 상시 long-poll 로 즉시 응답
# 중복 처리는 state.json 의 telegram_update_offset 으로 방지한다.

HELP_TEXT = (
    "🤖 유튜브 요약봇 명령\n\n"
    "• 채널명 / @핸들 / URL / UC아이디 를 그냥 보내면 → 모니터링 채널로 추가\n"
    "   예) 삼프로TV   |   @3protv   |   https://youtube.com/@3protv\n"
    "• /list — 등록된 채널 목록\n"
    "• /remove <번호|@핸들|UC아이디> — 채널 삭제\n"
    "• /run — 지금 새 영상 확인 실행\n"
    "• /help — 이 도움말"
)


def handle_command_text(config, text):
    """텔레그램 메시지 한 건을 해석·적용하고 응답 문자열을 반환.

    '/run' 요청이면 특수 신호 '__RUN__' 을 반환 (실제 실행은 호출부가 결정).
    처리할 게 없으면 None.
    """
    text = (text or "").strip()
    if not text:
        return None
    low = text.lower()

    if low in ("/help", "help", "도움말", "/start", "start"):
        return HELP_TEXT
    if low in ("/list", "list", "목록", "/channels", "채널"):
        return list_channels_core(config)
    if low in ("/run", "run", "실행", "확인"):
        return "__RUN__"
    if low.startswith(("/remove", "/delete", "삭제")):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return "삭제할 채널 번호나 ID를 함께 보내주세요. 예) /remove 2"
        return remove_channel_core(config, parts[1])[1]
    if low.startswith("/add"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return "추가할 채널명이나 @핸들을 함께 보내주세요. 예) /add 삼프로TV"
        text = parts[1].strip()
    # 그 외 모든 일반 텍스트 = 채널 추가 시도
    return add_channel_core(config, text)[1]


def _authorized_chat(config, chat_id):
    """설정된 telegram_chat_id 와 일치하는 채팅만 명령 허용 (타인 조작 방지)."""
    want = str(config.get('telegram_chat_id', ''))
    return bool(want) and str(chat_id) == want


def process_incoming_commands(config, debug=False, run_callback=None, long_poll=False):
    """텔레그램에 쌓인 메시지를 offset 기준으로 소비해 채널 명령을 처리.

    long_poll=True 면 getUpdates 를 최대 ~50초 대기(즉시 응답용 --listen 에서 사용).
    run_callback 이 주어지고 '/run' 이 오면 즉시 실행, 없으면 안내만 응답.
    """
    token = config.get('telegram_bot_token', '')
    if not token:
        return
    offset = load_state().get('telegram_update_offset')
    params = {"timeout": 50 if long_poll else 0}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                         params=params, timeout=(55 if long_poll else 20)).json()
    except Exception as e:
        log(f"getUpdates 오류: {e}", debug, is_debug=True)
        return
    if not r.get("ok"):
        log(f"getUpdates 실패: {r.get('description')}", debug, is_debug=True)
        return

    updates = r.get("result", [])
    if not updates:
        return

    last_update_id = None
    processed = 0
    for upd in updates:
        last_update_id = upd["update_id"]
        msg = upd.get("message") or upd.get("channel_post") or {}
        text = msg.get("text", "")
        chat_id = msg.get("chat", {}).get("id")
        if not text:
            continue
        if not _authorized_chat(config, chat_id):
            log(f"권한 없는 채팅 무시: {chat_id}", debug, is_debug=True)
            continue
        log(f"명령 수신: {text}")
        reply = handle_command_text(config, text)
        if reply == "__RUN__":
            if run_callback:
                send_telegram_plain(config, "🔄 지금 새 영상을 확인합니다…")
                try:
                    n, _ = run_callback()
                    send_telegram_plain(config, f"✅ 확인 완료 (새 요약 {n}건)")
                except Exception as e:
                    send_telegram_plain(config, f"❌ 실행 오류: {e}")
            else:
                send_telegram_plain(config, "🔄 이번 실행에서 곧 새 영상을 확인합니다.")
        elif reply:
            send_telegram_plain(config, reply)
        processed += 1

    # 처리한 업데이트 이후만 다음에 받도록 offset 전진 (state 재로딩 후 저장)
    if last_update_id is not None:
        state = load_state()
        state['telegram_update_offset'] = last_update_id + 1
        save_state(state)
    if processed:
        log(f"텔레그램 명령 {processed}건 처리")


def listen_loop(config, debug=False):
    """--listen: long-poll 로 텔레그램 명령을 즉시 처리 + N시간 주기로 새 영상 자동 감지.

    getUpdates 를 소유하는 단일 프로세스가 (1) 명령 즉시 응답과 (2) 주기 감지를 함께 수행한다.
    → 별도 주기 잡을 두지 않아 같은 봇 두 프로세스의 getUpdates 충돌이 원천 발생하지 않는다.
    감지 주기는 config 의 schedule_interval_hours(기본 6). 시작 시 1회 즉시 감지한다.
    """
    log("텔레그램 리스너 시작 (long-poll + 주기 감지). 채널명을 보내면 추가됩니다. Ctrl+C 로 종료.")
    send_telegram_plain(config, "🤖 요약봇 리스너 시작됨. 채널명을 보내면 추가합니다.\n/help 로 명령 목록 확인.")
    interval_s = max(float(config.get('schedule_interval_hours', 6)), 0.05) * 3600
    last_detect = None  # None = 시작 즉시 1회 감지(부팅 직후 monotonic 값에 무관하게 보장)
    while True:
        try:
            if last_detect is None or (time.monotonic() - last_detect) >= interval_s:
                log(f"주기 감지 실행 (interval={interval_s/3600:.2g}h)")
                run_monitor(config, debug)
                last_detect = time.monotonic()
            process_incoming_commands(
                config, debug,
                run_callback=lambda: run_monitor(config, debug),
                long_poll=True)
        except KeyboardInterrupt:
            log("리스너 종료")
            break
        except Exception as e:
            log(f"리스너 오류(계속 실행): {e}")
            time.sleep(5)


def ping_healthcheck(config, suffix=""):
    """선택: config의 healthcheck_ping_url 로 핑 (healthchecks.io 등 외부 감시용).

    스케줄이 아예 안 돌면 모니터 자신은 그 사실을 알릴 수 없다(자기가 안 도니까).
    외부 감시 서비스에 매 실행마다 핑을 보내면, 핑이 늦을 때 그쪽에서 사용자에게 알려준다.
    healthchecks.io 기준: 성공 시 기본 URL, 시작 시 /start, 실패 시 /fail
    """
    url = config.get('healthcheck_ping_url')
    if not url:
        return
    try:
        requests.get(url.rstrip('/') + suffix, timeout=10)
    except Exception:
        pass


def append_daily_log(video, summary, source, config, debug=False):
    """요약을 옵시디언 날짜별 데일리 로그(YYYY-MM-DD.md)에 추가.

    config.obsidian_daily_dir 가 설정돼 있을 때만 동작. 같은 영상은 중복 저장 안 함.
    """
    daily_dir = config.get('obsidian_daily_dir', '')
    if not daily_dir:
        return
    try:
        ddir = Path(daily_dir).expanduser()
        ddir.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime('%Y-%m-%d')
        fpath = ddir / f"{today}.md"
        url = video.get('url', '')

        # 파일이 없으면 frontmatter + 헤더 생성
        if not fpath.exists():
            weekday = datetime.now().strftime('%a')
            fpath.write_text(
                f"---\ntype: youtube-summary-log\ndate: {today}\n"
                f"tags: [daily, youtube, summary]\n---\n\n"
                f"# 📺 {today} ({weekday}) 유튜브 요약 로그\n\n"
                f"> 봇이 자동 생성. 새 영상 요약이 시간순으로 추가됩니다.\n",
                encoding='utf-8')

        # 중복 방지: 같은 URL이 이미 있으면 skip
        if url and url in fpath.read_text(encoding='utf-8'):
            log(f"데일리 로그에 이미 있음(skip): {video.get('title')}", debug, is_debug=True)
            return

        ts = datetime.now().strftime('%H:%M')
        # 제목의 대괄호는 마크다운 링크를 깨뜨리므로 치환
        safe_title = str(video.get('title', '제목 없음')).replace('[', '(').replace(']', ')')
        block = (
            f"\n---\n\n## [{safe_title}]({url})\n"
            f"- 📺 {video.get('channel', '')} · 🔖 {source} · 🕘 {ts}\n\n"
            f"{summary.strip()}\n"
        )
        with open(fpath, 'a', encoding='utf-8') as f:
            f.write(block)
        log(f"데일리 로그 저장: {fpath.name}", debug, is_debug=True)
    except Exception as e:
        log(f"데일리 로그 저장 실패: {e}")


def format_message(video, summary, source="secondb.ai"):
    import html
    def esc(s):
        return html.escape(str(s or ""))

    published = video.get('published', '')[:10]
    date_str = f"📅 {esc(published)}\n" if published else ""
    return (
        f"🎬 <b>새 동영상 알림</b>\n\n"
        f"📺 {esc(video.get('channel'))}\n"
        f"🎥 <a href=\"{esc(video.get('url'))}\">{esc(video.get('title'))}</a>\n"
        f"{date_str}\n"
        f"📝 <b>{esc(source)} 요약</b>\n\n"
        f"{esc(summary[:3500])}"
    )


# ──────────────────────────── 메인 ────────────────────────────

def _check_missed_schedule(config, state):
    """직전 실행 이후 예상 주기보다 오래 비었으면 알림 (실행이 재개된 시점에 감지).

    한계: 스케줄이 '완전히' 멈추면 모니터 자신이 안 돌아 알릴 수 없다.
    그 경우는 config의 healthcheck_ping_url(외부 감시)로 보완한다.
    """
    last = state.get('last_checked')
    if not last:
        return
    interval_h = float(config.get('schedule_interval_hours', 1))
    try:
        gap_h = (datetime.now() - datetime.fromisoformat(last)).total_seconds() / 3600
    except Exception:
        return
    threshold = max(interval_h * 2.5, interval_h + 1)
    if gap_h >= threshold:
        log(f"스케줄 공백 감지: 약 {gap_h:.1f}시간 (예상 주기 {interval_h}시간)")
        send_telegram_alert(
            config,
            f"스케줄이 약 {gap_h:.1f}시간 동안 실행되지 않았습니다.\n"
            f"예상 주기: {interval_h}시간 — PC 꺼짐/절전/로그아웃 등으로 건너뛰었을 수 있습니다.\n"
            f"지금 정상 재개되었습니다."
        )


def _claude_enabled(config):
    """Anthropic API 키가 설정돼 있으면 Claude 폴백 사용 가능"""
    try:
        from claude_summary import claude_enabled
        return claude_enabled(config)
    except Exception:
        return False


def _summarize_with_fallback(session, video, language, config, debug=False):
    """secondb.ai 먼저 시도 → 실패하면 Claude(자막+요약) 폴백. (요약, 출처) 반환."""
    # 1) secondb.ai (세션이 열려 있을 때)
    if session is not None:
        api, headers = session
        s = _fetch_summary_via_api(api, video['url'], headers, language, debug)
        if s:
            return s, 'secondb.ai'

    # 2) Claude 폴백 (자막이 있는 영상만)
    if _claude_enabled(config):
        try:
            from claude_summary import claude_fallback_summary
            s = claude_fallback_summary(video, language, config, debug)
            if s:
                return s, 'Claude'
        except Exception as e:
            log(f"Claude 폴백 오류: {e}")

    return None, None


def run_monitor(config, debug=False):
    state = load_state()
    seen = set(state.get('seen_videos', []))
    channels = config.get('youtube_channels', [])
    if not channels:
        log("등록된 채널이 없습니다. --add-channel 로 추가하세요.")
        return 0, []

    # #2 스케줄 미실행(공백) 감지 — 실행 재개 시점에 알림
    _check_missed_schedule(config, state)

    language = config.get('summary_language', 'kr')
    # #3 신규/장기휴면 채널의 back-catalog 무더기 전송 방지용 상한 (기본 2)
    max_per_channel = int(config.get('max_videos_per_channel_per_run', 2))

    # 신규 영상 수집
    #  - '처음 보는' 채널(현재 피드에 본 영상이 하나도 없음 = 첫 등록/장기 휴면):
    #    과거 영상 무더기 방지를 위해 최신 N개만 전송, 나머지 과거분은 전송 없이 '본 영상' 처리.
    #  - 이미 추적 중이던 채널: 새 영상을 모두 전송(누락 방지). 실패분은 다음 실행에서 재시도.
    #    (예전엔 활성 채널도 최신 N개 외엔 건너뛰어, quota/타임아웃으로 밀린 영상이 영구 누락됐음)
    new_videos = []
    for channel_id in channels:
        vids = get_channel_videos(channel_id, debug)
        if not vids:
            continue
        unseen = [v for v in vids if v['id'] not in seen]
        if not unseen:
            continue
        unseen.sort(key=lambda v: v.get('published', ''), reverse=True)  # 최신 우선
        cname = vids[0]['channel']

        first_contact = len(unseen) == len(vids)  # 피드에 본 영상이 하나도 없음
        if first_contact and len(unseen) > max_per_channel:
            to_send = unseen[:max_per_channel]
            for v in unseen[max_per_channel:]:
                seen.add(v['id'])  # 신규/장기휴면 채널의 과거분만 전송 없이 기록
            log(f"{cname}: (신규/장기휴면) 신규 {len(unseen)}개 → 최신 {len(to_send)}개만 전송, "
                f"나머지 {len(unseen) - len(to_send)}개 과거분 건너뜀")
        else:
            to_send = unseen  # 기존 채널: 새 영상 전부 전송 (누락 없음)
            if len(to_send) > max_per_channel:
                log(f"{cname}: 기존 채널 — 밀린 새 영상 {len(to_send)}개 모두 처리(누락 방지)")
        new_videos.extend(to_send)

    new_videos.sort(key=lambda v: v.get('published', ''))  # 전송은 올라온 순서대로

    new_count, failed = 0, []
    if new_videos:
        log(f"처리 대상 {len(new_videos)}개")
        # 브라우저는 실행당 1회만 열고 세션을 모든 영상에 재사용
        with secondb_session(debug) as session:
            # secondb 세션이 없고 Claude 폴백도 없으면 알림 후 종료
            if session is None and not _claude_enabled(config):
                send_telegram_alert(
                    config,
                    "secondb.ai 로그인 세션이 없거나 만료되었습니다.\n"
                    "다음 명령으로 다시 로그인해 주세요:  python login.py\n"
                    "(해당 영상들은 다음 실행 때 자동 재시도됩니다.)"
                )
            else:
                if session is None:
                    log("secondb 세션 없음 → Claude 폴백으로 진행")
                for video in new_videos:
                    log(f"처리 중: {video['title']}")
                    summary, source = _summarize_with_fallback(
                        session, video, language, config, debug)
                    if summary:
                        if send_telegram(format_message(video, summary, source), config):
                            log(f"전송 완료({source}): {video['title']}")
                            append_daily_log(video, summary, source, config, debug)
                            seen.add(video['id'])
                            new_count += 1
                        else:
                            failed.append(video['title'])
                    else:
                        log(f"요약 실패 (다음 실행 시 재시도): {video['title']}")
                    time.sleep(2)
    else:
        log("새 동영상 없음")

    state['seen_videos'] = list(seen)
    state['last_checked'] = datetime.now().isoformat()
    save_state(state)
    log(f"완료: {new_count}개 전송" + (f", {len(failed)}개 실패" if failed else ""))
    return new_count, failed


def main():
    parser = argparse.ArgumentParser(description='YouTube → secondb.ai → Telegram')
    parser.add_argument('--debug', action='store_true', help='브라우저 표시 + 상세 로그')
    parser.add_argument('--list-channels', action='store_true', help='등록된 채널 목록')
    parser.add_argument('--add-channel', metavar='CH', help='채널 추가 (@핸들/URL/UC아이디)')
    parser.add_argument('--remove-channel', metavar='CH', help='채널 삭제 (번호 또는 ID)')
    parser.add_argument('--listen', action='store_true',
                        help='텔레그램 명령 상시 대기(long-poll) — 채널명 보내면 즉시 등록')
    parser.add_argument('--logfile', metavar='PATH',
                        help='모든 출력을 이 파일에 기록 (Windows 작업 스케줄러의 pythonw 실행용)')
    args = parser.parse_args()

    # --logfile 지정 시 stdout/stderr 를 파일로 (pythonw 는 콘솔이 없어 출력이 사라지므로)
    if args.logfile:
        try:
            fh = open(args.logfile, 'a', encoding='utf-8', buffering=1)
            sys.stdout = fh
            sys.stderr = fh
            print(f"\n===== {datetime.now().isoformat()} 실행 시작 =====")
        except Exception as e:
            print(f"로그 파일 열기 실패({args.logfile}): {e}")

    config = load_config()

    if args.list_channels:
        cmd_list_channels(config)
        return
    if args.add_channel:
        cmd_add_channel(config, args.add_channel)
        return
    if args.remove_channel:
        cmd_remove_channel(config, args.remove_channel)
        return
    if args.listen:
        listen_loop(config, args.debug)
        return

    # #1 실행 중 예외 발생 시 텔레그램 알림 + (선택) 외부 헬스체크 핑
    ping_healthcheck(config, "/start")
    try:
        # 크론 실행마다: 텔레그램에 쌓인 채널 명령을 먼저 반영 (모니터가 곧 실행되므로 run 콜백은 생략)
        process_incoming_commands(config, args.debug)
        run_monitor(config, args.debug)
        ping_healthcheck(config)  # 성공 핑
    except Exception as e:
        import traceback
        log("실행 중 오류 발생:\n" + traceback.format_exc())
        send_telegram_alert(config, f"실행 중 오류가 발생했습니다.\n{type(e).__name__}: {e}")
        ping_healthcheck(config, "/fail")
        raise


if __name__ == '__main__':
    main()
