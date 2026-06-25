---
name: youtube-telegram-summary
description: |
  지정한 YouTube 채널에 새 동영상이 올라오면 secondb.ai로 AI 요약을 생성해 Telegram으로 보내주는
  자동화 파이프라인을 설정하고 운영합니다. 다음과 같은 요청에 반드시 사용하세요:
  "유튜브 새 영상 나오면 텔레그램으로 요약 보내줘", "YouTube 채널 모니터링 + secondb.ai 요약 + 텔레그램",
  "새 영상 자동 요약 알림", monitor.py 실행/디버깅, 채널 추가·삭제, 구글 로그인 세션 문제 해결 등.
  핵심: YouTube RSS(새 영상 감지) → secondb.ai REST API(요약) → Telegram Bot(전송).
---

# YouTube → secondb.ai → Telegram 요약봇

지정한 YouTube 채널에 새 동영상이 올라오면, secondb.ai로 AI 요약을 만들어 Telegram 메시지로 보낸다.

## 동작 방식

```
YouTube RSS(새 영상 감지) → secondb.ai API(요약) → Telegram Bot(전송)
```

- **YouTube**: 채널 RSS 피드로 신규 영상 감지 (API 키 불필요)
- **secondb.ai**: 구글 로그인 세션을 영속 프로필로 재사용해 `api.secondb.ai` REST API 직접 호출
  - `GET  /api/v1/search_summary?url=` : 기존 요약 조회
  - `POST /api/v1/summarize` : 요약 생성 (URL 기준 멱등 — 중복 생성 안 됨)
  - `GET  /api/v1/summaries/{id}` : 생성 완료까지 폴링
  - 인증은 로그인된 브라우저가 보내는 Authorization 헤더를 관찰해 재사용 (실행당 브라우저 1회)
- **Telegram**: Bot API HTML 메시지 (특수문자 안전 처리)

## 구성 파일

| 파일 | 역할 |
|------|------|
| `setup.py` | 최초 설정 마법사 (텔레그램·채널·로그인·스케줄) |
| `login.py` | secondb.ai 구글 로그인 (브라우저에서 직접 1회, 세션 영속 저장) |
| `monitor.py` | 메인 실행 (감지·요약·전송) + 채널 관리 CLI |
| `install_schedule.py` | 매시간 자동 실행 등록 (macOS/Linux=cron, Windows=작업 스케줄러) |
| `diagnose.py` | secondb.ai 동작 점검용 진단 도구 |

## 최초 설정

```bash
pip install -r requirements.txt && playwright install chromium
python setup.py        # 텔레그램 토큰/채널 입력
python login.py        # secondb.ai 구글 로그인 (브라우저에서 직접)
```

설정/세션은 `~/.config/youtube-telegram-summary/` 에 저장된다 (config.json, browser_profile, state.json).

## 실행 / 운영

```bash
python monitor.py                       # 새 영상 확인·요약·전송
python monitor.py --debug               # 브라우저 표시 + 상세 로그
python monitor.py --add-channel @핸들   # 채널 추가 (@핸들 / URL / UC아이디 자동 변환)
python monitor.py --list-channels       # 채널 목록
python monitor.py --remove-channel 2    # 채널 삭제 (번호 또는 UC아이디)
```

## 자동 실행 (매시간) — macOS · Linux · Windows 공통

OS를 자동 감지해 등록한다:

```bash
python install_schedule.py              # 매시간 등록
python install_schedule.py --interval 6 # 6시간마다
python install_schedule.py --status     # 상태 확인
python install_schedule.py --remove     # 등록 해제
```

- **macOS / Linux**: `crontab` (`0 * * * * python monitor.py >> monitor.log 2>&1`)
  - macOS 등록 멈춤 시: 시스템 설정 → 개인정보 보호 및 보안 → 전체 디스크 접근 권한 에 터미널 추가 후 재시도
- **Windows**: 작업 스케줄러(`schtasks`). 콘솔창 없이 `pythonw.exe` 로 실행, 출력은 `--logfile` 로 `monitor.log` 에 기록
  - 상태: `schtasks /Query /TN YouTubeTelegramSummary` / 즉시 실행: `schtasks /Run /TN YouTubeTelegramSummary`
  - 기본적으로 로그인했을 때 실행됨

## 알림 (텔레그램 ⚠️)

요약 메시지와 별개로 다음 상황을 알린다:
- **실행 중 오류**: 예외 발생 시 오류 내용 전송 (`main()` 에서 try/except로 포착 후 재전파)
- **secondb.ai 로그인 만료**: 세션 없음/만료 시 `python login.py` 안내
- **스케줄 공백 감지**: 예상 주기의 2.5배 이상 비면 실행 재개 시점에 알림 (`schedule_interval_hours` 기준)
- (선택) **완전 중단 감지**: `config.healthcheck_ping_url` 설정 시 매 실행 핑 → healthchecks.io 등 외부에서
  핑 누락을 감지해 알림 (모니터가 아예 안 돌면 자기 자신은 못 알리므로 외부 감시로 보완)

## 신규/휴면 채널 무더기 방지

- `max_videos_per_channel_per_run`(기본 2): 채널당 한 번에 보낼 **최신 영상 수**. 신규 채널 추가나 오래
  안 돈 채널에 영상이 여러 개 있어도 **최신 N개만 전송**, 나머지 과거분은 전송 없이 '본 영상' 처리.

## 참고

- **secondb.ai 요약 quota(사용량 한도)** 가 있다. 한 번에 많은 영상을 몰아 요약하면 429(quota 초과)가
  날 수 있으며, 이 경우 해당 영상은 "본 영상"으로 기록되지 않아 **다음 실행에서 자동 재시도**된다.
- 긴 영상(수 시간)은 요약 생성에 수 분 걸릴 수 있고, 한 번에 못 끝내면 다음 실행에서 이어받는다.
- 로그인 만료 시 `python login.py` 재실행. 구조 변경 의심 시 `python diagnose.py`.
