---
name: craig-telegram-study
description: 텔레그램/옵시디언으로 링크·텍스트·이미지를 보내면 학습 파이프라인이 PARA 볼트(StudyVault)에 수집→선별·승격→간격반복 복습으로 정리한다. 봇은 큐 릴레이만 하고 지능은 learn-* 처리기가 담당(봇/지능 분리). 유튜브/인스타 동영상은 타임스탬프 전사(yt-dlp 자막→whisper), 웹=trafilatura, 이미지=Claude 비전. #ai/#biz 카테고리 힌트, /curate 승인 버튼으로 주제 노트 병합, /review 복습. "이거 정리해서 옵시디언에 넣어줘", "학습봇 파이프라인", "learn-ingest/curate/retro 디버깅" 같은 요청에 사용한다.
license: MIT
metadata:
  category: productivity
  locale: ko-KR
---

# Craig Telegram Study — 학습 파이프라인

> 설계 SSOT: `craig-telegram-study/학습파이프라인_설계안.md`. 프로토타입(구 모놀리식 study_bot)은 이 파이프라인으로 대체됨.

## 아키텍처 — 봇/지능 분리

```
텔레그램/옵시디언 입력
  → relay_bot (수신 → _System/Queue/incoming JSON → 발신만; 지능 없음)
  → learn-* 처리기(launchd, 큐/스케줄)가 지능 담당:
     ① learn-ingest  수집 → 00_Inbox 노트(동영상 타임스탬프 전사)
     ② learn-curate  가치평가 → 텔레그램 승인 버튼 → 02_Areas 주제노트 병합/생성 + MOC
     ②a learn-garden 평문→[[링크]] 변환·MOC 리프레시·고아 표시
     ③ learn-retro   SM-2 라이트(1→3→7→21→60) 복습 카드
     ④ learn-weekly  주간 리트로 리포트
```

봇은 단순해서 안 죽고, 지능은 처리기 스크립트 수정만으로 개선. 봇↔지능은 파일(JSON 큐) 핸드오프.

## 옵시디언 볼트 (PARA)

```
StudyVault/
├── 00_Inbox/          # Raw 착지점(불변). _attachments/media 에 이미지·오디오
├── 02_Areas/          # 지속 학습 영역 — AI-ML/, Business-Investing/ (+ _MOC_*.md)
├── 03_Resources/ 04_Archive/
└── _System/           # Templates/ · Queue/{incoming,outgoing,processed} · Review/(daily-queue·리트로)
```
카테고리는 AI-ML·Business-Investing로 시작. `#ai`/`#biz`로 힌트, 애매하면 unsorted.

## 파이프라인 단계

- **① 수집**: 링크(웹/유튜브/인스타)·텍스트·사진 → `00_Inbox`에 raw 노트(원본+요약+핵심포인트+카테고리 제안+연결 후보).
  - 동영상: 유튜브 자막API(타임스탬프) → yt-dlp VTT → **오디오+whisper 전사**. `## 스크립트 (전문)`에 `[MM:SS]` 섹션.
  - 웹: trafilatura. 이미지: Claude 비전. 추출 실패 + 텍스트 없으면 **정크 노트 안 만들고 안내**(threads/x 등 로그인 벽).
  - 중복(URL 정규화 해시)이면 기존 노트에 재수집 메모만.
- **② 선별·재조합**(`/curate`, 일 22시): 인박스 raw 평가 → 노트별 **승인 카드**(✅승인/📁보관/🗑버림) → 승인 시 **기존 주제노트에 병합 우선**(없으면 신규 + 복습 스케줄 시작) + MOC 갱신. 원본은 `status: promoted`.
- **②a 정비**(`/garden`, 수·일 21시): 평문 언급 → `[[링크]]`, MOC 리프레시, 고아 노트 표시(본문 의미 불변).
- **③ 복습**(`/review`, 매일 08시): `next_review ≤ 오늘`인 주제노트(일 상한 5) → 회상 질문 카드 → 정답 보기 → 👍쉬움/👌보통/👎어려움 → 주제노트 `review:` frontmatter 갱신.
- **④ 주간**(`/weekly`, 일 20시): 통계·약한 주제·다음 주 복습·코멘트 → `_System/Review/YYYY-Www_retro.md`.

## 명령 · 지시어

| | |
|---|---|
| (링크/텍스트/사진) | 인박스 수집 |
| `#ai` `#biz` | 카테고리 힌트 |
| `/curate` | 승인 카드 발송 · 버튼(✅📁🗑)으로 승격/보관/버림 |
| `/garden` | 링크·MOC 정비 |
| `/review` | 오늘 복습 카드(👍👌👎) |
| `/weekly` | 주간 리트로 |
| `/status` `/find 키워드` | 빠른 조회(봇 직접) |

**옵시디언에서 직접**: `00_Inbox/`에 노트를 직접 만들어도 됨(향후 스캔 연동 예정). 현재는 텔레그램 경로가 주.

## 구성 파일

| 파일 | 역할 |
|---|---|
| `pipeline/relay_bot.py` | 봇 = 큐 릴레이(`--listen`) |
| `pipeline/learn_ingest.py` | ① 수집·전사(`--queue`/`--text`/`--image`) |
| `pipeline/learn_curate.py` | ② 승인·병합(`--run`/`--queue`) |
| `pipeline/learn_garden.py` | ②a 정비(`--run`/`--queue`) |
| `pipeline/learn_retro.py` | ③ 복습(`--run`/`--queue`) |
| `pipeline/learn_weekly.py` | ④ 주간(`--run`/`--queue`) |
| `pipeline/pipeline.config.json` | 볼트·카테고리·SRS·video·모델(비밀값 제외) |
| `~/.config/craig-telegram-study/config.json` | 비밀값(토큰·anthropic 키·`ytdlp_cookies`). 저장소 밖, chmod 600 |

## 서버 스케줄 (launchd, 설계 §6)

| Label | 스크립트 | 주기 |
|---|---|---|
| `com.craig.skill.studybot` | `relay_bot.py --listen` | 상시 |
| `com.craig.skill.learn-ingest` | `learn_ingest.py --queue` | 5분(+메시지마다 즉시 트리거) |
| `com.craig.skill.learn-curate` | `learn_curate.py --run` | 일 22시(+/curate) |
| `com.craig.skill.learn-garden` | `learn_garden.py --run` | 수·일 21시(+/garden) |
| `com.craig.skill.learn-retro` | `learn_retro.py --run` | 매일 08시(+/review) |
| `com.craig.skill.learn-weekly` | `learn_weekly.py --run` | 일 20시(+/weekly) |

## Prerequisites

- `pip install anthropic requests trafilatura yt-dlp youtube-transcript-api`
- 음성인식(선택): `pip install openai-whisper` + `ffmpeg`. macOS12/arm64는 faster-whisper 휠 문제로 openai-whisper 권장.
- config: `telegram_bot_token`·`anthropic_api_key`(+ 선택 `telegram_chat_id`·`ytdlp_cookies`·`whisper_model`).
- 인스타/틱톡 쿠키: `bash craig-telegram-study/refresh_ig_cookies.sh`(Chrome 로그인 상태) → 서버 전송·재시작. 만료 시 재실행.

## Failure modes

- 로그인 벽 링크(threads·x·인스타) 추출 실패 + 텍스트 없음 → 노트 안 만들고 "텍스트 함께 보내달라" 안내.
- 동영상 전사 실패 → `transcript: failed` 표시(정크 요약 안 함). 스케줄 잡 발신 대상은 config `telegram_chat_id` 또는 봇에 한 번 메시지하면 잡히는 `last_chat`.

## Notes

- 서버 배포·운영: `SERVER_SETUP.md`·`deploy/`(launchd). StudyVault는 Obsidian Sync 소유(git 미추적).
- 로드맵·이력: SkillVault `1_Projects/학습 파이프라인 재설계`, `학습봇 학습도 강화 로드맵`(프로토타입).
