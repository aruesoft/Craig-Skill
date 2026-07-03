# Craig Telegram Study Bot (craig-telegram-study)

텔레그램으로 **링크 또는 학습 내용**을 보내면, Claude가 정리해 **Obsidian(StudyVault)**에 학습 노트로 저장하는 스킬 & 봇.

```
텔레그램(@CraigStudyBot) → 링크 / 텍스트 / 이미지
   → 본문 확보(웹=trafilatura · 유튜브/인스타=yt-dlp 자막·캡션, 없으면 whisper 음성인식 · 이미지=Claude 비전)
   → Claude 정리(요약·상세·인과관계·계층 태그·기존 노트 [[링크]])
   → StudyVault/Notes 저장 + Concepts 개념 허브 생성 → 텔레그램 회신
```

## 특징

- **다양한 입력**: 웹/유튜브/인스타 링크, 학습 텍스트, **노트·책 사진(이미지 OCR)**
- **학습 최적화 노트**: 핵심 요약 → 상세 정리 → **인과관계(A → B)** → 왜 중요한가/응용
- **인과·연관 연결**: 기존 노트/개념을 찾아 `[[위키링크]]`로 연결(그래프 뷰로 지식망 확인)
- **계층 태그**: `경제/금리`처럼 분야/하위 구조. `#태그` 입력 시 그 태그 반영
- **[주제] 이어쓰기**: `[주제]`로 시작하면 해당 주제 노트에 누적 저장(없으면 새로)
- **개념 허브**: `[[개념]]` 참조 시 `Concepts/`에 스텁 자동 생성 → 백링크 형성
- **음성인식**: 자막 없는 영상은 오디오를 whisper로 전사해 정리

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
- (선택) `ytdlp_cookies` — 쿠키 파일(Netscape) 경로. **인스타그램·틱톡 등 로그인 벽** 콘텐츠 자동 추출용.
  (선택) `ytdlp_cookies_from_browser` — 예: `"chrome"` (로그인된 브라우저 프로필이 있는 기기에서만).

> 인스타그램/틱톡은 로그인 벽이라 쿠키 없이는 자동 추출이 안 됩니다. 쿠키 미설정 시 봇이 안내하며,
> 캡션·자막·핵심 텍스트를 링크와 **함께 붙여** 보내면 그 텍스트로 정리합니다.

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
| 유튜브 URL | 자막(없으면 음성인식) → 학습 노트 |
| 인스타/틱톡 URL | 캡션(쿠키 필요) 또는 함께 붙인 텍스트로 정리 |
| 노트/책 **사진** | Claude 비전으로 텍스트화 → 정리 |
| 학습 텍스트/메모 | 정리·구조화 노트 |
| `#태그` 포함 | 그 태그를 노트에 반영 |
| `[주제] …` | 주제 노트에 이어쓰기(없으면 생성) |

## 라이선스

MIT
