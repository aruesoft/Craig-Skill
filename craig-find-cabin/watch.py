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
