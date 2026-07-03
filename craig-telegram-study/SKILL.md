---
name: craig-telegram-study
description: 텔레그램으로 링크 또는 학습 내용을 보내면 Claude가 정리해 Obsidian(StudyVault)에 학습 노트로 저장한다. 요약·상세정리·인과관계·계층 태그를 만들고, 기존 노트/개념과 [[위키링크]]로 연결한다. 웹 링크는 본문 추출(trafilatura), 유튜브는 자막(yt-dlp)으로 내용을 확보한다. "이거 정리해서 옵시디언에 넣어줘", "학습봇", "스터디 봇 설정/디버깅" 같은 요청에 사용한다.
license: MIT
metadata:
  category: productivity
  locale: ko-KR
---

# Craig Telegram Study Bot

## What this skill does

텔레그램 봇 **@CraigStudyBot** 에게 **링크(웹/유튜브) 또는 학습 텍스트**를 보내면:

1. 내용을 확보한다 — 웹은 `trafilatura` 본문 추출, 유튜브는 `yt-dlp` 자막, 텍스트는 그대로.
2. Claude(`claude-sonnet-5`)가 **학습·복습용 노트**로 정리한다 — 핵심 요약 / 상세 정리 / **인과관계(A → B)** / 왜 중요한가.
3. **계층 태그**(예: `경제/금리`)를 붙이고, **기존 노트·개념과 인과·연관을 찾아 `[[위키링크]]`로 연결**한다.
4. Obsidian **StudyVault** 에 저장한다 — `Notes/`에 학습 노트, `Concepts/`에 새 개념 허브(백링크로 연결).
5. 텔레그램으로 제목·태그·한줄요약·파일경로·연결/새개념을 회신한다.

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

- `pip install anthropic requests trafilatura yt-dlp` (yt-dlp는 유튜브 자막용)
- `config.json`에 `telegram_bot_token`, `anthropic_api_key`, `study_vault_dir` 설정
- 텔레그램 토큰은 [@BotFather](https://t.me/BotFather) 발급. **커밋 금지**(`.gitignore`).

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
