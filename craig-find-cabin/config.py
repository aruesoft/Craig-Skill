import json
import os
from dataclasses import dataclass, asdict

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/craig-find-cabin/config.json")
DEFAULT_TARGETS_PATH = os.path.join(os.path.dirname(__file__), "targets.json")


class ConfigError(Exception):
    pass


@dataclass
class Target:
    park: str
    dept: str
    shelter: str
    date: str      # YYYYMMDD
    party: int
    mode: str      # "auto" | "notify"

    def key(self):
        return (self.shelter, self.date)


@dataclass
class Config:
    knps_id: str
    knps_pw: str
    telegram_token: str
    telegram_chat_id: str | None
    poll_sec: int


def load_config(path: str = DEFAULT_CONFIG_PATH) -> Config:
    if not os.path.exists(path):
        raise ConfigError(f"config not found: {path}")
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    for req in ("knps_id", "knps_pw", "telegram_token"):
        if not d.get(req):
            raise ConfigError(f"missing required config key: {req}")
    return Config(
        knps_id=d["knps_id"],
        knps_pw=d["knps_pw"],
        telegram_token=d["telegram_token"],
        telegram_chat_id=(str(d["telegram_chat_id"]) if d.get("telegram_chat_id") else None),
        poll_sec=int(d.get("poll_sec", 180)),
    )


def save_config_chat_id(path: str, chat_id: str) -> None:
    """텔레그램 /start 시 chat_id를 config.json에 병합 저장."""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    d["telegram_chat_id"] = str(chat_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)


def load_targets(path: str = DEFAULT_TARGETS_PATH) -> list[Target]:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return [Target(t["park"], t["dept"], t["shelter"], t["date"],
                   int(t["party"]), t.get("mode", "auto")) for t in d["targets"]]


def load_poll_sec(path: str = DEFAULT_TARGETS_PATH) -> int:
    with open(path, encoding="utf-8") as f:
        return int(json.load(f).get("poll_sec", 180))


def save_targets(path: str, targets: list[Target]) -> None:
    poll = load_poll_sec(path) if os.path.exists(path) else 180
    obj = {"poll_sec": poll, "targets": [asdict(t) for t in targets]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
