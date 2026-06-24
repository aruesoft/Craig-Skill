# Craig-Skill

Claude / AI 에이전트용 스킬 모음.

## 스킬 목록

### [youtube-telegram-summary](youtube-telegram-summary/)

지정한 YouTube 채널에 새 동영상이 올라오면 [secondb.ai](https://secondb.ai/)로 AI 요약을 생성해
Telegram 메시지로 보내주는 자동화 파이프라인.

```
YouTube RSS(새 영상 감지) → secondb.ai REST API(요약) → Telegram Bot(전송)
```

- 여러 채널 모니터링 (@핸들 / URL / 채널ID 자동 변환)
- secondb.ai 구글 로그인 세션 재사용 + API 직접 호출
- 매시간 cron 자동 실행
- 설치형 패키지: [`youtube-telegram-summary.skill`](youtube-telegram-summary.skill)

자세한 설정·사용법은 [youtube-telegram-summary/README.md](youtube-telegram-summary/README.md) 참고.
