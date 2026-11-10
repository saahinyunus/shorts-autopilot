"""`reserve.py review`: doubtful reserve scripts are removed, clean ones and unjudgeable ones stay."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import reserve
from engine.config import Settings
from engine.generator import ScriptSpec
from test_llm_providers import valid_script


def put(root, channel, topic):
    item = reserve.ReserveItem(topic=topic, script=ScriptSpec.model_validate(valid_script()))
    return reserve.save_item(channel, item, root)


def checker_that(decide):
    def complete_json(system, user, schema):
        result = decide(user)
        if isinstance(result, Exception):
            raise result
        return {"issues": result}

    return types.SimpleNamespace(complete_json=complete_json)


def test_doubtful_reserve_scripts_are_deleted_and_the_rest_stay(tmp_path):
    root = tmp_path / "reserve"
    good = put(root, "money", "The story of the Dutch tulip bubble")
    bad = put(root, "psychology", "Why a dollar buys less every year here")
    issue = [{"scene_id": 2, "claim": "70% of people", "problem": "unsupported statistic"}]
    # the checker flags a script whose text differs: both fixtures share text, so decide by call order
    answers = iter([[], issue])
    checker = checker_that(lambda user: next(answers))
    removed = reserve.review_reserve(Settings(_env_file=None), checker, root)
    assert [p.name for p, _ in removed] == [bad.name] and good.exists() and not bad.exists()
    assert "70% of people" in removed[0][1][0]


def test_a_checker_that_is_down_never_deletes_anything(tmp_path):
    root = tmp_path / "reserve"
    keep = put(root, "money", "The story of the Dutch tulip bubble")
    removed = reserve.review_reserve(Settings(_env_file=None), checker_that(lambda user: RuntimeError("quota")), root)
    assert removed == [] and keep.exists()


def test_the_provider_second_opinion_is_used_when_available(tmp_path):
    root = tmp_path / "reserve"
    put(root, "money", "The story of the Dutch tulip bubble")
    calls = []
    other = types.SimpleNamespace(complete_json=lambda *a: calls.append("other") or {"issues": []})
    main = types.SimpleNamespace(complete_json=lambda *a: calls.append("main") or {"issues": []}, second_opinion=lambda: other)
    reserve.review_reserve(Settings(_env_file=None), main, root)
    assert calls == ["other"]
