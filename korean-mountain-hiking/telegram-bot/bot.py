#!/usr/bin/env python3
"""
한국 등산 텔레그램 봇 — korean-mountain-hiking 스킬 데이터 기반.

사용자가 텔레그램에서 산 이름을 보내면:
  - `references/mountains.json` 의 코스/높이/100대 명산 정보를 응답
  - 메시지에 날짜가 있으면 기상청 산악날씨(mtId, 5일) 또는 단기예보(3일) fallback 으로
    해당 날짜 날씨까지 함께 응답

실행:
  python bot.py --listen     # long-poll 상시 대기 (즉시 응답, Ctrl+C 종료)
  python bot.py --once       # 밀린 메시지 한 번만 처리 (cron 용)
  python bot.py --check "북한산 이번주 토요일"   # 텔레그램 없이 로컬 응답 미리보기

설정 (우선순위: 환경변수 > config.json):
  환경변수 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  또는  telegram-bot/config.json  ({"telegram_bot_token": "...", "telegram_chat_id": "..."})
  telegram_chat_id 를 비워두면 아무 채팅에서나 응답한다(공개 조회 봇).

의존성:  pip install requests
"""

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, date
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
MOUNTAINS_JSON = SKILL_DIR / "references" / "mountains.json"

CONFIG_DIR = Path.home() / ".config" / "korean-mountain-hiking"
STATE_FILE = CONFIG_DIR / "state.json"
HISTORY_FILE = CONFIG_DIR / "history.json"

# 대화 맥락(멀티턴) 보관 정책: 채팅별 최근 N개 메시지, TTL 지나면 새 대화로 취급
HISTORY_MAX_MESSAGES = 12   # user/assistant 합산 (6턴)
HISTORY_TTL_SECONDS = 2 * 3600

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ──────────────────────────── 설정 / 상태 ────────────────────────────

def load_config():
    """환경변수 우선, 없으면 telegram-bot/config.json → ~/.config/.../config.json."""
    cfg = {}
    for path in (SCRIPT_DIR / "config.json", CONFIG_DIR / "config.json"):
        if path.exists():
            try:
                cfg = json.loads(path.read_text())
                break
            except Exception as e:
                log(f"config.json 파싱 실패({path}): {e}")
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get("telegram_bot_token", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or cfg.get("telegram_chat_id", "")
    proxy = (os.environ.get("KSKILL_PROXY_BASE_URL")
             or cfg.get("kskill_proxy_base_url")
             or "https://k-skill-proxy.nomadamas.org")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or cfg.get("anthropic_api_key", "")
    claude_model = os.environ.get("CLAUDE_MODEL") or cfg.get("claude_model", "")
    return {"telegram_bot_token": token,
            "telegram_chat_id": str(chat_id) if chat_id else "",
            "kskill_proxy_base_url": proxy.rstrip("/"),
            "anthropic_api_key": anthropic_key,
            "claude_model": claude_model}


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def record_usage(kind):
    """일자별 사용 카운터 (state.json 의 usage 키). 실패해도 봇 동작엔 영향 없음."""
    try:
        state = load_state()
        day = date.today().isoformat()
        usage = state.setdefault("usage", {})
        day_stats = usage.setdefault(day, {})
        day_stats[kind] = day_stats.get(kind, 0) + 1
        # 30일 이전 기록은 정리
        for k in [d for d in usage if d < (date.today() - timedelta(days=30)).isoformat()]:
            usage.pop(k, None)
        save_state(state)
    except Exception as e:
        log(f"usage 기록 실패(무시): {e}")


# ──────────────────────────── 대화 히스토리 (멀티턴) ────────────────────────────

def _load_histories():
    try:
        return json.loads(HISTORY_FILE.read_text())
    except Exception:
        return {}


def _save_histories(hist):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(hist, ensure_ascii=False))
    except Exception as e:
        log(f"히스토리 저장 실패(무시): {e}")


def get_history(chat_id):
    """채팅별 최근 대화. TTL 지난 히스토리는 버린다. 반환: [{"role","content"}]."""
    if chat_id is None:
        return []
    entry = _load_histories().get(str(chat_id))
    if not entry:
        return []
    if time.time() - entry.get("updated", 0) > HISTORY_TTL_SECONDS:
        return []
    return entry.get("messages", [])


def append_history(chat_id, user_text, assistant_text):
    """한 턴(user+assistant)을 히스토리에 추가하고 최근 N개만 유지."""
    if chat_id is None:
        return
    hist = _load_histories()
    entry = hist.setdefault(str(chat_id), {"messages": []})
    if time.time() - entry.get("updated", 0) > HISTORY_TTL_SECONDS:
        entry["messages"] = []
    entry["messages"] += [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": assistant_text},
    ]
    entry["messages"] = entry["messages"][-HISTORY_MAX_MESSAGES:]
    entry["updated"] = time.time()
    _save_histories(hist)


def clear_history(chat_id):
    if chat_id is None:
        return
    hist = _load_histories()
    if hist.pop(str(chat_id), None) is not None:
        _save_histories(hist)


# ──────────────────────────── 일출·일몰 ────────────────────────────

def sun_times(lat, lon, target):
    """NOAA 근사식으로 일출·일몰 시각(KST, HH:MM) 계산. 극단 위도 등 계산 불가 시 None."""
    try:
        n = target.timetuple().tm_yday
        gamma = 2 * math.pi / 365 * (n - 1 + 0.5)
        eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                           - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
        decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
                - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
                - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
        lat_r = math.radians(lat)
        cos_ha = (math.cos(math.radians(90.833)) / (math.cos(lat_r) * math.cos(decl))
                  - math.tan(lat_r) * math.tan(decl))
        if not -1 <= cos_ha <= 1:
            return None
        ha = math.degrees(math.acos(cos_ha))
        sunrise_utc = 720 - 4 * (lon + ha) - eqtime   # 분 단위 UTC
        sunset_utc = 720 - 4 * (lon - ha) - eqtime

        def fmt(minutes):
            m = (minutes + 9 * 60) % 1440  # KST = UTC+9
            return f"{int(m // 60):02d}:{int(m % 60):02d}"

        return {"sunrise": fmt(sunrise_utc), "sunset": fmt(sunset_utc)}
    except Exception:
        return None


# ──────────────────────────── 산 데이터 ────────────────────────────

_MOUNTAINS = None


def load_mountains():
    global _MOUNTAINS
    if _MOUNTAINS is None:
        data = json.loads(MOUNTAINS_JSON.read_text())
        _MOUNTAINS = data.get("mountains", data if isinstance(data, list) else [])
    return _MOUNTAINS


def match_mountain(text):
    """메시지에서 산 이름을 찾아 해당 항목을 반환. 가장 긴 이름 우선 매칭."""
    text_norm = re.sub(r"\s+", "", text)
    best = None
    best_len = 0
    for m in load_mountains():
        names = [m.get("name", "")] + list(m.get("aliases", []) or [])
        for name in names:
            if not name:
                continue
            key = re.sub(r"\s+", "", name)
            # "산" 접미사 유무를 유연하게: 원형과 '산' 제거형 모두 시도
            candidates = {key}
            if key.endswith("산") and len(key) > 2:
                candidates.add(key[:-1])
            for cand in candidates:
                if cand and cand in text_norm and len(cand) > best_len:
                    best = m
                    best_len = len(cand)
    return best


# ──────────────────────────── 날짜 파싱 ────────────────────────────

_WEEKDAYS = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}


def parse_date(text, today=None):
    """한국어 메시지에서 날짜를 추출. 없으면 None.

    지원: 오늘/내일/모레/글피/이번주·다음주 요일/이번·다음 주말/M월 D일/MM-DD/YYYY-MM-DD.
    """
    today = today or date.today()
    t = text.replace(" ", "")

    # 1) 절대 날짜 YYYY-MM-DD / YYYY.MM.DD / YYYY/MM/DD
    m = re.search(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})", t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # 2) M월 D일
    m = re.search(r"(\d{1,2})월\s*(\d{1,2})일", text)
    if m:
        mm, dd = int(m.group(1)), int(m.group(2))
        year = today.year
        try:
            cand = date(year, mm, dd)
            if cand < today:  # 이미 지난 날짜면 내년
                cand = date(year + 1, mm, dd)
            return cand
        except ValueError:
            pass

    # 3) 상대 표현
    if "내일모레" in t or "모레" in t:
        return today + timedelta(days=2)
    if "글피" in t:
        return today + timedelta(days=3)
    if "내일" in t:
        return today + timedelta(days=1)
    if "오늘" in t:
        return today

    # 4) 요일 (이번주/다음주 + 요일, 또는 단독 요일)
    next_week = "다음주" in t or "담주" in t
    m = re.search(r"([월화수목금토일])요일", t) or re.search(r"([월화수목금토일])(?![요])", t)
    weekday_hit = None
    if m and m.group(1) in _WEEKDAYS:
        weekday_hit = _WEEKDAYS[m.group(1)]
    if "주말" in t and weekday_hit is None:
        weekday_hit = 5  # 토요일 기준
    if weekday_hit is not None:
        delta = (weekday_hit - today.weekday()) % 7
        if next_week:
            delta += 7
        elif delta == 0:
            delta = 0  # 오늘이 그 요일이면 오늘
        return today + timedelta(days=delta)

    # 5) "이번주말" 만 있고 요일 없을 때
    if "이번주" in t or "이번" in t:
        delta = (5 - today.weekday()) % 7
        return today + timedelta(days=delta)

    return None


# ──────────────────────────── 코스 정보 포맷 ────────────────────────────

def format_courses(m):
    name = m.get("name", "?")
    region = m.get("region", "")
    height = m.get("height_m")
    rank = m.get("rank_100")

    header = f"⛰️ {name}"
    meta = []
    if region:
        meta.append(region)
    if height:
        meta.append(f"{height}m")
    if rank:
        meta.append(f"100대 명산 #{rank}")
    if meta:
        header += f" ({' · '.join(meta)})"

    lines = [header]
    courses = m.get("courses") or []
    if courses:
        lines.append("")
        lines.append("📍 등산 코스")
        for c in courses:
            star = " ⭐추천" if c.get("recommended") else ""
            cname = c.get("name", "코스") + star
            parts = []
            if c.get("route"):
                parts.append(c["route"])
            spec = []
            if c.get("length_km") is not None:
                spec.append(f"{c['length_km']}km")
            if c.get("duration"):
                spec.append(c["duration"])
            if c.get("difficulty"):
                spec.append(c["difficulty"])
            if spec:
                parts.append(" · ".join(spec))
            detail = "  /  ".join(parts)
            lines.append(f"• {cname}\n    {detail}" if detail else f"• {cname}")
            if c.get("note"):
                lines.append(f"    └ {c['note']}")
    else:
        lines.append("")
        lines.append("코스 상세 데이터가 아직 없습니다. 산림청/국립공원공단에서 코스 확인 권장.")

    # 등산로 정보 지도 링크는 항상 포함
    name = m.get("name", "")
    lines.append("")
    lines.append("🗺️ 등산로 지도")
    if m.get("map_url"):
        lines.append(f"  • 공식 등산 지도: {m['map_url']}")
    # 네이버 지도 — 실제 등산로(트레일)가 표시됨
    nq = (name + " 등산로").replace(" ", "%20")
    lines.append(f"  • 네이버 지도(등산로): https://map.naver.com/p/search/{nq}")
    # 카카오맵 — 위치·주변 (유지)
    kq = (name + " 등산로").replace(" ", "+")
    lines.append(f"  • 카카오맵: https://map.kakao.com/?q={kq}")
    return "\n".join(lines)


# ──────────────────────────── 날씨 ────────────────────────────

def _weather_by_mtid(mt_id, target):
    """기상청 산악날씨 API (mtId, 최대 5일 3시간 간격). target=date → 그 날짜만."""
    url = ("https://www.weather.go.kr/w/wnuri-fct2021/theme/mountains-forecast.do"
           f"?mtId={mt_id}&hr1=N&unit=m/s")
    try:
        r = requests.get(url, headers={
            "User-Agent": UA,
            "Referer": "https://www.weather.go.kr/w/forecast/life/mountain.do",
            "X-Requested-With": "XMLHttpRequest",
        }, timeout=15, verify=False)
        html = r.text
    except Exception as e:
        log(f"산악날씨 요청 실패: {e}")
        return None, []

    daily_pat = re.compile(
        r'<div class="daily" data-date="(\d{4}-\d{2}-\d{2})"[^>]*>(.*?)</div>\s*</div>\s*</div>',
        re.DOTALL)
    item_pat = re.compile(
        r'data-time="[^"]+".*?<span>(\d+시)</span>.*?title="([^"]+)".*?'
        r'<span class="hid feel">(\d+)℃</span>.*?<span>(\d+%)</span>',
        re.DOTALL)

    results = {}
    order = []
    for m in daily_pat.finditer(html):
        d = m.group(1)
        section = m.group(2)
        tmin = re.search(r'minval">(\d+)℃', section)
        tmax = re.search(r'maxval">(\d+)℃', section)
        slots = []
        for sm in item_pat.finditer(section):
            slots.append({"time": sm.group(1), "sky": sm.group(2),
                          "temp": sm.group(3) + "℃", "pop": sm.group(4)})
        results[d] = {
            "tmin": (tmin.group(1) + "℃") if tmin else "-",
            "tmax": (tmax.group(1) + "℃") if tmax else "-",
            "slots": slots,
        }
        order.append(d)
    return results, order


def _weather_by_proxy(lat, lon, target, base_url):
    """k-skill-proxy 단기예보 fallback. 반환: {date_str: {...}} 또는 None."""
    if lat is None or lon is None:
        return None, []
    try:
        r = requests.get(f"{base_url}/v1/korea-weather/forecast",
                         params={"lat": lat, "lon": lon}, timeout=15)
        data = r.json()
    except Exception as e:
        log(f"단기예보 요청 실패: {e}")
        return None, []

    # 응답에서 item 리스트를 최대한 유연하게 추출
    items = None
    if isinstance(data, dict):
        for key in ("items", "forecast", "list", "data"):
            v = data.get(key)
            if isinstance(v, list):
                items = v
                break
        if items is None:
            try:
                items = data["response"]["body"]["items"]["item"]
            except Exception:
                items = None
    elif isinstance(data, list):
        items = data
    if not items:
        return None, []

    # category 기반(KMA 원형) 또는 평탄화된 dict 모두 대응
    by_date = {}
    order = []
    for it in items:
        d = it.get("fcstDate") or it.get("date")
        tm = it.get("fcstTime") or it.get("time") or ""
        if not d:
            continue
        d_norm = d if "-" in str(d) else f"{str(d)[:4]}-{str(d)[4:6]}-{str(d)[6:8]}"
        slot = by_date.setdefault(d_norm, {})
        if d_norm not in order:
            order.append(d_norm)
        cat = it.get("category")
        val = it.get("fcstValue")
        if cat is not None:
            slot.setdefault("_raw", {}).setdefault(tm, {})[cat] = val
        else:
            slot.setdefault("_flat", []).append(it)
    return by_date, order


_SKY = {"1": "맑음", "3": "구름많음", "4": "흐림"}
_PTY = {"0": "없음", "1": "비", "2": "비/눈", "3": "눈", "4": "소나기"}


def _fmt_proxy_day(day):
    """단기예보 하루치를 사람이 읽는 요약으로."""
    lines = []
    raw = day.get("_raw")
    if raw:
        temps = []
        for tm in sorted(raw):
            c = raw[tm]
            tmp = c.get("TMP") or c.get("T3H")
            sky = _SKY.get(str(c.get("SKY")), c.get("SKY", ""))
            pty = _PTY.get(str(c.get("PTY")), "")
            pop = c.get("POP")
            if tmp is not None:
                temps.append(int(float(tmp)))
            desc = sky
            if pty and pty != "없음":
                desc = f"{sky}, {pty}"
            hh = tm[:2] if len(str(tm)) >= 2 else tm
            extra = f" 강수확률 {pop}%" if pop not in (None, "") else ""
            lines.append(f"  {hh}시  {desc}  {tmp}℃{extra}")
        if temps:
            lines.insert(0, f"  최저 {min(temps)}℃ / 최고 {max(temps)}℃")
    elif day.get("_flat"):
        for it in day["_flat"][:8]:
            lines.append("  " + ", ".join(f"{k}:{v}" for k, v in it.items()
                                          if k not in ("fcstDate", "date")))
    return "\n".join(lines) if lines else "  (상세 항목 없음)"


def weather_fallback_links(m):
    """산 주소(region) 기반 날씨 확인 링크 — 산악날씨/단기예보가 없을 때 안내용."""
    name = m.get("name", "")
    region = (m.get("region") or "").split("/")[0].strip()
    naver_q = f"{region} {name} 날씨".strip().replace(" ", "+")
    return [
        "• 기상청 산악날씨: https://www.weather.go.kr/w/forecast/life/mountain.do",
        f"• 네이버 날씨({region + ' ' if region else ''}{name}): "
        f"https://search.naver.com/search.naver?query={naver_q}",
    ]


# ──────────── 중기예보 fallback (기상청 날씨누리 10일 예보, API 키 불필요) ────────────
#
# 산악날씨(5일)·단기예보(3일) 범위를 벗어난 날짜는 산 좌표(lat/lon) 기준으로
# 날씨누리 디지털예보(10일)의 중기 구간을 가져온다.
# 흐름: lat/lon → DFS 격자(x,y) → rest/zone/find/dong.do (법정동코드)
#       → wnuri digital-forecast.do?code= (10일 HTML) → data-midterm-forecast 일자 파싱

def _dfs_xy(lat, lon):
    """위경도 → 기상청 DFS 격자 (Lambert Conformal Conic, 표준 공식)."""
    RE, GRID = 6371.00877, 5.0
    SLAT1, SLAT2, OLON, OLAT = 30.0, 60.0, 126.0, 38.0
    XO, YO = 43, 136
    DEGRAD = math.pi / 180.0
    re_ = RE / GRID
    slat1, slat2 = SLAT1 * DEGRAD, SLAT2 * DEGRAD
    olon, olat = OLON * DEGRAD, OLAT * DEGRAD
    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = sf ** sn * math.cos(slat1) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re_ * sf / ro ** sn
    ra = math.tan(math.pi * 0.25 + lat * DEGRAD * 0.5)
    ra = re_ * sf / ra ** sn
    theta = lon * DEGRAD - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn
    x = int(ra * math.sin(theta) + XO + 0.5)
    y = int(ro - ra * math.cos(theta) + YO + 0.5)
    return x, y


def _dong_code(lat, lon):
    """산 좌표에서 가장 가까운 법정동 코드/이름. 실패 시 (None, None)."""
    x, y = _dfs_xy(lat, lon)
    url = ("https://www.weather.go.kr/w/rest/zone/find/dong.do"
           f"?x={x}&y={y}&lat={lat}&lon={lon}&lang=kor")
    try:
        r = requests.get(url, headers={"User-Agent": UA,
                                       "X-Requested-With": "XMLHttpRequest"},
                         timeout=15, verify=False)
        arr = r.json()
        if arr:
            return arr[0].get("code"), arr[0].get("name", "")
    except Exception as e:
        log(f"동코드 조회 실패: {e}")
    return None, None


_MID_SLIDE_PAT = re.compile(
    r'class="dfs-daily-slide"\s+data-date="(\d{4}-\d{2}-\d{2})"\s+'
    r'data-midterm-forecast="true".*?(?=class="dfs-daily-slide"|</body|$)',
    re.DOTALL)


def _weather_mid(lat, lon):
    """날씨누리 디지털예보(10일)에서 중기 구간만 파싱.

    반환: ({date: {tmin, tmax, am:{sky,pop}, pm:{sky,pop}, allday:{sky,pop}}}, order, 동이름)
    """
    if lat is None or lon is None:
        return {}, [], None
    code, dong_name = _dong_code(lat, lon)
    if not code:
        return {}, [], None
    url = ("https://www.weather.go.kr/w/wnuri-fct2021/main/digital-forecast.do"
           f"?code={code}&unit=m%2Fs&hr1=Y")
    try:
        html = requests.get(url, headers={"User-Agent": UA,
                                          "X-Requested-With": "XMLHttpRequest"},
                            timeout=15, verify=False).text
    except Exception as e:
        log(f"중기예보 요청 실패: {e}")
        return {}, [], None

    results, order = {}, []
    for m in _MID_SLIDE_PAT.finditer(html):
        d, seg = m.group(1), m.group(0)
        day = {}
        tmin = re.search(r"최저 : </strong><span>(-?\d+)℃", seg)
        tmax = re.search(r"최고 : </strong><span>(-?\d+)℃", seg)
        if tmin:
            day["tmin"] = tmin.group(1) + "℃"
        if tmax:
            day["tmax"] = tmax.group(1) + "℃"
        am_sky = re.search(r'title="오전 날씨 ([^"]+)"', seg)
        pm_sky = re.search(r'title="오후 날씨 ([^"]+)"', seg)
        all_sky = re.search(r'title="날씨 ([^"]+)"', seg)
        am_pop = re.search(r'>오전 강수확률</strong><span>(\d+)%', seg)
        pm_pop = re.search(r'>오후 강수확률</strong><span>(\d+)%', seg)
        all_pop = re.search(r'"hid">강수확률</strong><span>(\d+)%', seg)
        if am_sky or pm_sky:
            if am_sky:
                day["am"] = {"sky": am_sky.group(1),
                             "pop": (am_pop.group(1) + "%") if am_pop else None}
            if pm_sky:
                day["pm"] = {"sky": pm_sky.group(1),
                             "pop": (pm_pop.group(1) + "%") if pm_pop else None}
        elif all_sky:
            day["allday"] = {"sky": all_sky.group(1),
                             "pop": (all_pop.group(1) + "%") if all_pop else None}
        if day:
            results[d] = day
            order.append(d)
    return results, order, dong_name


def _format_mid_day(name, tstr, day, dong_name):
    lines = [f"🌤️ {name} 날씨 — {tstr} (중기예보)"]
    if day.get("tmin") or day.get("tmax"):
        lines.append(f"  최저 {day.get('tmin', '-')} / 최고 {day.get('tmax', '-')}")
    if day.get("allday"):
        a = day["allday"]
        pop = f"  강수확률 {a['pop']}" if a.get("pop") else ""
        lines.append(f"  종일  {a['sky']}{pop}")
    else:
        for label, k in (("오전", "am"), ("오후", "pm")):
            if day.get(k):
                s = day[k]
                pop = f"  강수확률 {s['pop']}" if s.get("pop") else ""
                lines.append(f"  {label}  {s['sky']}{pop}")
    where = f"{dong_name} 기준" if dong_name else "산 좌표 인근 기준"
    lines.append(f"\n(기상청 중기예보 · {where} — 정상 부근은 기온이 더 낮을 수 있어요)")
    return "\n".join(lines)


def format_weather(m, target):
    """target(date) 날씨를 조회해 텍스트로.

    범위초과·실패 시 중기예보(10일)를 먼저 시도하고, 그래도 없으면 주소 기반 안내 링크.
    """
    cfg = load_config()
    tstr = target.isoformat()
    name = m.get("name", "")
    fallback = "\n".join(weather_fallback_links(m))

    def _mid_fallback():
        """범위 밖 날짜용 중기예보 시도. 해당 날짜가 있으면 텍스트, 없으면 None."""
        mid, _, dong_name = _weather_mid(m.get("lat"), m.get("lon"))
        if mid and tstr in mid:
            return _format_mid_day(name, tstr, mid[tstr], dong_name)
        return None

    # 3-A: mtId 산악날씨
    if m.get("mtId"):
        results, order = _weather_by_mtid(m["mtId"], target)
        if results and tstr in results:
            day = results[tstr]
            head = f"🌤️ {name} 산악날씨 — {tstr}\n  최저 {day['tmin']} / 최고 {day['tmax']}"
            body = [head]
            for s in day["slots"]:
                body.append(f"  {s['time']:>3}  {s['sky']}  {s['temp']}  강수확률 {s['pop']}")
            body.append("\n(기상청 공식 산악날씨 · 최대 5일)")
            return "\n".join(body)
        if results and order:
            # mtId 는 되지만 요청 날짜가 범위 밖 → 중기예보(10일) 시도
            mid = _mid_fallback()
            if mid:
                return mid
            return (f"🌤️ {name}: 요청하신 {tstr} 는 산악날씨 예보 범위를 벗어났습니다.\n"
                    f"제공 가능한 날짜: {order[0]} ~ {order[-1]}\n"
                    f"그 이후는 아래에서 직접 확인해 주세요:\n{fallback}")

    # 3-B: 단기예보 fallback
    by_date, order = _weather_by_proxy(m.get("lat"), m.get("lon"), target,
                                       cfg["kskill_proxy_base_url"])
    if by_date and tstr in by_date:
        return f"🌤️ {name} 날씨 — {tstr} (단기예보)\n{_fmt_proxy_day(by_date[tstr])}"
    if by_date and order:
        mid = _mid_fallback()
        if mid:
            return mid
        return (f"🌤️ {name}: 요청하신 {tstr} 는 단기예보 범위를 벗어났습니다.\n"
                f"제공 가능한 날짜: {order[0]} ~ {order[-1]}\n"
                f"그 이후는 아래에서 직접 확인해 주세요:\n{fallback}")

    # 3-C: 단기 실패 → 중기라도 시도 후 주소 기반 링크 안내
    mid = _mid_fallback()
    if mid:
        return mid
    return (f"🌤️ {name} {tstr} 날씨를 지금 가져오지 못했습니다.\n"
            f"아래에서 직접 확인해 주세요:\n{fallback}")


# ──────────────────────────── 메시지 → 응답 ────────────────────────────

def _help_text():
    key = load_config().get("anthropic_api_key")
    lines = [
        "🏔️ 한국 등산 봇\n",
        "• 산 이름을 보내면 코스·높이·100대 명산 정보를 알려드려요.",
        "   예) 북한산   |   지리산 추천 코스",
        "• 날짜를 함께 보내면 그 날짜의 산악날씨·일출·일몰도 알려드려요.",
        "   예) 설악산 이번주 토요일   |   한라산 내일   |   북한산 7월 5일",
    ]
    if key:
        lines += [
            "• 자유롭게 물어봐도 돼요 (AI 응답). 이어지는 질문도 기억해요.",
            "   예) 초급 코스만 있는 산 추천   |   설악산이랑 지리산 중 어디가 더 높아?",
            "        이번 주말 설악산 등산 계획 짜줘 → \"거기 대중교통으로 가는 법은?\"",
            "• /reset — 대화 맥락 초기화 (새 주제로 시작할 때)",
        ]
    lines += ["• /help — 이 도움말\n",
              f"수록 산: {len(load_mountains())}곳 (산림청 100대 명산 포함)"]
    return "\n".join(lines)


HELP = _help_text()

# /start 에서 보여줄 인기 산 바로가기 버튼 (callback_data 가 그대로 질문 텍스트가 됨)
START_KEYBOARD = {"inline_keyboard": [
    [{"text": "⛰️ 북한산", "callback_data": "북한산 등산 코스 추천"},
     {"text": "⛰️ 설악산", "callback_data": "설악산 등산 코스 추천"}],
    [{"text": "⛰️ 지리산", "callback_data": "지리산 등산 코스 추천"},
     {"text": "⛰️ 한라산", "callback_data": "한라산 등산 코스 추천"}],
    [{"text": "❓ 도움말", "callback_data": "/help"}],
]}

_RESET_WORDS = ("/reset", "reset", "초기화", "새대화", "새 대화")


def compose_reply(text, cfg, chat_id=None):
    """API 키가 있으면 Claude tool-use(자유질문) 경로, 없거나 실패하면 규칙기반으로 폴백.

    chat_id 가 있으면 채팅별 대화 히스토리(멀티턴)를 읽고/기록한다.
    """
    t = (text or "").strip()
    if not t:
        return None
    if t.lower() in ("/start", "/help", "help", "도움말", "시작"):
        return HELP
    if t.lower() in _RESET_WORDS:
        clear_history(chat_id)
        return "🧹 대화 맥락을 초기화했어요. 새 질문을 보내주세요."
    try:
        import agent  # 지연 import — anthropic 미설치 환경에서도 규칙기반은 동작
        ans = agent.agentic_reply(t, cfg, history=get_history(chat_id))
        if ans:
            append_history(chat_id, t, ans)
            record_usage("ai")
            return ans
    except Exception as e:
        log(f"agent 경로 실패, 규칙기반 폴백: {e}")
    record_usage("rule")
    return build_reply(t)


def build_reply(text):
    text = (text or "").strip()
    if not text:
        return None
    if text.lower() in ("/start", "/help", "help", "도움말", "시작"):
        return HELP

    m = match_mountain(text)
    if not m:
        return ("해당 산을 데이터에서 찾지 못했어요. 🏔️\n"
                "산림청 100대 명산 이름으로 다시 보내주세요. 예) 북한산, 설악산, 지리산\n"
                "/help 로 사용법을 볼 수 있어요.")

    parts = [format_courses(m)]
    target = parse_date(text)
    if target is not None:
        parts.append("")
        parts.append(format_weather(m, target))
    else:
        parts.append("")
        parts.append("💡 날짜를 함께 보내면 산악날씨도 알려드려요. 예) \"" +
                     m.get("name", "북한산") + " 이번주 토요일\"")
    return "\n".join(parts)


# ──────────────────────────── 텔레그램 I/O ────────────────────────────

def tg_send(cfg, chat_id, text, reply_markup=None):
    token = cfg["telegram_bot_token"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [""]
    for i, chunk in enumerate(chunks):
        payload = {"chat_id": chat_id, "text": chunk,
                   "disable_web_page_preview": True}
        if reply_markup and i == len(chunks) - 1:  # 버튼은 마지막 메시지에만
            payload["reply_markup"] = reply_markup
        try:
            r = requests.post(url, json=payload, timeout=10).json()
            if not r.get("ok"):
                log(f"Telegram 오류: {r.get('description', r)}")
                return False
        except Exception as e:
            log(f"Telegram 전송 실패: {e}")
            return False
        time.sleep(0.3)
    return True


def tg_send_typing(cfg, chat_id):
    """'입력 중...' 표시 — AI 응답이 수십 초 걸릴 수 있어 사용자에게 진행 신호."""
    try:
        requests.post(f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/sendChatAction",
                      json={"chat_id": chat_id, "action": "typing"}, timeout=5)
    except Exception:
        pass


def tg_answer_callback(cfg, callback_id):
    try:
        requests.post(f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/answerCallbackQuery",
                      json={"callback_query_id": callback_id}, timeout=5)
    except Exception:
        pass


def process_updates(cfg, long_poll=False):
    token = cfg["telegram_bot_token"]
    if not token:
        log("TELEGRAM_BOT_TOKEN 이 없습니다. config.json 또는 환경변수를 설정하세요.")
        return 0
    allow = cfg["telegram_chat_id"]  # 비어있으면 모든 채팅 허용
    offset = load_state().get("telegram_update_offset")
    params = {"timeout": 50 if long_poll else 0}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                         params=params, timeout=(55 if long_poll else 20)).json()
    except Exception as e:
        log(f"getUpdates 오류: {e}")
        return 0
    if not r.get("ok"):
        log(f"getUpdates 실패: {r.get('description')}")
        return 0

    updates = r.get("result", [])
    last_id = None
    handled = 0
    for upd in updates:
        last_id = upd["update_id"]

        # 인라인 버튼 클릭 → callback_data 를 질문 텍스트처럼 처리
        cb = upd.get("callback_query")
        if cb:
            tg_answer_callback(cfg, cb.get("id"))
            text = cb.get("data", "")
            chat_id = (cb.get("message") or {}).get("chat", {}).get("id")
        else:
            msg = upd.get("message") or upd.get("channel_post") or {}
            text = msg.get("text", "")
            chat_id = msg.get("chat", {}).get("id")

        if not text or chat_id is None:
            continue
        if allow and str(chat_id) != allow:
            log(f"권한 없는 채팅 무시: {chat_id}")
            continue
        log(f"수신: {text!r} (chat {chat_id})")
        tg_send_typing(cfg, chat_id)
        try:
            reply = compose_reply(text, cfg, chat_id=chat_id)
        except Exception as e:
            reply = f"처리 중 오류가 발생했어요: {e}"
            log(f"build_reply 오류: {e}")
        if reply:
            markup = START_KEYBOARD if text.strip().lower() in ("/start", "시작") else None
            tg_send(cfg, chat_id, reply, reply_markup=markup)
            handled += 1

    if last_id is not None:
        state = load_state()
        state["telegram_update_offset"] = last_id + 1
        save_state(state)
    return handled


def listen_loop(cfg):
    log("리스너 시작 (long-poll). 산 이름을 보내면 응답합니다. Ctrl+C 종료.")
    while True:
        try:
            process_updates(cfg, long_poll=True)
        except KeyboardInterrupt:
            log("리스너 종료")
            break
        except Exception as e:
            log(f"리스너 오류(계속): {e}")
            time.sleep(5)


# ──────────────────────────── main ────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="한국 등산 텔레그램 봇")
    ap.add_argument("--listen", action="store_true", help="long-poll 상시 대기")
    ap.add_argument("--once", action="store_true", help="밀린 메시지 1회 처리 (cron)")
    ap.add_argument("--check", metavar="TEXT", help="텔레그램 없이 로컬 응답 미리보기")
    args = ap.parse_args()

    if args.check:
        print(compose_reply(args.check, load_config()))
        return

    cfg = load_config()
    if args.listen:
        listen_loop(cfg)
    elif args.once:
        n = process_updates(cfg, long_poll=False)
        log(f"처리 완료: {n}건")
    else:
        ap.print_help()


if __name__ == "__main__":
    # weather.go.kr 자체서명/구버전 TLS 대응: verify=False 사용 시 경고 억제
    try:
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass
    main()
