"""Rotation of narrative formats and visual themes, the planner/plan integration, sources and the fact check."""
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import daily, planner, variety
from engine.config import Settings
from engine.generator import FactCheckUnresolved, FailoverProvider, ScriptSpec, _review, fact_check, generate_script
from engine.history import History, HistoryEntry
from engine.uploader import build_metadata
from test_llm_providers import valid_script

ROOT = Path(__file__).resolve().parent.parent


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


# ============================================================ formats and themes (rotation)
def test_formats_never_repeat_back_to_back_and_cycle_through_all():
    picks = variety.choose_formats([""] * 7, [])
    assert all(a != b for a, b in zip(picks, picks[1:])) and set(picks[:6]) == set(variety.FORMAT_IDS)
    # the planner's pick is honoured unless it repeats the previous video or the one before in the batch
    assert variety.choose_formats(["true_story", "true_story"], ["myth_vs_reality"])[0] == "true_story"
    assert variety.choose_formats(["true_story", "true_story"], [])[1] != "true_story"
    assert variety.choose_formats(["myth_vs_reality"], ["myth_vs_reality"])[0] != "myth_vs_reality"
    assert variety.choose_formats(["nonsense"], [])[0] in variety.FORMAT_IDS  # unknown -> rotation


def test_least_recently_used_format_comes_first():
    recent = ["myth_vs_reality", "true_story", "study_or_experiment", "spot_the_signs", "before_after"]
    assert variety.choose_formats([""], recent) == ["common_mistake"]  # never used yet


@pytest.mark.parametrize("channel", ["money", "psychology", "unknown_channel"])
def test_themes_rotate_within_the_channels_palettes(channel):
    names = variety.choose_themes(channel, 8, [])
    assert all(a != b for a, b in zip(names, names[1:]))
    assert set(names) <= {t.name for t in variety.themes_for(channel)}
    assert len(set(names[: len(variety.themes_for(channel))])) == len(variety.themes_for(channel))  # all used before a repeat
    first = variety.themes_for(channel)[0]
    assert variety.theme_named(channel, first.name) == first and variety.theme_named(channel, "nope") is None
    assert first.props()["bgStyle"] in {"grid", "dots", "diagonal", "rings", "waves"}


def test_every_background_style_is_implemented_in_remotion():
    code = (ROOT / "src/components/motion/GridBackground.tsx").read_text(encoding="utf-8")
    for channel in ("money", "psychology", "other"):
        for theme in variety.themes_for(channel):
            assert f"bgStyle === '{theme.bg_style}'" in code, theme


def test_brand_theme_keeps_the_channels_main_colour():
    assert variety.themes_for("money")[0].accent == "#00E58F"
    assert variety.themes_for("psychology")[0].accent == "#FFE600"


# ================================================================== planner + plan items
def llm_with_formats(items):
    return types.SimpleNamespace(complete_json=lambda s, u, sch: {"topics": [{"topic": t, "format": f} for t, f in items]})


def test_planner_returns_the_format_it_chose_and_drops_unknown_ones():
    from engine.channels import load_channel

    llm = llm_with_formats(
        [
            ("How the Dassler feud created Adidas and Puma", "true_story"),
            ("Why a dollar loses value every year", "weird"),
            ("What a Ponzi scheme really is", "myth_vs_reality"),
        ]
    )
    out = planner.plan_topics_detailed(load_channel("money"), 3, [], llm)
    assert [(p.topic, p.format) for p in out] == [
        ("How the Dassler feud created Adidas and Puma", "true_story"),
        ("Why a dollar loses value every year", ""),
        ("What a Ponzi scheme really is", "myth_vs_reality"),
    ]
    assert "format" in planner.TOPICS_SCHEMA["properties"]["topics"]["items"]["required"]


def test_planner_prompt_lists_formats_and_recent_use():
    from engine.channels import load_channel

    seen = []

    def complete(system, user, schema):
        seen.append(user)
        topics = ["Topic number one is here", "Topic number two is here", "Topic number three is here"]
        return {"topics": [{"topic": t, "format": "true_story"} for t in topics]}

    llm = types.SimpleNamespace(complete_json=complete)
    planner.plan_topics_detailed(load_channel("money"), 1, [], llm, recent_formats=["true_story"])
    assert "myth_vs_reality" in seen[0] and "Recently used on this channel (oldest first): true_story" in seen[0]


FRESH = [
    "How Nintendo survived a century",
    "What a Ponzi scheme really is",
    "Why the penny costs more than its value",
    "How inflation quietly erodes savings",
]


def test_plan_items_carry_a_varied_format_and_theme(tmp_path):
    llm = llm_with_formats([(t, "true_story") for t in FRESH])  # the planner wants the same format every time
    settings = Settings(_env_file=None, state_dir=tmp_path / "state")
    items, errors = daily.build_plan(["money"], utc(2026, 11, 10, 3, 23), settings, History(), lambda: llm)
    assert errors == [] and len(items) == 3
    fmts, themes = [i["format"] for i in items], [i["theme"] for i in items]
    assert all(a != b for a, b in zip(fmts, fmts[1:])) and all(a != b for a, b in zip(themes, themes[1:]))
    assert set(fmts) <= set(variety.FORMAT_IDS) and set(themes) <= {t.name for t in variety.themes_for("money")}


def test_plan_continues_the_rotation_from_the_history(tmp_path):
    old = HistoryEntry(plan_id="old", channel="money", topic="Old topic number one here", format="true_story", theme="mint-grid")
    llm = llm_with_formats([(t, "true_story") for t in FRESH[:3]])
    settings = Settings(_env_file=None, state_dir=tmp_path / "state")
    items, _ = daily.build_plan(["money"], utc(2026, 11, 10, 3, 23), settings, History(entries=[old]), lambda: llm)
    assert items[0]["format"] != "true_story" and items[0]["theme"] != "mint-grid"  # not the same as the last video


def test_reserve_based_plan_items_also_get_variety(tmp_path):
    from test_reserve import put

    for t in ["The story of the Dutch tulip bubble", "Why a dollar buys less every year", "How the gold standard ended abruptly"]:
        put(tmp_path, "money", t)
    broken = types.SimpleNamespace(complete_json=lambda s, u, sch: (_ for _ in ()).throw(RuntimeError("down")))
    settings = Settings(_env_file=None, state_dir=tmp_path / "state")
    items, errors = daily.build_plan(["money"], utc(2026, 11, 10, 3, 23), settings, History(), lambda: broken)
    assert errors == [] and items and all(i["format"] and i["theme"] for i in items)


# ====================================================================== render props + pipeline
def test_props_carry_the_theme_and_its_accent():
    from engine import renderer
    from engine.audio_engine import SceneTiming, Timeline, WordTiming

    script = ScriptSpec.model_validate(valid_script())
    scenes = [SceneTiming(id=i + 1, start=i * 3.0, end=(i + 1) * 3.0) for i in range(8)]
    timeline = Timeline(words=[WordTiming(text="a", start=0.0, end=0.2)], scenes=scenes, duration=24.0)
    theme = variety.themes_for("money")[1]
    settings = Settings(_env_file=None)
    props = renderer.build_props(script, timeline, {}, "voice.wav", Path("."), settings, theme)
    assert props["theme"] == theme.props() and props["accentColor"] == theme.accent
    assert "theme" not in renderer.build_props(script, timeline, {}, "voice.wav", Path("."), settings)


def test_the_narrative_format_reaches_the_script_prompt(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from engine import pipeline
    from engine.config import get_settings

    seen = {}

    def fake_generate(topic, settings, **kw):
        seen["brief"], seen["review"] = kw["brief"], kw["review"]
        raise RuntimeError("stop here")

    monkeypatch.setattr(pipeline, "generate_script", fake_generate)
    monkeypatch.setattr(pipeline, "ensure_sfx", lambda s: {})
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    get_settings.cache_clear()
    args = ["--channel", "money", "--topic", "x", "--format", "myth_vs_reality", "--theme", "gold-dots"]
    CliRunner().invoke(pipeline.app, args)
    get_settings.cache_clear()
    assert "NARRATIVE FORMAT FOR THIS VIDEO" in seen["brief"] and "MYTH vs REALITY" in seen["brief"]
    assert "Educational content only" in seen["brief"] and seen["review"] is True  # channel rules kept, fact check on


def test_workflow_passes_format_and_theme_to_the_producer():
    import yaml

    wf = yaml.safe_load((ROOT / ".github/workflows/daily.yml").read_text(encoding="utf-8"))
    step = next(s for s in wf["jobs"]["produce"]["steps"] if str(s.get("name", "")).startswith("Produce the video"))
    assert {"FORMAT", "THEME"} <= set(step["env"]) and "--format" in step["run"] and "--theme" in step["run"]
    assert "${{" not in step["run"]


# ============================================================ sources + fact check
ISSUE = {"scene_id": 3, "claim": "73% of people", "problem": "invented-sounding statistic"}


def checker(issues=None, error=None):
    def complete(system, user, schema):
        if error:
            raise error
        return {"issues": issues or []}

    return complete


def provider_with(revise, complete_json):
    return types.SimpleNamespace(revise=revise, complete_json=complete_json, name="p")


def test_description_lists_the_sources():
    raw = {**valid_script(), "sources": ["Zeigarnik, 1927 (Berlin study)", "Ovsiankina, 1928"]}
    _, description, _ = build_metadata(ScriptSpec.model_validate(raw))
    assert "Sources: Zeigarnik, 1927 (Berlin study); Ovsiankina, 1928" in description
    assert "Sources:" not in build_metadata(ScriptSpec.model_validate(valid_script()))[1]  # old scripts have none


def test_fact_check_formats_issues_and_never_raises():
    script = ScriptSpec.model_validate(valid_script())
    c = lambda fn: types.SimpleNamespace(complete_json=fn)  # noqa: E731
    assert fact_check(script, c(checker([ISSUE]))) == ['Scene 3: "73% of people" (invented-sounding statistic)']
    assert fact_check(script, c(checker([]))) == []
    assert fact_check(script, c(checker(error=RuntimeError("quota")))) == []  # a missing check never costs the video


def test_review_repairs_a_doubtful_script_once():
    script = ScriptSpec.model_validate(valid_script())
    fixed = valid_script()
    fixed["scenes"][2]["text"] = "Research suggests many people notice this pattern every day."
    calls = []

    def revise(problems):
        calls.append(problems)
        return fixed

    result = _review(script, provider_with(revise, checker([ISSUE])), Settings(_env_file=None))
    assert result.review_notes and result.review_notes[0].startswith("FIXED")
    assert "Research suggests" in result.scenes[2].text
    assert len(calls) == 1 and "73% of people" in calls[0][1]


def test_unresolved_doubts_raise_with_the_flagged_script_instead_of_passing_silently():
    script = ScriptSpec.model_validate(valid_script())
    too_short = {**valid_script(), "scenes": valid_script()["scenes"][:3]}

    def broken(problems):
        raise RuntimeError("down")

    for revise in (broken, lambda problems: too_short):
        with pytest.raises(FactCheckUnresolved) as err:
            _review(script, provider_with(revise, checker([ISSUE])), Settings(_env_file=None))
        assert err.value.script is script and script.review_notes[0].startswith("FLAGGED")
        assert "73% of people" in str(err.value)


def test_the_repair_gets_a_second_round_with_the_reason_the_first_failed():
    script = ScriptSpec.model_validate(valid_script())
    fixed = valid_script()
    fixed["scenes"][2]["text"] = "Research suggests many people notice this pattern every day."
    answers = iter([{**valid_script(), "scenes": valid_script()["scenes"][:3]}, fixed])
    seen = []

    def revise(problems):
        seen.append(problems)
        return next(answers)

    result = _review(script, provider_with(revise, checker([ISSUE])), Settings(_env_file=None))
    assert len(seen) == 2 and "broke these rules" in seen[1][0] and result.review_notes[0].startswith("FIXED")


def test_review_can_be_switched_off_and_a_clean_script_passes_untouched():
    script = ScriptSpec.model_validate(valid_script())

    def never(*a, **k):
        raise AssertionError("must not be called")

    assert _review(script, provider_with(never, never), Settings(_env_file=None, fact_check=False)) is script
    assert _review(script, provider_with(never, checker([])), Settings(_env_file=None)).review_notes == []


def test_the_checker_is_a_different_provider_when_there_is_one():
    class P:
        def __init__(self, name):
            self.name, self.settings = name, Settings(_env_file=None)
            self.is_free, self.model_label, self.cost_summary = True, "m", lambda: ""

    a, b = P("a"), P("b")
    chain = FailoverProvider([a, b])
    assert chain.second_opinion() is b
    chain.index = 1
    assert chain.second_opinion() is a  # wraps around


def test_generate_script_reviews_only_when_asked():
    calls = []

    class Prov:
        name = "p"

        def propose(self, topic, brief=""):
            return valid_script()

        def complete_json(self, *a):
            calls.append("check")
            return {"issues": []}

    settings = Settings(_env_file=None, gemini_api_key="k")
    generate_script("t", settings, provider=Prov())
    assert calls == []  # default: no extra call
    generate_script("t", settings, provider=Prov(), review=True)
    assert calls == ["check"]


def test_telegram_message_shows_format_theme_sources_and_review():
    from engine import telegram_bot as tb

    item = {"channel": "money", "publish_at": "2026-11-10T13:00:00Z"}
    res = {
        "uploaded": True, "title": "T", "url": "u", "channel": "money", "format": "true_story", "theme": "gold-dots",
        "sources": ["Dassler brothers, 1948"], "review_notes": ["FIXED: Scene 3: x (y)"], "comment_text": "Q?",
    }
    show = dict(dry_run=False, run_url="r", notify_tz="Europe/Istanbul", verdict=None)
    text = tb.message_for_video(item, res, **show)
    assert "Biçim: Gerçek hikâye" in text and "Tema: gold-dots" in text and "Kaynak: Dassler brothers, 1948" in text
    assert "düzeltti" in text
    flagged = tb.message_for_video(item, {**res, "review_notes": ["FLAGGED: Scene 2: z (w)"]}, **show)
    assert "düzeltilemedi" in flagged
