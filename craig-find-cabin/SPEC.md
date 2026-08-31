# craig-find-cabin — KNPS 대피소 취소표 감시·선점 시스템 설계

작성: 2026-08-31. 스파이크(실사이트 정찰) 결과 반영.

## 1. 목표

- 국립공원 예약시스템(reservation.knps.or.kr)에서 **설악산 소청대피소 2026-10-15, 양폭대피소 2026-10-16**의 취소표(예약가능/대기가능 전환)를 3분 주기로 감시한다.
- 상태 변화 시 즉시 텔레그램 알림. 예약가능/대기가능이 뜨면 **캡차 릴레이**로 수 초~수 분 내 선점한다.
- 감시 대상(대피소·날짜·인원)은 설정 파일로 추가/변경 가능하다.
- 2시간마다 하트비트 보고, 상태 페이지(5분 내 갱신) 제공.
- 운영은 맥북 에어 서버 launchd 상시가동(기존 봇 3종과 동일 컨벤션).

인원: 기본 5명. 잔여가 1 이상이면 알림·선점 시도, 예약 인원 = min(설정 인원, 잔여).

## 2. 스파이크로 확인된 사이트 동작 (2026-08-31 기준)

### 2-1. 캘린더 조회 — 익명 HTTP로 가능

```
POST https://reservation.knps.or.kr/reservation/shelter/tabShelter.do
Content-Type: application/x-www-form-urlencoded
deptId=B03&deptNm=설악산&isGreenpoint=N
```

- 로그인·NetFunnel 없이 200 OK (~590KB HTML). 설악산 4개 대피소 × 61일(오늘~약 2달) 상태 셀 244개.
- 브라우저 UI는 NetFunnel(대기열)을 거치지만 직접 호출엔 현재 불필요. 차단될 경우를 대비해 NetFunnel 핸드셰이크 폴백을 에러 처리에 남겨둔다.
- 공원 탭: 지리산 B01, 설악산 B03, 덕유산 B05, 소백산 B12.

### 2-2. 셀 마크업 (파싱 대상)

```html
<i class="icon-reservation" title="예약가능:11" data-reser_tp="R"
   data-park-nm="설악산" data-fclt-nm="수렴동대피소" data-dept-id="B031001"
   data-prd-id="SB03100101001" data-use_dt="20261015" data-max_cnt="16"
   data-price="30000" data-crtr-dow="목" data-rsvt-cnt="11" ...>
<i class="icon-waiting"  ... data-reser_tp="W" ...>          <!-- 대기가능 -->
<i class="icon-none-reservation" title="예약만료"></i>        <!-- 매진: data 속성 없음 -->
```

- 매진 셀은 데이터 속성이 없으므로 **행 순서(대피소) × 열 순서(날짜)** 로 위치 매핑해 판정한다.
- 대기예약은 별도 상품(prd-id 끝자리 다름, 예: 소청 예약 `SB03100201001` / 대기 `SB03100201002`, 대기 정원 별도 max_cnt).
- 예약/대기 셀이 나타나면 예약 제출에 필요한 모든 데이터가 셀에 실려 오므로 **prd-id 하드코딩 불필요**.

### 2-3. 로그인 — requests로 가능, 캡차 없음

```
GET  /mmb/mmbLogin.do            → loginForm hidden 필드 수집 (loginType=Member)
POST /mmb/mmbLoginProc.do        (mmbId, passWd, hidden) → 302 /
GET  /reservation/auth.do        → 로그인 상태면 200 {}, 아니면 401
```

### 2-4. 예약 제출 — 숫자 캡차 필수

```
GET  /reserCaptcha.do?dummy={ts}     → 100×50 PNG 숫자 캡차 (세션 바인딩)
POST /common/shelter/createReservation.do   Content-Type: application/json
{
  "prds": [{
    "reserTp": "R"|"W", "prdId", "deptId", "useDt", "useBgnDtm"(=useDt),
    "crtrDow", "parkNm", "updNm", "fcltNm", "prdNm"(=fcltNm),
    "areaCode"(=prdId[1:5]), "salAmt"(=price×인원), "rsrvtQntt"(인원),
    "price"(콤마 포맷)
  }],
  "captcha": "1234", "wtngCancel": "", "isGreenpoint": "N"
}
→ {result:'Y', sttlmMtDtms:[{reserTp, prdNm, sttlmMtDtm2(결제만기)}]} 또는 오류 메시지
```

- 예약(R) 성공 시 결제 만기일시가 반환되고 **미결제 시 자동 취소**된다. 결제는 사람이 직접(설계 원칙).
- 대기(W)는 "대기신청 완료"로 끝난다.
- 캡차는 자동 해독하지 않는다(정책). 아래 캡차 릴레이로 사람이 입력.

### 2-5. 현재 목표 날짜 상태

- 소청 10/15: 예약만료(대기 정원 5명도 마감), 양폭 10/16: 예약만료 → **취소표 감시 전략**.
- 참고: 수렴동 10/15 잔여 11, 10/16 잔여 8 (대안 후보).

## 3. 구조

```
craig-find-cabin/
├── SPEC.md              # 이 문서
├── knps.py              # KNPS 클라이언트 (조회·파싱·로그인·예약제출) — 부수효과 없는 라이브러리
├── monitor.py           # 데몬 진입점: 폴링 루프 + 텔레그램 long-poll + 캡차 릴레이 상태머신
├── notify.py            # 알림 추상화 (telegram 필수, kakao 선택)
├── status_page.py       # status.json + index.html 생성
├── targets.json         # 감시 대상 (git 추적, 비밀값 없음)
├── status/              # 생성물 (git 무시)
├── deploy/
│   └── com.craig.skill.findcabin.plist
└── logs/                # git 무시
```

- 비밀값은 `~/.config/craig-find-cabin/config.json` (chmod 600, git 밖): `knps_id`, `knps_pw`, `telegram_token`, `telegram_chat_id`, `kakao_enabled`.
- `targets.json` 예:

```json
{ "poll_sec": 180,
  "targets": [
    {"park": "설악산", "dept": "B03", "shelter": "소청대피소", "date": "20261015", "party": 5, "mode": "auto"},
    {"park": "설악산", "dept": "B03", "shelter": "양폭대피소", "date": "20261016", "party": 5, "mode": "auto"}
  ]}
```

- `mode`: `auto`(발견 즉시 캡차 릴레이 시작) / `notify`(알림만).

## 4. 동작 흐름

### 4-1. 폴링 루프 (3분 ± 10초 지터)

1. `tabShelter.do` 익명 조회(대상 공원별 1회) → 244셀 파싱 → 타깃 셀 상태 추출.
2. 직전 상태(state.json)와 비교. 변화 종류:
   - 예약만료 → **예약가능(R)**: 최우선. 즉시 알림 + auto면 캡차 릴레이 시작.
   - 예약만료 → **대기가능(W)**: 알림 + auto면 캡차 릴레이(대기신청).
   - 잔여수 변화, 상태 하락(가능→만료)도 알림.
3. status.json/HTML 갱신. state.json 저장.
4. 조회 실패: 30초 후 1회 재시도, 연속 5회 실패 시 경고 알림 + 백오프(최대 15분). NetFunnel/차단 의심 응답(비HTML, 302, 대기열 페이지)은 별도 로그.

### 4-2. 캡차 릴레이 (선점)

1. 발견 즉시: KNPS 로그인(세션 재사용, auth.do로 검증) → `reserCaptcha.do` 이미지 획득.
2. 텔레그램으로 전송: 캡차 사진 + "소청 10/15 예약가능 3자리! 3명 예약 진행. 캡차 숫자를 답장하세요. (인원 변경: `2 1234` 형식, 건너뛰기: `pass`)"
3. 사용자 답장 수신 → `createReservation.do` 제출 → 결과 통보:
   - 성공(R): "선점 완료! 결제 만기 {일시}. 미결제 시 자동취소 — 지금 결제하세요: https://reservation.knps.or.kr/mypage/dashBoard.do?prdDvcd=S"
   - 성공(W): "대기신청 완료."
   - 캡차 오류/자리 소진: 새 캡차로 1회 재시도 안내, 소진 시 감시 복귀.
4. 릴레이 대기 중에도 폴링은 계속한다. 답장 전 자리가 사라지면 릴레이 취소 알림.
5. 선점 성공한 타깃은 `done` 처리하고 감시 중단(다른 타깃은 계속).

### 4-3. 텔레그램 명령 (동일 long-poll에서 처리)

- `/status` 현재 감시 상태 즉시 보고
- `/targets` 목록, `/add 양폭 20261017 5`, `/remove 양폭 20261017`
- `/pause`, `/resume`
- 캡차 답장: 숫자(4~6자리) 또는 `<인원> <숫자>` 또는 `pass`
- 봇 최초 `/start` 시 chat_id를 config에 자동 저장.

### 4-4. 하트비트·알림 규칙

- 상태 변화: 즉시.
- 하트비트: 2시간마다 "10/15 소청, 10/16 양폭 감시 중 — 변화 없음 (마지막 확인 HH:MM)".
- 채널: 텔레그램 기본. 서버에서 kakaocli 동작 확인되면 카카오 "나에게 보내기"를 하트비트·알림에 병행(캡차 릴레이는 텔레그램 전용).

### 4-5. 상태 페이지

- 매 폴링마다 `status/index.html` + `status.json` 생성(감시 대상별 현재 상태, 잔여수 추이 최근 24h, 마지막 확인 시각, 데몬 헬스).
- 서빙: 서버 기존 dashboard(launchd `com.craig.skill.dashboard`) 디렉토리에 결합하거나, 불가하면 `python -m http.server` 별도 포트 + Tailscale 접근. 외부 도메인 배포는 v2.

## 5. 운영

- launchd `com.craig.skill.findcabin`: `monitor.py --listen`, KeepAlive, stdout/err → logs/. SERVER_SETUP.md에 절차 추가.
- 봇 인스턴스 1개 원칙(신규 토큰이므로 기존 봇과 충돌 없음).
- 의존성: `requests`뿐. Playwright 불필요.
- 개발기에서 `--check` (1회 조회·파싱 출력), `--dry-run` (예약 제출 직전까지) 지원.

## 6. 에러 처리 요약

| 상황 | 대응 |
|---|---|
| 조회 실패/타임아웃 | 30초 후 재시도 → 연속 5회 시 경고 + 백오프 |
| 사이트 구조 변경(파싱 셀 수 급변) | 경고 알림 + 마지막 정상 상태 유지 |
| 로그인 실패 | 즉시 알림(자격증명 확인 요청), 감시는 계속 |
| 캡차 오답 | 새 캡차 이미지로 재릴레이 |
| 릴레이 중 자리 소진 | 취소 알림, 감시 복귀 |
| 텔레그램 API 오류 | 재시도, 로그 |

## 7. 검증 계획

1. `knps.py` 파서 단위 테스트: 스파이크에서 저장한 실제 HTML 고정본으로 244셀·상태 3종·타깃 추출 검증.
2. `--check` 실행으로 라이브 조회 확인.
3. 로그인·캡차 이미지 획득 라이브 확인(제출 없이).
4. **실예약 리허설(사용자 승인 후)**: 잔여 많은 평일 수렴동 1자리를 캡차 릴레이 전체 플로우로 선점 → 결제하지 않고 자동취소되는 것까지 확인. 시스템 신뢰도 확보용.
5. 서버 배포 후 하트비트 2회 수신 확인.

## 8. v2 후보 (이번 범위 아님)

- 외부 도메인 상태 페이지(cabin.craigpark.kr), 다공원 감시, 잔여수 추이 그래프, 카카오 양방향.
