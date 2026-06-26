---
name: korean-mountain-hiking
description: 산 이름을 입력하면 등산 코스·날씨·하산식 맛집을 안내한다. 산림청 100대 명산 100곳 데이터 포함. 날짜가 있으면 기상청 산악날씨(최대 5일)를 조회하고, 하산 후 근처 맛집(하산식 식당)도 함께 추천한다. "북한산 등산 코스", "이번 주말 설악산 날씨", "지리산 하산 후 맛집", "한라산 코스 추천" 같은 요청에 사용한다.
license: MIT
metadata:
  category: outdoor
  locale: ko-KR
  phase: v3
---

# Korean Mountain Hiking

## What this skill does

- 산 이름을 받아 `references/mountains.json`에 정리된 **등산 코스 정보**(코스명, 구간, 길이, 소요시간, 난이도, 추천 여부)를 안내한다.
- **산림청 선정 100대 명산** 전체 데이터(`rank_100`, `height_m`)를 포함하며, 코스 상세 데이터가 있는 산은 표 형태로, 없는 산은 기본 정보(높이, 위치)와 함께 공식 출처 안내를 제공한다.
- 날짜가 함께 주어지면, **기상청 산악날씨 API**(`mtId` 기반, 5일 예보)로 날씨를 조회한다. `mtId`가 없는 산은 `k-skill-proxy` 단기예보(3일)로 대체한다.
- **하산 후 맛집(하산식)**: 요청하면 또는 등산 정보와 함께 자동으로 하산 지점 인근 식당을 `naver-map` 스킬 또는 웹 검색으로 조회해 추천한다.
- 데이터셋에 없는 산이면, 산림청/웹 검색으로 코스 정보를 찾아 정리해 보여주고 `references/mountains.json`에 자동 추가한다.

## When to use

- "북한산 등산 코스 알려줘"
- "설악산 대청봉 코스 길이랑 소요시간 알려줘"
- "지리산 추천 코스 뭐가 있어?"
- "이번 주 토요일 관악산 날씨 어때?"
- "다음 주말 월출산 날씨랑 코스 알려줘"
- "한라산 성판악 코스 정보랑 내일 날씨 같이 알려줘"
- "북한산 등산 후 맛집 추천해줘"
- "설악산 오색 하산 후 뭐 먹을 수 있어?"
- "지리산 중산리 근처 하산식 식당 알려줘"

## Prerequisites

- optional: `jq`
- optional: `KSKILL_PROXY_BASE_URL` (self-host 프록시를 쓸 때만 설정. 비우면 기본 hosted `https://k-skill-proxy.nomadamas.org` 를 사용한다.)
- optional: `naver-map` 스킬 (하산식 맛집 조회 시 활용)
- optional: Chrome MCP (`mcp__Claude_in_Chrome__*`) — 네이버 카페 회원 전용 게시글 접근 시 필요

사용자가 별도 API key를 발급받을 필요는 없다.

## Reference Cafes (등산 커뮤니티 카페)

등산 코스 후기·맛집 정보 조회 시 아래 네이버 카페를 **우선 참조**한다.

| 카페 | URL | 특징 |
|---|---|---|
| 바람막이 (Windstopper) | https://cafe.naver.com/windstopper | 등산 장비·코스 후기, 하산식 맛집 추천 |
| 하이킹F (HikingF) | https://cafe.naver.com/hikingf | 등산 코스·일정·맛집 정보 |

**접근 방식**:
1. **Chrome MCP 연결된 경우**: `mcp__Claude_in_Chrome__navigate`로 해당 카페에 직접 접근 → 로그인 세션 활용해 회원 전용 게시글도 조회 가능.
   - 카페 내 검색: `https://cafe.naver.com/windstopper?search={산 이름}+코스` 형태로 이동
   - 게시글 내용은 `mcp__Claude_in_Chrome__get_page_text`로 추출
2. **Chrome MCP 미연결 또는 로그인 안 된 경우**: 웹 검색으로 `"windstopper" OR "hikingf" {산 이름} 코스` 검색. 공개 게시글이나 구글 캐시에서 내용을 수집한다.

## Data

`references/mountains.json`에 **100개 산** (산림청 100대 명산 + 자동 추가 항목)의 다음 정보가 들어있다.

- `name`, `aliases`: 산 이름과 별칭
- `region`: 위치(시/도)
- `lat`, `lon`: 날씨 조회용 대표 좌표
- `height_m`: 정상 높이(m) — 산림청 100대 명산 eBook 기준
- `rank_100`: 산림청 선정 100대 명산 순번 (가나다 순, 1~100)
- `mtId` (선택): 기상청 산악날씨 관측지점 ID. 있는 경우 공식 산악날씨 API로 최대 5일 예보를 가져온다. (현재 20개 산 보유)
- `courses[]`: 코스별 상세 정보 (현재 19개 산에 상세 데이터 있음)
- `map_url` (선택): 등산 지도 링크

`source: "pdf-100대명산"` 항목은 PDF 데이터 기반으로 추가된 항목이며, 코스 상세 데이터가 없을 수 있다. `source: "auto-search"` 항목은 웹 검색으로 자동 추가된 항목이다.

## Workflow

### 1. 산 이름 매칭

사용자가 입력한 산 이름을 `references/mountains.json`의 `name`/`aliases`와 비교해 매칭한다 (대소문자, "산" 유무, 국립공원 표기 등 약간의 표기 차이는 유연하게 처리).

```bash
jq '.mountains[] | select(.name == "북한산" or (.aliases[]? == "북한산"))' \
  ~/.agents/skills/korean-mountain-hiking/references/mountains.json
```

목록에 없는 산이면 **1.5 데이터셋에 없는 산 처리**로 넘어간다.

### 1.5 데이터셋에 없는 산: 검색 후 자동 추가

1. **코스 정보 검색**: 아래 우선순위로 탐색. 나열된 코스를 **모두** 수집한다.
   1. **네이버 카페 (Chrome MCP 연결 시 우선)**: `cafe.naver.com/windstopper`, `cafe.naver.com/hikingf`에서 "{산 이름} 코스" 검색
   2. 산림청 국가숲길정보시스템(forest.go.kr)
   3. 일반 웹 검색 — `"windstopper" OR "hikingf" {산 이름} 등산코스`로 카페 공개 글 우선 탐색
2. **좌표 확보**: 검색 결과에 없으면 `kakao-map` 스킬로 지오코딩.
3. **등산 지도 검색**: "{산 이름} 등산지도" 검색 → 공식 링크 있으면 `map_url`에 저장.
4. **`references/mountains.json`에 추가**: `source: "auto-search"`, `added_at: "YYYY-MM-DD"` 포함.
   ```bash
   FILE=~/.agents/skills/korean-mountain-hiking/references/mountains.json
   jq --argjson new '{...}' '.mountains += [$new]' "$FILE" > "${FILE}.tmp" && mv "${FILE}.tmp" "$FILE"
   ```
5. 답변에 "웹 검색으로 정리한 정보이며 공식 출처에서 재확인 권장" 안내 포함.

### 2. 등산 코스 정보 정리

매칭된 산의 데이터에 따라:

**코스 상세 데이터 있음** (`courses[]` 비어있지 않은 경우):
```markdown
## {산 이름} 등산 코스 ({region}) — {height_m}m · 100대 명산 #{rank_100}

| 코스 | 구간 | 거리 | 소요시간 | 난이도 | 비고 |
|---|---|---|---|---|---|
| {course.name}{recommended면 " ⭐추천"} | {route} | {length_km}km | {duration} | {difficulty} | {note} |
```

**코스 상세 데이터 없음** (`courses[]` 비어있는 경우):
- 기본 정보(높이, 위치, 100대 명산 순번)를 먼저 보여준다.
- 웹 검색(`{산 이름} 등산코스 소요시간`)으로 코스 정보를 조회해 보여준다.
- 검색으로 찾은 코스를 `references/mountains.json`의 해당 항목 `courses[]`에 업데이트한다.

`recommended: true`인 코스가 있으면 추천 코스임을 명확히 언급한다. `map_url`이 있으면 표 아래에 "등산 지도: {링크}" 형태로 안내한다.

### 3. (날짜가 있으면) 날씨 조회

#### 3-A. 기상청 산악날씨 API (mtId 있는 경우, 우선 시도)

`mtId` 필드가 있으면 기상청 산악날씨 API로 **최대 5일** 예보를 가져온다.

```bash
curl -fsS -k \
  "https://www.weather.go.kr/w/wnuri-fct2021/theme/mountains-forecast.do?mtId={mtId}&hr1=N&unit=m/s" \
  -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36" \
  -H "Referer: https://www.weather.go.kr/w/forecast/life/mountain.do" \
  -H "X-Requested-With: XMLHttpRequest" \
  -o /tmp/mt_fcst.html

cat > /tmp/parse_mt.py << 'SCRIPT'
import re, sys

with open('/tmp/mt_fcst.html') as f:
    html = f.read()

target_date = sys.argv[1] if len(sys.argv) > 1 else ""

daily_pattern = re.compile(
    r'<div class="daily" data-date="(\d{4}-\d{2}-\d{2})"[^>]*>(.*?)</div>\s*</div>\s*</div>',
    re.DOTALL
)

results = []
for m in daily_pattern.finditer(html):
    date = m.group(1)
    section = m.group(2)
    tmin = re.search(r'minval">(\d+)℃', section)
    tmax = re.search(r'maxval">(\d+)℃', section)
    slots = []
    item_pat = re.compile(
        r'data-time="[^"]+".*?<span>(\d+시)</span>.*?title="([^"]+)".*?'
        r'<span class="hid feel">(\d+)℃</span>.*?<span>(\d+%)</span>.*?<span>(\d+%)</span>',
        re.DOTALL
    )
    for sm in item_pat.finditer(section):
        slots.append({
            "time": sm.group(1), "sky": sm.group(2),
            "temp": sm.group(3)+"℃", "pop": sm.group(4)
        })
    results.append({
        "date": date,
        "tmin": tmin.group(1)+"℃" if tmin else "-",
        "tmax": tmax.group(1)+"℃" if tmax else "-",
        "slots": slots
    })

if not results:
    print("NO_DATA")
    sys.exit(0)

dates_available = [r["date"] for r in results]
print("AVAILABLE_DATES:" + ",".join(dates_available))

for r in results:
    if target_date and r["date"] != target_date:
        continue
    print(f"\n📅 {r['date']}  최저:{r['tmin']} 최고:{r['tmax']}")
    for s in r["slots"]:
        print(f"  {s['time']:>4}  {s['sky']:<12}  {s['temp']:>5}  강수확률:{s['pop']}")
SCRIPT

python3 /tmp/parse_mt.py {YYYY-MM-DD 또는 생략}
```

- **API 범위**: 오늘부터 4일 후(5일치) 3시간 간격. 기상청 공식 산악 지점 기준.
- `NO_DATA` 또는 `curl` 실패 시 → **3-B**로 넘어간다.
- `AVAILABLE_DATES`에 요청 날짜 없으면 → **3-C**로 넘어간다.

#### 3-B. 기상청 단기예보 fallback (mtId 없거나 3-A 실패)

```bash
BASE="${KSKILL_PROXY_BASE_URL:-https://k-skill-proxy.nomadamas.org}"
curl -fsS --get "${BASE}/v1/korea-weather/forecast" \
  --data-urlencode "lat={lat}" \
  --data-urlencode "lon={lon}"
```

응답에서 요청 날짜(`fcstDate`)에 해당하는 `TMP`, `SKY`, `PTY`, `POP`, `REH`, `WSD`를 요약한다. 날짜가 범위 밖이면 3-C로 넘어간다.

#### 3-C. 범위 초과 안내

5일 산악날씨 + 3일 단기예보 범위를 모두 벗어난 날짜:
- 추측해서 날씨를 만들어내지 않는다.
- [기상청 산악날씨 페이지](https://www.weather.go.kr/w/forecast/life/mountain.do)에서 직접 확인하라고 안내한다.

### 4. (요청이 있거나 등산 정보와 함께) 하산식 맛집 추천

등산 후 하산식 식당을 추천한다. 사용자가 "맛집", "하산식", "먹을 곳", "식당" 등을 언급하거나, 등산 계획 전반적인 정보를 요청하면 자동으로 포함한다.

1. **하산 지점 파악**: 코스에서 하산 종료 지점(탐방지원센터·주차장 등)을 확인한다.
2. **맛집 검색**: 아래 우선순위로 조회한다.
   - **네이버 카페 (Chrome MCP 연결 시 최우선)**: `cafe.naver.com/windstopper` 또는 `cafe.naver.com/hikingf`에서 "{산 이름} 맛집" 또는 "{산 이름} 하산식" 검색. 실제 등산객의 생생한 후기 위주로 수집.
   - `kakao-map` 스킬 또는 웹 검색: `카카오맵 {하산지점 또는 산 근처 지역} 맛집` 검색
   - 웹 검색: `"windstopper" OR "hikingf" {산 이름} 맛집` 또는 `카카오맵 {산 이름} {근처 읍/면} 하산식 맛집 추천` 검색
3. **결과 필터링 기준 (필수)**:
   - **카카오맵 평점 3.5 이상**인 식당만 포함한다.
   - 평점이 동일하면 **리뷰 수 많은 순**으로 정렬한다.
   - 평점·리뷰 수를 확인할 수 없는 식당은 포함하지 않는다.
4. **결과 정리**: 식당명, 카카오맵 평점, 리뷰 수, 대표 메뉴, 특징을 하산 지점별로 구분해 보여준다.
   ```markdown
   ## 🍽️ 하산 후 맛집 추천 ({하산지점} 근처)
   | 식당명 | 평점 | 리뷰 수 | 메뉴 | 특징 |
   |---|---|---|---|---|
   | {name} | ⭐ {rating} | {review_count}개 | {menu} | {note} |
   ```
5. 검색으로 찾지 못했을 경우 추측해서 식당을 지어내지 말고, 카카오맵 검색 링크(`https://map.kakao.com/?q={하산지점}+맛집`)를 안내한다.

### 5. 답변 구성

다음 섹션을 포함해 정리한다 (해당 섹션만):
1. **등산 코스** — 표 + 지도 링크 + 100대 명산 정보
2. **날씨** — (날짜가 있는 경우) 산악날씨 또는 단기예보 요약
3. **하산식 맛집** — (요청하거나 종합 정보 요청 시) 근처 식당 추천
4. **주의사항** — 탐방로 통제·예약 확인 안내

## Done when

- 요청한 산이 데이터셋에 있는지 확인했다.
- 코스 데이터가 있으면 **모든 코스**를 표로 정리했고, `map_url`이 있으면 지도 링크도 안내했다.
- 코스 데이터가 없으면 웹 검색으로 코스를 조회해 보여줬다.
- 날짜가 주어졌다면 기상청 산악날씨(3-A) 또는 단기예보(3-B) 결과, 또는 범위 초과 시 공식 출처 안내를 포함했다.
- 하산식 맛집 요청이 있거나 종합 정보 요청 시 하산 지점 근처 식당 정보를 조회해 안내했다.
- 최신 통제 정보는 국립공원공단/산림청에서 재확인이 필요함을 언급했다.

## Failure modes

- 데이터셋에도 없고 검색으로도 신뢰할 만한 코스 정보를 못 찾은 산: 추측하지 말고 솔직히 안내하며 국립공원공단/산림청 직접 검색을 권장한다.
- 기상청 산악날씨 API 접근 실패: 3-B 단기예보로 자동 대체한다.
- 맛집 검색 실패: 추측해서 식당을 지어내지 말고, 카카오맵(`https://map.kakao.com/?q={하산지점}+맛집`)에서 직접 검색을 안내한다.
- `pdf-100대명산` 출처 항목 중 코스 데이터가 없는 산: 웹 검색으로 보완하고, 찾은 코스를 JSON에 업데이트한다.
- 등산 지도를 찾지 못한 경우: 링크를 지어내지 말고 생략, 필요시 공식 사이트 안내만 한다.

## Notes

- `mtId`가 있는 산(20곳)은 기상청 공식 산악날씨 API로 5일 예보를 제공한다.
- 기상청 산악날씨 API HTML 파싱은 `/tmp/parse_mt.py`에 저장 후 실행한다 (heredoc과 HTML 내용의 충돌 방지).
- 좌표가 필요한데 검색 결과에 없으면 `kakao-map` 스킬로 지오코딩한다.
- 하산식 맛집 조회는 카카오맵 기준 **평점 3.5 이상 + 리뷰 많은 순**으로 정렬한다. `kakao-map` 스킬 또는 웹 검색을 활용하며, 하산 지점이 명확하지 않으면 산 근처 대표 마을/읍 기준으로 검색한다.
- `references/mountains.json`의 `pdf-100대명산` 항목에서 코스 데이터를 웹 검색으로 보완한 경우, 해당 항목의 `courses[]`를 업데이트하고 `source`를 `"pdf+web"`으로 변경한다.
