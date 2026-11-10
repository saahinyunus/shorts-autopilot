"""First comment under every video: question text, posting, retry of failures, notifications."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import daily, telegram_bot as tb, uploader
from engine.config import Settings
from engine.generator import ScriptSpec, SYSTEM_PROMPT_BASE, STRICT_SCRIPT_SCHEMA
from engine.history import History, HistoryEntry
from test_llm_providers import valid_script


def script_with(**extra):
    return ScriptSpec.model_validate({**valid_script(), **extra})


def test_question_text_is_cleaned_and_has_fallbacks():
    q = uploader.comment_text_for(script_with(comment_question="Which habit hurts you most?  #psychology https://x.co/a  "))
    assert q == "Which habit hurts you most?"
    assert uploader.comment_text_for(script_with(comment_question="")) == "Why does this happen to you?"  # the hook is a question
    no_q = script_with(comment_question="", hook="This is a statement.")
    assert uploader.comment_text_for(no_q) == uploader.DEFAULT_QUESTION
    assert len(uploader.comment_text_for(script_with(comment_question="word " * 100))) <= uploader.MAX_COMMENT_CHARS


def test_prompt_and_schema_ask_for_the_question():
    assert "comment_question" in SYSTEM_PROMPT_BASE and "comment_question" in STRICT_SCRIPT_SCHEMA["properties"]
    assert "comment_question" not in STRICT_SCRIPT_SCHEMA["required"]  # older/backup scripts stay valid


class FakeYouTube:
    def __init__(self, fail=None):
        self.fail, self.posted = fail, []

    def commentThreads(self):
        def insert(part, body):
            def execute():
                if self.fail:
                    raise self.fail
                self.posted.append(body)
                return {"snippet": {"topLevelComment": {"id": "c123"}}}

            return types.SimpleNamespace(execute=execute)

        return types.SimpleNamespace(insert=insert)


def test_post_comment_sends_a_top_level_comment():
    yt = FakeYouTube()
    assert uploader.post_comment(yt, "vid1", "Which one?") == "c123"
    assert yt.posted[0]["snippet"]["videoId"] == "vid1"
    assert yt.posted[0]["snippet"]["topLevelComment"]["snippet"]["textOriginal"] == "Which one?"


def test_pending_comments_are_posted_and_failures_stay_pending():
    ok_entry = HistoryEntry(plan_id="a", channel="money", topic="t", video_id="v1", comment_text="Q1?")
    failing = HistoryEntry(plan_id="b", channel="psychology", topic="t", video_id="v2", comment_text="Q2?")
    done = HistoryEntry(plan_id="c", channel="money", topic="t", video_id="v3", comment_text="Q3?", comment_id="old")
    none = HistoryEntry(plan_id="d", channel="money", topic="t", video_id="v4")  # video from before this feature
    history = History(entries=[ok_entry, failing, done, none])
    clients = {"money": FakeYouTube(), "psychology": FakeYouTube(fail=RuntimeError("commentsDisabled"))}
    outcome = daily.post_pending_comments(history, Settings(_env_file=None), youtube_for=lambda cid: clients[cid])
    assert [(e.plan_id, err) for e, err in outcome] == [("a", ""), ("b", "RuntimeError")]
    assert ok_entry.comment_id == "c123" and failing.comment_id == "" and done.comment_id == "old"
    assert len(clients["money"].posted) == 1  # nothing posted twice


def test_record_keeps_the_comment_fields_in_the_history():
    assert {"comment_text", "comment_id"} <= set(HistoryEntry.model_fields)


def test_telegram_message_tells_how_to_pin():
    item = {"channel": "money", "publish_at": "2026-11-10T13:00:00Z"}
    base = {"uploaded": True, "title": "T", "url": "https://youtu.be/a", "channel": "money"}
    posted = tb.message_for_video(item, {**base, "comment_text": "Which one?", "comment_id": "c1"}, dry_run=False, run_url="r", notify_tz="Europe/Istanbul", verdict=None)
    assert "Which one?" in posted and "Sabitle" in posted
    pending = tb.message_for_video(item, {**base, "comment_text": "Which one?", "comment_id": ""}, dry_run=False, run_url="r", notify_tz="Europe/Istanbul", verdict=None)
    assert "denenecek" in pending and "Sabitle" not in pending


def test_workflow_retries_comments_before_committing():
    import yaml

    wf = yaml.safe_load((ROOT_WF := Path(__file__).resolve().parent.parent / ".github/workflows/daily.yml").read_text(encoding="utf-8"))
    names = [s.get("name", "") for s in wf["jobs"]["record"]["steps"]]
    run = [s for s in wf["jobs"]["record"]["steps"] if "daily.py comments" in s.get("run", "")]
    assert run and run[0]["continue-on-error"] is True and "YOUTUBE_TOKENS_JSON" in run[0]["env"]
    assert names.index(run[0]["name"]) < next(i for i, n in enumerate(names) if n.startswith("Commit"))
