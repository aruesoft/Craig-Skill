# 🖥 Craig-Skill 봇 서버 운영 런북 — 맥북 에어

> 목적: Craig-Skill 의 봇 2종(**등산봇** + **유튜브 요약봇**)을 개발기가 아닌 **운영서버(맥북 에어)**에서
> 고정 상시 구동한다. 방식은 `stock_prediction_project` 서버 운영 패턴을 이식했다.
>
> 전제: 이 맥북 에어는 이미 `stock_prediction_project` 를 `com.craig.stock.*` launchd + crontab 으로
> 상시 운영 중인 **그 서버**다. 따라서 macOS 기본 설정(계정 `craigpark`·절전끄기·자동로그인·정전자동시작)과
> Homebrew·Anaconda 는 이미 되어 있다고 본다. (새 기기라면 `stock_prediction_project/SERVER_SETUP_AIR.md` §2·§3 먼저.)
>
> - 🤖 = 서버의 Claude/터미널이 실행 가능  · 🙋 = 사람이 직접(로그인/브라우저/폰 승인)

---

## 0. 황금률

1. **계정명 `craigpark`, 저장소 경로 `/Users/craigpark/Github/Craig-Skill`** — plist·스크립트가 이 절대경로에 하드코딩. 다르면 전면 수정.
2. **파이썬 = anaconda base** `/Users/craigpark/anaconda3/bin/python3` (stock 서버와 동일 런타임).
3. **단일 인스턴스 철칙** — 같은 텔레그램 봇을 두 곳에서 폴링하면 `Conflict`. 서버로 옮기면 **개발기 + 구 클론을 반드시 종료**(§4).
4. **비밀값은 git 밖** — `~/.config/*/config.json`. 코드/커밋/문서에 토큰 붙여넣지 말 것.
5. **볼트(SkillVault)는 Obsidian Sync 소유(git 미추적)** — 유튜브 요약 로그가 여기 쌓인다.

---

## 1. 이 봇들이 하는 일 (인벤토리)

| 구성요소 | 스크립트 | 등록 | 스케줄 |
|---|---|---|---|
| 등산봇(텔레그램, 상시) | `korean-mountain-hiking/telegram-bot/bot.py --listen` | launchd `com.craig.skill.mountainbot` | 상시(이벤트 응답) |
| 유튜브 요약봇(상시 리스너) | `youtube-telegram-summary/monitor.py --listen` | launchd `com.craig.skill.youtube` | 상시(명령 즉시응답 + 6시간 주기 자동감지) |
| 자동배포 | `deploy/auto_deploy.sh` | crontab | 10분마다 |

- 등산봇: 산 이름/날짜 → 코스·산악날씨·하산식 맛집. 데이터=`references/mountains.json`(231곳).
- 유튜브봇: **상시 리스너** — 채널 명령(추가/삭제/list/run) 즉시 응답 + 같은 프로세스가 `schedule_interval_hours`(config, 기본 6h)마다 새 영상 감지 → secondb.ai/Claude 요약 → 텔레그램 전송 → **SkillVault 데일리 로그** 기록. getUpdates 소유 프로세스가 하나라 충돌 없음.

---

## 2. 코드 이관 🙋→🤖

```bash
# 저장소 클론 (경로 황금률 준수)
mkdir -p /Users/craigpark/Github
cd /Users/craigpark/Github
git clone https://github.com/aruesoft/Craig-Skill.git
#  (SSH도 가능: git@github.com:aruesoft/Craig-Skill.git)
cd Craig-Skill
```

---

## 3. 파이썬 의존성 🤖

```bash
PY=/Users/craigpark/anaconda3/bin/python3
# 등산봇: requests 필수, anthropic 선택(자유질문 AI 모드)
$PY -m pip install -U requests anthropic
# 유튜브봇: secondb 세션 브라우저 + 요약/자막(+ secondb quota 시 yt-dlp 자막 폴백)
$PY -m pip install -U playwright requests anthropic youtube-transcript-api yt-dlp
$PY -m playwright install chromium
# (선택) deno — yt-dlp 의 JS 런타임. 자막 추출 신뢰도↑(YouTube 429 완화).
#   brew 는 구버전 macOS 에서 소스빌드 실패 → prebuilt 바이너리 권장:
#   curl -fsSL https://deno.land/install.sh | sh  &&  ln -sf ~/.deno/bin/deno /opt/homebrew/bin/deno
```

> `bootstrap_server.sh` 가 이 3개(requests/anthropic/playwright) 존재를 프리플라이트로 검사한다.

---

## 4. 단일 인스턴스 컷오버 — 기존 실행 종료 🙋 (매우 중요)

서버에서 켜기 **전에**, 같은 봇의 다른 인스턴스를 모두 끈다. 안 그러면 `getUpdates Conflict`.

```bash
# (개발기에서) 지금 도는 두 봇 종료
pkill -f "Craig-Skill/korean-mountain-hiking/telegram-bot/bot.py --listen"
pkill -f "youtube-telegram-summary/monitor.py"

# (서버에서) 구 유튜브 리스너/클론 은퇴 — 있으면
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.craig.youtube-telegram-listener.plist 2>/dev/null
rm -f ~/Library/LaunchAgents/com.craig.youtube-telegram-listener.plist
#  구 crontab 유튜브 라인(~/youtube-telegram-summary/monitor.py)이 있으면 제거: crontab -e
```

> 등산봇과 유튜브봇은 **서로 다른 텔레그램 토큰**(config 디렉터리 분리)이라 둘 사이엔 충돌이 없다.
> 충돌은 오직 "같은 봇의 두 인스턴스"에서만 난다.

---

## 5. 비밀값 / 세션 배치 🙋 (git 밖)

### 5-1. 등산봇 config
`~/.config/korean-mountain-hiking/config.json` (chmod 600):
```json
{
  "telegram_bot_token": "<등산봇 토큰>",
  "telegram_chat_id": "",
  "anthropic_api_key": "<선택: 자유질문 AI 모드>"
}
```
- `telegram_chat_id` 비우면 아무 채팅에서나 응답(공개 조회봇). `anthropic_api_key` 없으면 규칙기반만.

### 5-2. 유튜브봇 config
`~/.config/youtube-telegram-summary/config.json`:
```json
{
  "youtube_channels": ["UCxxxx"],
  "telegram_bot_token": "<유튜브봇 토큰>",
  "telegram_chat_id": "<허용 chat id>",
  "summary_language": "kr",
  "obsidian_daily_dir": "/Users/craigpark/Github/Craig-Skill/SkillVault/3_Resources/유튜브-요약로그",
  "anthropic_api_key": "<선택: secondb 실패 시 Claude 폴백>"
}
```
- **`obsidian_daily_dir` 가 핵심** — 요약을 이 볼트에 쌓게 한다(StockVault 아님). → [[유튜브-요약로그]]

### 5-3. 유튜브 secondb.ai 로그인 🙋 (브라우저 1회)
```bash
cd /Users/craigpark/Github/Craig-Skill/youtube-telegram-summary
/Users/craigpark/anaconda3/bin/python3 login.py   # 구글 로그인, 세션 영속 저장
```
> 개발기의 `~/.config/youtube-telegram-summary/` (browser_profile·state.json)를 AirDrop 으로 통째 이관해도 됨.

---

## 6. 서비스 등록 🤖 — 한 방 부트스트랩

```bash
cd /Users/craigpark/Github/Craig-Skill
DRY=1 bash deploy/bootstrap_server.sh   # 먼저 점검(부작용 없음)
bash deploy/bootstrap_server.sh         # 실제: launchd 2종 + crontab 자동배포 등록
```
등록 결과:
- `~/Library/LaunchAgents/com.craig.skill.{mountainbot,youtube}.plist` 로드
- crontab 에 `*/10 * * * * deploy/auto_deploy.sh` 추가

수동으로 하려면:
```bash
for j in mountainbot youtube; do
  cp deploy/launchd/com.craig.skill.$j.plist ~/Library/LaunchAgents/
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.craig.skill.$j.plist
done
```

---

## 7. 검증 — 스모크 🤖

```bash
PY=/Users/craigpark/anaconda3/bin/python3
# ① 등산봇 로컬 응답(텔레그램 없이)
$PY korean-mountain-hiking/telegram-bot/bot.py --check "북한산 이번주 토요일"
# ② 유튜브봇 1회 수동 실행(감지·요약·전송·볼트로그)
$PY youtube-telegram-summary/monitor.py
ls -la SkillVault/3_Resources/유튜브-요약로그/     # 오늘자 .md 생겼는지
# ③ 서비스 라이브
launchctl list | grep com.craig.skill              # mountainbot / youtube
# ④ 텔레그램에서 등산봇에게 "북한산" 보내 응답 오면 상시 서비스 정상
```

---

## 8. 자동배포 흐름

개발기에서 `git push` (master) → 서버 crontab `auto_deploy.sh`(10분) 가:
1. `git fetch` → 로컬이 뒤면 `git merge --ff-only`
2. 변경이 `korean-mountain-hiking/telegram-bot/` 면 등산봇 `launchctl kickstart` 재시작
3. 유튜브봇은 매 주기 최신 파일을 읽으므로 재시작 불필요
4. 텔레그램 🚀 통지

- **볼트가 git 미추적**이라, 서버가 유튜브 요약을 SkillVault 에 계속 써도 ff-only pull 이 충돌하지 않는다.
- diverge/충돌 시 강제하지 않고 ⚠️ 경보 후 중단(안전). → 개발기=코드 / 서버=볼트 역할 분리.

---

## 9. 운영 명령 치트시트

```bash
# 상태
launchctl list | grep com.craig.skill
# 로그
tail -f logs/mountainbot.err.log logs/youtube.err.log logs/auto_deploy.log
# 재시작
launchctl kickstart -k gui/$(id -u)/com.craig.skill.mountainbot
# 내리기/올리기
launchctl bootout   gui/$(id -u) ~/Library/LaunchAgents/com.craig.skill.mountainbot.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.craig.skill.mountainbot.plist
```

---

## 10. 함정 체크리스트

- [ ] 계정명 `craigpark`·경로 `/Users/craigpark/Github/Craig-Skill` 인가?
- [ ] `import requests`(+선택 anthropic/playwright) 되나?
- [ ] config.json 2종 배치 + 토큰 채웠나? (chmod 600)
- [ ] 유튜브 `obsidian_daily_dir` = SkillVault 경로인가?
- [ ] **개발기·구 클론(`~/youtube-telegram-summary`) 종료**했나? (단일 인스턴스)
- [ ] secondb 로그인(`login.py`) 했나?
- [ ] 절전 끄기·자동 로그인(재부팅 후 launchd 복구) 켜져 있나? (stock 런북 §2)

---

## 부록. 볼트 참고
- 서버 운영 노트(옵시디언): `SkillVault/2_Areas/서버 운영 (맥북 에어).md`
- 봇 운영 일반: `SkillVault/2_Areas/봇 운영.md`
- 유튜브 요약 로그: `SkillVault/3_Resources/유튜브-요약로그/`
