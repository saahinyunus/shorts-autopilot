"""The planner never schedules a second video into a slot that already has one on YouTube."""
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import daily
from engine.config import Settings
from engine.history import History


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


class FakeYouTube:
    """channels().list / playlistItems().list / videos().list with canned data."""

    def __init__(self, statuses, fail=None):
        self.statuses, self.fail = statuses, fail  # {video id: status dict}

    def _ex(self, payload):
        if self.fail:
            raise self.fail
        return types.SimpleNamespace(execute=lambda: payload)

    def channels(self):
        return types.SimpleNamespace(list=lambda **k: self._ex({"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU123"}}}]}))

    def playlistItems(self):
        return types.SimpleNamespace(list=lambda **k: self._ex({"items": [{"contentDetails": {"videoId": v}} for v in self.statuses]}))

    def videos(self):
        def lst(part, id):
            return self._ex({"items": [{"id": v, "status": self.statuses[v]} for v in id.split(",")]})

        return types.SimpleNamespace(list=lst)


SETTINGS = Settings(_env_file=None)


def test_scheduled_publish_times_are_read_from_youtube():
    yt = FakeYouTube({
        "a": {"privacyStatus": "private", "publishAt": "2026-11-13T13:00:00Z"},
        "b": {"privacyStatus": "private", "publishAt": "2026-11-14T00:00:00.000Z"},
        "c": {"privacyStatus": "public"},  # already live: no publishAt
    })
    assert daily.youtube_scheduled_slots(SETTINGS, "money", youtube=yt) == {"2026-11-13T13:00:00Z", "2026-11-14T00:00:00Z"}
    assert daily.youtube_scheduled_slots(SETTINGS, "money", youtube=FakeYouTube({})) == set()


def test_a_youtube_failure_falls_back_to_the_history_instead_of_stopping():
    assert daily.youtube_scheduled_slots(SETTINGS, "money", youtube=FakeYouTube({"a": {}}, fail=RuntimeError("quota"))) == set()


def llm(topics):
    return types.SimpleNamespace(complete_json=lambda s, u, sch: {"topics": [{"topic": t, "format": "true_story"} for t in topics]})


FRESH = ["How Nintendo survived a century", "What a Ponzi scheme really is", "Why the penny costs more than its value", "How inflation erodes savings"]


def plan(tmp_path, lookup):
    settings = Settings(_env_file=None, state_dir=tmp_path / "state")
    return daily.build_plan(["money"], utc(2026, 11, 10, 3, 23), settings, History(), lambda: llm(FRESH), scheduled_lookup=lookup)[0]


def test_slots_that_already_exist_on_youtube_are_not_planned_again(tmp_path):
    everything = plan(tmp_path, None)
    assert len(everything) >= 2
    taken = {everything[0]["publish_at"]}  # YouTube already has a video for the first slot (history knows nothing)
    items = plan(tmp_path, lambda cid: taken)
    assert [i["publish_at"] for i in items] == [e["publish_at"] for e in everything[1:]]


def test_every_slot_taken_means_nothing_to_produce(tmp_path):
    everything = plan(tmp_path, None)
    assert plan(tmp_path, lambda cid: {i["publish_at"] for i in everything}) == []


def test_the_cli_plan_uses_the_youtube_lookup(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from engine.config import get_settings

    calls = []
    monkeypatch.setattr(daily, "youtube_scheduled_slots", lambda settings, cid, youtube=None: calls.append(cid) or set())
    monkeypatch.setattr(daily, "get_llm_provider", lambda settings: llm(FRESH))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    get_settings.cache_clear()
    result = CliRunner().invoke(daily.app, ["plan", "--channels", "money", "--now", "2026-11-10T03:23:00Z"])
    get_settings.cache_clear()
    assert result.exit_code == 0 and calls == ["money"]
