"""대피소 감시 상태 페이지 + 감시대상 편집 웹앱.
- GET  /         현재 상태 표 + 대상 추가 폼 + 대상별 삭제 버튼
- POST /add      감시대상 추가 (런타임 targets.json에 기록 → 데몬이 자동 리로드)
- POST /remove   감시대상 삭제
상태는 데몬이 매 폴링마다 쓰는 status/status.json에서 읽는다. 예약/결제는 하지 않는다.
실행: python3 web.py [--port 8790] [--host 0.0.0.0]"""
import os
import json
import html
import base64
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from config import load_targets, save_targets, Target

# 편집(추가/삭제) 암호. main()에서 config로 채운다. None이면 편집 무인증.
EDIT_PASSWORD = None

STATUS_JSON = os.path.join(os.path.dirname(__file__), "status", "status.json")
# 설악산(B03) 대피소 — 현재 감시 범위
SHELTERS = ["소청대피소", "양폭대피소", "수렴동대피소", "희운각대피소"]
PARK, DEPT = "설악산", "B03"

_LABEL = {"available": ("예약가능", "#1a7f37"), "waiting": ("대기가능", "#9a6700"),
          "soldout": ("매진", "#767676")}

# 하단 배너 — 국립공원 대피소 예약 바로가기 (전 공원 공통 페이지, 산은 그 페이지 탭에서 선택)
KNPS_SHELTER_URL = "https://reservation.knps.or.kr/reservation/shelter/searchSimpleShelterReservation.do"
PARK_SHELTERS = [
    ("설악산", ["소청", "양폭", "수렴동", "희운각"]),
    ("지리산", ["노고단", "세석", "장터목", "벽소령", "연하천", "치밭목", "로타리"]),
    ("덕유산", ["삿갓재"]),
    ("소백산", ["연화봉"]),
]


def _statuses():
    try:
        with open(STATUS_JSON, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("updated", "-"), {(t["shelter"], t["date"]): t
                                       for t in d.get("targets", [])}
    except (OSError, ValueError):
        return "-", {}


def _valid_date(s):
    return len(s) == 8 and s.isdigit()


def _park_banners():
    cards = ""
    for park, shelters in PARK_SHELTERS:
        chips = "".join(f"<span>{s}</span>" for s in shelters)
        cards += (f'<a class="park" href="{KNPS_SHELTER_URL}" target="_blank" rel="noopener">'
                  f'<div class="nm">⛰️ {park}</div><div class="sh">{chips}</div></a>')
    return cards


def render_page(msg=""):
    updated, st = _statuses()
    targets = load_targets()
    rows = ""
    for t in targets:
        info = st.get((t.shelter, t.date), {})
        label, color = _LABEL.get(info.get("status"), ("조회 대기", "#333"))
        rsvt = info.get("rsvt", 0)
        rsvt_s = f" (잔여 {rsvt})" if info.get("status") in ("available", "waiting") else ""
        d = t.date
        pretty = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        rows += (f'<tr><td>{html.escape(t.shelter)}</td><td>{pretty}</td>'
                 f'<td style="color:{color};font-weight:600">{label}{rsvt_s}</td>'
                 f'<td>{t.party}명</td>'
                 f'<td><form method="post" action="/remove" onsubmit="return confirm(\'삭제할까요?\')" style="margin:0">'
                 f'<input type="hidden" name="shelter" value="{html.escape(t.shelter)}">'
                 f'<input type="hidden" name="date" value="{t.date}">'
                 f'<button class="del">삭제</button></form></td></tr>')
    options = "".join(f"<option>{s}</option>" for s in SHELTERS)
    banner = f'<p class="msg">{html.escape(msg)}</p>' if msg else ""
    return f"""<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>설악원정대 대피소 취소표 감시 프로그램</title>
<style>
body{{font-family:-apple-system,sans-serif;max-width:680px;margin:2rem auto;padding:0 1rem;color:#222}}
h1{{font-size:1.4rem}} .meta{{color:#666;font-size:.9rem}}
table{{width:100%;border-collapse:collapse;margin:1rem 0}}
th,td{{padding:.55rem;border-bottom:1px solid #eee;text-align:left;font-size:.95rem}}
form.add{{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;background:#f6f6f6;padding:1rem;border-radius:8px}}
select,input,button{{padding:.5rem;font-size:.95rem;border:1px solid #ccc;border-radius:6px}}
button{{cursor:pointer;background:#1a7f37;color:#fff;border:none}}
button.del{{background:#c0392b;padding:.35rem .7rem;font-size:.85rem}}
.msg{{background:#eef7ee;border:1px solid #cbe6cb;padding:.6rem;border-radius:6px}}
.parks{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.6rem;margin:.8rem 0}}
a.park{{display:block;padding:.8rem .9rem;border:1px solid #dbe7db;border-radius:10px;
  background:linear-gradient(135deg,#f4faf4,#eaf4ea);text-decoration:none;color:#222;
  transition:box-shadow .15s,transform .15s}}
a.park:hover{{box-shadow:0 3px 10px rgba(26,127,55,.18);transform:translateY(-2px)}}
a.park .nm{{font-weight:700;color:#1a5c30;display:flex;align-items:center;gap:.3rem}}
a.park .nm::after{{content:"↗";margin-left:auto;font-weight:400;color:#7aa88a;font-size:.85em}}
a.park .sh{{display:flex;flex-wrap:wrap;gap:.25rem;margin-top:.5rem}}
a.park .sh span{{background:#fff;border:1px solid #e0e9e0;border-radius:999px;
  padding:.1rem .5rem;font-size:.75rem;color:#556b57}}
</style></head><body>
<h1>⛰️ 설악원정대 대피소 취소표 감시 프로그램</h1>
<p class="meta">마지막 확인: {updated} · 5분마다 자동 새로고침 · 예약가능/대기가능이 뜨면 텔레그램·전화·Pushover로 알림</p>
{banner}
<table><thead><tr><th>대피소</th><th>날짜</th><th>상태</th><th>인원</th><th></th></tr></thead>
<tbody>{rows or '<tr><td colspan=5>감시 대상 없음</td></tr>'}</tbody></table>
<h2 style="font-size:1.1rem">감시 대상 추가</h2>
<form class="add" method="post" action="/add">
  <select name="shelter">{options}</select>
  <input name="date" placeholder="YYYYMMDD 예:20261012" maxlength="8" required>
  <input name="party" type="number" min="1" max="20" value="5" style="width:5rem">
  <button type="submit">추가</button>
</form>
<p class="meta">※ 설악산 대피소만 지원. 추가하면 데몬이 자동으로 감시에 포함합니다(수 분 내).</p>
<h2 style="font-size:1.1rem;margin-top:2rem">🔗 국립공원 대피소 예약 바로가기</h2>
<div class="parks">{_park_banners()}</div>
<p class="meta">국립공원공단 예약 페이지로 이동합니다. 산 선택은 이동한 페이지의 탭에서 하세요.</p>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, msg=""):
        # Location 헤더는 latin-1만 가능 → 한글 msg는 percent-encode
        loc = "/?msg=" + quote(msg) if msg else "/"
        self.send_response(303)
        self.send_header("Location", loc)
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/status.json"):
            try:
                with open(STATUS_JSON, encoding="utf-8") as f:
                    self._send(200, f.read(), "application/json; charset=utf-8")
            except OSError:
                self._send(404, "{}", "application/json")
            return
        if self.path == "/" or self.path.startswith("/?"):
            msg = parse_qs(urlparse(self.path).query).get("msg", [""])[0]
            self._send(200, render_page(msg))
            return
        self._send(404, "not found")

    def _form(self):
        n = int(self.headers.get("Content-Length", 0))
        return parse_qs(self.rfile.read(n).decode("utf-8"))

    def _authorized(self):
        if not EDIT_PASSWORD:
            return True
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                _, _, pw = base64.b64decode(h[6:]).decode("utf-8").partition(":")
                return pw == EDIT_PASSWORD
            except (ValueError, UnicodeDecodeError):
                return False
        return False

    def _require_auth(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="cabin edit"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        if not self._authorized():
            self._require_auth()
            return
        f = self._form()
        if self.path == "/add":
            shelter = (f.get("shelter", [""])[0]).strip()
            date = (f.get("date", [""])[0]).strip()
            try:
                party = max(1, min(20, int(f.get("party", ["5"])[0])))
            except ValueError:
                party = 5
            if not shelter.endswith("대피소"):
                shelter += "대피소"
            if shelter not in SHELTERS:
                return self._redirect("지원하지 않는 대피소입니다")
            if not _valid_date(date):
                return self._redirect("날짜는 YYYYMMDD 8자리로 입력하세요")
            targets = load_targets()
            if any(t.shelter == shelter and t.date == date for t in targets):
                return self._redirect("이미 감시 중인 대상입니다")
            targets.append(Target(PARK, DEPT, shelter, date, party, "auto"))
            save_targets(targets=targets)
            return self._redirect(f"추가됨: {shelter} {date}")
        if self.path == "/remove":
            shelter = (f.get("shelter", [""])[0]).strip()
            date = (f.get("date", [""])[0]).strip()
            targets = [t for t in load_targets()
                       if not (t.shelter == shelter and t.date == date)]
            save_targets(targets=targets)
            return self._redirect(f"삭제됨: {shelter} {date}")
        self._send(404, "not found")

    def log_message(self, *a):
        pass  # quiet


def main():
    global EDIT_PASSWORD
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--host", default="0.0.0.0")
    a = ap.parse_args()
    try:
        from config import load_config
        EDIT_PASSWORD = load_config().web_edit_password
    except Exception as e:
        print(f"config load skipped ({e}); edit auth disabled")
    print(f"find-cabin web on {a.host}:{a.port} (edit auth: {'ON' if EDIT_PASSWORD else 'OFF'})")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
