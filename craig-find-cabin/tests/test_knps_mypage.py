"""마이페이지(나의 예약목록) 파싱 — 같은 날 기존 예약 감지용."""
import os
from knps import parse_my_reservations, parse_reservation_qty

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _read(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def test_parse_list_rows():
    rows = parse_my_reservations(_read("mypage_list.html"))
    assert len(rows) == 3
    r = rows[0]
    assert r.rsvt_id == "SB0310020100220260902106743"
    assert r.shelter == "소청대피소"
    assert r.use_dt == "20261015"
    assert r.status == "예약(미결제)"
    assert r.kind == "R"
    assert r.active


def test_waiting_row_has_kind_W_and_qty_from_cancel_args():
    rows = parse_my_reservations(_read("mypage_list.html"))
    w = rows[1]
    assert w.kind == "W"
    assert w.active
    assert w.qty == 2
    assert w.prd_id == "SB03100601006"


def test_cancelled_row_not_active():
    rows = parse_my_reservations(_read("mypage_list.html"))
    assert rows[2].active is False


def test_parse_detail_qty():
    assert parse_reservation_qty(_read("mypage_detail.html")) == 3
