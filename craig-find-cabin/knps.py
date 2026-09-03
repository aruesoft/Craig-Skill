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


@dataclass
class Reservation:
    """마이페이지 '나의 예약목록' 한 행. 같은 날 기존 예약 감지·취소에 사용."""
    rsvt_id: str
    shelter: str
    use_dt: str        # YYYYMMDD
    status: str        # 사이트 표시 문자열(예약(미결제)·대기·예약취소(본인)…)
    kind: str          # 'R' 예약 | 'W' 대기
    qty: int | None    # 인원(대기 행은 목록에서, 예약 행은 상세 페이지에서)
    prd_id: str = ""

    @property
    def active(self) -> bool:
        return "취소" not in self.status


_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
_STATUS_RE = re.compile(r'<span class="statusSpan\d+">\s*([^<]+?)\s*</span>')
_RSVT_RE = re.compile(r"rsvtId=([A-Z0-9]+)")
_NAME_RE = re.compile(r"-\s*([^<\s]+)\s*</a>")
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s*\[")
_WAIT_RE = re.compile(r"cancelWait\('([^']*)','([^']*)','([^']*)','([^']*)','([^']*)'\)")
_QTY_RE = re.compile(r"사용인원</dt>\s*<dd>\s*(\d+)\s*명")


def parse_my_reservations(html: str) -> list[Reservation]:
    """dashBoard.do(prdDvcd=S) HTML → 대피소 예약 행 목록(취소된 행 포함, active로 구분)."""
    out = []
    for row in _ROW_RE.findall(html):
        m_id = _RSVT_RE.search(row)
        m_st = _STATUS_RE.search(row)
        m_dt = _DATE_RE.search(row)
        if not (m_id and m_st and m_dt):
            continue
        m_nm = _NAME_RE.search(row)
        status = m_st.group(1)
        m_w = _WAIT_RE.search(row)
        kind = "W" if (m_w or "대기" in status) else "R"
        qty = _to_int(m_w.group(5), 0) if m_w else None
        prd_id = m_w.group(2) if m_w else m_id.group(1)[:13]
        out.append(Reservation(m_id.group(1), m_nm.group(1) if m_nm else "",
                               "".join(m_dt.groups()), status, kind, qty, prd_id))
    return out


def parse_reservation_qty(html: str) -> int | None:
    m = _QTY_RE.search(html)
    return int(m.group(1)) if m else None


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

    def my_reservations(self, use_dt: str | None = None) -> list[Reservation]:
        """로그인 세션으로 '나의 예약목록(대피소)'의 활성 행을 가져온다(use_dt 지정 시 그 날짜만).
        예약(R) 행은 인원이 목록에 없어 상세 페이지를 한 번 더 읽는다."""
        try:
            r = self.s.get(f"{BASE}/mypage/dashBoard.do",
                           params={"pageNo": 1, "type": "rsvt", "prdDvcd": "S"}, timeout=20)
        except requests.RequestException as e:
            raise KnpsError(f"mypage request failed: {e}") from e
        if r.status_code != 200 or "예약목록" not in r.text:
            raise KnpsError(f"unexpected mypage response: HTTP {r.status_code}")
        rows = [x for x in parse_my_reservations(r.text)
                if x.active and (use_dt is None or x.use_dt == use_dt)]
        for x in rows:
            if x.qty is None:
                try:
                    d = self.s.get(f"{BASE}/mypage/selectReservationDetail.do",
                                   params={"rsvtId": x.rsvt_id, "prdDvcd": "S"}, timeout=20)
                    x.qty = parse_reservation_qty(d.text)
                except requests.RequestException:
                    x.qty = None
        return rows

    def cancel_reservation(self, res: Reservation) -> None:
        """기존 예약(R)은 전체취소, 대기(W)는 대기취소. 취소 후 목록을 다시 읽어 실제로 사라졌는지 검증."""
        try:
            if res.kind == "W":
                self.s.post(f"{BASE}/mypage/cancelWait.do",
                            data={"rsvtId": res.rsvt_id, "prdSalYmd": res.use_dt,
                                  "prdId": res.prd_id, "prdDvcd": "S",
                                  "pttNopCnt": res.qty or 1}, timeout=30)
            else:
                self.s.post(f"{BASE}/appendix/reservationCancelProc.do",
                            data={"rsvtId": res.rsvt_id, "allCancel": "Y"}, timeout=30)
        except requests.RequestException as e:
            raise KnpsError(f"cancel request failed: {e}") from e
        still = [x for x in self.my_reservations(res.use_dt) if x.rsvt_id == res.rsvt_id]
        if still:
            raise KnpsError(f"cancel not applied: {res.rsvt_id} still {still[0].status}")

    def submit_reservation(self, cell: Cell, qty: int, captcha: str) -> ReservationResult:
        """qty명 그대로 제출(잔여 대비 캡은 호출자 책임 — 취소 직후 잔여가 목록보다 클 수 있음)."""
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
