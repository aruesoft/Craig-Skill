# 한국 등산 텔레그램 봇

`korean-mountain-hiking` 스킬 데이터(`references/mountains.json`, 산림청 100대 명산 100곳)를
텔레그램에서 바로 조회하는 봇이다.

- **산 이름** → 코스(구간·거리·소요시간·난이도), 높이, 100대 명산 순번, 등산 지도
- **산 이름 + 날짜** → 기상청 산악날씨(`mtId` 보유 20곳, 최대 5일) 또는 단기예보(3일) fallback

기본은 LLM 없이 순수 파이썬으로 동작한다(의존성: `requests`).

## 자유질문 모드 (선택 · Claude tool-use)

`ANTHROPIC_API_KEY` 를 설정하면 **자유로운 질문**에도 답한다. 봇이 Claude(기본 **Sonnet 5**,
adaptive thinking + prompt caching)를 중간에 두고, 아래 도구를 스스로 골라 호출한 뒤
데이터 기반으로 답을 작성한다.

- `lookup_mountain` — 특정 산 코스·높이·순번·mtId 조회
- `list_mountains` — 100대 명산 전체 요약(조건 필터·비교·추천용)
- `resolve_date` — 상대적 날짜 표현 → YYYY-MM-DD
- `get_mountain_weather` — 산악날씨/단기예보 조회
- `get_sun_times` — 산 위치 기준 일출·일몰 시각(NOAA 근사식, 로컬 계산)
- `web_search` — 하산식 맛집·탐방로 통제정보·대중교통 등 데이터셋 밖 정보
  (최신 모델은 `web_search_20260209`, 구형 모델은 `_20250305` 자동 선택)

```
초급 코스만 있는 산 추천해줘
설악산이랑 지리산 중 어디가 더 높아?
이번 주말 설악산 등산하고 하산식 맛집 알려줘
(이어서) 거기 대중교통으로 가는 법은?
```

- **멀티턴**: 채팅별 대화 히스토리(최근 6턴, 2시간 TTL)를 유지해 후속 질문이 이어진다.
  `/reset` 으로 초기화. 저장 위치: `~/.config/korean-mountain-hiking/history.json`.
- 데이터는 `references/mountains.json` 진실원본에서만 나온다(지어내지 않음).
- **키가 없거나 실패하면** 위의 규칙기반(산 이름 + 날짜) 응답으로 자동 폴백한다.
- 설치: `pip install anthropic` (선택). 모델 변경은 config 의 `claude_model`
  (예: 품질 최우선이면 `"claude-opus-4-8"`, 비용 최소화는 `"claude-haiku-4-5"`).
- 로컬 미리보기: `python bot.py --check "초급 코스만 있는 산 추천"`

## 설치

```bash
pip install requests
```

## 설정

봇 토큰은 [@BotFather](https://t.me/BotFather)에서 발급한다. 토큰은 **저장소에 커밋하지 말 것**
(`.gitignore`로 `config.json`을 제외해 둠).

방법 1 — 환경변수:

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC..."
# (선택) 특정 채팅만 허용하려면
export TELEGRAM_CHAT_ID="숫자ID"
```

방법 2 — config 파일 (`~/.config/korean-mountain-hiking/config.json`, 저장소 밖 권장):

```json
{
  "telegram_bot_token": "123456:ABC...",
  "telegram_chat_id": "",
  "kskill_proxy_base_url": "https://k-skill-proxy.nomadamas.org"
}
```

- `telegram_chat_id`를 **비워두면 아무 채팅에서나** 응답한다(공개 조회 봇).
  특정 사람/그룹만 쓰게 하려면 해당 chat id를 넣는다.

## 실행

```bash
python bot.py --listen                 # 상시 대기(long-poll) — 메시지에 즉시 응답
python bot.py --once                   # 밀린 메시지 1회 처리 (cron 용)
python bot.py --check "북한산 이번주 토요일"  # 텔레그램 없이 로컬 응답 미리보기
```

## 사용 예 (텔레그램에서 봇에게)

| 보내는 메시지 | 응답 |
|---|---|
| `북한산` | 코스·높이·100대 명산 정보 |
| `설악산 이번주 토요일` | 코스 + 토요일 산악날씨·일출일몰 |
| `한라산 내일` | 코스 + 내일 날씨 |
| `지리산 7월 5일` | 코스 + 해당 날짜 날씨 |
| `/start` | 인사 + 인기 산 바로가기 버튼 |
| `/reset` | 대화 맥락 초기화 (자유질문 모드) |
| `/help` | 도움말 |

날짜 표현: `오늘`, `내일`, `모레`, `글피`, `이번주/다음주 요일`, `주말`, `M월 D일`, `YYYY-MM-DD`.

## 상시 실행 (macOS)

세션이 끊겨도 봇이 살아있게 하려면 백그라운드로 상시 실행한다.

- 간단히: `nohup python3 bot.py --listen > bot.log 2>&1 &`
- cron으로 1분마다 밀린 메시지 처리(응답 지연 최대 1분):
  ```
  * * * * * cd /path/to/telegram-bot && /usr/bin/python3 bot.py --once >> bot.log 2>&1
  ```
- launchd(권장): `KeepAlive`로 `bot.py --listen`을 데몬 등록.

## 참고

- 코스/높이/순번 데이터는 스킬의 `../references/mountains.json`을 그대로 읽는다.
  데이터에 산을 추가하면 봇도 즉시 인식한다.
- 날씨는 기상청 공식 산악날씨 API(HTML 파싱)와 `k-skill-proxy` 단기예보를 사용한다.
  범위를 벗어난 날짜는 지어내지 않고 기상청 페이지 링크로 안내한다.
- 하산식 맛집 추천·자유질문은 `ANTHROPIC_API_KEY` 설정 시 "자유질문 모드"로 지원한다(위 참고).
  키 없이 순수 파이썬만 쓰려면 그대로 두면 규칙기반으로 동작한다.
