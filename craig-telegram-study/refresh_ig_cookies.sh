#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# 소셜 쿠키 갱신 — 이 Mac의 Chrome(로그인 상태)에서 인스타·스레드·X 쿠키를
# 추출해 서버로 옮기고 학습봇을 재시작한다. (인스타=yt-dlp 캡션, threads·x=브라우저 렌더)
#
# 언제: 학습봇이 소셜 링크를 "내용 못 가져옴"으로 안내하면(쿠키 만료/미로그인), 실행.
# 전제: Chrome 에서 instagram.com·threads.com·x.com 에 로그인돼 있어야 각 쿠키가 잡힘.
# 안전: 해당 도메인 쿠키만 필터링해 전송(나머지는 임시파일째 삭제). 값은 출력하지 않음.
#
# 사용: bash craig-telegram-study/refresh_ig_cookies.sh
#   (Chrome 키체인 접근 허용 팝업이 뜨면 '허용')
# ═══════════════════════════════════════════════════════════════════════════
set -uo pipefail

AIR="${STUDY_SERVER:-craigpark@Craigui-MacBookAir.local}"
YTDLP="${YTDLP:-/Users/craigpark/anaconda3/bin/yt-dlp}"
REEL="https://www.instagram.com/reel/DZ6UVLLPbYG/"   # 쿠키 로드 트리거용(아무 공개 릴스)
DEST=".config/craig-telegram-study/ig_cookies.txt"

TMP_ALL="$(mktemp)"; TMP_IG="$(mktemp)"
trap 'rm -f "$TMP_ALL" "$TMP_IG"' EXIT

echo "1) Chrome에서 쿠키 추출 (키체인 팝업 뜨면 허용)…"
"$YTDLP" --cookies-from-browser chrome --cookies "$TMP_ALL" \
  --skip-download --no-warnings -q "$REEL" >/dev/null 2>&1 || true

echo "2) 소셜 쿠키(인스타·스레드·X)만 필터…"
python3 - "$TMP_ALL" "$TMP_IG" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
KEEP = ("instagram", "threads", "x.com", "twitter", "facebook")
with open(src) as f, open(dst, "w") as o:
    o.write("# Netscape HTTP Cookie File\n")
    for line in f:
        if line.startswith("#"):
            continue
        dom = line.split("\t")[0].lower() if "\t" in line else ""
        if any(k in dom for k in KEEP):
            o.write(line)
PY
grep -q "sessionid\|threads\|x.com" "$TMP_IG" || { echo "❌ 소셜 세션 없음 — Chrome에서 instagram/threads/x 로그인 확인 후 재시도"; exit 1; }
echo "   포함 도메인: $(grep -oE 'instagram|threads|x\.com|twitter|facebook' "$TMP_IG" | sort -u | tr '\n' ' ')"
chmod 600 "$TMP_IG"

echo "3) 서버 전송(chmod 600) + 학습봇 재시작…"
scp -q -o BatchMode=yes "$TMP_IG" "$AIR:$DEST"
ssh -o BatchMode=yes "$AIR" 'chmod 600 ~/.config/craig-telegram-study/ig_cookies.txt; launchctl kickstart -k gui/$(id -u)/com.craig.skill.studybot' >/dev/null 2>&1
echo "✅ 완료 — 인스타 쿠키 갱신 및 학습봇 재시작됨"
