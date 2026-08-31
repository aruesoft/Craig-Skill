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
    assert 'content="300"' in html
    assert "소청대피소" in html
    assert "대기가능" in html
