"""Safety nets for unattended weeks: the watchdog's slot table matches the real channels, and every Python install
in every workflow is pinned by constraints.txt."""
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent


def test_the_watchdog_slot_table_matches_the_real_channel_files():
    code = (ROOT / "worker" / "index.js").read_text(encoding="utf-8")
    slots_src = re.search(r"const CHANNEL_SLOTS = \{(.*?)\};", code).group(1)
    zones_src = re.search(r"const CHANNEL_TZ = \{(.*?)\};", code).group(1)
    slots = {name: re.findall(r'"([^"]+)"', body) for name, body in re.findall(r"(\w+): \[([^\]]*)\]", slots_src)}
    zones = dict(re.findall(r'(\w+): "([^"]+)"', zones_src))
    channels = sorted(p.stem for p in (ROOT / "channels").glob("*.json"))
    assert sorted(slots) == channels == sorted(zones)
    for channel in channels:
        real = json.loads((ROOT / "channels" / f"{channel}.json").read_text(encoding="utf-8"))
        assert slots[channel] == real["slots"], channel  # a changed slot must be changed in the Worker too
        assert zones[channel] == real["timezone"], channel
    assert re.search(r"const CHANNELS = \[(.*?)\];", code).group(1).replace('"', "").replace(" ", "").split(",") == channels


def test_every_pip_install_is_pinned_by_the_constraints_file():
    lines = []
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        data = yaml.safe_load(wf.read_text(encoding="utf-8"))
        for job in data["jobs"].values():
            lines += [(wf.name, s["run"]) for s in job["steps"] if "pip install" in str(s.get("run", ""))]
    assert len(lines) >= 6
    for name, run in lines:
        assert "-c constraints.txt" in run, f"{name}: {run}"


def test_constraints_pin_exactly_what_the_engine_needs():
    text = (ROOT / "constraints.txt").read_text(encoding="utf-8").lower()
    pins = dict(line.split("==", 1) for line in text.splitlines() if "==" in line and not line.startswith("#"))
    for package in ("pydantic", "pydantic-settings", "typer", "anthropic", "httpx", "numpy", "faster-whisper", "kokoro-onnx",
                    "edge-tts", "av", "google-api-python-client", "google-auth-oauthlib", "pyyaml", "tzdata", "onnxruntime"):
        assert package in pins, package
    wanted = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for line in wanted.splitlines():
        name = re.split(r"[<>=#\s]", line.strip())[0]
        if name and name != "pytest":
            assert name in pins or name.replace("_", "-") in pins, name
    assert not any(line.startswith("-e") for line in text.splitlines())  # no editable/local installs in the pin list


def test_the_daily_workflow_never_disables_itself_and_alerts_on_failure():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8"))
    jobs = wf["jobs"]
    assert "alert" in jobs and "notify" in jobs and "record" in jobs
    assert len(wf[True if True in wf else "on"]["schedule"]) == 2  # a second chance every day
    assert wf["concurrency"]["cancel-in-progress"] is False
