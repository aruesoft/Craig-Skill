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
- 매시간 자동 실행 — **macOS/Linux(cron) · Windows(작업 스케줄러) 공통 지원**
- 설치형 패키지: [`youtube-telegram-summary.skill`](youtube-telegram-summary.skill)

자세한 설정·사용법은 [youtube-telegram-summary/README.md](youtube-telegram-summary/README.md) 참고.

---

### [korean-mountain-hiking](korean-mountain-hiking/)

산림청 선정 **100대 명산** 데이터를 기반으로 등산 코스 안내·날씨 조회·하산 맛집 추천을 한 번에 제공하는 스킬.

```
산 선택 → 코스 안내(네이버 카페 / 산림청 / 웹) → 기상청 산악날씨 5일 예보 → 하산식 맛집 추천
```

- **100대 명산** 전체 수록 — 높이·위치·대표 코스 포함
- **기상청 산악날씨 API** 연동 — mtId 기반 5일 3시간 간격 예보 (mtId 없는 산은 단기예보 fallback)
- **네이버 카페 우선 검색** — [윈드스토퍼](https://cafe.naver.com/windstopper) · [등산의정석](https://cafe.naver.com/hikingf) 실제 후기 반영
- **하산식 맛집 추천** — 등산 후 근처 식당을 naver-map 스킬 또는 웹 검색으로 안내
- 설치형 패키지: [`korean-mountain-hiking.skill`](korean-mountain-hiking.skill)

자세한 설정·사용법은 [korean-mountain-hiking/README.md](korean-mountain-hiking/README.md) 참고.
