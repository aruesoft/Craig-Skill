#!/usr/bin/env python3
"""매일 아침 텔레그램으로 봇 명령어 리스트를 전송하는 루틴.

launchd `com.craig.skill.morning-commands` 가 매일 08:00 에 실행한다.
발신 봇: 학습봇(@CraigStudyBot) — 토큰·chat_id 는 ~/.config/craig-telegram-study/config.json.
명령어 목록을 바꾸려면 아래 MESSAGE 만 고치면 된다 (봇 명령이 바뀌면 함께 갱신할 것).
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import requests

CFG = Path.home() / ".config" / "craig-telegram-study" / "config.json"
RELAY_STATE = Path.home() / ".config" / "craig-telegram-study" / "relay_state.json"
LEGACY_STATE = Path.home() / ".config" / "craig-telegram-study" / "state.json"

MESSAGE = (
    "☀️ 좋은 아침! 오늘의 봇 명령어 안내\n"
    "\n"
    "⛰️ 등산봇\n"
    "• 산 이름 + 날짜 → 코스·산악날씨·일출일몰·하산식 맛집\n"
    "   예) 설악산 이번주 토요일\n"
    "• 자유 질문(AI)도 가능, 후속 질문 기억\n"
    "• /reset 대화 초기화 · /help 도움말\n"
    "\n"
    "📚 학습봇 (@CraigStudyBot)\n"
    "• 링크·텍스트·사진 전송 → 인박스 수집 (#ai #biz 힌트)\n"
    "• /curate 인박스 정리 · /review 오늘 복습\n"
    "• /status 현황 · /find 키워드 검색\n"
    "• /garden 노트 정리 · /weekly 주간 요약\n"
    "\n"
    "🎬 유튜브 요약봇\n"
    "• 채널명·@핸들·URL 전송 → 모니터링 채널 추가\n"
    "• /list 채널 목록 · /remove 번호 삭제\n"
    "• /run 지금 새 영상 확인 · /help 도움말"
)


def main():
    cfg = json.load(open(CFG))
    token = cfg.get("telegram_bot_token", "")
    # chat_id: config → relay_state(last_chat) → 구 프로토타입 state(last_chat) 순으로 폴백
    chat_id = cfg.get("telegram_chat_id", "")
    for p in (RELAY_STATE, LEGACY_STATE):
        if not chat_id and p.exists():
            chat_id = json.load(open(p)).get("last_chat", "")
    if not token or not chat_id:
        print("토큰/chat_id 없음 — config.json 의 telegram_chat_id 또는 relay_state.json 확인")
        return 1
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": MESSAGE, "disable_web_page_preview": True},
        timeout=20,
    ).json()
    if r.get("ok"):
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 아침 명령어 안내 전송 완료")
        return 0
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 전송 실패: {r.get('description')}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
