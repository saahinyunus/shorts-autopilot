"""Variety against the 'templated content' problem: new scene types, per-video themes, narrative formats,
sources and the fact check."""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.generator import (
    MIN_WORDS_PER_SCENE,
    MOTION_TYPES,
    STRICT_SCRIPT_SCHEMA,
    SYSTEM_PROMPT_BASE,
    ScriptSpec,
    validate_quality,
)
from test_llm_providers import valid_script

NEW = {
    "COMPARISON": {"type": "COMPARISON", "left_label": "Coffee", "left_value": "$5 a day", "right_label": "Invest", "right_value": "$1,800 a year", "winner": "RIGHT"},
    "TIMELINE": {"type": "TIMELINE", "events": [{"label": "1948", "text": "The split"}, {"label": "1949", "text": "Two brands"}]},
    "STEPS": {"type": "STEPS", "title": "Spot it", "steps": ["Notice", "Name it", "Pause"]},
    "CALLOUT": {"type": "CALLOUT", "tag": "MYTH", "statement": "Money buys happiness", "tone": "BAD"},
}


def script_with(scene_index, action):
    raw = valid_script()
    raw["scenes"][scene_index]["motion_action"] = action
    return raw


def test_new_scene_types_are_registered_everywhere():
    for name in NEW:
        assert name in MOTION_TYPES and name in MIN_WORDS_PER_SCENE and name in SYSTEM_PROMPT_BASE
    variants = STRICT_SCRIPT_SCHEMA["properties"]["scenes"]["items"]["properties"]["motion_action"]["anyOf"]
    assert {v["properties"]["type"]["enum"][0] for v in variants} >= set(NEW)


@pytest.mark.parametrize("name", list(NEW))
def test_each_new_scene_type_validates_and_passes_the_quality_gate(name):
    script = ScriptSpec.model_validate(script_with(2, NEW[name]))
    assert script.scenes[2].motion_action.type == name
    assert validate_quality(script) == []


def test_new_scene_types_reject_bad_shapes():
    bad = [
        {**NEW["TIMELINE"], "events": [{"label": "1948", "text": "only one"}]},                      # needs 2-4
        {**NEW["TIMELINE"], "events": [{"label": "x", "text": "t"}] * 5},
        {**NEW["STEPS"], "steps": ["one"]},
        {**NEW["STEPS"], "steps": ["a", "b", "c", "d", "e"]},
        {**NEW["STEPS"], "steps": ["a", "x" * 60]},                                                  # too long for the card
        {**NEW["CALLOUT"], "tone": "ANGRY"},
        {**NEW["COMPARISON"], "winner": "BOTH"},
        {**NEW["COMPARISON"], "left_value": "x" * 40},
    ]
    for action in bad:
        with pytest.raises(ValidationError):
            ScriptSpec.model_validate(script_with(2, action))


def test_short_narration_is_rejected_for_the_text_heavy_new_scenes():
    raw = script_with(2, NEW["TIMELINE"])
    raw["scenes"][2]["text"] = "Too short here."  # 3 words, a timeline needs 8
    assert any("TIMELINE" in p for p in validate_quality(ScriptSpec.model_validate(raw)))
