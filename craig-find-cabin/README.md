# craig-find-cabin

KNPS 대피소 취소표 감시·선점 데몬. 설계는 `SPEC.md`, 구현계획은 `../../docs/superpowers/plans/2026-08-31-craig-find-cabin.md`.

## 실행

- `python3 monitor.py --check`   1회 조회 (텔레그램·예약 없음)
- `python3 monitor.py --dry-run` 데몬, 예약 제출 직전까지만
- `python3 monitor.py --listen`  상시 데몬 (운영)

## 텔레그램 명령

`/start` `/status` `/targets` `/add <대피소> <YYYYMMDD> [인원]` `/remove <대피소> <YYYYMMDD>`

캡차 답장: 숫자 / `<인원> <숫자>` / `pass`

## 설정

`~/.config/craig-find-cabin/config.json` (chmod 600):
- `knps_id`: KNPS 사용자명
- `knps_pw`: KNPS 비밀번호
- `telegram_token`: 봇 토큰
- `telegram_chat_id`: 알림 채팅ID (사용자 `/start` 후 자동 저장)
- `poll_sec`: 폴링 주기(초, 기본 180)

감시대상은 `targets.json`.

## 배포

`deploy/launchd/com.craig.skill.findcabin.plist` → `~/Library/LaunchAgents/` 심볼릭 링크 후 `launchctl bootstrap`. 상세는 상위 `SERVER_SETUP.md`.

## 알림

텔레그램 @FindCabin_bot 채널만 지원 (신규 봇 토큰).
