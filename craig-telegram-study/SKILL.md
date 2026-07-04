---
name: craig-telegram-study
description: 텔레그램으로 링크·학습 텍스트·이미지(노트/책 캡처)를 보내면 Claude가 정리해 Obsidian(StudyVault)에 학습 노트로 저장한다. 요약·상세정리·인과관계·계층 태그를 만들고 기존 노트/개념과 [[위키링크]]로 연결한다. 웹=trafilatura, 유튜브/인스타=yt-dlp 자막·캡션(없으면 음성인식 whisper), 이미지=Claude 비전으로 텍스트화. #태그로 태그 지정, [주제]로 기존 노트에 이어쓰기. "이거 정리해서 옵시디언에 넣어줘", "학습봇", "스터디 봇 설정/디버깅" 같은 요청에 사용한다.
license: MIT
metadata:
  category: productivity
  locale: ko-KR
---

# Craig Telegram Study Bot

## What this skill does

텔레그램 봇 **@CraigStudyBot** 에게 **링크·학습 텍스트·이미지**를 보내면:

1. 내용을 확보한다:
   - **웹 링크** → `trafilatura` 본문 추출
   - **유튜브** → `yt-dlp` 자막 → 없으면 **오디오 음성인식(whisper)**
   - **인스타/틱톡 등** → `yt-dlp` 캡션(쿠키 있으면) → 없으면 음성인식(쿠키 필요)
   - **이미지(사진/책 캡처)** → **Claude 비전으로 텍스트화**(손글씨 포함)
   - **텍스트** → 그대로
2. Claude(`claude-sonnet-5`)가 **학습·복습용 노트**로 정리 — 핵심 요약 / 상세 정리 / **인과관계(A → B)** / 왜 중요한가.
3. **계층 태그**(예: `경제/금리`)를 붙이고, **기존 노트·개념과 인과·연관을 찾아 `[[위키링크]]`로 연결**.
4. Obsidian **StudyVault** 에 저장 — `Notes/`에 학습 노트, `Concepts/`에 새 개념 허브(백링크).
5. 텔레그램으로 제목·태그·한줄요약·파일경로·연결/새개념을 회신.

## 학습 파이프라인 (수집 → 선별·재조합 → 학습·복습)

모든 입력을 학습노트로 만들지 않는다. 성숙도 단계로 나눈다:

```
① 수집   보내면 0_Inbox/ 에 경량 저장(요약·태그, 카드 없음) — 기본
② 선별   /curate → 봇이 인박스를 주제별로 묶어 승격 제안 → /promote 1 3|all (여러 조각을 하나로 재조합)
         즉시 승격: !학습 또는 [주제] 로 보내면 그 자리에서 학습노트+카드
③ 복습   승격된 카드를 간격 반복(SM-2). 매일 8·21시 알림 + /quiz 로 즉석 복습.
         카드마다 정답 보기 → ❌다시/😓어려움/🙂알맞음/😎쉬움 → 다음 복습일 자동 조정.
```

**상기(리마인드)**: `remind_hour`(config, 기본 9시) 이후 하루 1회, 인박스에 쌓이면 알림.

**옵시디언에서 직접**: 텔레그램 없이 `0_Inbox/`에 노트를 만들어도 됨. 봇이 15분마다 스캔해
원시 노트를 인식·강화(프론트매터·요약·태그)하고, `#승격` 태그(또는 `!학습`/`[주제]`)를 달면 학습 노트로 승격.
(`_` 로 시작하는 파일은 스캔 제외. 링크·음성·이미지 추출은 텔레그램 권장.)

## 메시지 지시어 · 명령

| 입력 | 동작 |
|---|---|
| (지시어 없음) | **인박스에 수집**(경량, 카드 없음) |
| `!학습 내용` | 즉시 **학습 노트 + 복습 카드**로 승격 |
| `[주제] 내용` | **`주제` 노트에 이어서(append)** 날짜 섹션 저장(없으면 생성)+카드. 태그 병합 |
| `#태그` (본문 어디든) | 그 태그를 tags 에 반드시 반영(맨 앞) |
| 사진/이미지 | Claude 비전으로 읽어 처리(캡션의 `#태그`·`[주제]`·`!학습` 인식) |
| `/inbox` | 인박스 대기 목록 |
| `/curate` | 인박스 자동 클러스터링·승격 제안 |
| `/promote 1 3` \| `all` | 제안 묶음을 학습노트로 합성 |
| `/quiz` | 오늘 복습할 카드 세션 시작(버튼 채점) |
| `/status` | 복습 대기·전체·오늘 복습·인박스 현황 |

**복습 관련 config**(선택): `quiz_times`(기본 `[8,21]`), `quiz_max`(1회 카드 수, 기본 20), `remind_hour`(인박스 상기, 기본 9).

예) `https://...`(→인박스) · `!학습 #경제 https://...` · `[금리] 배당은…`

## When to use

- "이 링크 정리해서 옵시디언에 넣어줘" / "이거 학습노트로 만들어줘"
- 학습봇(`study_bot.py`) 실행·디버깅, 서버 상시가동 설정
- StudyVault 구조·태그·링크 관련 질문

## 구성

| 파일 | 역할 |
|---|---|
| `telegram-bot/study_bot.py` | 봇 진입점 — 텔레그램 폴링·본문추출·Claude 정리·볼트 기록 |
| `~/.config/craig-telegram-study/config.json` | 비밀값·설정(토큰·anthropic 키·`study_vault_dir`). **저장소 밖**, chmod 600 |
| StudyVault(`study_vault_dir`) | 결과가 쌓이는 Obsidian 볼트 (`Notes/`·`Concepts/`) |

## 실행

```bash
python study_bot.py --listen                 # 상시 long-poll(즉시 응답)
python study_bot.py --once                   # 밀린 메시지 1회(cron)
python study_bot.py --check "URL 또는 텍스트"  # 텔레그램 없이 로컬 처리(볼트 기록·미리보기)
```

## Prerequisites

- `pip install anthropic requests trafilatura yt-dlp` (필수)
- (음성인식용, 선택) `pip install openai-whisper` + `ffmpeg` — 자막 없는 영상 오디오 전사.
  macOS 12/arm64 는 faster-whisper 의 av/onnxruntime 휠 문제로 **openai-whisper 권장**.
- `config.json`: `telegram_bot_token`, `anthropic_api_key`, `study_vault_dir` (+ 선택 `whisper_model`, `ytdlp_cookies`)
- 텔레그램 토큰은 [@BotFather](https://t.me/BotFather) 발급. **커밋 금지**(`.gitignore`).

### 인스타그램/틱톡 (로그인 벽)
URL만으론 자동 추출 불가. 두 방법:
1. **캡션·핵심 텍스트를 링크와 함께** 붙여 보내면 그 텍스트로 정리(가장 간단).
2. `config.ytdlp_cookies` 에 로그인 쿠키(Netscape `cookies.txt`) 지정 → yt-dlp가 캡션/오디오 확보 → (오디오면) whisper 전사.
   - **설정/갱신**: 이 Mac의 Chrome(instagram.com 로그인)에서 `bash craig-telegram-study/refresh_ig_cookies.sh` 실행 →
     IG 쿠키만 추출·필터해 서버(`~/.config/craig-telegram-study/ig_cookies.txt`, chmod 600)로 전송 + 학습봇 재시작.
   - 쿠키는 **몇 주~몇 달 뒤 만료** → 인스타가 다시 "접근 불가"로 나오면 위 스크립트 재실행.

### 음성인식 모델 (`whisper_model`)
`base`(빠름·정확도↓) / `small`(기본) / `medium`(한국어 정확도↑·느림) / `large-v3`(최고·매우 느림).

## Obsidian 정리 규칙 (StudyVault)

- **Notes/** — 제출 자료별 원자 노트 `YYYY-MM-DD 제목.md`. 프론트매터(`type/title/tags/source/created`) + 본문(요약·상세·인과관계·응용).
- **Concepts/** — 개념 허브. 노트가 `[[개념]]`으로 참조하면 없을 때 스텁 자동 생성 → 백링크로 인과·연관망 형성.
- **태그**는 계층형(`분야/하위`), 소문자, 공백은 하이픈. 그래프 뷰·태그 패널로 탐색.
- 데이터에 없는 사실은 지어내지 않는다(자료 근거).

## Workflow (내부 동작)

1. `extract_content()` — 메시지에서 URL 감지 → 유튜브/웹/텍스트 분기해 본문 확보.
2. `vault_index()` — 기존 Notes/Concepts 제목·태그 수집(링크 대상 후보).
3. `organize()` — Claude에 자료+기존목록 전달 → JSON(제목·태그·본문·links·new_concepts) 수신.
4. `write_note()` — `Notes/`에 노트, `Concepts/`에 새 개념 스텁 작성.

## Done when

- 보낸 링크/텍스트가 StudyVault `Notes/`에 노트로 저장됐다.
- 요약·인과관계·계층 태그가 포함됐고, 기존 개념/노트와 `[[링크]]`로 연결됐다.
- 새 개념은 `Concepts/`에 허브가 생겨 백링크가 형성됐다.

## Failure modes

- 웹 추출 실패 → 링크/원문만으로 정리(그래도 노트 생성). 유튜브 자막 없음/429 → 텍스트 없이 진행.
- anthropic 키 없음/오류 → 정리 실패 안내(회신). 
- Claude JSON 파싱 실패 → 원문을 `inbox` 태그 노트로 저장(유실 방지).

## Notes

- 봇 상시가동·서버 배포는 `deploy/`(launchd) 패턴과 동일 → `SERVER_SETUP.md` 참고.
- StudyVault는 Obsidian Sync 소유(git 미추적).
