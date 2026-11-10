"""A slot must never stay empty: LLM failover, the reserve of ready-made scripts, planner + pipeline fallbacks."""
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import daily, reserve
from engine.config import Settings
from engine.generator import FailoverProvider, GeminiProvider, GroqProvider, LLMOutputError, ScriptSpec, get_llm_provider
from engine.history import History, HistoryEntry
from test_llm_providers import valid_script

ROOT = Path(__file__).resolve().parent.parent


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


class Fake:
    """Minimal LLMProvider stand-in; `fail` makes every call raise the given exception."""

    def __init__(self, name, fail=None, raw=None):
        self.name, self.fail, self.raw, self.calls = name, fail, raw or valid_script(), []
        self.settings = Settings(_env_file=None, gemini_api_key="k")
        self.is_free = True
        self.model_label, self.cost_summary = "m", lambda: "free"

    def _go(self, what):
        self.calls.append(what)
        if self.fail:
            raise self.fail
        return self.raw

    def propose(self, topic, brief=""):
        return self._go("propose")

    def revise(self, problems):
        return self._go("revise")

    def complete_json(self, system, user, schema):
        return self._go("json")


# ------------------------------------------------------------------ LLM failover
def test_failover_switches_to_the_next_provider_when_one_is_down():
    down, up = Fake("gemini", fail=RuntimeError("Gemini API error 429")), Fake("groq")
    chain = FailoverProvider([down, up])
    assert chain.propose("t") == up.raw and chain.name == "groq"
    assert down.calls == ["propose"] and up.calls == ["propose"]


def test_failover_restarts_the_conversation_on_the_new_provider():
    first, second = Fake("gemini"), Fake("groq")
    chain = FailoverProvider([first, second])
    chain.propose("topic")
    first.fail = RuntimeError("quota")  # dies between attempt 1 and the revision
    chain.revise(["too short"])
    assert second.calls == ["propose"]  # a fresh proposal, not a revision of a conversation it never saw


def test_failover_does_not_swallow_bad_output_and_reports_when_everything_is_down():
    bad = Fake("gemini", fail=LLMOutputError("not json"))
    with pytest.raises(LLMOutputError):
        FailoverProvider([bad, Fake("groq")]).propose("t")
    with pytest.raises(RuntimeError, match="All LLM providers failed"):
        FailoverProvider([Fake("a", fail=RuntimeError("x")), Fake("b", fail=RuntimeError("y"))]).complete_json("s", "u", {})


def test_get_llm_provider_chains_every_provider_that_has_a_key():
    s = Settings(_env_file=None, gemini_api_key="g", groq_api_key="q")
    chain = get_llm_provider(s)
    assert isinstance(chain, FailoverProvider) and [p.name for p in chain.providers] == ["gemini", "groq", "groq"]
    assert isinstance(get_llm_provider(Settings(_env_file=None, gemini_api_key="g")), GeminiProvider)
    assert isinstance(get_llm_provider(Settings(_env_file=None, gemini_api_key="g", groq_api_key="q", llm_failover=False)), GeminiProvider)
    s2 = Settings(_env_file=None, llm_provider="groq", groq_api_key="q", gemini_api_key="g")
    assert [p.name for p in get_llm_provider(s2).providers] == ["groq", "groq", "gemini"]


# --------------------------------------------------------------------- reserve store
def reserve_root(tmp_path):
    return tmp_path / "state" / "reserve"


def put(tmp_path, channel, topic):
    return reserve.save_item(channel, reserve.ReserveItem(topic=topic, script=ScriptSpec.model_validate(valid_script())), reserve_root(tmp_path))


def test_available_skips_published_topics_and_prune_deletes_them(tmp_path):
    keep = put(tmp_path, "money", "The story of the Dutch tulip bubble")
    used = put(tmp_path, "money", "How compound interest builds wealth slowly")
    history = History(entries=[HistoryEntry(plan_id="x", channel="money", topic="How compound interest builds wealth slowly")])
    assert [p for p, _ in reserve.available("money", history, root=reserve_root(tmp_path))] == [keep]
    assert reserve.prune(history, reserve_root(tmp_path)) == [used] and not used.exists() and keep.exists()


def test_broken_reserve_file_is_ignored(tmp_path):
    (reserve_root(tmp_path) / "money").mkdir(parents=True)
    (reserve_root(tmp_path) / "money" / "junk.json").write_text("{not json", encoding="utf-8")
    assert reserve.available("money", History(), root=reserve_root(tmp_path)) == []


# ------------------------------------------------------------------- planner fallback
def settings_for(tmp_path):
    return Settings(_env_file=None, state_dir=tmp_path / "state")


def test_plan_uses_the_reserve_when_the_llm_cannot_plan(tmp_path):
    for i, t in enumerate(["The story of the Dutch tulip bubble", "Why a dollar buys less every year", "How the gold standard ended abruptly"]):
        put(tmp_path, "money", t)

    def broken(system, user, schema):
        raise RuntimeError("Gemini API error 429")

    items, errors = daily.build_plan(
        ["money"], utc(2026, 11, 10, 3, 23), settings_for(tmp_path), History(), lambda: types.SimpleNamespace(complete_json=broken)
    )
    assert errors == [] and len(items) == 3
    assert all(i["reserve"] for i in items) and len({i["topic"] for i in items}) == 3


def test_plan_gives_every_slot_a_distinct_backup_script(tmp_path):
    for t in ["The story of the Dutch tulip bubble", "Why a dollar buys less every year", "How the gold standard ended abruptly"]:
        put(tmp_path, "money", t)
    fresh = ["How Nintendo survived a century", "Why decoy pricing works on shoppers", "What a Ponzi scheme really is"]
    llm = types.SimpleNamespace(complete_json=lambda s, u, sch: {"topics": [{"topic": t} for t in fresh]})
    items, errors = daily.build_plan(["money"], utc(2026, 11, 10, 3, 23), settings_for(tmp_path), History(), lambda: llm)
    assert errors == [] and [i["topic"] for i in items] == fresh
    assert len({i["reserve"] for i in items}) == 3 and all(items[k]["reserve"] for k in range(3))


def test_plan_reports_an_error_only_when_neither_llm_nor_reserve_can_fill_a_slot(tmp_path):
    def broken(system, user, schema):
        raise RuntimeError("down")

    put(tmp_path, "money", "The story of the Dutch tulip bubble")
    items, errors = daily.build_plan(
        ["money"], utc(2026, 11, 10, 3, 23), settings_for(tmp_path), History(), lambda: types.SimpleNamespace(complete_json=broken)
    )
    assert len(items) == 1 and len(errors) == 1 and "2 slot" in errors[0]


# ------------------------------------------------------------- pipeline fallback
def test_pipeline_falls_back_to_the_reserve_script_when_generation_fails(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from engine import pipeline

    item = reserve.save_item("money", reserve.ReserveItem(topic="The story of the Dutch tulip bubble", script=ScriptSpec.model_validate(valid_script())), tmp_path)

    def boom(*a, **k):
        raise RuntimeError("All LLM providers failed")

    monkeypatch.setattr(pipeline, "generate_script", boom)
    monkeypatch.setattr(pipeline, "ensure_sfx", lambda s: {})
    stop = RuntimeError("stop after the script step")
    monkeypatch.setattr(pipeline, "synthesize_audio", lambda *a, **k: (_ for _ in ()).throw(stop))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    from engine.config import get_settings

    get_settings.cache_clear()
    result = CliRunner().invoke(pipeline.app, ["--channel", "money", "--topic", "Fresh topic", "--reserve-file", str(item)])
    get_settings.cache_clear()
    assert "using the reserve script 'The story of the Dutch tulip bubble'" in result.output
    run_dir = next((tmp_path / "out").iterdir())
    assert json.loads((run_dir / "script.json").read_text(encoding="utf-8"))["title"] == "T"


def test_pipeline_without_a_reserve_still_fails_loudly(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from engine import pipeline

    monkeypatch.setattr(pipeline, "generate_script", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("quota")))
    monkeypatch.setattr(pipeline, "ensure_sfx", lambda s: {})
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    from engine.config import get_settings

    get_settings.cache_clear()
    result = CliRunner().invoke(pipeline.app, ["--topic", "x"])
    get_settings.cache_clear()
    assert result.exit_code != 0


# ------------------------------------------------------------------ real reserve files
def test_every_committed_reserve_script_is_valid():
    from engine.generator import validate_quality

    for path in (ROOT / "state" / "reserve").glob("*/*.json"):
        item = reserve.load_item(path)
        assert validate_quality(item.script) == [], path


def test_workflow_retries_and_wires_the_reserve():
    import yaml

    wf = yaml.safe_load((ROOT / ".github/workflows/daily.yml").read_text(encoding="utf-8"))
    run = next(s for s in wf["jobs"]["produce"]["steps"] if str(s.get("name", "")).startswith("Produce the video"))["run"]
    assert "for attempt in 1 2 3" in run and "--reserve-file" in run and "--resume" in run
    record = " ".join(str(s.get("run", "")) for s in wf["jobs"]["record"]["steps"])
    assert "reserve.py fill" in record and "git add state/" in record


# --------------------------------------------------------------- Gemini daily quota
def test_daily_quota_moves_to_the_second_model_without_waiting(monkeypatch):
    from engine import generator as gen
    from test_llm_providers import FakeResp, Recorder, gemini_reply, gemini_settings

    day = FakeResp(429, {"error": {"message": "Quota exceeded ... GenerateRequestsPerDayPerProjectPerModel-FreeTier"}})
    rec = Recorder(day, gemini_reply(valid_script()))
    monkeypatch.setattr(gen.httpx, "post", rec)
    monkeypatch.setattr(gen.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("must not wait for a daily quota")))
    gen.generate_script("t", gemini_settings(gemini_model="a-flash", gemini_fallback_model="b-flash"))
    assert "a-flash" in rec.calls[0]["url"] and "b-flash" in rec.calls[1]["url"]


def test_per_minute_limit_is_still_retried(monkeypatch):
    from engine import generator as gen
    from test_llm_providers import FakeResp, Recorder, gemini_reply, gemini_settings

    rec = Recorder(FakeResp(429, {"error": {"message": "Too many requests per minute"}}), gemini_reply(valid_script()))
    monkeypatch.setattr(gen.httpx, "post", rec)
    monkeypatch.setattr(gen.time, "sleep", lambda s: None)
    gen.generate_script("t", gemini_settings())
    assert len(rec.calls) == 2 and rec.calls[0]["url"] == rec.calls[1]["url"]


def test_planner_treats_other_channels_reserve_topics_as_taken(tmp_path):
    put(tmp_path, "psychology", "How the decoy effect tricks you into buying larger sizes")
    seen = []

    def complete(system, user, schema):
        seen.append(user)
        return {"topics": [{"topic": t} for t in ["How Nintendo survived a century", "What a Ponzi scheme really is", "Why the penny costs more than its value"]]}

    daily.build_plan(["money"], utc(2026, 11, 10, 3, 23), settings_for(tmp_path), History(), lambda: types.SimpleNamespace(complete_json=complete))
    assert "decoy effect tricks you into buying larger sizes" in seen[0]


def test_planner_also_avoids_its_own_channels_reserve_topics(tmp_path):
    put(tmp_path, "money", "How a gold standard once limited every government")
    seen = []

    def complete(system, user, schema):
        seen.append(user)
        return {"topics": [{"topic": t} for t in ["How Nintendo survived a century", "What a Ponzi scheme really is", "Why the penny costs more than its value"]]}

    daily.build_plan(["money"], utc(2026, 11, 10, 3, 23), settings_for(tmp_path), History(), lambda: types.SimpleNamespace(complete_json=complete))
    assert "gold standard once limited every government" in seen[0]  # shown as already taken


# ----------------------------------------------------- rule-breaking scripts hop to the next provider
def bad_script():
    raw = valid_script()
    raw["scenes"] = raw["scenes"][:3]  # far too short: breaks the quality rules
    return raw


def test_a_provider_that_keeps_breaking_the_rules_is_replaced_after_two_tries():
    from engine.generator import generate_script

    weak, strong = Fake("groq-a", raw=bad_script()), Fake("groq-b")
    chain = FailoverProvider([weak, strong])
    script = generate_script("topic", Settings(_env_file=None, gemini_api_key="k"), provider=chain)
    assert len(script.scenes) == 8
    assert weak.calls == ["propose", "revise"] and strong.calls == ["propose"]  # 2 tries, then a fresh start elsewhere


def test_attempts_scale_with_the_number_of_providers():
    assert FailoverProvider([Fake("a")]).suggested_attempts == 3
    assert FailoverProvider([Fake("a"), Fake("b"), Fake("c")]).suggested_attempts == 6
    assert FailoverProvider([Fake(str(i)) for i in range(5)]).suggested_attempts == 6


def test_actions_use_groq_first_because_gemini_free_tier_is_tiny():
    import yaml

    wf = yaml.safe_load((Path(__file__).resolve().parent.parent / ".github/workflows/daily.yml").read_text(encoding="utf-8"))
    assert wf["env"]["LLM_PROVIDER"] == "groq"
