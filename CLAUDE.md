# Craig-Skill — 프로젝트 지침

Claude / AI 에이전트용 스킬 모음 저장소. 원격: https://github.com/aruesoft/Craig-Skill

## 구성

- `korean-mountain-hiking/` — 한국 등산 안내 스킬 (산림청 100대 명산 코스 + 기상청 산악날씨 + 하산식 맛집)
  - `references/mountains.json` — **데이터 진실원본**. 100개 산(코스·높이·위치·rank·mtId·map_url).
  - `SKILL.md` — 스킬 워크플로우.
  - `telegram-bot/` — 위 데이터를 텔레그램에서 조회하는 봇 (아래 참고).
- `youtube-telegram-summary/` — YouTube 새 영상 → secondb.ai 요약 → 텔레그램 전송 파이프라인.
- `craig-telegram-study/` — 학습봇(@CraigStudyBot). 텔레그램 링크/텍스트 → Claude 정리 → Obsidian StudyVault 노트(인과 [[링크]]·계층 태그). `telegram-bot/study_bot.py`. 본문추출: trafilatura(웹)·yt-dlp(유튜브).
- `SkillVault/`, `StudyVault/` — Obsidian 볼트(둘 다 git 미추적, Obsidian Sync 소유). SkillVault=이 프로젝트 위키(PARA+카파시 LLM-Wiki), StudyVault=학습봇 결과.
- `deploy/`, `SERVER_SETUP.md` — 봇들을 맥북 에어 서버에서 launchd 상시가동 + pull 자동배포. plist: `com.craig.skill.{mountainbot,youtube,studybot}`.

## korean-mountain-hiking 텔레그램 봇 (`telegram-bot/`)

- `bot.py` — 진입점. `compose_reply()`가 라우팅: **ANTHROPIC_API_KEY 있으면 자유질문(AI) 모드, 없거나 실패 시 규칙기반(산 이름+날짜)으로 자동 폴백.**
  - 실행: `--listen`(상시 long-poll) / `--once`(cron) / `--check "텍스트"`(로컬 미리보기, 텔레그램 불필요).
- `agent.py` — 자유질문 모드. Claude(`claude-haiku-4-5`) tool-use 루프. 도구는 전부 `bot.py` 함수/데이터 재사용:
  `lookup_mountain` / `list_mountains` / `resolve_date` / `get_mountain_weather` + 서버 `web_search_20250305`(맛집·최신정보).

### 봇 응답 형식 지침 (사용자 요구 — `agent.py` `_system_prompt()`에 인코딩되어 매 요청 강제됨)

특정 산 등산 질문이면 기본으로 아래를 **상세히** 담는다:
1. **⛰️ 등산 코스** — 전체 코스(구간·거리·소요시간·난이도). `map_url` 있으면 🗺️ 지도 링크.
2. **추천 코스** — recommended 코스를 ⭐ + 추천 이유 한 줄.
3. **🌤️ 날씨** — 날짜 언급 시(‘이번 주말’ 포함) `resolve_date`로 정확한 날짜를 구해 산악날씨(기온·강수확률·하늘상태)를 구체적으로. 끝에 기상청 산악날씨 링크. 날짜 없으면 생략+안내.
4. **🍽️ 하산식 맛집** — 하산 지점/근처 지역 기준 `web_search`, 평점 3.5+ 3~5곳 + 대표메뉴. 끝에 카카오맵 검색 링크.
5. **🔗 참고 링크** — 국립공원 산이면 국립공원공단 예약/통제(`https://reservation.knps.or.kr`) 반드시 포함.

- 비교·목록·단순 사실질문 등 특정 산 등산 계획이 아니면 관련 항목만 답한다.
- 상대적 날짜는 직접 계산하지 말고 `resolve_date` 도구를 쓴다(요일 계산 실수 방지).
- 데이터에 없는 정보는 지어내지 않는다. 마크다운 기호(`**`, `##`) 금지, 이모지 섹션제목 + `• ` 목록.

**중요**: 이 지침은 코드 시스템프롬프트가 진실원본이다. 수정하려면 `agent.py` `_system_prompt()`를 고치고
**실행 중인 `--listen`을 재시작**해야 반영된다(파이썬은 로드 시점 코드를 메모리에 유지). 변경 후 `python bot.py --check "..."`로 먼저 검증한다.

## 운영 주의

- **봇 인스턴스는 하나만.** 텔레그램은 봇당 `getUpdates` long-poll을 동시에 하나만 허용 — 두 번 띄우면 `Conflict` 에러. 재시작 전 기존 프로세스 종료(`pgrep -f "bot.py --listen"`).
- **비밀값은 저장소 밖.** 텔레그램 토큰·`anthropic_api_key`는 `~/.config/korean-mountain-hiking/config.json`(chmod 600). `config.json`은 `.gitignore` 처리됨. 키를 코드/커밋에 넣지 않는다.
- 의존성: 규칙기반은 `requests`만, 자유질문 모드는 `anthropic` 추가(`pip install anthropic`).
