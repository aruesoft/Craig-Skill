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
