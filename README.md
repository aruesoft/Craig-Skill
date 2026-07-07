# Craig-Skill

Claude / AI 에이전트용 스킬 모음.

## 스킬 목록

### [craig-telegram-study](craig-telegram-study/)

텔레그램(**@CraigStudyBot**)/옵시디언으로 링크·텍스트·이미지를 보내면 **수집 → 선별·승격 → 간격반복 복습**으로
**Obsidian PARA 볼트(StudyVault)**에 정리하는 학습 파이프라인. 봇은 큐 릴레이만, 지능은 learn-* 처리기가 담당.

```
입력 → relay_bot(큐 릴레이) → learn-ingest(수집·전사) → 00_Inbox
  → learn-curate(/curate 승인 버튼 → 주제노트 병합) → 02_Areas + MOC
  → learn-garden(링크 정비) · learn-retro(SM-2 복습) · learn-weekly(주간 리트로)
```

- 동영상 타임스탬프 전사(자막API→yt-dlp→whisper), 웹=trafilatura, 이미지=Claude 비전
- 기존 주제노트에 **병합 우선**, MOC 자동 갱신, `[[위키링크]]` 정비
- 서버 상시가동: launchd `com.craig.skill.{studybot,learn-*}`

자세한 설정·사용법은 [craig-telegram-study/README.md](craig-telegram-study/README.md) 참고.

---

### [youtube-telegram-summary](youtube-telegram-summary/)

지정한 YouTube 채널에 새 동영상이 올라오면 [secondb.ai](https://secondb.ai/)로 AI 요약을 생성해
Telegram 메시지로 보내주는 자동화 파이프라인.

```
YouTube RSS(새 영상 감지) → secondb.ai REST API(요약) → Telegram Bot(전송)
```

- 여러 채널 모니터링 (@핸들 / URL / 채널ID 자동 변환)
- secondb.ai 구글 로그인 세션 재사용 + API 직접 호출
- 매시간 자동 실행 — **macOS/Linux(cron) · Windows(작업 스케줄러) 공통 지원**
- 설치형 패키지: [`youtube-telegram-summary.skill`](youtube-telegram-summary.skill)

자세한 설정·사용법은 [youtube-telegram-summary/README.md](youtube-telegram-summary/README.md) 참고.

---

### [korean-mountain-hiking](korean-mountain-hiking/)

**총 231개 산** 데이터(산림청 100대 명산 포함)를 기반으로 등산 코스 안내·날씨 조회·하산 맛집 추천을 한 번에 제공하는 스킬.

```
산 선택 → 코스 안내(네이버 카페 / 산림청 / 웹) → 기상청 산악날씨 5일 예보 → 하산식 맛집 추천
```

- **231개 산 수록**(100대 명산 포함) — 높이·위치·대표 코스 포함
- **기상청 산악날씨 API** 연동 — mtId 기반 5일 3시간 간격 예보 (mtId 없는 산은 단기예보 fallback)
- **네이버 카페 우선 검색** — [바람막이](https://cafe.naver.com/windstopper) · [하이킹F](https://cafe.naver.com/hikingf) 실제 후기 반영
- **하산식 맛집 추천** — 등산 후 근처 식당을 naver-map 스킬 또는 웹 검색으로 안내
- 설치형 패키지: [`korean-mountain-hiking.skill`](korean-mountain-hiking.skill)

자세한 설정·사용법은 [korean-mountain-hiking/README.md](korean-mountain-hiking/README.md) 참고.
