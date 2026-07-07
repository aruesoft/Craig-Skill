# deploy/ — Craig-Skill 서버 배포

봇 3종 + 학습 파이프라인 잡들을 개발기가 아닌 **운영서버(맥북 에어)**에서 고정 상시 구동하기 위한 산출물.
패턴은 `stock_prediction_project` 의 서버 운영 방식을 이식했다. 전체 절차·운영 SSOT: 저장소 루트 **`SERVER_SETUP.md`**.

## 구성

| 파일 | 역할 |
|---|---|
| `launchd/com.craig.skill.mountainbot.plist` | 등산봇 `bot.py --listen` 상시(KeepAlive) |
| `launchd/com.craig.skill.youtube.plist` | 유튜브봇 `monitor.py --listen` **상시 리스너**(명령 즉시응답 + 내부 6시간 주기 감지·요약·전송·볼트로그) |
| `launchd/com.craig.skill.studybot.plist` | 학습봇 `pipeline/relay_bot.py --listen` 상시(큐 릴레이) |
| `launchd/com.craig.skill.learn-{ingest,curate,garden,retro,weekly}.plist` | 학습 파이프라인 처리기 (5분 / 일 22시 / 수·일 21시 / 매일 08시 / 일 20시) |
| `launchd/com.craig.skill.dashboard.plist` | 헬스체크 대시보드(웹 :8788) 상시 |
| `dashboard.py` | 봇·launchd·배포·시스템 상태 웹 대시보드(의존성 없음). `http://<서버>:8788`, JSON `/health` |
| `launchd/com.craig.skill.watchdog.plist` | 워치독(5분마다) |
| `watchdog.py` | 서비스 중단·오류 감지 → 자동 재시작 + 텔레그램 올림보고(복구 시 ✅). stdlib |
| `bootstrap_server.sh` | 프리플라이트 + launchd 등록 + crontab(자동배포) 한 방 등록. `DRY=1` 지원 |
| `auto_deploy.sh` | pull 기반 자동배포(개발기 push → 서버 ff-only 당김 + 변경 서비스 재시작 + 텔레그램 통지) |

## 빠른 시작 (서버에서)

```bash
cd /Users/craigpark/Github/Craig-Skill
DRY=1 bash deploy/bootstrap_server.sh   # 점검만
bash deploy/bootstrap_server.sh         # 실제 등록
launchctl list | grep com.craig.skill   # 확인
```

## 원칙

- **경로 고정**: `/Users/craigpark/Github/Craig-Skill` (plist 절대경로 하드코딩 — 계정명 `craigpark`).
- **파이썬**: anaconda base `/Users/craigpark/anaconda3/bin/python3`.
- **단일 인스턴스**: 같은 봇을 두 곳(개발기+서버)에서 `--listen`/폴링하면 텔레그램 `Conflict`. 서버로 옮기면 개발기·구 클론(`~/youtube-telegram-summary`) 반드시 종료.
- **비밀값**: `~/.config/{korean-mountain-hiking,youtube-telegram-summary}/config.json` (git 밖).
- **볼트**: `SkillVault/` 는 git 미추적(Obsidian Sync 소유). 유튜브 요약 로그가 여기 쌓인다.
