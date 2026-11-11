"""Plain characters and headline-like titles (found in the live check of the published videos)."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import reserve
from engine.generator import ScriptSpec, normalize_text, validate_quality
from engine.uploader import build_metadata
from test_llm_providers import valid_script

ROOT = Path(__file__).resolve().parent.parent
ODD = re.compile("[‐-‒−  ​-‍﻿]")


def test_look_alike_characters_become_plain_ones():
    assert normalize_text("foot‑in‑the‑door") == "foot-in-the-door"
    assert normalize_text("a b​c") == "a bc"
    assert normalize_text({"x": ["a‐b", {"y": "c−d"}], "n": 3}) == {"x": ["a-b", {"y": "c-d"}], "n": 3}
    assert normalize_text("en – and em — dashes and curly ’ quotes stay") == "en – and em — dashes and curly ’ quotes stay"


def test_every_script_path_normalises_title_description_tags_and_narration():
    raw = valid_script()
    raw["title"] = "Foot‑in‑the‑door: how a tiny request hijacks you"
    raw["description"] = "The foot‑in‑the‑door technique"
    raw["tags"] = ["foot‑in‑the‑door"]
    raw["scenes"][1]["text"] = "A super‑productive day of task‑switching costs you more."
    script = ScriptSpec.model_validate(raw)
    from_json = ScriptSpec.model_validate_json(json.dumps(raw))  # the --script-file / reserve path
    for s in (script, from_json):
        title, description, tags = build_metadata(s)
        assert title.startswith("Foot-in-the-door") and "foot-in-the-door" in description and tags == ["foot-in-the-door"]
        assert "super-productive" in s.scenes[1].text and not ODD.search(s.narration)


def test_existing_reserve_scripts_load_with_plain_characters():
    files = list((ROOT / "state" / "reserve").glob("*/*.json"))
    assert files
    for path in files:
        item = reserve.load_item(path)
        blob = item.script.model_dump_json()
        assert not ODD.search(blob), path.name


def test_a_keyword_list_title_is_rejected_with_a_clear_instruction():
    raw = valid_script()
    raw["title"] = "multitasking myth productivity myth busted"  # the real title that went live
    problems = validate_quality(ScriptSpec.model_validate(raw))
    assert len(problems) == 1 and "capital letter" in problems[0] and "myth" in problems[0]


def test_headline_titles_pass():
    for title in ("Why Multitasking Hurts Your Focus", "5 Signs Someone Is Anchoring Your Price", "Stroop Test: Why Conflicting Words Slow Your Brain"):
        raw = valid_script()
        raw["title"] = title
        assert validate_quality(ScriptSpec.model_validate(raw)) == [], title
    for bad in ("Too short", "lowercase start of a long title", "Money Money everywhere and Money again"):
        raw = valid_script()
        raw["title"] = bad
        assert any("headline" in p for p in validate_quality(ScriptSpec.model_validate(raw))), bad


def test_channel_tags_are_never_off_topic():
    for channel in ("money", "psychology"):
        tags = json.loads((ROOT / "channels" / f"{channel}.json").read_text(encoding="utf-8"))["tags"]
        assert "body language" not in tags  # it was added to every psychology video, e.g. a Stroop test
