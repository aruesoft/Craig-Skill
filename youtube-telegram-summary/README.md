# YouTube → secondb.ai → Telegram 요약봇

지정한 YouTube 채널에 새 동영상이 올라오면, [secondb.ai](https://secondb.ai/)로 AI 요약을 만들어
Telegram 메시지로 보내주는 자동화 도구.

## 동작 방식

```
YouTube RSS(새 영상 감지) → secondb.ai API(요약) → Telegram Bot(전송)
```

- **YouTube**: 채널 RSS 피드로 신규 영상 감지 (API 키 불필요)
- **secondb.ai**: 구글 로그인 세션을 재사용해 `api.secondb.ai` REST API 직접 호출
  - `GET  /api/v1/search_summary?url=` : 기존 요약 조회
  - `POST /api/v1/summarize` : 요약 생성 (URL 기준 멱등 — 중복 생성 안 됨)
  - `GET  /api/v1/summaries/{id}` : 생성 완료까지 폴링
- **Telegram**: Bot API로 HTML 메시지 전송 (특수문자 안전 처리)
- **양방향**: 봇에게 **채팅으로 채널명을 보내면** 그 채널을 자동 등록 (아래 "텔레그램으로 채널 관리" 참고)

## 파일 구성

| 파일 | 역할 |
|------|------|
| `setup.py` | 최초 설정 마법사 (텔레그램·채널·로그인·cron) |
| `login.py` | secondb.ai 구글 로그인 (브라우저에서 직접 1회) |
| `monitor.py` | 메인 실행 (감지·요약·전송) + 채널 관리 |
| `claude_summary.py` | secondb.ai 실패 시 Claude 폴백 요약 (자막→Claude API) |
| `install_schedule.py` | 매시간 자동 실행 등록 (macOS/Linux=cron, Windows=작업 스케줄러) |
| `diagnose.py` | secondb.ai 동작 점검용 진단 도구 (문제 시) |

## 최초 설정

```bash
# 1) 패키지
pip install playwright requests && playwright install chromium

# 2) 설정 마법사 (텔레그램 토큰/채널 입력)
python ~/youtube-telegram-summary/setup.py

# 3) secondb.ai 구글 로그인 (브라우저에서 직접)
python ~/youtube-telegram-summary/login.py
```

## 일상 사용

```bash
# 수동 실행
python ~/youtube-telegram-summary/monitor.py

# 디버그(브라우저 표시 + 상세 로그)
python ~/youtube-telegram-summary/monitor.py --debug

# 채널 관리
python ~/youtube-telegram-summary/monitor.py --add-channel @채널핸들
python ~/youtube-telegram-summary/monitor.py --add-channel https://www.youtube.com/@채널
python ~/youtube-telegram-summary/monitor.py --add-channel UCxxxxxxxxxxxxxxxxxxxxxx
python ~/youtube-telegram-summary/monitor.py --list-channels
python ~/youtube-telegram-summary/monitor.py --remove-channel 2   # 번호 또는 UC아이디

# 텔레그램 명령 상시 대기(즉시 응답) — 채널명 보내면 수초 내 등록
python ~/youtube-telegram-summary/monitor.py --listen
```

## 텔레그램으로 채널 관리 (양방향)

봇에게 **채팅으로 메시지를 보내면** 채널을 추가/삭제/조회합니다. `config.telegram_chat_id` 와
일치하는 채팅만 허용해 타인 조작을 막습니다.

| 보내는 메시지 | 동작 |
|--------------|------|
| `삼프로TV` / `@3protv` / 채널 URL / `UC...` | 모니터링 채널로 **추가** (한글 채널명은 유튜브 검색으로 자동 해석) |
| `/list` | 등록된 채널 목록 |
| `/remove 2` 또는 `/remove @핸들` | 채널 삭제 |
| `/run` | 지금 새 영상 확인 실행 |
| `/help` | 명령 도움말 |

**언제 반영되나 — 2가지 경로가 공존**

1. **크론 실행마다 자동 반영** (추가 설정 불필요): 매 실행 시작 때 밀린 명령을 처리 → 최대 실행 주기만큼 지연.
2. **즉시 응답(상시 리스너)**: `--listen` 을 항상 켜두면 수초 내 등록·응답.

### 상시 리스너 자동 실행 (즉시 응답 + 죽으면 자동 재시작)

macOS는 launchd 로 `--listen` 을 상시 실행하도록 등록합니다. 꺼지면 자동 재시작(`KeepAlive`),
로그인/재부팅 시 자동 시작(`RunAtLoad`) — **별도 해제 요청 전까지 계속 살아납니다.**

```bash
python install_schedule.py --install-listener   # 리스너 상시 실행 등록
python install_schedule.py --remove-listener     # 리스너 해제
# 상태: launchctl list | grep youtube-telegram
# 로그: ~/youtube-telegram-summary/listener.log
```

- 시작 시 봇이 텔레그램으로 "🤖 요약봇 리스너 시작됨" 을 보냅니다(재부팅/재시작 때마다 = "살아있다" 신호).
- Windows: `--listen` 을 백그라운드로 직접 실행하거나 작업 스케줄러 '로그온 시' 트리거로 등록 (`--install-listener` 실행 시 안내 출력).

## 자동 실행 (매시간) — macOS · Linux · Windows 공통

OS를 자동 감지해 등록해 주는 스크립트를 쓰는 게 가장 간단합니다.

```bash
python install_schedule.py              # 매시간 등록
python install_schedule.py --interval 6 # 6시간마다
python install_schedule.py --status     # 등록 상태 확인
python install_schedule.py --remove     # 등록 해제
```

- **macOS / Linux**: `crontab` 에 등록
  ```
  0 * * * * /path/python /path/monitor.py >> /path/monitor.log 2>&1
  ```
  > macOS에서 등록이 멈추면: 시스템 설정 → 개인정보 보호 및 보안 → 전체 디스크 접근 권한 에
  > 터미널을 추가한 뒤 재시도.

- **Windows**: 작업 스케줄러(`schtasks`)에 등록. 콘솔창이 뜨지 않도록 `pythonw.exe` 로 실행하고,
  출력은 `monitor.log` 에 기록됩니다.
  ```
  schtasks /Create /SC HOURLY /MO 1 /TN YouTubeTelegramSummary ^
    /TR "\"C:\...\pythonw.exe\" \"C:\...\monitor.py\" --logfile \"C:\...\monitor.log\"" /F
  ```
  - 상태 확인: `schtasks /Query /TN YouTubeTelegramSummary`
  - 즉시 실행: `schtasks /Run /TN YouTubeTelegramSummary`
  - 기본적으로 **로그인했을 때** 실행됩니다(PC가 켜져 있고 로그인된 상태).

## 설정 파일

`~/.config/youtube-telegram-summary/config.json`
```json
{
  "youtube_channels": ["UCxxxx", "UCyyyy"],
  "telegram_bot_token": "...",
  "telegram_chat_id": "...",
  "summary_language": "kr",
  "max_videos_per_channel_per_run": 2,
  "schedule_interval_hours": 1,
  "healthcheck_ping_url": ""
}
```

- `summary_language`: 요약 언어 (기본 `kr`)
- `max_videos_per_channel_per_run`: **채널당 한 번에 보낼 최신 영상 수 (기본 2)**. 신규 채널을 추가했거나
  한동안 안 돈 채널에 영상이 여러 개 쌓여 있어도 최신 N개만 전송하고, 나머지 과거분은 전송 없이 '본 영상' 처리.
- `schedule_interval_hours`: 스케줄 주기(시간). 스케줄 미실행(공백) 감지에 사용 (등록 시 자동 기록).
- `healthcheck_ping_url`: (선택) 외부 감시 핑 URL. → 아래 "알림" 참고.
- `anthropic_api_key` / `claude_model`: Claude 폴백용. → 아래 "Claude 폴백" 참고.
- `obsidian_daily_dir`: (선택) 설정 시 전송한 요약을 **날짜별 .md 로그**로도 저장. → 아래 "옵시디언 데일리 로그" 참고.

## 옵시디언 데일리 로그 (선택)

`obsidian_daily_dir` 에 폴더 경로를 넣으면, 텔레그램으로 전송한 요약을 **그 날짜의 `YYYY-MM-DD.md` 파일**에도
누적 저장합니다 (옵시디언/일반 마크다운 호환).

```json
"obsidian_daily_dir": "/Users/me/.../StockVault/1_Projects/유튜브-요약봇/요약로그"
```

- 파일이 없으면 frontmatter(`type/date/tags`) + 헤더로 새로 만들고, 새 영상 요약을 시간순으로 추가
- 영상별: `## [제목](url)` + `📺 채널 · 🔖 출처 · 🕘 시각` + 요약 본문
- 같은 영상은 중복 저장 안 함 / 비워두면 비활성

상태 파일 `state.json`(이미 처리한 영상 목록)을 초기화하려면:
```bash
echo '{"seen_videos":[],"last_checked":null}' > ~/.config/youtube-telegram-summary/state.json
```

## 알림 (텔레그램)

요약 메시지와 별개로, 다음 상황에서 ⚠️ 알림을 텔레그램으로 보냅니다.

1. **실행 중 오류** — 예외 발생 시 오류 내용을 전송 (스크립트는 그대로 로그도 남김)
2. **secondb.ai 로그인 만료** — `python login.py` 재실행 안내
3. **스케줄 공백 감지** — 예상 주기의 2.5배 이상 실행이 비면, 재개 시점에 "그동안 안 돌았음" 알림

### 스케줄이 '완전히' 멈춘 경우까지 감지하려면 (선택)

스케줄이 아예 안 돌면 모니터 자신은 그 사실을 알릴 수 없습니다(자기가 안 도니까). 이를 잡으려면
외부 감시 서비스의 "데드맨 스위치"를 씁니다 — [healthchecks.io](https://healthchecks.io) 무료 사용 추천:

1. healthchecks.io에서 체크 생성(주기 1시간, grace 15분 등) → Ping URL 복사
2. `config.json` 의 `healthcheck_ping_url` 에 그 URL 입력
3. 이후 매 실행마다 핑을 보내고, 핑이 늦으면 healthchecks.io가 **이메일/텔레그램 등으로** 알려줍니다
   (성공=기본 URL, 시작=`/start`, 실패=`/fail` 자동 전송)

## Claude 폴백 (secondb.ai 실패 시)

secondb.ai가 quota 초과(429)·다운·로그인 만료 등으로 요약하지 못하면, **유튜브 자막을 받아
Claude API로 직접 요약**해 전송합니다. 텔레그램 메시지의 제목이 "Claude 요약"으로 표시됩니다.

- **동작**: `youtube-transcript-api`로 자막 확보 → `claude-haiku-4-5`로 요약 (입력 $1 / 출력 $5 per 1M, 영상 1건당 수원~수십원)
- **전제**: 자막이 있는 영상만 가능 (한국 채널은 대부분 자동 한국어 자막 있음)
- **켜는 법**: Anthropic API 키를 환경변수 또는 config에 설정
  ```bash
  # 방법 1) 환경변수 (권장)
  export ANTHROPIC_API_KEY=sk-ant-...
  # 방법 2) config.json 의 anthropic_api_key 에 입력
  ```
  키가 없으면 폴백은 자동 비활성 — secondb.ai만 사용합니다.
- **설치**: `pip install anthropic youtube-transcript-api` (requirements.txt에 포함)
- **모델 변경**: config의 `claude_model` (예: `claude-sonnet-4-6`로 품질↑·비용↑)

> cron(작업 스케줄러)으로 무인 실행될 때도 스크립트가 Claude API를 직접 호출하므로,
> 환경변수 키가 cron 환경에서도 보이도록 하려면 config.json의 `anthropic_api_key`에 넣는 게 확실합니다.

## 문제 해결

- **로그인 만료/요약이 안 됨**: `python ~/youtube-telegram-summary/login.py` 재실행 (또는 Claude 폴백 활성화)
- **secondb.ai 구조 변경 의심**: `python ~/youtube-telegram-summary/diagnose.py` 실행 후
  `~/.config/youtube-telegram-summary/inspect/` 의 `network.json`·스크린샷 확인
- **긴 영상(수 시간)**: 요약에 수 분 걸릴 수 있음. 한 번에 못 끝내면 다음 실행에서 자동 이어받음
