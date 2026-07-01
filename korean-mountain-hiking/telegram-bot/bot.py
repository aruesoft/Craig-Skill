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

    if m.get("map_url"):
        lines.append("")
        lines.append(f"🗺️ 등산 지도: {m['map_url']}")
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


def format_weather(m, target):
    """target(date) 날씨를 조회해 텍스트로. 조회 실패/범위초과 시 안내 링크."""
    cfg = load_config()
    tstr = target.isoformat()
    name = m.get("name", "")
    kma_link = "https://www.weather.go.kr/w/forecast/life/mountain.do"

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
            # mtId 는 되지만 요청 날짜가 범위 밖
            return (f"🌤️ {name}: 요청하신 {tstr} 는 산악날씨 예보 범위를 벗어났습니다.\n"
                    f"제공 가능한 날짜: {order[0]} ~ {order[-1]}\n"
                    f"그 이후는 {kma_link} 에서 확인해 주세요.")

    # 3-B: 단기예보 fallback
    by_date, order = _weather_by_proxy(m.get("lat"), m.get("lon"), target,
                                       cfg["kskill_proxy_base_url"])
    if by_date and tstr in by_date:
        return f"🌤️ {name} 날씨 — {tstr} (단기예보)\n{_fmt_proxy_day(by_date[tstr])}"
    if by_date and order:
        return (f"🌤️ {name}: 요청하신 {tstr} 는 단기예보 범위를 벗어났습니다.\n"
                f"제공 가능한 날짜: {order[0]} ~ {order[-1]}\n"
                f"그 이후는 {kma_link} 에서 확인해 주세요.")

    # 3-C: 실패
    return (f"🌤️ {name} {tstr} 날씨를 지금 가져오지 못했습니다.\n"
            f"{kma_link} 에서 직접 확인해 주세요.")


# ──────────────────────────── 메시지 → 응답 ────────────────────────────

def _help_text():
    key = load_config().get("anthropic_api_key")
    lines = [
        "🏔️ 한국 등산 봇\n",
        "• 산 이름을 보내면 코스·높이·100대 명산 정보를 알려드려요.",
        "   예) 북한산   |   지리산 추천 코스",
        "• 날짜를 함께 보내면 그 날짜의 산악날씨도 알려드려요.",
        "   예) 설악산 이번주 토요일   |   한라산 내일   |   북한산 7월 5일",
    ]
    if key:
        lines += [
            "• 자유롭게 물어봐도 돼요 (AI 응답).",
            "   예) 초급 코스만 있는 산 추천   |   설악산이랑 지리산 중 어디가 더 높아?",
            "        이번 주말 설악산 등산하고 하산식 맛집 알려줘",
        ]
    lines += ["• /help — 이 도움말\n",
              f"수록 산: {len(load_mountains())}곳 (산림청 100대 명산)"]
    return "\n".join(lines)


HELP = _help_text()


def compose_reply(text, cfg):
    """API 키가 있으면 Claude tool-use(자유질문) 경로, 없거나 실패하면 규칙기반으로 폴백."""
    t = (text or "").strip()
    if not t:
        return None
    if t.lower() in ("/start", "/help", "help", "도움말", "시작"):
        return HELP
    try:
        import agent  # 지연 import — anthropic 미설치 환경에서도 규칙기반은 동작
        ans = agent.agentic_reply(t, cfg)
        if ans:
            return ans
    except Exception as e:
        log(f"agent 경로 실패, 규칙기반 폴백: {e}")
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

def tg_send(cfg, chat_id, text):
    token = cfg["telegram_bot_token"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in [text[i:i + 4000] for i in range(0, len(text), 4000)] or [""]:
        try:
            r = requests.post(url, json={
                "chat_id": chat_id, "text": chunk,
                "disable_web_page_preview": True}, timeout=10).json()
            if not r.get("ok"):
                log(f"Telegram 오류: {r.get('description', r)}")
                return False
        except Exception as e:
            log(f"Telegram 전송 실패: {e}")
            return False
        time.sleep(0.3)
    return True


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
        msg = upd.get("message") or upd.get("channel_post") or {}
        text = msg.get("text", "")
        chat_id = msg.get("chat", {}).get("id")
        if not text or chat_id is None:
            continue
        if allow and str(chat_id) != allow:
            log(f"권한 없는 채팅 무시: {chat_id}")
            continue
        log(f"수신: {text!r} (chat {chat_id})")
        try:
            reply = compose_reply(text, cfg)
        except Exception as e:
            reply = f"처리 중 오류가 발생했어요: {e}"
            log(f"build_reply 오류: {e}")
        if reply:
            tg_send(cfg, chat_id, reply)
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
