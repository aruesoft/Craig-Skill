#!/usr/bin/env python3
"""
LLM 백엔드 (Claude tool-use 루프) — 텔레그램 봇의 자유질문 응답.

기존 `bot.py`의 순수 파이썬 함수(match_mountain / format_courses / format_weather 등)를
Claude(Haiku 4.5)의 "도구"로 재사용한다. 사용자가 자유롭게 던진 질문을 Claude가 해석해
필요한 도구를 스스로 골라 호출하고, 데이터 기반으로 답을 작성한다.

- 데이터는 항상 `references/mountains.json` 진실원본에서만 나온다(지어내지 않음).
- 맛집·최신 정보 등 데이터셋 밖 질문은 Claude 네이티브 web_search 도구로 처리한다.

`agentic_reply(text)` 는 ANTHROPIC_API_KEY 가 없으면 None 을 반환해서,
호출측(bot.py)이 기존 규칙기반 응답으로 자동 폴백하도록 한다.

의존성: pip install anthropic
설정:   환경변수 ANTHROPIC_API_KEY  (또는 config.json 의 anthropic_api_key)
        (선택) config.json 의 claude_model 로 모델 변경
"""

import json
import os
from datetime import date, datetime

import bot  # 같은 폴더의 bot.py — 데이터/코스/날씨 헬퍼 재사용

DEFAULT_MODEL = "claude-haiku-4-5"
MAX_ITERS = 8          # tool-use 루프 안전장치
MAX_TOKENS = 2048

CUSTOM_TOOLS = [
    {
        "name": "lookup_mountain",
        "description": (
            "산 이름으로 등산 코스(구간·거리·소요시간·난이도·추천 여부)·정상 높이·위치·"
            "산림청 100대 명산 순번·기상청 산악날씨 지점(mtId) 보유 여부를 조회한다. "
            "데이터셋에 있는 산만 반환하며, 없으면 not_found 를 반환한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "산 이름 (예: 북한산, 설악산, 지리산)"}
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_mountains",
        "description": (
            "수록된 산림청 100대 명산 전체의 요약 목록을 반환한다. "
            "각 항목: 이름·지역·높이(m)·100대 명산 순번·코스상세 보유 여부·산악날씨 지원 여부. "
            "'초급 코스인 산', '1500m 이상인 산', '두 산 중 어디가 더 높아?' 처럼 "
            "조건 필터·비교·추천 질문에 사용한다."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "resolve_date",
        "description": (
            "'오늘/내일/모레/글피/이번주 토요일/다음주 일요일/주말/7월 5일/2026-07-05' 같은 "
            "한국어 날짜 표현을 정확한 YYYY-MM-DD 로 변환한다. 상대적 날짜는 직접 계산하지 말고 "
            "반드시 이 도구로 변환한 뒤 get_mountain_weather 에 넘겨라."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "날짜 표현 (예: 이번주 토요일)"}
            },
            "required": ["expression"],
        },
    },
    {
        "name": "get_mountain_weather",
        "description": (
            "산 이름과 날짜로 기상청 산악날씨(mtId 보유 산은 최대 5일) 또는 "
            "단기예보(3일) fallback 을 조회한다. 범위를 벗어난 날짜는 지어내지 않고 안내한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "산 이름"},
                "date": {"type": "string", "description": "날짜 YYYY-MM-DD"},
            },
            "required": ["name", "date"],
        },
    },
]

# Haiku 4.5 는 구형 티어 — 기본 web_search 변형을 사용한다(_20260209 는 최신 모델 전용).
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}

CUSTOM_TOOL_NAMES = {t["name"] for t in CUSTOM_TOOLS}


def _system_prompt():
    today = date.today()
    weekday = ["월", "화", "수", "목", "금", "토", "일"][today.weekday()]
    return (
        "너는 한국 등산 도우미 텔레그램 봇이다. 산림청 선정 100대 명산 데이터와 "
        "기상청 산악날씨를 도구로 조회해 사용자의 자유로운 질문에 답한다.\n\n"
        f"오늘은 {today.isoformat()} ({weekday}요일) 이다. '이번 주말', '내일' 같은 상대적 날짜는 "
        "직접 계산하지 말고 resolve_date 도구로 정확한 날짜를 얻어라(요일 계산 실수 방지).\n\n"
        "도구 사용 원칙:\n"
        "- 특정 산의 코스·높이·정보 → lookup_mountain\n"
        "- 조건 필터·비교·추천(난이도/높이/지역 등) → list_mountains 로 후보를 좁힌다. "
        "각 항목의 difficulties 로 난이도 필터가 가능하고, 상세가 필요하면 lookup_mountain 으로 확인\n"
        "- 상대적 날짜 → resolve_date 로 YYYY-MM-DD 변환\n"
        "- 날씨 → get_mountain_weather (날짜는 resolve_date 로 얻은 YYYY-MM-DD 사용)\n"
        "- 하산식 맛집·최신 통제정보 등 데이터셋 밖 정보 → web_search. "
        "맛집은 '카카오맵 {지역} 맛집' 형태로 검색하고, 평점 3.5 이상 위주로 정리한다.\n\n"
        "기본 응답 구성 (특정 산에 대한 등산 질문이면 아래를 스킬로 조회해 상세히 포함한다):\n"
        "1) 등산 코스 — lookup_mountain 으로 모든 코스의 구간·거리·소요시간·난이도를 정리한다. "
        "**lookup_mountain 의 courses 가 비어 있으면(데이터셋 미등록 산) 그냥 '코스 없음'으로 끝내지 말고, "
        "web_search 로 '{산이름} 등산코스 소요시간 난이도'를 검색해 실제 코스(구간·거리·소요시간·난이도)를 "
        "정리해 보여준다. 이때 '웹 검색으로 정리한 정보이며 공식 출처 재확인 권장'을 덧붙인다.**\n"
        "코스 정보에는 **반드시 '🗺️ 등산로 지도' 섹션을 넣고 실제 등산로가 표시되는 지도 링크를 첨부**한다:\n"
        "   a. map_url 이 있으면 '공식 등산 지도: {map_url}' 를 최우선으로 넣는다.\n"
        "   b. 국립공원인 산(설악산·지리산·북한산·한라산 등)이면 web_search 로 "
        "'{산이름} 국립공원 탐방로 지도' 또는 '{산이름} 등산지도'를 찾아 **국립공원공단(knps.or.kr) 등 "
        "공식/신뢰 출처의 등산 지도 링크**를 넣는다. 링크는 반드시 검색으로 확인된 실제 URL만 쓴다(지어내지 말 것).\n"
        "   c. 네이버 지도 등산로 링크도 넣는다: 'https://map.naver.com/p/search/{산이름}%20등산로' (실제 등산로 표시).\n"
        "   d. 카카오맵 링크도 유지: 'https://map.kakao.com/?q={산이름}+등산로'.\n"
        "   ({산이름}은 실제 이름, 공백은 %20 또는 +)\n"
        "2) 추천 코스 — recommended 인 코스를 ⭐로 표시하고 왜 추천인지 한 줄 덧붙인다.\n"
        "3) 날씨 — 사용자가 날짜를 언급했으면(또는 '이번 주말' 등) resolve_date + "
        "get_mountain_weather 로 그 날짜의 산악날씨를 구체적으로(기온·강수확률·하늘상태) 넣고, "
        "끝에 '기상청 산악날씨: https://www.weather.go.kr/w/forecast/life/mountain.do' 링크를 붙인다. "
        "날짜 언급이 없으면 날씨는 생략하고 '날짜를 알려주면 산악날씨도 알려드려요'라고 안내한다.\n"
        "4) 하산식 맛집 — 하산 지점(또는 산 근처 대표 지역) 기준으로 web_search 해 "
        "평점 높은 식당 3~5곳을 대표 메뉴와 함께 넣고, "
        "끝에 '카카오맵에서 더 보기: https://map.kakao.com/?q={지역}+맛집' 링크를 붙인다({지역}은 실제 지명).\n"
        "→ 특정 산 질문에는 이 4가지를 기본으로 빠짐없이 담아 상세하게 답한다. "
        "단, 비교·목록·단순 사실질문처럼 특정 산 등산 계획이 아니면 관련 항목만 답한다.\n\n"
        "규칙:\n"
        "- 도구가 준 데이터에만 근거하고, 없는 정보는 지어내지 마라. 모르면 솔직히 말한다.\n"
        "- 텔레그램 메시지이므로 마크다운 기호(**, ##)는 쓰지 말고 평문으로 쓴다. "
        "섹션은 이모지 제목(예: ⛰️ 등산 코스 / 🌤️ 날씨 / 🍽️ 하산식 맛집 / 🔗 참고 링크)으로 구분하고 "
        "항목은 '• '로 나열한다. 상세하되 읽기 좋게 정리한다.\n"
        "- 링크는 각 섹션 안에 자연스럽게 넣거나, 답변 끝에 '🔗 참고 링크' 섹션으로 모아 넣는다. "
        "국립공원 산이면 '국립공원공단(탐방로 통제·예약): https://reservation.knps.or.kr' 을 반드시 포함한다.\n"
        "- 최신 탐방로 통제·예약은 국립공원공단/산림청에서 재확인하도록 안내한다."
    )


def _mountain_summary(m):
    courses = m.get("courses") or []
    difficulties = sorted({c.get("difficulty") for c in courses if c.get("difficulty")})
    return {
        "name": m.get("name"),
        "region": m.get("region"),
        "height_m": m.get("height_m"),
        "rank_100": m.get("rank_100"),
        "has_courses": bool(courses),
        "difficulties": difficulties,      # 예: ["상급","초급"] (코스상세 없으면 [])
        "weather_supported": bool(m.get("mtId")),
    }


def _dispatch(name, tool_input):
    """커스텀 도구 실행 → 문자열(주로 JSON) 반환."""
    try:
        if name == "lookup_mountain":
            m = bot.match_mountain(tool_input.get("name", ""))
            if not m:
                return json.dumps({"error": "not_found",
                                   "message": "데이터셋에 없는 산입니다."}, ensure_ascii=False)
            return json.dumps(m, ensure_ascii=False)

        if name == "list_mountains":
            summaries = [_mountain_summary(m) for m in bot.load_mountains()]
            return json.dumps(summaries, ensure_ascii=False)

        if name == "resolve_date":
            target = bot.parse_date(str(tool_input.get("expression", "")))
            if target is None:
                return json.dumps({"error": "bad_date",
                                   "message": "날짜 표현을 이해하지 못했습니다."}, ensure_ascii=False)
            return json.dumps({"date": target.isoformat()}, ensure_ascii=False)

        if name == "get_mountain_weather":
            m = bot.match_mountain(tool_input.get("name", ""))
            if not m:
                return json.dumps({"error": "not_found"}, ensure_ascii=False)
            raw = str(tool_input.get("date", ""))
            try:
                target = date.fromisoformat(raw)
            except ValueError:
                target = bot.parse_date(raw)
            if target is None:
                return json.dumps({"error": "bad_date",
                                   "message": "날짜를 이해하지 못했습니다."}, ensure_ascii=False)
            return bot.format_weather(m, target)

        return json.dumps({"error": "unknown_tool", "tool": name}, ensure_ascii=False)
    except Exception as e:  # 도구 실패는 모델이 복구할 수 있게 넘긴다
        return json.dumps({"error": "tool_failed", "message": str(e)}, ensure_ascii=False)


def _load_api_key(cfg):
    if cfg and cfg.get("anthropic_api_key"):
        return cfg["anthropic_api_key"]
    return os.environ.get("ANTHROPIC_API_KEY", "")


def agentic_reply(text, cfg=None):
    """자유질문 → Claude tool-use 루프로 응답. 키/SDK 없으면 None(폴백)."""
    text = (text or "").strip()
    if not text:
        return None

    key = _load_api_key(cfg)
    if not key:
        return None
    try:
        import anthropic
    except ImportError:
        bot.log("anthropic 미설치 — 규칙기반으로 폴백 (pip install anthropic)")
        return None

    model = (cfg or {}).get("claude_model") or DEFAULT_MODEL
    client = anthropic.Anthropic(api_key=key)
    tools = CUSTOM_TOOLS + [WEB_SEARCH_TOOL]
    messages = [{"role": "user", "content": text}]

    try:
        for _ in range(MAX_ITERS):
            resp = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=_system_prompt(),
                tools=tools,
                messages=messages,
            )

            if resp.stop_reason == "refusal":
                return "죄송해요, 이 요청은 도와드리기 어려워요."

            if resp.stop_reason == "pause_turn":
                # 서버 도구(web_search)가 반복 한도에 걸림 — 그대로 이어서 재요청
                messages.append({"role": "assistant", "content": resp.content})
                continue

            if resp.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": resp.content})
                results = []
                for block in resp.content:
                    if block.type == "tool_use" and block.name in CUSTOM_TOOL_NAMES:
                        out = _dispatch(block.name, block.input or {})
                        results.append({"type": "tool_result",
                                        "tool_use_id": block.id, "content": out})
                if not results:
                    # 커스텀 도구 호출이 없다면(서버도구뿐) 더 진행 불가 — 종료
                    break
                messages.append({"role": "user", "content": results})
                continue

            # end_turn 등 — 최종 텍스트 추출
            parts = [b.text for b in resp.content if b.type == "text"]
            answer = "\n".join(p for p in parts if p).strip()
            return answer or None

        # 루프 소진
        parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        return ("\n".join(p for p in parts if p).strip()
                or "요청이 복잡해서 다 처리하지 못했어요. 좀 더 구체적으로 물어봐 주세요.")
    except Exception as e:
        bot.log(f"agentic_reply 오류(규칙기반 폴백): {e}")
        return None


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) or "이번 주말 설악산 등산하고 하산식 맛집 추천해줘"
    cfg = bot.load_config()
    print(agentic_reply(q, cfg) or "(폴백: ANTHROPIC_API_KEY 미설정 또는 처리 실패)")
