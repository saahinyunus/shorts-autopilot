"""Tolerant validation (over-long on-screen text is shortened, not rejected) and the fact checker's severity kinds."""
import sys
import types
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.generator import ScriptSpec, _shorten, _validate_tolerant, fact_check
from test_llm_providers import valid_script


def test_shorten_cuts_at_a_word_boundary():
    assert _shorten("short", 40) == "short"
    out = _shorten("Why more money does not automatically buy more happiness", 30)
    assert len(out) <= 30 and out == "Why more money does not"
    assert _shorten("x" * 50, 10) == "x" * 10  # a single endless word is cut hard


def test_overlong_on_screen_strings_are_shortened_instead_of_rejected():
    raw = valid_script()
    raw["scenes"][0]["motion_action"] = {"type": "MINIMAL_TEXT", "headline": "A headline that is far too long for the screen to show it", "subtext": "ok"}
    raw["scenes"][2]["motion_action"] = {"type": "STEPS", "title": "Spot it", "steps": ["Notice the pattern that repeats every single day of the week", "Name it"]}
    raw["scenes"][3]["motion_action"] = {"type": "CALLOUT", "tag": "MYTH", "statement": "word " * 40, "tone": "BAD"}
    with pytest.raises(ValidationError):
        ScriptSpec.model_validate(raw)  # the strict model still refuses it ...
    script = _validate_tolerant(raw)  # ... the tolerant path repairs exactly the long strings
    assert len(script.scenes[0].motion_action.headline) <= 40
    assert all(len(s) <= 40 for s in script.scenes[2].motion_action.steps) and len(script.scenes[2].motion_action.steps) == 2
    assert len(script.scenes[3].motion_action.statement) <= 90
    assert script.scenes[0].motion_action.subtext == "ok"  # nothing else is touched


def test_other_validation_errors_are_not_hidden_by_the_tolerance():
    raw = valid_script()
    raw["scenes"][0]["motion_action"] = {"type": "MINIMAL_TEXT"}  # missing headline: a real error
    with pytest.raises(ValidationError):
        _validate_tolerant(raw)
    raw = valid_script()
    raw["scenes"][1]["motion_action"] = {"type": "CALLOUT", "tag": "X", "statement": "long " * 30, "tone": "ANGRY"}  # long AND invalid
    with pytest.raises(ValidationError):
        _validate_tolerant(raw)


def checker(*issues):
    return types.SimpleNamespace(complete_json=lambda s, u, sch: {"issues": list(issues)})


def issue(kind, claim="73% of people"):
    return {"scene_id": 3, "claim": claim, "problem": "doubtful", "kind": kind}


def test_only_wrong_facts_and_invented_specifics_block_a_script_not_exaggeration():
    script = ScriptSpec.model_validate(valid_script())
    out = fact_check(script, checker(issue("overclaim", "it instantly works"), issue("invented_specific"), issue("false_claim", "the sun is cold")))
    assert len(out) == 2 and "73% of people" in out[0] and "the sun is cold" in out[1]
    assert fact_check(script, checker(issue("overclaim"))) == []


def test_an_issue_without_a_kind_still_counts():
    script = ScriptSpec.model_validate(valid_script())
    assert len(fact_check(script, checker({"scene_id": 1, "claim": "x claim", "problem": "p"}))) == 1


def test_the_checker_is_told_that_a_missing_citation_is_not_an_error():
    from engine.generator import FACT_CHECK_SCHEMA, FACT_CHECK_SYSTEM

    assert "absence of a citation is not an error" in FACT_CHECK_SYSTEM
    kinds = FACT_CHECK_SCHEMA["properties"]["issues"]["items"]["properties"]["kind"]["enum"]
    assert kinds == ["false_claim", "invented_specific", "overclaim"]
