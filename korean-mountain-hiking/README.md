# korean-mountain-hiking

Claude Agent Skill — 한국 등산 코스 안내 + 기상청 산악날씨 + 하산식 맛집

## 기능

- **등산 코스**: 산림청 100대 명산 전체 데이터 포함 (100개 산, 높이·위치·rank)
- **날씨**: 기상청 산악날씨 API(mtId 기반, 최대 5일) + 단기예보 fallback
- **하산식 맛집**: 하산 지점 근처 식당 자동 조회 및 추천
- **자동 추가**: 데이터셋에 없는 산은 산림청/웹 검색으로 자동 보완

## 데이터 현황

- 총 100개 산 (`references/mountains.json`)
- 코스 상세 데이터: 19개 산
- 기상청 mtId 보유: 20개 산
- 출처: 산림청 선정 100대 명산 eBook (2024), 국립공원공단, 기상청

## 설치 (Agent Skills)

```bash
npx skills add aruesoft/Craig-Skill --path korean-mountain-hiking
```

## 사용 예시

- "북한산 등산 코스 알려줘"
- "이번 주 토요일 월출산 날씨랑 코스 알려줘"
- "지리산 중산리 하산 후 맛집 추천해줘"
- "설악산 오색 코스 소요시간이랑 다음 주 날씨"
- "100대 명산 태백산 정보 알려줘"

## 라이선스

MIT
