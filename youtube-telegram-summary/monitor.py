#!/usr/bin/env python3
"""
YouTube 채널 모니터 → secondb.ai 요약 → Telegram 전송

사용법:
  python monitor.py                      새 동영상 확인 후 요약·전송
  python monitor.py --debug              브라우저 표시 + 상세 로그
  python monitor.py --list-channels      등록된 채널 목록
  python monitor.py --add-channel @핸들  채널 추가 (@핸들 / URL / UC아이디 모두 가능)
  python monitor.py --remove-channel X   채널 삭제 (UC아이디 또는 목록 번호)
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


def cmd_list_channels(config):
    channels = config.get('youtube_channels', [])
    if not channels:
        print("등록된 채널이 없습니다. --add-channel 로 추가하세요.")
        return
    print(f"\n등록된 채널 {len(channels)}개:")
    for i, ch in enumerate(channels, 1):
        print(f"  {i}. {get_channel_name(ch)}  ({ch})")
    print()


def cmd_add_channel(config, value):
    channel_id = resolve_channel_id(value)
    if not channel_id:
        print(f"채널 ID를 찾지 못했습니다: {value}")
        print("YouTube 채널 페이지의 @핸들, 전체 URL, 또는 UC로 시작하는 ID를 입력하세요.")
        sys.exit(1)

    channels = config.get('youtube_channels', [])
    if channel_id in channels:
        print(f"이미 등록된 채널입니다: {get_channel_name(channel_id)} ({channel_id})")
        return

    channels.append(channel_id)
    config['youtube_channels'] = channels
    save_config(config)
    print(f"채널 추가됨: {get_channel_name(channel_id)} ({channel_id})")


def cmd_remove_channel(config, value):
    channels = config.get('youtube_channels', [])
    target = None

    # 목록 번호로 삭제
    if value.isdigit():
        idx = int(value) - 1
        if 0 <= idx < len(channels):
            target = channels[idx]
    # UC 아이디 또는 핸들/URL로 삭제
    if target is None:
        rid = resolve_channel_id(value) or value
        if rid in channels:
            target = rid

    if target is None:
        print(f"해당 채널을 찾지 못했습니다: {value}")
        print("--list-channels 로 번호나 ID를 확인하세요.")
        sys.exit(1)

    channels.remove(target)
    config['youtube_channels'] = channels
    save_config(config)
    print(f"채널 삭제됨: {target}")


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


def format_message(video, summary):
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
        f"📝 <b>secondb.ai 요약</b>\n\n"
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
            if session is None:
                # #1 로그인 만료/세션 없음 — 사람이 조치해야 하므로 알림
                send_telegram_alert(
                    config,
                    "secondb.ai 로그인 세션이 없거나 만료되었습니다.\n"
                    "다음 명령으로 다시 로그인해 주세요:  python login.py\n"
                    "(해당 영상들은 다음 실행 때 자동 재시도됩니다.)"
                )
            else:
                api, headers = session
                for video in new_videos:
                    log(f"처리 중: {video['title']}")
                    summary = _fetch_summary_via_api(api, video['url'], headers, language, debug)
                    if summary:
                        if send_telegram(format_message(video, summary), config):
                            log(f"전송 완료: {video['title']}")
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

    # #1 실행 중 예외 발생 시 텔레그램 알림 + (선택) 외부 헬스체크 핑
    ping_healthcheck(config, "/start")
    try:
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
