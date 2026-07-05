# Craig Telegram Study — 학습 파이프라인

텔레그램/옵시디언으로 링크·텍스트·이미지를 보내면 **수집 → 선별·승격 → 간격반복 복습**으로 PARA 볼트(StudyVault)에 정리하는 파이프라인. **봇은 큐 릴레이만, 지능은 처리기(learn-\*)가 담당**.

```
입력 → relay_bot(수신→Queue→발신)
  → learn-ingest(수집·동영상 타임스탬프 전사) → 00_Inbox
  → learn-curate(/curate 승인 버튼 → 병합/생성) → 02_Areas 주제노트 + MOC
  → learn-garden(링크·MOC 정비) · learn-retro(SM-2 복습) · learn-weekly(주간 리트로)
```

설계 SSOT: [`학습파이프라인_설계안.md`](학습파이프라인_설계안.md). 상세: [`SKILL.md`](SKILL.md).

## 특징
- **다양한 입력**: 웹/유튜브/인스타 링크, 텍스트, 노트·책 사진(이미지 OCR)
- **동영상 타임스탬프 전사**: 자막API → yt-dlp → whisper. `## 스크립트 (전문)`에 `[MM:SS]`
- **인간 승인 큐레이션**: `/curate` → 노트별 ✅승인/📁보관/🗑버림 버튼 → 주제노트에 **병합 우선**
- **PARA 볼트 + MOC**: `02_Areas/{AI-ML,Business-Investing}/_MOC_*` 자동 갱신
- **간격 반복 복습**: SM-2 라이트(1→3→7→21→60), 텔레그램 카드 👍👌👎
- **정크 방지**: 로그인 벽(threads·x·인스타) 추출 실패 시 노트 안 만들고 안내

## 명령
`/curate`(선별·승격) · `/garden`(정비) · `/review`(복습) · `/weekly`(주간) · `/status` · `/find 키워드` · `#ai`/`#biz` 힌트

## 설치·설정
```bash
pip install anthropic requests trafilatura yt-dlp youtube-transcript-api openai-whisper
```
- config `~/.config/craig-telegram-study/config.json`: `telegram_bot_token`·`anthropic_api_key`(+선택 `telegram_chat_id`·`ytdlp_cookies`·`whisper_model`). **저장소 밖**.
- 서버 상시가동: `deploy/`(launchd) + `SERVER_SETUP.md`.

## 실행(로컬 테스트)
```bash
python pipeline/relay_bot.py --listen                 # 봇(큐 릴레이)
python pipeline/learn_ingest.py --text "URL 또는 텍스트" # 수집 1건
python pipeline/learn_curate.py --run                 # 승인 카드 발송
python pipeline/learn_retro.py --run                  # 복습 카드
```

## 라이선스
MIT
