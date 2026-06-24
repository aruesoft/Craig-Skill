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

## 파일 구성

| 파일 | 역할 |
|------|------|
| `setup.py` | 최초 설정 마법사 (텔레그램·채널·로그인·cron) |
| `login.py` | secondb.ai 구글 로그인 (브라우저에서 직접 1회) |
| `monitor.py` | 메인 실행 (감지·요약·전송) + 채널 관리 |
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
```

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
  "summary_language": "kr"
}
```

- `summary_language`: 요약 언어 (기본 `kr`)

상태 파일 `state.json`(이미 처리한 영상 목록)을 초기화하려면:
```bash
echo '{"seen_videos":[],"last_checked":null}' > ~/.config/youtube-telegram-summary/state.json
```

## 문제 해결

- **로그인 만료/요약이 안 됨**: `python ~/youtube-telegram-summary/login.py` 재실행
- **secondb.ai 구조 변경 의심**: `python ~/youtube-telegram-summary/diagnose.py` 실행 후
  `~/.config/youtube-telegram-summary/inspect/` 의 `network.json`·스크린샷 확인
- **긴 영상(수 시간)**: 요약에 수 분 걸릴 수 있음. 한 번에 못 끝내면 다음 실행에서 자동 이어받음
