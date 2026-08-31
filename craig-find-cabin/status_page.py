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
<title>설악원정대 대피소 취소표 감시 프로그램</title>
<style>body{{font-family:-apple-system,sans-serif;max-width:640px;margin:2rem auto;padding:0 1rem}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:.6rem;border-bottom:1px solid #eee;text-align:left}}
.meta{{color:#666;font-size:.9rem}}</style></head><body>
<h1>⛰️ 설악원정대 대피소 취소표 감시 프로그램</h1>
<p class="meta">마지막 확인: {last_check} · 데몬: {health} · 5분마다 자동 새로고침</p>
<table><thead><tr><th>대피소</th><th>날짜</th><th>상태</th><th>인원</th></tr></thead>
<tbody>{trs}</tbody></table></body></html>"""


def write_status(out_dir: str, targets, state, last_check: str, healthy: bool) -> None:
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "status.json"), "w", encoding="utf-8") as f:
        f.write(render_json(targets, state, last_check, healthy))
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_html(targets, state, last_check, healthy))
