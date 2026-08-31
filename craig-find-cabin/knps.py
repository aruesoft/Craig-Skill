import re
import time
from dataclasses import dataclass

import requests

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
        try:
            r = self.s.get(f"{BASE}/mmb/mmbLogin.do", timeout=20)
        except requests.RequestException as e:
            raise KnpsError(f"login request failed: {e}") from e
        m = _LOGIN_FORM_RE.search(r.text)
        hidden = dict(_HIDDEN_RE.findall(m.group(0))) if m else {"loginType": "Member"}
        hidden.pop("mmbId", None)
        hidden.pop("passWd", None)
        data = {**hidden, "mmbId": mmb_id, "passWd": passwd}
        try:
            r2 = self.s.post(f"{BASE}/mmb/mmbLoginProc.do", data=data,
                             allow_redirects=False, timeout=20)
        except requests.RequestException as e:
            raise KnpsError(f"login request failed: {e}") from e
        if r2.status_code not in (301, 302) or not self.is_authenticated():
            raise KnpsError(f"login failed: HTTP {r2.status_code}")

    def is_authenticated(self) -> bool:
        try:
            r = self.s.get(f"{BASE}/reservation/auth.do", timeout=20)
            return r.status_code == 200
        except requests.RequestException:
            return False

    def get_captcha(self) -> bytes:
        try:
            r = self.s.get(f"{BASE}/reserCaptcha.do?dummy={int(time.time()*1000)}", timeout=20)
        except requests.RequestException as e:
            raise KnpsError(f"captcha request failed: {e}") from e
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
