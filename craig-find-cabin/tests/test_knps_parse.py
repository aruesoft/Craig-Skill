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
