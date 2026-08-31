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
