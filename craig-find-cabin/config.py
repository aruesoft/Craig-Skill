import json
import os
from dataclasses import dataclass, asdict

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/craig-find-cabin/config.json")
# 런타임 감시대상: 저장소 밖(편집돼도 git pull 충돌 없음). 없으면 저장소 seed로 부트스트랩.
DEFAULT_TARGETS_PATH = os.path.expanduser("~/.config/craig-find-cabin/targets.json")
SEED_TARGETS_PATH = os.path.join(os.path.dirname(__file__), "targets.json")


def _targets_source(path):
    """런타임 파일이 있으면 그것을, 없으면 저장소 seed를 읽을 경로."""
    if path == DEFAULT_TARGETS_PATH and not os.path.exists(path):
        return SEED_TARGETS_PATH
    return path


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
    # 긴급 알림 채널 (선택 — 키 없으면 비활성)
    pushover_user: str | None = None
    pushover_token: str | None = None
    twilio_sid: str | None = None
    twilio_token: str | None = None
    twilio_from: str | None = None
    twilio_to: str | None = None
    # 릴레이 에스컬레이션 타이밍(초)
    alert_repeat_sec: int = 45       # 텔레그램 반복 독촉 간격
    captcha_refresh_sec: int = 120   # 활성 릴레이 캡차 재발송 간격
    call_repeat_sec: int = 300       # 전화 재시도 간격


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
        pushover_user=d.get("pushover_user") or None,
        pushover_token=d.get("pushover_token") or None,
        twilio_sid=d.get("twilio_sid") or None,
        twilio_token=d.get("twilio_token") or None,
        twilio_from=d.get("twilio_from") or None,
        twilio_to=d.get("twilio_to") or None,
        alert_repeat_sec=int(d.get("alert_repeat_sec", 45)),
        captcha_refresh_sec=int(d.get("captcha_refresh_sec", 120)),
        call_repeat_sec=int(d.get("call_repeat_sec", 300)),
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
    with open(_targets_source(path), encoding="utf-8") as f:
        d = json.load(f)
    return [Target(t["park"], t["dept"], t["shelter"], t["date"],
                   int(t["party"]), t.get("mode", "auto")) for t in d["targets"]]


def load_poll_sec(path: str = DEFAULT_TARGETS_PATH) -> int:
    with open(_targets_source(path), encoding="utf-8") as f:
        return int(json.load(f).get("poll_sec", 180))


def targets_mtime(path: str = DEFAULT_TARGETS_PATH) -> float:
    """유효 대상 파일(런타임 or seed)의 수정시각 — 데몬이 변경 감지에 사용."""
    try:
        return os.path.getmtime(_targets_source(path))
    except OSError:
        return 0.0


def save_targets(path: str = DEFAULT_TARGETS_PATH, targets=None) -> None:
    poll = load_poll_sec(path) if os.path.exists(_targets_source(path)) else 180
    obj = {"poll_sec": poll, "targets": [asdict(t) for t in (targets or [])]}
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
