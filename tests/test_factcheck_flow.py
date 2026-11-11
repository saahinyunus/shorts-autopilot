"""What the pipeline does with a script the fact check could not fix."""
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from typer.testing import CliRunner

from engine import pipeline, reserve
from engine.config import get_settings
from engine.generator import FactCheckUnresolved, ScriptSpec
from test_llm_providers import valid_script


def run_pipeline(monkeypatch, tmp_path, extra_args):
    flagged = ScriptSpec.model_validate({**valid_script(), "title": "Flagged script"})
    flagged.review_notes = ["FLAGGED: Scene 3: x (y)"]

    def doubtful(*a, **k):
        raise FactCheckUnresolved(flagged, ["Scene 3: x (y)"])

    monkeypatch.setattr(pipeline, "generate_script", doubtful)
    monkeypatch.setattr(pipeline, "ensure_sfx", lambda s: {})
    stop = RuntimeError("stop after the script step")
    monkeypatch.setattr(pipeline, "synthesize_audio", lambda *a, **k: (_ for _ in ()).throw(stop))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    get_settings.cache_clear()
    result = CliRunner().invoke(pipeline.app, ["--channel", "money", "--topic", "Fresh topic", *extra_args])
    get_settings.cache_clear()
    run_dir = next((tmp_path / "out").iterdir())
    return result, json.loads((run_dir / "script.json").read_text(encoding="utf-8"))


def test_a_reserve_script_is_preferred_over_a_script_with_a_known_wrong_claim(monkeypatch, tmp_path):
    item = reserve.save_item("money", reserve.ReserveItem(topic="The story of the Dutch tulip bubble", script=ScriptSpec.model_validate(valid_script())), tmp_path)
    result, script = run_pipeline(monkeypatch, tmp_path, ["--reserve-file", str(item)])
    assert "using the reserve script" in result.output and script["title"] == "Why This Happens To You Every Day" and script["review_notes"] == []


def test_without_a_reserve_the_flagged_script_is_published_with_a_warning_not_dropped(monkeypatch, tmp_path):
    result, script = run_pipeline(monkeypatch, tmp_path, [])
    assert "WARNING" in result.output and script["title"] == "Flagged script"
    assert script["review_notes"][0].startswith("FLAGGED")  # the owner is told through the notification
