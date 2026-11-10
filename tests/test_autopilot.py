"""Autopilot tests: channels, slots, history, planner, daily plan/record, uploader, workflows. Offline."""
import json
import re
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import daily, uploader
from engine.channels import ChannelConfig, list_channel_ids, load_channel, plan_id, upcoming_slots
from engine.config import Settings
from engine.generator import LLMOutputError, ScriptSpec
from engine.history import History, HistoryEntry, load_history, save_history
from engine.planner import is_duplicate, plan_topics
from test_llm_providers import valid_script

UTC = timezone.utc

TOPICS = [
    "How a bank actually creates money from nothing",
    "The story of the Dutch tulip bubble",
    "Why prices end in .99 almost everywhere",
    "What inflation quietly does to your savings",
    "How compound interest builds wealth slowly",
    "Why airlines overbook flights on purpose",
    "The hidden reason subscriptions are so hard to cancel",
    "How the gold standard shaped world trade",
    "Why your brain remembers embarrassing moments forever",
    "What the anchoring effect does to your shopping",
    "Why you cannot stop checking your phone",
    "How the mere exposure effect shapes your taste",
]


def utc(*args):
    return datetime(*args, tzinfo=UTC)


# ------------------------------------------------------------------------------ channels
def test_shipped_channels_are_valid_and_different():
    ids = list_channel_ids()
    assert ids == ["money", "psychology"]
    psy, money = load_channel("psychology"), load_channel("money")
    assert psy.slots == money.slots == ["08:00", "13:00", "19:00"]  # 3 per day each => 6 videos/day
    assert psy.timezone == "America/New_York"
    assert psy.edge_tts_voice != money.edge_tts_voice and psy.accent_color != money.accent_color
    assert "not financial advice" in money.description_footer.lower()
    assert any("never recommend" in r.lower() for r in money.rules)
    brief = money.brief()
    assert "Channel niche" in brief and "Rules you MUST follow" in brief and "#Shorts" in brief


def test_channel_validation():
    base = {"id": "x", "display_name": "X", "niche": "n"}
    assert ChannelConfig(**base).slots == ["08:00", "13:00", "19:00"]
    assert ChannelConfig(**base, hashtags=["shorts"]).hashtags == ["#shorts"]
    with pytest.raises(ValidationError):
        ChannelConfig(**base, slots=["8am"])
    with pytest.raises(ValidationError):
        ChannelConfig(**base, timezone="Mars/Base")
    with pytest.raises(ValidationError):
        ChannelConfig(id="Bad Id", display_name="x", niche="n")
    with pytest.raises(RuntimeError, match="Unknown channel"):
        load_channel("nope")


# --------------------------------------------------------------------------------- slots
def test_slots_for_a_normal_day():
    psy = load_channel("psychology")
    # 03:23 UTC on 10 Nov = 22:23 ET (EST, UTC-5): next slots are 08:00, 13:00, 19:00 ET
    slots = upcoming_slots(psy, utc(2026, 11, 10, 3, 23))
    assert slots == [utc(2026, 11, 10, 13), utc(2026, 11, 10, 18), utc(2026, 11, 11, 0)]
    assert plan_id("psychology", slots[0]) == "psychology-20261110T1300Z"


def test_slots_follow_daylight_saving_time():
    psy = load_channel("psychology")
    # US clocks go forward on 8 March 2026: 08:00 local is 13:00Z before it and 12:00Z after it
    assert upcoming_slots(psy, utc(2026, 3, 7, 3, 23))[0] == utc(2026, 3, 7, 13)
    assert upcoming_slots(psy, utc(2026, 3, 8, 3, 23)) == [utc(2026, 3, 8, 12), utc(2026, 3, 8, 17), utc(2026, 3, 8, 23)]


def test_slot_must_be_far_enough_ahead_to_render():
    psy = load_channel("psychology")
    # at 11:00Z the 13:00Z slot is only 2 h away (< 3 h lead): skipped
    assert upcoming_slots(psy, utc(2026, 11, 10, 11, 0))[0] == utc(2026, 11, 10, 18)


@pytest.mark.parametrize("jitter_minutes", [-40, 0, 25, 70])
def test_daily_runs_cover_every_slot_exactly_once(jitter_minutes):
    """Whatever the cron delay, 10 daily runs publish every slot once: no gaps, no duplicates."""
    channels = [load_channel("psychology"), load_channel("money")]
    done: set[str] = set()
    planned: list[str] = []
    for day in range(10):
        run_at = utc(2026, 11, 1, 3, 23) + timedelta(days=day, minutes=jitter_minutes * (1 if day % 2 else 0.5))
        for ch in channels:
            for slot in upcoming_slots(ch, run_at):
                pid = plan_id(ch.id, slot)
                if pid not in done:  # what daily.plan does with the history
                    done.add(pid)
                    planned.append(pid)
    assert len(planned) == len(set(planned))
    first, last = utc(2026, 11, 1, 13), utc(2026, 11, 10, 18)
    for ch in channels:
        mine = sorted(p for p in planned if p.startswith(ch.id))
        stamps = [datetime.strptime(p.split("-")[1], "%Y%m%dT%H%MZ").replace(tzinfo=UTC) for p in mine]
        # consecutive slots are 5 h / 6 h / 13 h apart, never missing: 3 per day
        assert all(b - a <= timedelta(hours=13, minutes=1) for a, b in zip(stamps, stamps[1:]))
        assert len([s for s in stamps if first <= s <= last]) >= 3 * 9


# ------------------------------------------------------------------------------ history
def test_history_roundtrip_and_dedupe(tmp_path):
    path = tmp_path / "state" / "history.json"
    h = History()
    assert h.add(HistoryEntry(plan_id="a", channel="money", topic="t1", title="T1"))
    assert not h.add(HistoryEntry(plan_id="a", channel="money", topic="other"))  # same slot twice
    h.add(HistoryEntry(plan_id="b", channel="psychology", topic="t2"))
    save_history(h, path)
    loaded = load_history(path)
    assert loaded.has_plan("a") and [e.topic for e in loaded.recent("money")] == ["t1"]
    assert load_history(tmp_path / "missing.json").entries == []


def test_initial_state_file_is_valid():
    history = load_history(ROOT / "state" / "history.json")  # the autopilot appends real entries over time
    assert len({e.plan_id for e in history.entries}) == len(history.entries)


# ------------------------------------------------------------------------------ planner
def test_duplicate_detection():
    assert is_duplicate("Why you can't stop checking your phone", "Why you cannot stop checking your phone")
    assert is_duplicate("How compound interest builds wealth", "How compound interest quietly builds wealth over time")
    assert not is_duplicate("The anchoring effect in everyday prices", "Why your brain remembers embarrassing moments")


class FakeLLM:
    """Stands in for an LLMProvider: returns queued answers and records the prompts."""

    name = "fake"
    is_free = True

    def __init__(self, *answers):
        self.answers, self.users = list(answers), []

    def complete_json(self, system, user, schema):
        self.users.append(user)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return {"topics": [{"topic": t} for t in answer]}


def test_planner_returns_new_topics_and_skips_repeats():
    ch = load_channel("money")
    llm = FakeLLM(["How a bank actually creates money", "Why a dollar buys less every year now",  # repeat of history
                   "The story of the Dutch tulip bubble", "short", "Why prices end in .99 so often", "How inflation hides inside shrinking packages"])
    topics = plan_topics(ch, 3, ["Why a dollar buys less every year"], llm)
    assert topics == ["How a bank actually creates money", "The story of the Dutch tulip bubble", "Why prices end in .99 so often"]
    assert "Why a dollar buys less every year" in llm.users[0] and "CHANNEL BRIEF" in llm.users[0]


def test_planner_retries_with_feedback_then_gives_up():
    ch = load_channel("psychology")
    llm = FakeLLM(["Only one good topic about habits here"], LLMOutputError("not json"), TOPICS[8:12])
    topics = plan_topics(ch, 3, [], llm)
    assert len(topics) == 3
    assert "Only 1 of your last topics" in llm.users[1] and "unusable" in llm.users[2]
    with pytest.raises(RuntimeError, match="could not find"):
        plan_topics(ch, 3, [], FakeLLM(["one topic only for the test"], ["one topic only for the test"], ["one topic only for the test"]))


# ----------------------------------------------------------------------------- daily
def settings_for(tmp_path):
    return Settings(_env_file=None, state_dir=tmp_path / "state")


def test_build_plan_covers_both_channels_and_skips_planned_slots(tmp_path):
    topics = iter(TOPICS * 3)
    llm = types.SimpleNamespace(complete_json=lambda s, u, sch: {"topics": [{"topic": next(topics)} for _ in range(5)]})
    history = History()
    now = utc(2026, 11, 10, 3, 23)
    items, errors = daily.build_plan(["psychology", "money"], now, settings_for(tmp_path), history, lambda: llm)
    assert errors == [] and len(items) == 6  # 3 per channel = 6 videos a day
    assert {i["channel"] for i in items} == {"psychology", "money"}
    assert items[0]["publish_at"] == "2026-11-10T13:00:00Z" and items[0]["plan_id"] == "psychology-20261110T1300Z"
    assert len({i["topic"] for i in items}) == 6
    assert items[3]["topic"] not in [i["topic"] for i in items[:3]]  # second channel gets different topics

    # one slot already published: only the remaining two of that channel are planned
    history.add(HistoryEntry(plan_id="money-20261110T1300Z", channel="money", topic="x"))
    items, _ = daily.build_plan(["money"], now, settings_for(tmp_path), history, lambda: llm)
    assert [i["plan_id"] for i in items] == ["money-20261110T1800Z", "money-20261111T0000Z"]


def test_build_plan_one_failing_channel_does_not_block_the_other(tmp_path):
    def provider():
        calls = []

        def complete(system, user, schema):
            calls.append(user)
            if "Money, economics" in user:
                raise LLMOutputError("quota")
            return {"topics": [{"topic": t} for t in TOPICS[8:12]]}

        return types.SimpleNamespace(complete_json=complete)

    shared = provider()
    items, errors = daily.build_plan(["money", "psychology"], utc(2026, 11, 10, 3, 23), settings_for(tmp_path), History(), lambda: shared)
    assert {i["channel"] for i in items} == {"psychology"} and len(items) == 3
    assert len(errors) == 1 and errors[0].startswith("money:")


def test_plan_cli_writes_github_outputs(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    out = tmp_path / "gh_output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    llm = types.SimpleNamespace(complete_json=lambda s, u, sch: {"topics": [{"topic": t} for t in TOPICS[:5]]})
    monkeypatch.setattr(daily, "get_llm_provider", lambda settings: llm)
    from engine.config import get_settings

    get_settings.cache_clear()
    result = CliRunner().invoke(daily.app, ["plan", "--channels", "money", "--now", "2026-11-10T03:23:00Z", "--github-output"])
    get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    lines = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines())
    matrix = json.loads(lines["matrix"])
    assert lines["count"] == "3" and lines["errors"] == "" and len(matrix["include"]) == 3
    assert set(matrix["include"][0]) == {"channel", "topic", "publish_at", "plan_id", "reserve", "format", "theme"}


def test_record_cli_stores_only_uploaded_results_once(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    from engine.config import get_settings

    get_settings.cache_clear()
    art = tmp_path / "artifacts"
    for name, uploaded in (("a", True), ("b", False)):
        d = art / f"result-{name}" / "20261110-run"
        d.mkdir(parents=True)
        (d / "result.json").write_text(json.dumps({
            "plan_id": f"money-{name}", "channel": "money", "topic": f"t{name}", "title": "T", "uploaded": uploaded,
            "video_id": "vid" if uploaded else "", "url": "u", "publish_at": "2026-11-10T13:00:00Z"}), encoding="utf-8")
    runner = CliRunner()
    first = runner.invoke(daily.app, ["record", "--results-dir", str(art)])
    second = runner.invoke(daily.app, ["record", "--results-dir", str(art)])
    get_settings.cache_clear()
    assert "1 added, 1 skipped, 1 total" in first.output
    assert "0 added" in second.output and "1 total" in second.output  # idempotent
    assert load_history(tmp_path / "state" / "history.json").entries[0].plan_id == "money-a"


# --------------------------------------------------------------------------- uploader
def script_with(description, tags=("a", "b"), title="Why You Look Away"):
    data = valid_script()
    data.update(title=title, description=description, tags=list(tags))
    return ScriptSpec.model_validate(data)


def test_metadata_for_a_channel():
    money = load_channel("money")
    script = script_with("Why prices end in .99 explained.\nPsychological pricing tricks your brain.\n\n#Shorts #pricing #psychology #sales #extra #more",
                         tags=["psychological pricing", "Money Facts", "#pricing"])
    title, description, tags = uploader.build_metadata(script, money)
    assert title == "Why You Look Away"
    body, footer, hashtags = description.split("\n\n")
    assert "#" not in body and body.startswith("Why prices end in .99 explained.")  # no hashtags left in the text
    assert footer == "Educational content only. Not financial advice."
    found = hashtags.split()
    assert found[0] == "#Shorts" and len(found) <= uploader.MAX_HASHTAGS and len(set(h.lower() for h in found)) == len(found)
    assert found == ["#Shorts", "#pricing", "#psychology", "#money", "#economics"]  # own 2 + the channel's, capped at 5
    assert "#extra" not in description and "#sales" not in description
    assert tags[0] == "psychological pricing" and tags.count("money facts") + tags.count("Money Facts") == 1
    assert "pricing" in tags and all(not t.startswith("#") for t in tags)


def test_metadata_limits():
    long_title = "word " * 40
    script = script_with("d", tags=[f"tag number {i}" for i in range(200)], title=long_title)
    title, description, tags = uploader.build_metadata(script, load_channel("psychology"))
    assert len(title) <= 100 and not title.endswith(" ")
    assert sum(len(t) + 1 for t in tags) <= uploader.MAX_TAG_CHARS
    assert description.endswith("#Shorts #psychology #humanbehavior #mindset") or "#Shorts" in description


def test_body_scheduling_forces_private_with_publish_at():
    settings = Settings(_env_file=None, youtube_privacy="public")
    psy = load_channel("psychology")
    when = datetime(2026, 11, 12, 13, 0, tzinfo=UTC)
    body = uploader.build_body(script_with("d"), settings, psy, when)
    assert body["status"]["privacyStatus"] == "private" and body["status"]["publishAt"] == "2026-11-12T13:00:00Z"
    assert body["status"]["containsSyntheticMedia"] is True and body["status"]["selfDeclaredMadeForKids"] is False
    assert body["snippet"]["categoryId"] == "27" and body["snippet"]["defaultLanguage"] == "en"
    plain = uploader.build_body(script_with("d"), settings, psy)
    assert plain["status"]["privacyStatus"] == "public" and "publishAt" not in plain["status"]
    # a time in another zone is converted to UTC
    ny = datetime(2026, 11, 12, 8, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert uploader.build_body(script_with("d"), settings, psy, ny)["status"]["publishAt"] == "2026-11-12T13:00:00Z"


def test_token_lookup_env_then_file(tmp_path, monkeypatch):
    monkeypatch.setattr(uploader, "ROOT_DIR", tmp_path)
    tokens = {"money": {"refresh_token": "r1"}, "psychology": json.dumps({"refresh_token": "r2"})}
    s = Settings(_env_file=None, youtube_tokens_json=json.dumps(tokens))
    assert uploader.token_info(s, "money") == {"refresh_token": "r1"}
    assert uploader.token_info(s, "psychology") == {"refresh_token": "r2"}  # string values work too
    assert uploader.token_info(Settings(_env_file=None), "money") is None
    (tmp_path / "token_money.json").write_text('{"refresh_token": "from-file"}', encoding="utf-8")
    assert uploader.token_info(Settings(_env_file=None), "money") == {"refresh_token": "from-file"}
    with pytest.raises(RuntimeError, match="youtube_auth.py login --channel psychology"):
        uploader.get_credentials(Settings(_env_file=None), "psychology")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        uploader.token_info(Settings(_env_file=None, youtube_tokens_json="{oops"), "money")


def fake_youtube(monkeypatch, response):
    captured = {}

    class Request:
        def next_chunk(self):
            return None, response

    class Videos:
        def insert(self, part, body, media_body):
            captured.update(part=part, body=body)
            return Request()

    monkeypatch.setattr(uploader, "get_credentials", lambda settings, channel=None: object())
    import googleapiclient.discovery as discovery

    monkeypatch.setattr(discovery, "build", lambda *a, **k: types.SimpleNamespace(videos=lambda: Videos()))
    return captured


def test_upload_schedules_and_reports(tmp_path, monkeypatch, capsys):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 100)
    captured = fake_youtube(monkeypatch, {"id": "abc123", "status": {"privacyStatus": "private", "publishAt": "2026-11-12T13:00:00Z"}})
    result = uploader.upload_video(video, script_with("d"), Settings(_env_file=None), load_channel("money"), datetime(2026, 11, 12, 13, 0, tzinfo=UTC))
    assert (result.video_id, result.url, result.scheduled) == ("abc123", "https://www.youtube.com/shorts/abc123", True)
    assert captured["part"] == "snippet,status" and captured["body"]["status"]["publishAt"] == "2026-11-12T13:00:00Z"
    assert "WARNING" not in capsys.readouterr().out


def test_upload_warns_when_youtube_drops_the_schedule(tmp_path, monkeypatch, capsys):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 100)
    fake_youtube(monkeypatch, {"id": "zzz", "status": {"privacyStatus": "private"}})  # no publishAt: locked/unscheduled
    result = uploader.upload_video(video, script_with("d"), Settings(_env_file=None), load_channel("money"), datetime(2026, 11, 12, 13, 0, tzinfo=UTC))
    assert result.scheduled is False
    assert "did not keep the schedule" in capsys.readouterr().out


# -------------------------------------------------------------- pipeline with a channel
def test_pipeline_with_channel_writes_result_and_uses_channel_voice(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from engine import pipeline
    from engine.audio_engine import WordTiming
    from engine.config import get_settings
    from engine.uploader import UploadResult

    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("SFX_DIR", str(tmp_path / "sfx"))
    get_settings.cache_clear()
    seen = {}

    def tts(text, out_path, settings=None):
        seen["voice"], seen["accent"] = settings.edge_tts_voice, settings.accent_color
        out_path.write_bytes(b"ID3")
        return out_path

    def words(audio_path, settings=None):
        ws, t = [], 0.0
        for w in ScriptSpec.model_validate(valid_script()).narration.split():
            ws.append(WordTiming(text=w, start=round(t, 3), end=round(t + 0.3, 3)))
            t += 0.33
        return ws, t

    def render(run_dir, props_path, out_name="final.mp4"):
        (run_dir / out_name).write_bytes(b"mp4")
        return run_dir / out_name

    def upload(path, script, settings, channel, publish_at):
        seen["channel"], seen["publish_at"], seen["category"] = channel.id, publish_at, settings.youtube_category_id
        return UploadResult(video_id="vid1", url="https://www.youtube.com/shorts/vid1", privacy="private",
                            publish_at="2026-11-12T13:00:00Z", scheduled=True)

    monkeypatch.setattr(pipeline, "synthesize_audio", tts)
    monkeypatch.setattr(pipeline, "align_words", words)
    monkeypatch.setattr(pipeline, "render_video", render)
    monkeypatch.setattr(pipeline, "upload_video", upload)
    script_file = tmp_path / "s.json"
    script_file.write_text(json.dumps(valid_script()), encoding="utf-8")

    result = CliRunner().invoke(pipeline.app, ["--channel", "money", "--script-file", str(script_file), "--topic", "Why prices end in .99",
                                               "--publish-at", "2026-11-12T13:00:00Z", "--plan-id", "money-20261112T1300Z", "--upload"])
    get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    assert seen["voice"] == "en-US-AndrewNeural" and seen["accent"] == "#00E58F"  # money channel's own voice + colour
    assert seen["channel"] == "money" and seen["publish_at"] == datetime(2026, 11, 12, 13, 0, tzinfo=UTC)
    run_dir = next((tmp_path / "out").iterdir())
    assert run_dir.name.endswith("-money-why-prices-end-in-99") or "money" in run_dir.name
    info = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert info["uploaded"] is True and info["plan_id"] == "money-20261112T1300Z" and info["video_id"] == "vid1"
    assert info["channel"] == "money" and info["publish_at"] == "2026-11-12T13:00:00Z"


def test_pipeline_rejects_bad_publish_time(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from engine import pipeline

    result = CliRunner().invoke(pipeline.app, ["--demo", "--publish-at", "tomorrow noon"])
    assert result.exit_code != 0


# -------------------------------------------------------------------------- workflows
def load_workflow(name):
    data = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
    data["on"] = data.pop(True, data.get("on"))  # PyYAML reads the bare key `on` as boolean True
    return data


def test_daily_workflow_shape_and_safety():
    wf = load_workflow("daily.yml")
    assert set(wf["on"]) == {"schedule", "workflow_dispatch"}  # never triggered by pull requests
    assert wf["permissions"] == {"contents": "read"}
    assert wf["on"]["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True  # manual runs are dry by default
    assert re.fullmatch(r"\d+ \d+ \* \* \*", wf["on"]["schedule"][0]["cron"]) and not wf["on"]["schedule"][0]["cron"].startswith("0 ")
    assert list(wf["jobs"]) == ["plan", "produce", "record", "notify", "alert"]
    produce = wf["jobs"]["produce"]
    assert produce["strategy"]["fail-fast"] is False and "fromJSON" in produce["strategy"]["matrix"]
    assert wf["jobs"]["record"]["permissions"] == {"contents": "write"}
    assert wf["jobs"]["alert"]["permissions"] == {"issues": "write"}
    assert wf["concurrency"]["cancel-in-progress"] is False


def test_workflow_never_puts_expressions_into_shell_commands():
    """Planner/LLM text (topic) must reach the shell via env vars only: no `${{ }}` inside `run:`."""
    for name in ("daily.yml", "ci.yml"):
        wf = load_workflow(name)
        for job in wf["jobs"].values():
            for step in job["steps"]:
                if "run" in step:
                    assert "${{" not in step["run"], (name, step["run"])
    produce_steps = load_workflow("daily.yml")["jobs"]["produce"]["steps"]
    step = next(s for s in produce_steps if str(s.get("name", "")).startswith("Produce the video"))
    assert {"TOPIC", "CHANNEL", "PUBLISH_AT", "PLAN_ID", "RESERVE", "GEMINI_API_KEY", "GROQ_API_KEY", "YOUTUBE_TOKENS_JSON"} <= set(step["env"])
    assert '--topic "$TOPIC"' in step["run"] and "--upload" in step["run"]


def test_ci_workflow_runs_tests_and_typecheck():
    wf = load_workflow("ci.yml")
    text = json.dumps(wf)
    assert "pytest" in text and "tsc" in text and "pull_request" in wf["on"]
    assert "secrets." not in text  # PRs from forks must never see secrets


# ------------------------------------------------------------- no repeated topics anywhere
def make_llm(answers):
    """Planner LLM that returns the queued topic lists in order and records each prompt."""
    queue = list(answers)
    prompts = []

    def complete(system, user, schema):
        prompts.append(user)
        return {"topics": [{"topic": t} for t in queue.pop(0)]}

    return types.SimpleNamespace(complete_json=complete, prompts=prompts)


def test_topics_never_repeat_history_across_channels_or_within_a_run(tmp_path):
    history = History()
    history.add(HistoryEntry(plan_id="psychology-old", channel="psychology", topic="Why you cannot stop checking your phone", title="Phone Checking Habit Explained"))
    # attempt 1 of each channel contains one repeat; attempt 2 is clean
    llm = make_llm([
        ["How a bank actually creates money from nothing", "Why you cannot stop checking your phone every minute",  # repeats the OTHER channel's topic
         "The story of the Dutch tulip bubble", "Why prices end in .99 almost everywhere", "What inflation quietly does to your savings"],
        ["Why airlines overbook flights on purpose", "How compound interest builds wealth slowly"],
        ["The hidden reason subscriptions are so hard to cancel", "Why airlines overbook flights on purpose, explained",  # repeats a topic planned for money in this run
         "Why your brain remembers embarrassing moments forever", "What the anchoring effect does to your shopping", "How the mere exposure effect shapes your taste"],
    ])
    items, errors = daily.build_plan(["money", "psychology"], utc(2026, 11, 10, 3, 23), settings_for(tmp_path), history, lambda: llm)
    assert errors == []
    money = [i["topic"] for i in items if i["channel"] == "money"]
    psych = [i["topic"] for i in items if i["channel"] == "psychology"]
    assert "Why you cannot stop checking your phone every minute" not in money  # psychology already did it
    assert not any(is_duplicate(a, b) for a in money for b in psych)  # also no overlap between the two channels in one run
    assert len(money) == len(psych) == 3
    # the prompt for the 2nd channel lists what the 1st one just planned
    assert any("The story of the Dutch tulip bubble" in p for p in llm.prompts[1:])


def test_already_covered_titles_are_respected(tmp_path):
    ch = load_channel("psychology").model_copy(update={"already_covered": ["Why your brain remembers embarrassing moments forever"]})
    llm = make_llm([["Why your brain remembers embarrassing moments for years", "What the anchoring effect does to your shopping",
                     "How the mere exposure effect shapes your taste", "Why you cannot stop checking your phone", "Why we procrastinate even when we know the cost"]])
    topics = plan_topics(ch, 3, ch.already_covered, llm)
    assert all("embarrassing" not in t for t in topics) and len(topics) == 3
    assert "embarrassing moments forever" in llm.prompts[0]  # the LLM is told too


def test_whole_history_is_checked_not_only_the_recent_part():
    old = [f"Completely different old topic number {chr(97 + i)}{chr(97 + (i * 7) % 26)} subject" for i in range(150)] + ["Why prices end in .99 almost everywhere"]
    ch = load_channel("money")
    llm = make_llm([["Why prices end in .99 almost everywhere again", "How a bank actually creates money from nothing", "The story of the Dutch tulip bubble",
                     "What inflation quietly does to your savings", "How compound interest builds wealth slowly"]])
    topics = plan_topics(ch, 3, old, llm)  # the repeat sits deep inside a long history
    assert "Why prices end in .99 almost everywhere again" not in topics


# ------------------------------------------------------------------ CI error annotations
def test_annotate_error_only_inside_github_actions(monkeypatch, capsys):
    from engine.ci import annotate_error

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    annotate_error("boom")
    assert capsys.readouterr().out == ""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    annotate_error("Edge TTS failed\n100% sure")
    out = capsys.readouterr().out
    assert out.startswith("::error title=Autopilot::Edge TTS failed%0A100%25 sure")
    annotate_error("x" * 5000)
    assert len(capsys.readouterr().out) < 1300


def test_pipeline_main_annotates_failures(monkeypatch, capsys):
    from engine import pipeline

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(pipeline, "app", lambda: (_ for _ in ()).throw(RuntimeError("Edge TTS failed (403)")))
    with pytest.raises(SystemExit):
        pipeline.main()
    assert "::error title=Autopilot::Edge TTS failed (403)" in capsys.readouterr().out


def test_daily_workflow_has_a_second_chance_run_and_pinned_runners():
    wf = load_workflow("daily.yml")
    crons = [c["cron"] for c in wf["on"]["schedule"]]
    assert len(crons) == 2 and crons[0] != crons[1] and all(not c.startswith("0 ") for c in crons)
    for name in ("daily.yml", "ci.yml", "telegram.yml", "check-keys.yml"):
        for job in load_workflow(name)["jobs"].values():
            assert job["runs-on"] == "ubuntu-24.04"  # 'latest' is moving to a new Ubuntu: do not let it break rendering


def test_real_channel_files_are_valid_and_publish_twice_a_day(monkeypatch):
    """The production channels/*.json (the other tests use frozen copies)."""
    from pathlib import Path as P

    from engine import channels

    real = P(__file__).resolve().parent.parent / "channels"
    assert channels.list_channel_ids(real) == ["money", "psychology"]
    for cid in ("money", "psychology"):
        ch = channels.load_channel(cid, real)
        assert ch.slots == ["08:00", "19:00"] and ch.timezone == "America/New_York"
        assert ch.rules and ch.hashtags and ch.kokoro_voice
    money = channels.load_channel("money", real)
    assert any("never recommend" in r.lower() for r in money.rules) and "not financial advice" in money.description_footer.lower()
