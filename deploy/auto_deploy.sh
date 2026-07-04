#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Craig-Skill Pull 기반 자동배포 — 개발기에서 push 하면 서버가 스스로 최신화
# (stock_prediction_project/auto_deploy.sh 패턴 이식)
#
# 동작:
#   1) git fetch origin master
#   2) 로컬이 origin 뒤(behind)면 → git merge --ff-only 로 당김
#      ※ 볼트(SkillVault/)는 .gitignore(Obsidian Sync 소유)라 git 트리를 더럽히지 않는다
#         → ff-only 가 볼트 쓰기(유튜브 요약 로그)와 충돌할 일이 없다.
#      diverge/충돌 시엔 강제하지 않고 경보 후 중단(안전).
#   3) 변경 파일에 해당하는 launchd 서비스만 재시작
#      (youtube 주기잡은 매 실행 최신 파일을 읽으므로 재시작 불필요)
#   4) 텔레그램 통지
#
# crontab 등록(상시, 10분마다):
#   */10 * * * * .../deploy/auto_deploy.sh >> .../logs/auto_deploy.log 2>&1
# ═══════════════════════════════════════════════════════════════════════════
set -uo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/Users/craigpark/anaconda3/bin:$PATH"
ROOT="/Users/craigpark/Github/Craig-Skill"
BRANCH="master"
cd "$ROOT" || exit 1

LOG_DIR="$ROOT/logs"; mkdir -p "$LOG_DIR"
LOCK="$LOG_DIR/auto_deploy.lock"

# 텔레그램 통지용 토큰/챗ID — 등산봇 config.json 재사용
CFG="$HOME/.config/korean-mountain-hiking/config.json"
TG_TOKEN=""; TG_CHAT=""
if [ -f "$CFG" ]; then
    TG_TOKEN=$(/usr/bin/python3 -c "import json,sys;print(json.load(open('$CFG')).get('telegram_bot_token',''))" 2>/dev/null)
    TG_CHAT=$(/usr/bin/python3 -c "import json,sys;print(json.load(open('$CFG')).get('telegram_chat_id',''))" 2>/dev/null)
fi
[ -n "${TELEGRAM_BOT_TOKEN:-}" ] && TG_TOKEN="$TELEGRAM_BOT_TOKEN"
[ -n "${TELEGRAM_CHAT_ID:-}" ] && TG_CHAT="$TELEGRAM_CHAT_ID"

tg() {
    [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ] || return 0
    curl -s "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TG_CHAT}" --data-urlencode "text=$1" >/dev/null 2>&1 || true
}
log() { echo "[$(TZ=Asia/Seoul date '+%F %T')] $*"; }

# 동시실행 방지
if [ -d "$LOCK" ] && [ -n "$(find "$LOCK" -maxdepth 0 -mmin +15 2>/dev/null)" ]; then rmdir "$LOCK" 2>/dev/null || true; fi
mkdir "$LOCK" 2>/dev/null || exit 0
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

git fetch -q origin "$BRANCH" 2>/dev/null || { log "fetch 실패(네트워크?)"; exit 0; }

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")
BASE=$(git merge-base HEAD "origin/$BRANCH")

[ "$LOCAL" = "$REMOTE" ] && exit 0                      # 최신 — 조용히 종료

if [ "$LOCAL" != "$BASE" ]; then                       # diverge 또는 로컬이 앞섬
    if [ "$REMOTE" = "$BASE" ]; then
        log "로컬이 origin 앞섬(미푸시 커밋) — 자동배포 스킵"
    else
        log "히스토리 diverge — 수동 확인 필요"
        tg "⚠️ [Craig-Skill 배포] origin과 히스토리가 갈렸습니다(diverge). 서버에서 수동 확인 필요."
    fi
    exit 0
fi

COMMITS=$(git log --oneline "HEAD..origin/$BRANCH" | wc -l | tr -d ' ')
CHANGED=$(git diff --name-only HEAD "origin/$BRANCH")

if ! git merge --ff-only -q "origin/$BRANCH" 2>>"$LOG_DIR/auto_deploy.log"; then
    log "ff-only 병합 실패 — 강제하지 않음"
    tg "⚠️ [Craig-Skill 배포] 코드 갱신(${COMMITS}커밋)이 로컬 수정과 충돌해 보류. 서버에서 수동 확인 필요."
    exit 0
fi

# 변경 파일 → 재시작할 launchd 서비스 매핑 (둘 다 상시 프로세스라 코드 변경 시 재시작 필요)
RELOADED=""
reload() { launchctl kickstart -k "gui/$(id -u)/com.craig.skill.$1" 2>/dev/null && RELOADED="$RELOADED $2"; }
echo "$CHANGED" | grep -qE '^korean-mountain-hiking/telegram-bot/' && reload mountainbot "등산봇"
echo "$CHANGED" | grep -qE '^youtube-telegram-summary/'          && reload youtube "유튜브봇"
echo "$CHANGED" | grep -qE '^craig-telegram-study/'              && reload studybot "학습봇"
echo "$CHANGED" | grep -qE '^deploy/dashboard\.py'              && reload dashboard "대시보드"

log "배포 완료: ${COMMITS}커밋, 재시작=[${RELOADED:- 없음}]"
tg "🚀 [Craig-Skill 배포] ${COMMITS}커밋 반영 → $(git rev-parse --short origin/$BRANCH). 재시작:${RELOADED:- 없음(유튜브는 다음 주기 자동 반영)}"
exit 0
