# Craig Telegram Study Bot (craig-telegram-study)

텔레그램으로 **링크 또는 학습 내용**을 보내면, Claude가 정리해 **Obsidian(StudyVault)**에 학습 노트로 저장하는 스킬 & 봇.

```
텔레그램(@CraigStudyBot) → 링크/텍스트
   → 본문 추출(웹=trafilatura · 유튜브=yt-dlp 자막 · 텍스트=그대로)
   → Claude 정리(요약·상세·인과관계·계층 태그·기존 노트 [[링크]])
   → StudyVault/Notes 저장 + Concepts 개념 허브 생성 → 텔레그램 회신
```

## 특징

- **학습 최적화 노트**: 핵심 요약 → 상세 정리 → **인과관계(A → B)** → 왜 중요한가/응용
- **인과·연관 연결**: 기존 노트/개념을 찾아 `[[위키링크]]`로 연결(그래프 뷰로 지식망 확인)
- **계층 태그**: `경제/금리`처럼 분야/하위 구조
- **개념 허브**: `[[개념]]` 참조 시 `Concepts/`에 스텁 자동 생성 → 백링크 형성

## 설치

```bash
pip install anthropic requests trafilatura yt-dlp
```

## 설정 (`~/.config/craig-telegram-study/config.json`, 저장소 밖·chmod 600)

```json
{
  "telegram_bot_token": "BotFather 토큰",
  "telegram_chat_id": "",
  "anthropic_api_key": "sk-ant-...",
  "claude_model": "claude-sonnet-5",
  "study_vault_dir": "/path/to/StudyVault"
}
```

- `telegram_chat_id` 비우면 아무 채팅이나 응답(개인봇). 특정인만 쓰려면 chat id 지정.
- `study_vault_dir` — 결과가 쌓일 Obsidian 볼트 경로.

## 실행

```bash
python telegram-bot/study_bot.py --listen                 # 상시 대기(즉시 응답)
python telegram-bot/study_bot.py --once                   # 밀린 메시지 1회(cron)
python telegram-bot/study_bot.py --check "https://... 또는 학습 텍스트"   # 로컬 미리보기
```

## 사용 예 (텔레그램에서)

| 보내는 것 | 결과 |
|---|---|
| 웹 기사 URL | 본문 추출 → 요약·인과관계·태그 노트 |
| 유튜브 URL | 자막 추출 → 학습 노트 |
| 학습 텍스트/메모 | 정리·구조화 노트 |

## 라이선스

MIT
