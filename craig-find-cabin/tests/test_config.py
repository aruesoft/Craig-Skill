import json, os, tempfile
import pytest
from config import load_config, load_targets, save_targets, Target, Config, ConfigError

def _write(tmp, name, obj):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    return p

def test_load_targets_parses_all_fields():
    with tempfile.TemporaryDirectory() as tmp:
        p = _write(tmp, "targets.json", {
            "poll_sec": 180,
            "targets": [
                {"park": "설악산", "dept": "B03", "shelter": "소청대피소",
                 "date": "20261015", "party": 5, "mode": "auto"}
            ],
        })
        ts = load_targets(p)
        assert len(ts) == 1
        assert ts[0] == Target("설악산", "B03", "소청대피소", "20261015", 5, "auto")

def test_load_config_reads_secrets():
    with tempfile.TemporaryDirectory() as tmp:
        p = _write(tmp, "config.json", {
            "knps_id": "u", "knps_pw": "p",
            "telegram_token": "t", "telegram_chat_id": "123", "poll_sec": 120,
        })
        c = load_config(p)
        assert c.knps_id == "u" and c.telegram_token == "t"
        assert c.telegram_chat_id == "123" and c.poll_sec == 120

def test_load_config_missing_required_raises():
    with tempfile.TemporaryDirectory() as tmp:
        p = _write(tmp, "config.json", {"knps_id": "u"})  # missing pw/token
        with pytest.raises(ConfigError):
            load_config(p)

def test_config_missing_file_raises():
    with pytest.raises(ConfigError):
        load_config("/nonexistent/config.json")

def test_save_and_reload_targets_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "targets.json")
        save_targets(p, [Target("설악산", "B03", "양폭대피소", "20261016", 5, "auto")])
        ts = load_targets(p)
        assert ts[0].shelter == "양폭대피소" and ts[0].party == 5
