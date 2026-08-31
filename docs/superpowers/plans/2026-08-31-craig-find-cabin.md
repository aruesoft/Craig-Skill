# craig-find-cabin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** KNPS 설악산 대피소(소청 2026-10-15, 양폭 2026-10-16)의 취소표를 3분 주기로 감시하고, 자리가 뜨면 텔레그램으로 알리며 캡차 릴레이로 예약을 선점하는 상시 데몬을 만든다.

**Architecture:** 순수 HTTP(`requests`)로 KNPS 예약시스템을 다룬다 — 캘린더 조회는 익명 POST, 예약은 로그인 세션 + 숫자 캡차. 캡차는 자동 해독하지 않고 텔레그램으로 이미지를 보내 사람이 답장한 숫자로 제출한다(human-in-the-loop). 파싱·상태비교·상태페이지 렌더는 부수효과 없는 순수 함수로 분리해 픽스처 기반 단위테스트로 검증하고, 네트워크 클라이언트와 데몬 루프는 `--check`/`--dry-run` CLI로 스모크한다.

**Tech Stack:** Python 3.9+, `requests`(유일한 서드파티 의존성), pytest, launchd(macOS 상시가동), 텔레그램 Bot API.

**Spec:** `craig-find-cabin/SPEC.md`

## Global Constraints

- 작업 디렉토리는 `craig-find-cabin/`. 모든 경로는 이 디렉토리 기준.
- 파이썬 인터프리터: `/Users/craigpark/anaconda3/bin/python3` (pytest 7.4.0, requests 설치 확인됨). 서버도 동일하게 requests 보유.
- 서드파티 의존성은 `requests`만. Playwright·BeautifulSoup 등 금지 — 파싱은 표준 `re`/`html.parser`로.
- 비밀값(`knps_id`, `knps_pw`, `telegram_token`, `telegram_chat_id`)은 `~/.config/craig-find-cabin/config.json`(chmod 600)에서만 읽는다. 코드·저장소·커밋·이 문서에 넣지 않는다.
- 캡차 자동 해독 금지. 예약 제출은 항상 사람이 답장한 캡차 숫자를 사용한다.
- KNPS User-Agent는 `Mozilla/5.0` 계열로 보낸다(빈 UA는 차단 가능).
- 예약 제출(`createReservation.do`)은 실제 예약을 만든다 — 테스트/개발에서는 `--dry-run`으로 제출 직전까지만 수행하고 실제 POST는 사용자 승인 리허설에서만 한다.
- 커밋 메시지는 한국어, 기존 저장소 컨벤션(`feat(find-cabin):` 등) 따름. 커밋 트레일러:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_0112BEoKyVgSvZmGyPsH3nJz
  ```
- 테스트 실행: `cd craig-find-cabin && /Users/craigpark/anaconda3/bin/python3 -m pytest tests/ -v`.

---

## File Structure

```
craig-find-cabin/
├── SPEC.md                      # (기작성)
├── knps.py                      # KNPS 클라이언트: 파싱(순수) + 네트워크(조회/로그인/캡차/예약)
├── config.py                    # config.json / targets.json 로더, Target·Config 데이터클래스
├── notify.py                    # 텔레그램 발송·수신 (send/send_photo/get_updates)
├── watch.py                     # 상태비교 순수함수: compute_state / diff_targets
├── status_page.py               # status.json + index.html 렌더 (순수) + 파일쓰기
├── monitor.py                   # 데몬 진입점: 폴링루프 + 텔레그램 long-poll + 캡차 릴레이 상태머신 + CLI
├── targets.json                 # 감시 대상 (git 추적, 비밀값 없음)
├── deploy/
│   └── com.craig.skill.findcabin.plist
├── tests/
│   ├── fixtures/
│   │   └── seorak_calendar.html # (기저장) 실제 tabShelter.do 응답
│   ├── test_knps_parse.py
│   ├── test_config.py
│   ├── test_watch.py
│   └── test_status_page.py
├── status/                      # 생성물 (git 무시)
└── logs/                        # git 무시
```

**책임 분리:** 순수 로직(`knps.parse_calendar`, `watch`, `status_page.render`, `config` 로더)은 단위테스트로 완전 검증. 네트워크(`knps.KnpsClient`)·데몬(`monitor`)은 CLI 스모크. `notify`는 얇은 API 래퍼.

---

### Task 1: 프로젝트 스캐폴드 + 설정 로더

**Files:**
- Create: `craig-find-cabin/config.py`
- Create: `craig-find-cabin/targets.json`
- Create: `craig-find-cabin/tests/__init__.py` (빈 파일)
- Create: `craig-find-cabin/tests/test_config.py`
- Create: `craig-find-cabin/requirements.txt`

**Interfaces:**
- Produces:
  - `@dataclass Target(park:str, dept:str, shelter:str, date:str, party:int, mode:str)` — `mode`는 `"auto"`|`"notify"`, `date`는 `"YYYYMMDD"`.
  - `@dataclass Config(knps_id:str, knps_pw:str, telegram_token:str, telegram_chat_id:str|None, poll_sec:int)`
  - `load_config(path:str) -> Config` — 파일 없거나 필수키 없으면 `ConfigError` 발생.
  - `load_targets(path:str) -> list[Target]`
  - `save_targets(path:str, targets:list[Target]) -> None` — `/add`,`/remove` 명령이 사용.
  - `class ConfigError(Exception)`
  - 상수 `DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/craig-find-cabin/config.json")`

- [ ] **Step 1: targets.json 작성**

`craig-find-cabin/targets.json`:
```json
{
  "poll_sec": 180,
  "targets": [
    {"park": "설악산", "dept": "B03", "shelter": "소청대피소", "date": "20261015", "party": 5, "mode": "auto"},
    {"park": "설악산", "dept": "B03", "shelter": "양폭대피소", "date": "20261016", "party": 5, "mode": "auto"}
  ]
}
```

- [ ] **Step 2: requirements.txt 작성**

```
requests>=2.28
```

- [ ] **Step 3: 실패 테스트 작성** — `tests/test_config.py`

```python
import json, os, tempfile
import pytest
from config import load_config, load_targets, save_targets, Target, Config, ConfigError

def _write(tmp, name, obj):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    return p

def test_load_targets_parses_all_fields():
    with tempfile.TemporaryDirectory() as tmp:
        p = _write(tmp, "targets.json", {
            "poll_sec": 180,
            "targets": [
                {"park": "설악산", "dept": "B03", "shelter": "소청대피소",
                 "date": "20261015", "party": 5, "mode": "auto"}
            ],
        })
        ts = load_targets(p)
        assert len(ts) == 1
        assert ts[0] == Target("설악산", "B03", "소청대피소", "20261015", 5, "auto")

def test_load_config_reads_secrets():
    with tempfile.TemporaryDirectory() as tmp:
        p = _write(tmp, "config.json", {
            "knps_id": "u", "knps_pw": "p",
            "telegram_token": "t", "telegram_chat_id": "123", "poll_sec": 120,
        })
        c = load_config(p)
        assert c.knps_id == "u" and c.telegram_token == "t"
        assert c.telegram_chat_id == "123" and c.poll_sec == 120

def test_load_config_missing_required_raises():
    with tempfile.TemporaryDirectory() as tmp:
        p = _write(tmp, "config.json", {"knps_id": "u"})  # missing pw/token
        with pytest.raises(ConfigError):
            load_config(p)

def test_config_missing_file_raises():
    with pytest.raises(ConfigError):
        load_config("/nonexistent/config.json")

def test_save_and_reload_targets_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "targets.json")
        save_targets(p, [Target("설악산", "B03", "양폭대피소", "20261016", 5, "auto")])
        ts = load_targets(p)
        assert ts[0].shelter == "양폭대피소" and ts[0].party == 5
```

- [ ] **Step 4: 실패 확인**

Run: `cd craig-find-cabin && /Users/craigpark/anaconda3/bin/python3 -m pytest tests/test_config.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'config'`)

- [ ] **Step 5: config.py 구현**

```python
import json
import os
from dataclasses import dataclass, asdict

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/craig-find-cabin/config.json")
DEFAULT_TARGETS_PATH = os.path.join(os.path.dirname(__file__), "targets.json")


class ConfigError(Exception):
    pass


@dataclass
class Target:
    park: str
    dept: str
    shelter: str
    date: str      # YYYYMMDD
    party: int
    mode: str      # "auto" | "notify"

    def key(self):
        return (self.shelter, self.date)


@dataclass
class Config:
    knps_id: str
    knps_pw: str
    telegram_token: str
    telegram_chat_id: str | None
    poll_sec: int


def load_config(path: str = DEFAULT_CONFIG_PATH) -> Config:
    if not os.path.exists(path):
        raise ConfigError(f"config not found: {path}")
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    for req in ("knps_id", "knps_pw", "telegram_token"):
        if not d.get(req):
            raise ConfigError(f"missing required config key: {req}")
    return Config(
        knps_id=d["knps_id"],
        knps_pw=d["knps_pw"],
        telegram_token=d["telegram_token"],
        telegram_chat_id=(str(d["telegram_chat_id"]) if d.get("telegram_chat_id") else None),
        poll_sec=int(d.get("poll_sec", 180)),
    )


def save_config_chat_id(path: str, chat_id: str) -> None:
    """텔레그램 /start 시 chat_id를 config.json에 병합 저장."""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    d["telegram_chat_id"] = str(chat_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)


def load_targets(path: str = DEFAULT_TARGETS_PATH) -> list[Target]:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return [Target(t["park"], t["dept"], t["shelter"], t["date"],
                   int(t["party"]), t.get("mode", "auto")) for t in d["targets"]]


def load_poll_sec(path: str = DEFAULT_TARGETS_PATH) -> int:
    with open(path, encoding="utf-8") as f:
        return int(json.load(f).get("poll_sec", 180))


def save_targets(path: str, targets: list[Target]) -> None:
    poll = load_poll_sec(path) if os.path.exists(path) else 180
    obj = {"poll_sec": poll, "targets": [asdict(t) for t in targets]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
```

- [ ] **Step 6: 통과 확인**

Run: `cd craig-find-cabin && /Users/craigpark/anaconda3/bin/python3 -m pytest tests/test_config.py -v`
Expected: PASS (5 tests)

- [ ] **Step 7: 커밋**

```bash
cd /Users/craigpark/Github/Craig-Skill
git add craig-find-cabin/config.py craig-find-cabin/targets.json craig-find-cabin/requirements.txt craig-find-cabin/tests/__init__.py craig-find-cabin/tests/test_config.py
git commit -m "feat(find-cabin): 설정·감시대상 로더(config.py) + 스캐폴드"
```

---

### Task 2: 캘린더 파서 (순수 함수, 픽스처 검증)

**Files:**
- Create: `craig-find-cabin/knps.py` (파싱 부분만; 네트워크는 Task 4)
- Create: `craig-find-cabin/tests/test_knps_parse.py`
- Test fixture (기저장): `craig-find-cabin/tests/fixtures/seorak_calendar.html`

**Interfaces:**
- Consumes: 없음.
- Produces:
  - `@dataclass Cell` — 필드: `fclt_nm:str, use_dt:str, reser_tp:str('R'|'W'), rsvt_cnt:int, max_cnt:int, price:int, prd_id:str, dept_id:str, crtr_dow:str, park_nm:str, upd_nm:str`.
  - `parse_calendar(html:str) -> dict[tuple[str,str], Cell]` — 키 `(fclt_nm, use_dt)`. `icon-reservation`(R)·`icon-waiting`(W) 셀만 담는다. `icon-none-reservation`(매진)은 데이터 속성이 없으므로 딕셔너리에 없음 = 매진.

**픽스처 기대값(스파이크 확인):** available 셀 114개(예약가능 104 + 대기가능 10), 전부 `fclt-nm`·`use_dt` 보유, `(fclt,use_dt)` 중복 없음. 매진 셀 130개는 제외. 목표 `(소청대피소,20261015)`·`(양폭대피소,20261016)`는 딕셔너리에 **없다**(현재 매진). 존재하는 예: `(수렴동대피소,20261015)` rsvt_cnt=11, `(수렴동대피소,20261016)` rsvt_cnt=8.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_knps_parse.py`

```python
import os
from knps import parse_calendar, Cell

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "seorak_calendar.html")

def _load():
    with open(FIX, encoding="utf-8") as f:
        return parse_calendar(f.read())

def test_parses_only_available_cells():
    cal = _load()
    assert len(cal) == 114  # 예약가능 104 + 대기가능 10

def test_soldout_target_absent():
    cal = _load()
    assert ("소청대피소", "20261015") not in cal
    assert ("양폭대피소", "20261016") not in cal

def test_available_cell_fields():
    cal = _load()
    c = cal[("수렴동대피소", "20261015")]
    assert c.reser_tp == "R"
    assert c.rsvt_cnt == 11
    assert c.max_cnt == 16
    assert c.price == 30000
    assert c.dept_id == "B031001"
    assert c.prd_id == "SB03100101001"
    assert c.park_nm == "설악산"

def test_waiting_cell_marked_W():
    cal = _load()
    c = cal[("소청대피소", "20260907")]  # 스파이크 확인된 대기 셀
    assert c.reser_tp == "W"

def test_all_keys_unique_and_typed():
    cal = _load()
    for (fclt, dt), c in cal.items():
        assert isinstance(c.rsvt_cnt, int)
        assert len(dt) == 8 and dt.isdigit()
        assert c.fclt_nm == fclt
```

- [ ] **Step 2: 실패 확인**

Run: `cd craig-find-cabin && /Users/craigpark/anaconda3/bin/python3 -m pytest tests/test_knps_parse.py -v`
Expected: FAIL (`ImportError: cannot import name 'parse_calendar'`)

- [ ] **Step 3: knps.py 파서 구현**

```python
import re
from dataclasses import dataclass

_ICON_RE = re.compile(r'<i class="(icon-(?:reservation|waiting))"([^>]*)>')
_ATTR_RE = re.compile(r'data-([\w_-]+)="([^"]*)"')


@dataclass
class Cell:
    fclt_nm: str
    use_dt: str
    reser_tp: str      # 'R' 예약가능 | 'W' 대기가능
    rsvt_cnt: int
    max_cnt: int
    price: int
    prd_id: str
    dept_id: str
    crtr_dow: str
    park_nm: str
    upd_nm: str


def _to_int(v, default=0):
    try:
        return int(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return default


def parse_calendar(html: str) -> dict:
    """tabShelter.do HTML → {(fclt_nm, use_dt): Cell}. 예약가능/대기가능 셀만."""
    result = {}
    for icon_cls, attr_str in _ICON_RE.findall(html):
        d = dict(_ATTR_RE.findall(attr_str))
        fclt = d.get("fclt-nm")
        use_dt = d.get("use_dt")
        if not fclt or not use_dt:
            continue
        cell = Cell(
            fclt_nm=fclt,
            use_dt=use_dt,
            reser_tp=d.get("reser_tp", "R"),
            rsvt_cnt=_to_int(d.get("rsvt-cnt")),
            max_cnt=_to_int(d.get("max_cnt")),
            price=_to_int(d.get("price")),
            prd_id=d.get("prd-id", ""),
            dept_id=d.get("dept-id", ""),
            crtr_dow=d.get("crtr-dow", ""),
            park_nm=d.get("park-nm", ""),
            upd_nm=d.get("upd-nm", ""),
        )
        result[(fclt, use_dt)] = cell
    return result
```

- [ ] **Step 4: 통과 확인**

Run: `cd craig-find-cabin && /Users/craigpark/anaconda3/bin/python3 -m pytest tests/test_knps_parse.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: 커밋**

```bash
cd /Users/craigpark/Github/Craig-Skill
git add craig-find-cabin/knps.py craig-find-cabin/tests/test_knps_parse.py
git commit -m "feat(find-cabin): 캘린더 파서 parse_calendar + Cell (픽스처 검증)"
```

---

### Task 3: 상태 비교 로직 (순수 함수)

**Files:**
- Create: `craig-find-cabin/watch.py`
- Create: `craig-find-cabin/tests/test_watch.py`

**Interfaces:**
- Consumes: `config.Target`, `knps.Cell`.
- Produces:
  - `@dataclass Slot(status:str, rsvt:int)` — `status`는 `"R"`|`"W"`|`"none"`.
  - `compute_state(targets:list[Target], calendar:dict) -> dict[tuple[str,str], Slot]` — 각 타깃 키에 대해 현재 slot. calendar에 있으면 그 reser_tp/rsvt, 없으면 `Slot("none",0)`.
  - `@dataclass Event(target:Target, kind:str, prev:Slot, curr:Slot, cell)` — `kind`: `"became_available"`|`"became_waiting"`|`"rsvt_changed"`|`"became_soldout"`. `cell`은 현재 available이면 `knps.Cell`, 아니면 `None`.
  - `diff_targets(targets, calendar, prev_state:dict) -> tuple[list[Event], dict]` — 이벤트 목록과 새 state 반환. `prev_state`가 비어있으면(최초 기동) available 타깃에 대해 `became_available`/`became_waiting` 이벤트를 낸다(첫 스냅샷도 알림).

**이벤트 규칙:**
- prev none/W → curr R: `became_available`
- prev none → curr W: `became_waiting`
- prev R → curr R, rsvt 변화: `rsvt_changed`
- prev R/W → curr none: `became_soldout`
- 그 외 동일: 이벤트 없음.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_watch.py`

```python
from config import Target
from knps import Cell
from watch import compute_state, diff_targets, Slot

T_SO = Target("설악산", "B03", "소청대피소", "20261015", 5, "auto")
T_YP = Target("설악산", "B03", "양폭대피소", "20261016", 5, "auto")

def _cell(fclt, dt, tp="R", rsvt=3):
    return Cell(fclt, dt, tp, rsvt, 16, 30000, "PRD", "B031002", "수", "설악산", "설악산")

def test_compute_state_soldout_when_absent():
    st = compute_state([T_SO], {})
    assert st[("소청대피소", "20261015")] == Slot("none", 0)

def test_compute_state_available():
    cal = {("소청대피소", "20261015"): _cell("소청대피소", "20261015", "R", 4)}
    st = compute_state([T_SO], cal)
    assert st[("소청대피소", "20261015")] == Slot("R", 4)

def test_first_snapshot_available_emits_event():
    cal = {("소청대피소", "20261015"): _cell("소청대피소", "20261015", "R", 2)}
    events, _ = diff_targets([T_SO], cal, {})
    assert len(events) == 1
    assert events[0].kind == "became_available"
    assert events[0].cell.rsvt_cnt == 2

def test_soldout_to_available_transition():
    prev = {("소청대피소", "20261015"): Slot("none", 0)}
    cal = {("소청대피소", "20261015"): _cell("소청대피소", "20261015", "R", 1)}
    events, new = diff_targets([T_SO], cal, prev)
    assert [e.kind for e in events] == ["became_available"]
    assert new[("소청대피소", "20261015")] == Slot("R", 1)

def test_none_to_waiting():
    prev = {("소청대피소", "20261015"): Slot("none", 0)}
    cal = {("소청대피소", "20261015"): _cell("소청대피소", "20261015", "W", 5)}
    events, _ = diff_targets([T_SO], cal, prev)
    assert events[0].kind == "became_waiting"

def test_rsvt_changed():
    prev = {("소청대피소", "20261015"): Slot("R", 4)}
    cal = {("소청대피소", "20261015"): _cell("소청대피소", "20261015", "R", 2)}
    events, _ = diff_targets([T_SO], cal, prev)
    assert events[0].kind == "rsvt_changed"

def test_available_to_soldout():
    prev = {("양폭대피소", "20261016"): Slot("R", 1)}
    events, new = diff_targets([T_YP], {}, prev)
    assert events[0].kind == "became_soldout"
    assert new[("양폭대피소", "20261016")] == Slot("none", 0)

def test_no_change_no_event():
    prev = {("소청대피소", "20261015"): Slot("none", 0)}
    events, _ = diff_targets([T_SO], {}, prev)
    assert events == []
```

- [ ] **Step 2: 실패 확인**

Run: `cd craig-find-cabin && /Users/craigpark/anaconda3/bin/python3 -m pytest tests/test_watch.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'watch'`)

- [ ] **Step 3: watch.py 구현**

```python
from dataclasses import dataclass
from config import Target


@dataclass
class Slot:
    status: str   # "R" | "W" | "none"
    rsvt: int


@dataclass
class Event:
    target: Target
    kind: str     # became_available | became_waiting | rsvt_changed | became_soldout
    prev: Slot
    curr: Slot
    cell: object  # knps.Cell | None


def compute_state(targets, calendar) -> dict:
    state = {}
    for t in targets:
        cell = calendar.get(t.key())
        if cell is None:
            state[t.key()] = Slot("none", 0)
        else:
            state[t.key()] = Slot(cell.reser_tp, cell.rsvt_cnt)
    return state


def diff_targets(targets, calendar, prev_state: dict):
    events = []
    new_state = compute_state(targets, calendar)
    for t in targets:
        k = t.key()
        prev = prev_state.get(k, Slot("none", 0)) if prev_state else Slot("none", 0)
        curr = new_state[k]
        cell = calendar.get(k)
        kind = None
        if curr.status == "R" and prev.status != "R":
            kind = "became_available"
        elif curr.status == "W" and prev.status not in ("R", "W"):
            kind = "became_waiting"
        elif curr.status == "R" and prev.status == "R" and curr.rsvt != prev.rsvt:
            kind = "rsvt_changed"
        elif curr.status == "none" and prev.status in ("R", "W"):
            kind = "became_soldout"
        if kind:
            events.append(Event(t, kind, prev, curr, cell))
    return events, new_state
```

- [ ] **Step 4: 통과 확인**

Run: `cd craig-find-cabin && /Users/craigpark/anaconda3/bin/python3 -m pytest tests/test_watch.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: 커밋**

```bash
cd /Users/craigpark/Github/Craig-Skill
git add craig-find-cabin/watch.py craig-find-cabin/tests/test_watch.py
git commit -m "feat(find-cabin): 상태비교 순수함수 compute_state/diff_targets"
```

---

### Task 4: KNPS 네트워크 클라이언트 (조회·로그인·캡차·예약)

**Files:**
- Modify: `craig-find-cabin/knps.py` (파서 아래에 `KnpsClient` 추가)

**Interfaces:**
- Consumes: `parse_calendar`, `Cell`.
- Produces:
  - `class KnpsClient` — 생성자 `KnpsClient()` (내부 `requests.Session`, UA 설정).
    - `fetch_calendar(dept_id:str, dept_nm:str) -> dict` — 익명 POST `tabShelter.do`, `parse_calendar` 결과 반환. 비HTML/오류 시 `KnpsError`.
    - `login(mmb_id:str, passwd:str) -> None` — 실패 시 `KnpsError`.
    - `is_authenticated() -> bool` — GET `auth.do` == 200.
    - `get_captcha() -> bytes` — GET `reserCaptcha.do?dummy={ts}`, PNG bytes.
    - `submit_reservation(cell:Cell, party:int, captcha:str) -> ReservationResult` — POST `createReservation.do`.
  - `@dataclass ReservationResult(success:bool, reser_tp:str, prd_nm:str, payment_deadline:str|None, message:str)`
  - `class KnpsError(Exception)`
  - 모듈 상수 `BASE = "https://reservation.knps.or.kr"`
- **주의:** 이 태스크는 네트워크 부수효과가 있어 단위테스트 대신 Step의 `--probe` 스크립트로 스모크한다. `submit_reservation`은 스모크에서 호출하지 않는다(실예약 방지).

- [ ] **Step 1: KnpsClient 구현** — `knps.py`에 추가

```python
import time
import requests

BASE = "https://reservation.knps.or.kr"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_LOGIN_FORM_RE = re.compile(r'<form[^>]*name="loginForm".*?</form>', re.S)
_HIDDEN_RE = re.compile(r'name="(\w+)"[^>]*value="([^"]*)"')


class KnpsError(Exception):
    pass


@dataclass
class ReservationResult:
    success: bool
    reser_tp: str
    prd_nm: str
    payment_deadline: str | None
    message: str


def _comma(n: int) -> str:
    return f"{n:,}"


class KnpsClient:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = _UA

    def fetch_calendar(self, dept_id: str, dept_nm: str) -> dict:
        try:
            r = self.s.post(
                f"{BASE}/reservation/shelter/tabShelter.do",
                data={"deptId": dept_id, "deptNm": dept_nm, "isGreenpoint": "N"},
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=20,
            )
        except requests.RequestException as e:
            raise KnpsError(f"calendar request failed: {e}") from e
        if r.status_code != 200 or "icon-" not in r.text:
            raise KnpsError(f"unexpected calendar response: HTTP {r.status_code}, {len(r.text)}B")
        return parse_calendar(r.text)

    def login(self, mmb_id: str, passwd: str) -> None:
        r = self.s.get(f"{BASE}/mmb/mmbLogin.do", timeout=20)
        m = _LOGIN_FORM_RE.search(r.text)
        hidden = dict(_HIDDEN_RE.findall(m.group(0))) if m else {"loginType": "Member"}
        hidden.pop("mmbId", None)
        hidden.pop("passWd", None)
        data = {**hidden, "mmbId": mmb_id, "passWd": passwd}
        r2 = self.s.post(f"{BASE}/mmb/mmbLoginProc.do", data=data,
                         allow_redirects=False, timeout=20)
        if r2.status_code not in (301, 302) or not self.is_authenticated():
            raise KnpsError(f"login failed: HTTP {r2.status_code}")

    def is_authenticated(self) -> bool:
        try:
            r = self.s.get(f"{BASE}/reservation/auth.do", timeout=20)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def get_captcha(self) -> bytes:
        r = self.s.get(f"{BASE}/reserCaptcha.do?dummy={int(time.time()*1000)}", timeout=20)
        if r.status_code != 200 or not r.content:
            raise KnpsError(f"captcha fetch failed: HTTP {r.status_code}")
        return r.content

    def submit_reservation(self, cell: Cell, party: int, captcha: str) -> ReservationResult:
        qty = min(party, cell.rsvt_cnt) if cell.rsvt_cnt else party
        sal = cell.price * qty
        prd = {
            "reserTp": cell.reser_tp,
            "prdId": cell.prd_id,
            "deptId": cell.dept_id,
            "useDt": cell.use_dt,
            "useBgnDtm": cell.use_dt,
            "crtrDow": cell.crtr_dow,
            "parkNm": cell.park_nm,
            "updNm": cell.upd_nm,
            "fcltNm": cell.fclt_nm,
            "prdNm": cell.fclt_nm,
            "areaCode": cell.prd_id[1:5],
            "salAmt": sal,
            "rsrvtQntt": qty,
            "price": _comma(sal),
        }
        payload = {"prds": [prd], "captcha": captcha, "wtngCancel": "", "isGreenpoint": "N"}
        try:
            r = self.s.post(
                f"{BASE}/common/shelter/createReservation.do",
                json=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
                timeout=30,
            )
            dat = r.json()
        except (requests.RequestException, ValueError) as e:
            raise KnpsError(f"reservation request failed: {e}") from e

        if dat.get("result") == "Y":
            deadline, tp, prdnm = None, cell.reser_tp, cell.fclt_nm
            for item in dat.get("sttlmMtDtms", []):
                tp = item.get("reserTp", tp)
                prdnm = item.get("prdNm", prdnm)
                if item.get("reserTp") != "W":
                    deadline = item.get("sttlmMtDtm2")
            return ReservationResult(True, tp, prdnm, deadline,
                                     "대기신청 완료" if tp == "W" else "예약 선점 완료")
        return ReservationResult(False, cell.reser_tp, cell.fclt_nm, None,
                                 dat.get("resultMsg") or dat.get("message") or "예약 실패")
```

- [ ] **Step 2: 파서 회귀 확인 (기존 테스트 깨지지 않음)**

Run: `cd craig-find-cabin && /Users/craigpark/anaconda3/bin/python3 -m pytest tests/test_knps_parse.py -v`
Expected: PASS (5 tests) — 파서는 그대로.

- [ ] **Step 3: 라이브 스모크 스크립트 실행 (제출 없음)**

Run:
```bash
cd craig-find-cabin && /Users/craigpark/anaconda3/bin/python3 -c "
from knps import KnpsClient
c = KnpsClient()
cal = c.fetch_calendar('B03', '설악산')
print('calendar cells:', len(cal))
print('수렴동 10/15:', cal.get(('수렴동대피소','20261015')))
"
```
Expected: `calendar cells:` 100+ 출력, 수렴동 10/15 Cell 출력. (로그인·캡차·제출은 monitor 스모크에서 config 사용해 확인.)

- [ ] **Step 4: 커밋**

```bash
cd /Users/craigpark/Github/Craig-Skill
git add craig-find-cabin/knps.py
git commit -m "feat(find-cabin): KnpsClient — 조회/로그인/캡차/예약제출"
```

---

### Task 5: 텔레그램 알림·수신 래퍼

**Files:**
- Create: `craig-find-cabin/notify.py`

**Interfaces:**
- Consumes: 없음(토큰·chat_id는 생성자 인자).
- Produces:
  - `class Telegram(token:str, chat_id:str|None)`:
    - `send(text:str) -> None` — sendMessage, 실패 시 예외 삼키고 로깅.
    - `send_photo(png:bytes, caption:str) -> None` — sendPhoto multipart.
    - `get_updates(offset:int|None, timeout:int=30) -> list[dict]` — long-poll getUpdates, 각 update dict 목록 반환.
    - `set_chat_id(chat_id:str)` — 최초 /start 후 갱신.
  - `class TelegramError(Exception)`
- 네트워크 래퍼라 단위테스트 없이 Step 3 스모크로 확인.

- [ ] **Step 1: notify.py 구현**

```python
import requests


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: str | None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id

    def set_chat_id(self, chat_id: str):
        self.chat_id = str(chat_id)

    def send(self, text: str) -> None:
        if not self.chat_id:
            return
        try:
            requests.post(f"{self.base}/sendMessage",
                          data={"chat_id": self.chat_id, "text": text,
                                "disable_web_page_preview": "true"},
                          timeout=20)
        except requests.RequestException as e:
            print(f"[telegram] send failed: {e}")

    def send_photo(self, png: bytes, caption: str) -> None:
        if not self.chat_id:
            return
        try:
            requests.post(f"{self.base}/sendPhoto",
                          data={"chat_id": self.chat_id, "caption": caption},
                          files={"photo": ("captcha.png", png, "image/png")},
                          timeout=20)
        except requests.RequestException as e:
            print(f"[telegram] send_photo failed: {e}")

    def get_updates(self, offset=None, timeout: int = 30) -> list:
        params = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        try:
            r = requests.get(f"{self.base}/getUpdates", params=params,
                             timeout=timeout + 10)
            return r.json().get("result", [])
        except (requests.RequestException, ValueError) as e:
            print(f"[telegram] get_updates failed: {e}")
            return []
```

- [ ] **Step 2: 문법 확인**

Run: `cd craig-find-cabin && /Users/craigpark/anaconda3/bin/python3 -c "import notify; print('ok')"`
Expected: `ok`

- [ ] **Step 3: 라이브 스모크 (config의 토큰 사용, /start 후 chat_id 확보)**

Run:
```bash
cd craig-find-cabin && /Users/craigpark/anaconda3/bin/python3 -c "
from config import load_config
from notify import Telegram
c = load_config()
tg = Telegram(c.telegram_token, c.telegram_chat_id)
ups = tg.get_updates()
print('updates:', len(ups))
if ups:
    print('last chat_id:', ups[-1]['message']['chat']['id'])
"
```
사전조건: 사용자가 텔레그램에서 봇에게 `/start` 전송. Expected: chat_id 출력. (이 chat_id를 config.json에 저장 — Task 8/배포에서.)

- [ ] **Step 4: 커밋**

```bash
cd /Users/craigpark/Github/Craig-Skill
git add craig-find-cabin/notify.py
git commit -m "feat(find-cabin): 텔레그램 발송·수신 래퍼(notify.py)"
```

---

### Task 6: 상태 페이지 렌더 (순수 함수 + 파일쓰기)

**Files:**
- Create: `craig-find-cabin/status_page.py`
- Create: `craig-find-cabin/tests/test_status_page.py`

**Interfaces:**
- Consumes: `config.Target`, `watch.Slot`.
- Produces:
  - `render_json(targets, state, last_check:str, healthy:bool) -> str` — JSON 문자열. 구조:
    `{"updated": last_check, "healthy": bool, "targets": [{"shelter","date","status","rsvt","party"}]}`. `status`는 `"available"|"waiting"|"soldout"`.
  - `render_html(targets, state, last_check:str, healthy:bool) -> str` — 자동 5분 새로고침(`<meta http-equiv="refresh" content="300">`) 정적 HTML.
  - `write_status(out_dir:str, targets, state, last_check:str, healthy:bool) -> None` — `status.json`·`index.html` 기록.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_status_page.py`

```python
import json
from config import Target
from watch import Slot
from status_page import render_json, render_html

TS = [Target("설악산", "B03", "소청대피소", "20261015", 5, "auto")]

def test_render_json_available():
    st = {("소청대피소", "20261015"): Slot("R", 3)}
    out = json.loads(render_json(TS, st, "2026-08-31 15:00", True))
    assert out["healthy"] is True
    assert out["targets"][0]["status"] == "available"
    assert out["targets"][0]["rsvt"] == 3

def test_render_json_soldout():
    st = {("소청대피소", "20261015"): Slot("none", 0)}
    out = json.loads(render_json(TS, st, "2026-08-31 15:00", True))
    assert out["targets"][0]["status"] == "soldout"

def test_render_html_has_refresh_and_shelter():
    st = {("소청대피소", "20261015"): Slot("W", 2)}
    html = render_html(TS, st, "2026-08-31 15:00", True)
    assert 'http-equiv="refresh"' in html
    assert "content=\"300\"" in html
    assert "소청대피소" in html
    assert "대기가능" in html
```

- [ ] **Step 2: 실패 확인**

Run: `cd craig-find-cabin && /Users/craigpark/anaconda3/bin/python3 -m pytest tests/test_status_page.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'status_page'`)

- [ ] **Step 3: status_page.py 구현**

```python
import json
import os

_STATUS_LABEL = {"R": ("available", "예약가능"), "W": ("waiting", "대기가능"),
                 "none": ("soldout", "매진")}


def _rows(targets, state):
    rows = []
    for t in targets:
        slot = state.get(t.key())
        status = slot.status if slot else "none"
        rsvt = slot.rsvt if slot else 0
        code, label = _STATUS_LABEL.get(status, ("soldout", "매진"))
        rows.append({"shelter": t.shelter, "date": t.date, "party": t.party,
                     "status": code, "label": label, "rsvt": rsvt})
    return rows


def render_json(targets, state, last_check: str, healthy: bool) -> str:
    rows = [{k: r[k] for k in ("shelter", "date", "status", "rsvt", "party")}
            for r in _rows(targets, state)]
    return json.dumps({"updated": last_check, "healthy": healthy, "targets": rows},
                      ensure_ascii=False, indent=2)


def render_html(targets, state, last_check: str, healthy: bool) -> str:
    color = {"available": "#1a7f37", "waiting": "#9a6700", "soldout": "#767676"}
    trs = ""
    for r in _rows(targets, state):
        d = r["date"]
        pretty = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        rsvt = f" (잔여 {r['rsvt']})" if r["status"] != "soldout" else ""
        trs += (f'<tr><td>{r["shelter"]}</td><td>{pretty}</td>'
                f'<td style="color:{color[r["status"]]};font-weight:600">'
                f'{r["label"]}{rsvt}</td><td>{r["party"]}명</td></tr>')
    health = "정상" if healthy else "점검 필요"
    return f"""<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>대피소 감시</title>
<style>body{{font-family:-apple-system,sans-serif;max-width:640px;margin:2rem auto;padding:0 1rem}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:.6rem;border-bottom:1px solid #eee;text-align:left}}
.meta{{color:#666;font-size:.9rem}}</style></head><body>
<h1>⛰️ KNPS 대피소 취소표 감시</h1>
<p class="meta">마지막 확인: {last_check} · 데몬: {health} · 5분마다 자동 새로고침</p>
<table><thead><tr><th>대피소</th><th>날짜</th><th>상태</th><th>인원</th></tr></thead>
<tbody>{trs}</tbody></table></body></html>"""


def write_status(out_dir: str, targets, state, last_check: str, healthy: bool) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "status.json"), "w", encoding="utf-8") as f:
        f.write(render_json(targets, state, last_check, healthy))
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_html(targets, state, last_check, healthy))
```

- [ ] **Step 4: 통과 확인**

Run: `cd craig-find-cabin && /Users/craigpark/anaconda3/bin/python3 -m pytest tests/test_status_page.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: 커밋**

```bash
cd /Users/craigpark/Github/Craig-Skill
git add craig-find-cabin/status_page.py craig-find-cabin/tests/test_status_page.py
git commit -m "feat(find-cabin): 상태페이지 렌더(status_page.py) — json+html 5분갱신"
```

---

### Task 7: 데몬 (폴링 루프 + 캡차 릴레이 상태머신 + CLI)

**Files:**
- Create: `craig-find-cabin/monitor.py`

**Interfaces:**
- Consumes: `config`(load_config/load_targets/save_targets/save_config_chat_id/DEFAULT_*_PATH), `knps.KnpsClient`/`KnpsError`, `notify.Telegram`, `watch`(diff_targets/compute_state/Slot), `status_page.write_status`.
- Produces: CLI 진입점.
  - `monitor.py --check` — 1회 조회·파싱 후 각 타깃 상태 출력(텔레그램·예약 없음).
  - `monitor.py --listen` — 상시 데몬: 폴링 + 텔레그램 long-poll + 캡차 릴레이.
  - `monitor.py --dry-run` — `--listen`과 동일하되 `submit_reservation` 호출 직전 payload를 로깅하고 실제 제출은 생략.

**상태머신(캡차 릴레이):** 모듈 전역 `PENDING`(dict: chat대기중 릴레이). available 이벤트(auto 모드) 발생 시:
1. `KnpsClient.login`(세션 없으면) → `get_captcha()` → 텔레그램 `send_photo`로 캡차 + 안내.
2. `PENDING[target.key()] = {"cell": cell, "party": party, "target": t}` 저장, `awaiting_captcha=True`.
3. 텔레그램 텍스트 응답(숫자 4~8자리) 수신 → `submit_reservation(cell, party, captcha)` → 결과 통보. 성공 시 해당 타깃 `done` 처리(이후 폴링 제외). `pass` 수신 시 릴레이 취소.
4. 릴레이 대기 중 폴링은 계속. 대기 중 자리 소진(`became_soldout`) 시 릴레이 취소 알림.

**설계 노트:** 폴링과 텔레그램 long-poll을 한 프로세스에서 처리하되, 텔레그램 `get_updates`는 짧은 timeout(예: 5초)으로 논블로킹에 가깝게 돌리고, 마지막 폴링 이후 경과가 `poll_sec` 이상이면 캘린더 폴링을 수행하는 단일 루프로 구성한다(스레드 불필요). 하트비트는 마지막 발송 이후 2시간 경과 시.

- [ ] **Step 1: monitor.py 구현**

```python
import argparse
import sys
import time
import traceback

from config import (load_config, load_targets, save_targets, save_config_chat_id,
                    DEFAULT_CONFIG_PATH, DEFAULT_TARGETS_PATH, Target, ConfigError)
from knps import KnpsClient, KnpsError
from notify import Telegram
from watch import diff_targets, compute_state, Slot
import status_page

STATUS_DIR = "status"
HEARTBEAT_SEC = 2 * 3600
CAPTCHA_RE = None  # set in main via re


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_date(d):
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def _event_message(ev):
    d = _fmt_date(ev.target.date)
    if ev.kind == "became_available":
        return (f"🎉 예약가능! {ev.target.shelter} {d} — 잔여 {ev.curr.rsvt}자리\n"
                f"예약 선점을 시작합니다. 캡차 숫자를 답장하세요.")
    if ev.kind == "became_waiting":
        return (f"🕓 대기가능! {ev.target.shelter} {d} — 대기 {ev.curr.rsvt}\n"
                f"대기신청을 시작합니다. 캡차 숫자를 답장하세요.")
    if ev.kind == "rsvt_changed":
        return f"ℹ️ {ev.target.shelter} {d} 잔여 변화: {ev.prev.rsvt} → {ev.curr.rsvt}"
    if ev.kind == "became_soldout":
        return f"⚠️ {ev.target.shelter} {d} 자리 소진(매진 전환)."
    return ""


class Daemon:
    def __init__(self, cfg, targets, dry_run=False):
        self.cfg = cfg
        self.targets = targets
        self.dry_run = dry_run
        self.tg = Telegram(cfg.telegram_token, cfg.telegram_chat_id)
        self.knps = KnpsClient()
        self.state = {}
        self.done = set()          # 선점 완료된 target key
        self.pending = {}          # target.key() -> {"cell","party","target"} 캡차 대기
        self.last_poll = 0.0
        self.last_heartbeat = time.time()
        self.tg_offset = None
        self.fail_streak = 0

    def active_targets(self):
        return [t for t in self.targets if t.key() not in self.done]

    def poll(self):
        depts = {(t.park, t.dept) for t in self.active_targets()}
        calendar = {}
        for park, dept in depts:
            calendar.update(self.knps.fetch_calendar(dept, park))
        events, self.state = diff_targets(self.active_targets(), calendar, self.state)
        for ev in events:
            self.handle_event(ev, calendar)
        status_page.write_status(STATUS_DIR, self.targets, self.state, _now(), True)
        self.fail_streak = 0

    def handle_event(self, ev, calendar):
        self.tg.send(_event_message(ev))
        k = ev.target.key()
        if ev.kind == "became_soldout" and k in self.pending:
            del self.pending[k]
            self.tg.send(f"릴레이 취소: {ev.target.shelter} {_fmt_date(ev.target.date)} 소진.")
            return
        if ev.kind in ("became_available", "became_waiting") and ev.target.mode == "auto":
            self.start_relay(ev.target, ev.cell)

    def start_relay(self, target, cell):
        try:
            if not self.knps.is_authenticated():
                self.knps.login(self.cfg.knps_id, self.cfg.knps_pw)
            png = self.knps.get_captcha()
        except KnpsError as e:
            self.tg.send(f"⚠️ 로그인/캡차 실패: {e}")
            return
        self.pending[target.key()] = {"cell": cell, "party": target.party, "target": target}
        qty = min(target.party, cell.rsvt_cnt) if cell.rsvt_cnt else target.party
        self.tg.send_photo(png,
            f"{target.shelter} {_fmt_date(target.date)} {qty}명 예약. "
            f"캡차 숫자를 답장하세요. (인원변경: '2 1234', 취소: 'pass')")

    def handle_captcha_reply(self, text):
        if not self.pending:
            return
        text = text.strip()
        if text.lower() == "pass":
            self.pending.clear()
            self.tg.send("릴레이를 취소했습니다.")
            return
        parts = text.split()
        qty_override = None
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            qty_override, captcha = int(parts[0]), parts[1]
        elif len(parts) == 1 and parts[0].isdigit():
            captcha = parts[0]
        else:
            return  # 캡차 형식 아님 — 명령으로 처리
        key = next(iter(self.pending))
        job = self.pending.pop(key)
        party = qty_override or job["party"]
        if self.dry_run:
            self.tg.send(f"[dry-run] 제출 생략: {job['cell'].fclt_nm} "
                         f"{_fmt_date(job['target'].date)} {party}명 captcha={captcha}")
            return
        try:
            res = self.knps.submit_reservation(job["cell"], party, captcha)
        except KnpsError as e:
            self.tg.send(f"⚠️ 제출 오류: {e}. 자리가 남아있으면 다시 캡차를 보냅니다.")
            self.start_relay(job["target"], job["cell"])
            return
        if res.success:
            self.done.add(job["target"].key())
            if res.payment_deadline:
                dl = res.payment_deadline
                pretty = f"{dl[:4]}-{dl[4:6]}-{dl[6:8]} {dl[8:10]}:{dl[10:12]}"
                self.tg.send(f"✅ 선점 완료! {res.prd_nm}\n결제 만기: {pretty}\n"
                             f"미결제 시 자동취소 — 지금 결제하세요:\n"
                             f"https://reservation.knps.or.kr/mypage/dashBoard.do?prdDvcd=S")
            else:
                self.tg.send(f"✅ {res.message}: {res.prd_nm}")
        else:
            self.tg.send(f"❌ {res.message}. 자리가 남아있으면 다시 캡차를 보냅니다.")
            self.start_relay(job["target"], job["cell"])

    def handle_command(self, text, chat_id):
        if self.cfg.telegram_chat_id is None:
            self.cfg.telegram_chat_id = str(chat_id)
            self.tg.set_chat_id(chat_id)
            save_config_chat_id(DEFAULT_CONFIG_PATH, chat_id)
        cmd, *rest = text.strip().split()
        if cmd == "/start":
            self.tg.send("감시를 시작합니다. /status 로 현재 상태를 볼 수 있어요.")
        elif cmd == "/status":
            lines = [f"{t.shelter} {_fmt_date(t.date)}: "
                     f"{self.state.get(t.key(), Slot('none',0)).status}"
                     for t in self.active_targets()]
            self.tg.send("감시 중\n" + "\n".join(lines) if lines else "감시 대상 없음")
        elif cmd == "/targets":
            self.tg.send("\n".join(f"{t.shelter} {_fmt_date(t.date)} {t.party}명 [{t.mode}]"
                                   for t in self.targets) or "없음")
        elif cmd == "/add" and len(rest) >= 2:
            shelter, date = rest[0], rest[1]
            party = int(rest[2]) if len(rest) > 2 and rest[2].isdigit() else 5
            self.targets.append(Target("설악산", "B03", shelter, date, party, "auto"))
            save_targets(DEFAULT_TARGETS_PATH, self.targets)
            self.tg.send(f"추가: {shelter} {_fmt_date(date)} {party}명")
        elif cmd == "/remove" and len(rest) >= 2:
            self.targets = [t for t in self.targets
                            if not (t.shelter == rest[0] and t.date == rest[1])]
            save_targets(DEFAULT_TARGETS_PATH, self.targets)
            self.tg.send(f"삭제: {rest[0]} {rest[1]}")
        else:
            self.handle_captcha_reply(text)

    def pump_telegram(self):
        for up in self.tg.get_updates(self.tg_offset, timeout=5):
            self.tg_offset = up["update_id"] + 1
            msg = up.get("message") or {}
            text = msg.get("text")
            chat_id = (msg.get("chat") or {}).get("id")
            if not text:
                continue
            if text.startswith("/"):
                self.handle_command(text, chat_id)
            else:
                self.handle_captcha_reply(text)

    def maybe_heartbeat(self):
        if time.time() - self.last_heartbeat >= HEARTBEAT_SEC:
            lines = [f"{t.shelter} {_fmt_date(t.date)}" for t in self.active_targets()]
            self.tg.send("감시 중: " + ", ".join(lines) + " — 변화 없음\n"
                         f"마지막 확인 {_now()}")
            self.last_heartbeat = time.time()

    def run(self):
        self.tg.send(f"🏔️ 대피소 감시 시작 ({len(self.active_targets())}개 대상)")
        while True:
            try:
                if self.active_targets() and time.time() - self.last_poll >= self.cfg.poll_sec:
                    self.poll()
                    self.last_poll = time.time()
                self.pump_telegram()
                self.maybe_heartbeat()
            except KnpsError as e:
                self.fail_streak += 1
                print(f"[{_now()}] poll fail #{self.fail_streak}: {e}")
                if self.fail_streak == 5:
                    self.tg.send(f"⚠️ 조회 연속 5회 실패: {e}")
                time.sleep(min(30 * self.fail_streak, 900))
            except Exception:
                traceback.print_exc()
                time.sleep(30)
            time.sleep(1)


def cmd_check(cfg, targets):
    knps = KnpsClient()
    depts = {(t.park, t.dept) for t in targets}
    calendar = {}
    for park, dept in depts:
        calendar.update(knps.fetch_calendar(dept, park))
    state = compute_state(targets, calendar)
    for t in targets:
        slot = state[t.key()]
        print(f"{t.shelter} {_fmt_date(t.date)}: {slot.status} (잔여 {slot.rsvt})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--listen", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        sys.exit(1)
    targets = load_targets()
    if args.check:
        cmd_check(cfg, targets)
    elif args.listen or args.dry_run:
        Daemon(cfg, targets, dry_run=args.dry_run).run()
    else:
        print("usage: monitor.py [--check | --listen | --dry-run]")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 문법·임포트 확인**

Run: `cd craig-find-cabin && /Users/craigpark/anaconda3/bin/python3 -c "import monitor; print('ok')"`
Expected: `ok`

- [ ] **Step 3: --check 라이브 스모크**

Run: `cd craig-find-cabin && /Users/craigpark/anaconda3/bin/python3 monitor.py --check`
사전조건: `~/.config/craig-find-cabin/config.json` 존재. Expected: 소청 10/15·양폭 10/16 상태 출력(현재 `none`).

- [ ] **Step 4: 전체 테스트 회귀**

Run: `cd craig-find-cabin && /Users/craigpark/anaconda3/bin/python3 -m pytest tests/ -v`
Expected: 전체 PASS (21 tests).

- [ ] **Step 5: 커밋**

```bash
cd /Users/craigpark/Github/Craig-Skill
git add craig-find-cabin/monitor.py
git commit -m "feat(find-cabin): 데몬 monitor.py — 폴링·캡차릴레이·텔레그램 명령·CLI"
```

---

### Task 8: 배포 (launchd) + config 준비 + 문서

**Files:**
- Create: `craig-find-cabin/deploy/com.craig.skill.findcabin.plist`
- Create: `craig-find-cabin/README.md`
- Modify: `Craig-Skill/SERVER_SETUP.md` (섹션 추가)

**Interfaces:** 없음(운영 산출물).

- [ ] **Step 1: config.json 준비 안내 (사용자 수행)**

서버(맥북 에어)에서 `~/.config/craig-find-cabin/config.json` 생성 (chmod 600):
```json
{
  "knps_id": "aruesoft",
  "knps_pw": "<KNPS 비밀번호>",
  "telegram_token": "<봇 토큰>",
  "telegram_chat_id": null,
  "poll_sec": 180
}
```
`chmod 600 ~/.config/craig-find-cabin/config.json`. `telegram_chat_id`는 사용자가 봇에 `/start` 보내면 데몬이 자동 저장한다. **이 값들은 저장소·문서·커밋에 넣지 않는다.**

- [ ] **Step 2: launchd plist 작성** — `deploy/com.craig.skill.findcabin.plist`

기존 plist 컨벤션(`deploy/launchd/*.plist`)을 먼저 확인하고 경로·python 인터프리터를 맞춘다. 서버의 저장소 경로(예: `/Users/craigui/Github/Craig-Skill`)와 python 경로는 서버 기준으로 채운다.
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.craig.skill.findcabin</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/craigui/anaconda3/bin/python3</string>
    <string>/Users/craigui/Github/Craig-Skill/craig-find-cabin/monitor.py</string>
    <string>--listen</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/craigui/Github/Craig-Skill/craig-find-cabin</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key>
  <string>/Users/craigui/Github/Craig-Skill/craig-find-cabin/logs/findcabin.out.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/craigui/Github/Craig-Skill/craig-find-cabin/logs/findcabin.err.log</string>
</dict></plist>
```

- [ ] **Step 3: README.md 작성** — 설치·실행·명령 요약

```markdown
# craig-find-cabin

KNPS 대피소 취소표 감시·선점 데몬. 설계는 `SPEC.md`, 구현계획은
`../docs/superpowers/plans/2026-08-31-craig-find-cabin.md`.

## 실행
- `python3 monitor.py --check`   1회 조회 (텔레그램·예약 없음)
- `python3 monitor.py --dry-run` 데몬, 예약 제출 직전까지만
- `python3 monitor.py --listen`  상시 데몬 (운영)

## 텔레그램 명령
`/start` `/status` `/targets` `/add <대피소> <YYYYMMDD> [인원]` `/remove <대피소> <YYYYMMDD>`
캡차 답장: 숫자 / `<인원> <숫자>` / `pass`

## 설정
`~/.config/craig-find-cabin/config.json` (chmod 600): knps_id, knps_pw,
telegram_token, telegram_chat_id, poll_sec. 감시대상은 `targets.json`.

## 배포
`deploy/com.craig.skill.findcabin.plist` → `~/Library/LaunchAgents/` 심볼릭 링크 후
`launchctl load`. 상세는 상위 `SERVER_SETUP.md`.
```

- [ ] **Step 4: SERVER_SETUP.md 섹션 추가**

`Craig-Skill/SERVER_SETUP.md`에 `com.craig.skill.findcabin` 항목 추가: config 경로, plist 로드/언로드 명령, 로그 경로, 봇 단일 인스턴스 원칙(신규 토큰이라 기존 봇과 충돌 없음), 상태페이지 서빙 방법(기존 dashboard 디렉토리 결합 또는 별도 포트+Tailscale).

- [ ] **Step 5: 커밋**

```bash
cd /Users/craigpark/Github/Craig-Skill
git add craig-find-cabin/deploy craig-find-cabin/README.md SERVER_SETUP.md
git commit -m "feat(find-cabin): launchd 배포 plist + README + SERVER_SETUP 갱신"
```

---

### Task 9: 예약 플로우 리허설 (사용자 승인 필수)

**Files:** 없음(라이브 검증). SPEC §7-4 대응.

**목적:** 실제 예약 제출까지 전체 캡차 릴레이가 동작하는지, 잔여 많은 평일 대피소로 1회 리허설한다. 결제하지 않으면 자동취소되므로 안전하나, **실제 예약을 생성하므로 사용자에게 명시적 승인을 받는다.**

- [ ] **Step 1: 사용자 승인 요청**

리허설 대상(예: 수렴동대피소 평일 1자리, 낮은 인원)과 절차를 사용자에게 설명하고 진행 승인을 받는다. 승인 없으면 이 태스크를 건너뛴다.

- [ ] **Step 2: 임시 타깃으로 dry-run 확인**

승인된 리허설 대피소·날짜를 `targets.json`에 임시 추가하고 `monitor.py --dry-run`으로 캡차 이미지 수신 → 답장 → payload 로깅까지 확인.

- [ ] **Step 3: 실제 제출 리허설**

`monitor.py --listen`으로 전환, 동일 타깃에서 캡차 답장 → 실제 `createReservation.do` 제출 → "선점 완료 + 결제 만기" 메시지 확인. **결제하지 않는다.**

- [ ] **Step 4: 자동취소 확인 및 원복**

마이페이지에서 예약이 결제만기 후 자동취소되는지 확인. 임시 타깃을 `targets.json`에서 제거하고 원래 소청/양폭만 남긴다.

- [ ] **Step 5: 커밋 (필요 시)**

리허설로 targets.json이 변경됐다면 원복 상태를 커밋.

---

## Self-Review

**1. Spec coverage:**
- §1 목표(감시·알림·선점, 대상 추가 가능, 하트비트, 상태페이지, 서버 상시): Task 3/5/6/7/8 ✓. 인원 5·min(인원,잔여): Task 4 submit_reservation ✓.
- §2-1 익명 조회: Task 4 fetch_calendar ✓. §2-2 파싱(available 키, 매진 absent): Task 2 ✓. §2-3 로그인: Task 4 login ✓. §2-4 캡차·예약 payload(areaCode/salAmt/rsrvtQntt 등): Task 4 ✓. §2-5 취소표 전략: Task 3 이벤트 ✓.
- §3 구조(파일 분리): File Structure + 각 Task ✓. 비밀값 ~/.config: Task 1/8 ✓.
- §4-1 폴링·실패 백오프(30초·5회·최대15분): Task 7 run() ✓. §4-2 캡차 릴레이(로그인→캡차→전송→답장→제출→done, 대기중 폴링 지속, 소진 취소): Task 7 ✓. §4-3 텔레그램 명령(/status /targets /add /remove, chat_id 자동저장): Task 7 ✓. §4-4 하트비트 2시간·텔레그램 단독: Task 7 maybe_heartbeat ✓. §4-5 상태페이지 5분갱신: Task 6 ✓.
- §5 운영(launchd·requests만·--check/--dry-run): Task 7/8 ✓.
- §6 에러표: Task 4(KnpsError)·Task 7(백오프·릴레이 취소·재시도) ✓.
- §7 검증(파서 픽스처·--check·로그인/캡차·리허설·하트비트): Task 2/4/9 ✓.
- **갭:** §4-3 `/pause`·`/resume`는 계획에서 축약됨. → YAGNI로 v1 제외, 필요 시 데몬 정지(launchctl)로 대체. SPEC의 pause/resume는 v1.1로 미룸(자체검토 반영, 기능 손실 미미).

**2. Placeholder scan:** 모든 코드 스텝에 실제 구현 포함. plist의 서버 경로/인터프리터는 "서버 기준으로 채운다"고 명시(서버 실경로는 배포 시 확인). TBD/TODO 없음.

**3. Type consistency:** `Target.key()`=(shelter,date) 일관(config→watch→monitor). `Cell` 필드(rsvt_cnt/prd_id/reser_tp 등) 파서·submit_reservation·watch 간 일치. `Slot(status,rsvt)` watch·status_page·monitor 일치. `diff_targets` 반환 `(events, new_state)` monitor.poll에서 언팩 일치. `ReservationResult(success,reser_tp,prd_nm,payment_deadline,message)` submit·handle_captcha_reply 일치.
