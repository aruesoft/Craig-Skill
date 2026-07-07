#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Craig-Skill 서버 부트스트랩 — 맥북 에어(운영서버)에 봇 2종을 launchd 상시 등록
# (stock_prediction_project/bootstrap_server.sh 패턴 이식)
#
#   등산봇  : com.craig.skill.mountainbot  (bot.py --listen, 상시 KeepAlive)
#   유튜브봇: com.craig.skill.youtube      (monitor.py, 6시간 주기)
#   자동배포: crontab */10 (deploy/auto_deploy.sh)
#
# 사용:
#   DRY=1 bash deploy/bootstrap_server.sh   # 점검만(부작용 없음)
#   bash deploy/bootstrap_server.sh         # 실제 설치·등록
# ═══════════════════════════════════════════════════════════════════════════
set -uo pipefail
DRY="${DRY:-0}"
ROOT="/Users/craigpark/Github/Craig-Skill"
PY="/Users/craigpark/anaconda3/bin/python3"
UID_NUM="$(id -u)"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
FAIL=0; WARN=0

ok()   { echo "✅ $*"; }
warn() { echo "⚠️  $*"; WARN=$((WARN+1)); }
err()  { echo "❌ $*"; FAIL=$((FAIL+1)); }
run()  { if [ "$DRY" = "1" ]; then echo "   [DRY] $*"; else eval "$*"; fi; }

echo "═══ Craig-Skill 서버 부트스트랩 (DRY=$DRY) ═══"

# ── 가드 0: 경로·계정 ──────────────────────────────────────────────────
[ "$(whoami)" = "craigpark" ] || warn "계정명이 craigpark 가 아님 — plist 절대경로가 안 맞을 수 있음"
[ -d "$ROOT" ] || { err "저장소 경로 없음: $ROOT (황금률: 이 경로에 clone)"; exit 1; }
cd "$ROOT" || exit 1
[ -x "$PY" ] || err "anaconda python 없음: $PY (Anaconda base 설치 필요)"

# ── 가드 1: 파이썬 의존성 프리플라이트 ─────────────────────────────────
$PY -c "import requests" 2>/dev/null && ok "requests OK" || err "requests 없음 → $PY -m pip install requests"
$PY -c "import anthropic" 2>/dev/null && ok "anthropic OK(자유질문 모드)" || warn "anthropic 없음(선택) → 등산봇 규칙기반만 동작. AI모드 쓰려면 pip install anthropic"
$PY -c "import playwright" 2>/dev/null && ok "playwright OK(유튜브 secondb)" || warn "playwright 없음 → $PY -m pip install playwright && $PY -m playwright install chromium"
$PY -c "import yt_dlp" 2>/dev/null && ok "yt-dlp OK(secondb quota 시 자막 폴백)" || warn "yt-dlp 없음 → $PY -m pip install yt-dlp"
$PY -c "import trafilatura" 2>/dev/null && ok "trafilatura OK(학습봇 웹 추출)" || warn "trafilatura 없음 → $PY -m pip install trafilatura lxml_html_clean"
$PY -c "import whisper" 2>/dev/null && ok "openai-whisper OK(학습봇 음성인식)" || warn "openai-whisper 없음(선택) → $PY -m pip install openai-whisper (torch 필요, 자막없는 영상 전사용)"
command -v deno >/dev/null 2>&1 && ok "deno OK(yt-dlp JS런타임)" || warn "deno 없음(선택) — yt-dlp 자막 신뢰도↑. YouTube 429 잦으면 설치 검토"

# ── 가드 2: 비밀정보(config.json, git 밖) ──────────────────────────────
MCFG="$HOME/.config/korean-mountain-hiking/config.json"
YCFG="$HOME/.config/youtube-telegram-summary/config.json"
SCFG="$HOME/.config/craig-telegram-study/config.json"
[ -f "$MCFG" ] && ok "등산봇 config 있음" || warn "등산봇 config 없음: $MCFG (telegram_bot_token 필요)"
[ -f "$YCFG" ] && ok "유튜브봇 config 있음" || warn "유튜브봇 config 없음: $YCFG (token + obsidian_daily_dir 필요)"
[ -f "$SCFG" ] && ok "학습봇 config 있음" || warn "학습봇 config 없음: $SCFG (token + anthropic_api_key + study_vault_dir 필요)"
if [ -f "$SCFG" ]; then
    SVD=$($PY -c "import json;print(json.load(open('$SCFG')).get('study_vault_dir',''))" 2>/dev/null)
    [ -n "$SVD" ] && ok "학습봇 StudyVault → $SVD" || warn "study_vault_dir 미설정"
fi
if [ -f "$YCFG" ]; then
    ODIR=$($PY -c "import json;print(json.load(open('$YCFG')).get('obsidian_daily_dir',''))" 2>/dev/null)
    case "$ODIR" in
        *SkillVault*) ok "유튜브 요약 로그 → SkillVault ($ODIR)";;
        "") warn "obsidian_daily_dir 미설정 → 요약이 볼트에 안 쌓임. 권장값: $ROOT/SkillVault/3_Resources/유튜브-요약로그";;
        *) warn "obsidian_daily_dir 이 SkillVault 밖을 가리킴: $ODIR";;
    esac
fi

# ── 가드 3: 중복 인스턴스(단일 인스턴스 철칙) ──────────────────────────
if launchctl list 2>/dev/null | grep -q "com.craig.youtube-telegram-listener"; then
    warn "구 유튜브 리스너(com.craig.youtube-telegram-listener) 로드됨 → 같은 토큰 충돌 위험. 은퇴 필요:"
    echo "     launchctl bootout gui/$UID_NUM ~/Library/LaunchAgents/com.craig.youtube-telegram-listener.plist"
fi
pgrep -fl "youtube-telegram-summary/monitor.py" | grep -qv "$ROOT" && \
    warn "다른 경로의 youtube monitor.py 실행 중 — 종료 후 진행(getUpdates 충돌 방지)" || true

# ── 로그 디렉터리 ──────────────────────────────────────────────────────
run "mkdir -p '$ROOT/logs'"

# ── launchd 등록 ───────────────────────────────────────────────────────
for j in mountainbot youtube studybot dashboard watchdog learn-ingest learn-curate learn-garden learn-retro learn-weekly obsidian-keeper; do
    SRC="$ROOT/deploy/launchd/com.craig.skill.$j.plist"
    DST="$LAUNCH_DIR/com.craig.skill.$j.plist"
    [ -f "$SRC" ] || { err "plist 없음: $SRC"; continue; }
    run "cp '$SRC' '$DST'"
    run "launchctl bootout gui/$UID_NUM '$DST' 2>/dev/null || true"   # 이미 있으면 먼저 내림
    run "launchctl bootstrap gui/$UID_NUM '$DST'"
    ok "launchd 등록: com.craig.skill.$j"
done

# ── crontab: 자동배포 10분마다 (기존 라인 보존하며 병합) ────────────────
CRON_LINE="*/10 * * * * $ROOT/deploy/auto_deploy.sh >> $ROOT/logs/auto_deploy.log 2>&1"
if crontab -l 2>/dev/null | grep -qF "deploy/auto_deploy.sh"; then
    ok "crontab 자동배포 이미 등록됨"
else
    if [ "$DRY" = "1" ]; then echo "   [DRY] crontab += $CRON_LINE"
    else ( crontab -l 2>/dev/null; echo "# Craig-Skill 자동배포"; echo "$CRON_LINE" ) | crontab - && ok "crontab 자동배포 등록"; fi
fi

# ── 요약 ───────────────────────────────────────────────────────────────
echo "───────────────────────────────────────────────"
[ "$DRY" = "1" ] && echo "DRY 점검 종료(부작용 없음)."
echo "결과: 실패 $FAIL · 경고 $WARN"
if [ "$DRY" != "1" ]; then
    echo "확인: launchctl list | grep com.craig.skill"
    echo "로그: $ROOT/logs/{mountainbot,youtube,auto_deploy}.*.log"
    echo "남은 사람 작업(⚠️): config.json 2종 배치, 유튜브 secondb 로그인(python login.py), 구 리스너 은퇴"
fi
[ "$FAIL" -gt 0 ] && exit 1 || exit 0
