# Craig-Skill — 프로젝트 지침

Claude / AI 에이전트용 스킬 모음 저장소. 원격: https://github.com/aruesoft/Craig-Skill

## 구성

- `korean-mountain-hiking/` — 한국 등산 안내 스킬 (등산 코스 + 기상청 산악날씨 + 하산식 맛집)
  - `references/mountains.json` — **데이터 진실원본**. **총 231개 산**(산림청 100대 명산 `rank_100` 98곳 + 블랙야크 명산·인기 산·자동 추가). 코스 상세 19곳, 기상청 `mtId` 20곳. 산 개수를 문서에 쓸 때는 이 파일에서 세어 확인한다.
  - `SKILL.md` — 스킬 워크플로우.
  - `telegram-bot/` — 위 데이터를 텔레그램에서 조회하는 봇 (아래 참고).
- `youtube-telegram-summary/` — YouTube 새 영상 → secondb.ai 요약(실패 시 Claude 폴백) → 텔레그램 전송 파이프라인. 서버에선 `monitor.py --listen` 상시 리스너 1개가 명령 응답 + 6시간 주기 감지를 겸한다.
- `craig-telegram-study/` — 학습 파이프라인(@CraigStudyBot). **봇/지능 분리**: `pipeline/relay_bot.py`는 큐 릴레이만, 지능은 `pipeline/learn_{ingest,curate,garden,retro,weekly}.py`가 담당. 설계 SSOT는 `학습파이프라인_설계안.md`. ⚠️ `telegram-bot/study_bot.py`는 **폐기된 프로토타입** — 수정하지 말 것.
- `SkillVault/`, `StudyVault/` — Obsidian 볼트(둘 다 git 미추적, Obsidian Sync 소유). SkillVault=이 프로젝트 위키(PARA+카파시 LLM-Wiki), StudyVault=학습 파이프라인 결과.
- `deploy/`, `SERVER_SETUP.md` — 봇들을 맥북 에어 서버에서 launchd 상시가동 + pull 자동배포. plist: `com.craig.skill.{mountainbot,youtube,studybot,learn-ingest,learn-curate,learn-garden,learn-retro,learn-weekly,dashboard,watchdog}`. **운영·배포 절차의 SSOT는 `SERVER_SETUP.md`**.

## 진실원본(SSOT) 맵 — 어디를 고쳐야 하는가

| 바꾸려는 것 | 고칠 곳 | 함께 갱신할 곳 |
|---|---|---|
| 산 데이터(코스·좌표·mtId) | `korean-mountain-hiking/references/mountains.json` | 개수 언급하는 README/SKILL.md 수치 |
| 등산봇 응답 형식·톤 | `telegram-bot/agent.py` `_system_prompt()` | 이 문서의 "봇 응답 형식 지침" 요약 + `--listen` 재시작 |
| 학습 파이프라인 동작 | `craig-telegram-study/학습파이프라인_설계안.md` 먼저, 그다음 `pipeline/learn_*.py` | `SKILL.md`·`README.md` |
| 서버 운영·스케줄 | `SERVER_SETUP.md` + `deploy/launchd/*.plist` | `deploy/README.md` |
| bot.py/agent.py 함수 시그니처·mountains.json 스키마 | 해당 코드 | **mountain-web에 영향** — 아래 파생 프로젝트 참고 |

## 파생 프로젝트

- **mountain-web** (`~/Github/mountain-web`, 별도 저장소) — korean-mountain-hiking 스킬·봇 로직 기반 웹서비스(Next.js+FastAPI, secondb.ai 스타일). 이 저장소를 git submodule(`vendor/Craig-Skill`)로 참조하므로 **bot.py/agent.py/mountains.json 변경 시 웹에도 영향** — 인터페이스(함수 시그니처·JSON 스키마) 바꿀 때 주의.
- 이 저장소의 `mountain-web/` 폴더는 **기획 문서만**(CLAUDE.md·SPEC.md) 담는다. 웹 코드 작업은 `~/Github/mountain-web`에서 한다 — 여기서 웹 코드를 만들지 말 것.

## korean-mountain-hiking 텔레그램 봇 (`telegram-bot/`)

- `bot.py` — 진입점. `compose_reply()`가 라우팅: **ANTHROPIC_API_KEY 있으면 자유질문(AI) 모드, 없거나 실패 시 규칙기반(산 이름+날짜)으로 자동 폴백.**
  - 실행: `--listen`(상시 long-poll) / `--once`(cron) / `--check "텍스트"`(로컬 미리보기, 텔레그램 불필요).
  - **멀티턴**: 채팅별 대화 히스토리(최근 6턴·2시간 TTL)를 `~/.config/korean-mountain-hiking/history.json`에 유지, `/reset`으로 초기화. `sun_times()`(NOAA 근사식 일출·일몰), `/start` 인라인 버튼(callback_query 처리), 일자별 사용통계(state.json `usage`)도 bot.py 소관.
- `agent.py` — 자유질문 모드. Claude(**기본 `claude-sonnet-5`**, config `claude_model`로 변경) tool-use 루프. 최신 모델이면 adaptive thinking + prompt caching(`extra_body` 경유 — 구버전 SDK 호환) 적용. 도구는 전부 `bot.py` 함수/데이터 재사용:
  `lookup_mountain` / `list_mountains` / `resolve_date` / `get_mountain_weather` / `get_sun_times` + 서버 `web_search`(모델 세대별 `_20260209`/`_20250305` 자동 선택 — 맛집·통제정보·대중교통).

### 봇 응답 형식 지침 (사용자 요구 — `agent.py` `_system_prompt()`에 인코딩되어 매 요청 강제됨)

특정 산 등산 질문이면 기본으로 아래를 **상세히** 담는다:
1. **⛰️ 등산 코스** — 전체 코스(구간·거리·소요시간·난이도). `map_url` 있으면 🗺️ 지도 링크.
2. **추천 코스** — recommended 코스를 ⭐ + 추천 이유 한 줄.
3. **🌤️ 날씨·일출일몰** — 날짜 언급 시(‘이번 주말’ 포함) `resolve_date`로 정확한 날짜를 구해 산악날씨(기온·강수확률·하늘상태)를 구체적으로 + `get_sun_times`로 일출·일몰과 ‘늦어도 몇 시 출발’ 계산. 끝에 기상청 산악날씨 링크. 날짜 없으면 생략+안내. **날씨 조회 실패/범위초과 시 산 주소(region) 기반 폴백**: AI모드는 web_search로 ‘{지역} {산} 날씨’ 예보 요약(출처 명시), 규칙기반(`format_weather`)은 기상청+네이버 날씨 링크(`weather_fallback_links`) 안내 — 지어내지 않음.
4. **🍽️ 하산식 맛집** — 하산 지점/근처 지역 기준 `web_search`, 평점 3.5+ 3~5곳 + 대표메뉴. 끝에 카카오맵 검색 링크.
5. **🚌 가는 길** — 교통 질문이거나 서울 근교 산이면 `web_search`로 대중교통 경로 요약 + 네이버 길찾기 링크.
6. **⚠️ 통제·안전** — 국립공원 산이면 `web_search`로 최신 탐방로 통제 확인 후 안내.
7. **🔗 참고 링크** — 국립공원 산이면 국립공원공단 예약/통제(`https://reservation.knps.or.kr`) 반드시 포함.
- 멀티턴: 후속 질문(‘거기’, ‘그럼 다음주는?’)은 직전 대화 맥락으로 해석.

- 비교·목록·단순 사실질문 등 특정 산 등산 계획이 아니면 관련 항목만 답한다.
- 상대적 날짜는 직접 계산하지 말고 `resolve_date` 도구를 쓴다(요일 계산 실수 방지).
- 데이터에 없는 정보는 지어내지 않는다. 마크다운 기호(`**`, `##`) 금지, 이모지 섹션제목 + `• ` 목록.

**중요**: 이 지침은 코드 시스템프롬프트가 진실원본이다. 수정하려면 `agent.py` `_system_prompt()`를 고치고
**실행 중인 `--listen`을 재시작**해야 반영된다(파이썬은 로드 시점 코드를 메모리에 유지). 변경 후 `python bot.py --check "..."`로 먼저 검증한다.

## 운영 주의

- **봇 인스턴스는 하나만.** 텔레그램은 봇당 `getUpdates` long-poll을 동시에 하나만 허용 — 두 번 띄우면 `Conflict` 에러. 재시작 전 기존 프로세스 종료(`pgrep -f "bot.py --listen"`).
- **비밀값은 저장소 밖.** 텔레그램 토큰·`anthropic_api_key`는 `~/.config/{korean-mountain-hiking,youtube-telegram-summary,craig-telegram-study}/config.json`(chmod 600). `config.json`은 `.gitignore` 처리됨. 키를 코드/커밋/문서에 넣지 않는다.
- 의존성: 등산봇 규칙기반은 `requests`만, 자유질문 모드는 `anthropic` 추가(`pip install anthropic`).

## 흔한 실수 (하지 말 것)

- `study_bot.py`(프로토타입)를 고치는 것 — 학습봇 작업은 전부 `craig-telegram-study/pipeline/`에서.
- 상대 날짜를 직접 계산해 코드/프롬프트에 박는 것 — 봇은 `resolve_date` 도구를 쓴다.
- 산 개수·데이터 수치를 기억으로 쓰는 것 — 반드시 `mountains.json`에서 세어 쓴다.
- `agent.py` 프롬프트만 고치고 재시작을 안 하는 것 — 반영 안 됨(아래 검증 절차 참고).
- `--listen` 프로세스를 종료 확인 없이 새로 띄우는 것 — `Conflict`.
- SkillVault/StudyVault를 git에 추가하는 것 — Obsidian Sync 소유.

## 검증 명령 (수정 후 필수)

```bash
# 등산봇: 텔레그램 없이 로컬로 응답 확인 (프롬프트/로직 수정 후 항상)
python korean-mountain-hiking/telegram-bot/bot.py --check "이번 주말 북한산 등산"

# 데이터 수치 확인
python3 -c "import json; d=json.load(open('korean-mountain-hiking/references/mountains.json'))['mountains']; print(len(d), sum(1 for m in d if m.get('courses')), sum(1 for m in d if m.get('mtId')))"

# 실행 중 봇 확인 (재시작 전)
pgrep -fl "bot.py --listen|relay_bot.py --listen|monitor.py --listen"
```
