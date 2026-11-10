"""First comment under every video: question text, posting, retry of failures, notifications."""
import sys
import types
from datetime import datetime, timezone
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
    def __init__(self, fail=None, existing=()):
        self.fail, self.posted, self.existing = fail, [], list(existing)

    def commentThreads(self):
        def insert(part, body):
            def execute():
                if self.fail:
                    raise self.fail
                self.posted.append(body)
                return {"snippet": {"topLevelComment": {"id": "c123"}}}

            return types.SimpleNamespace(execute=execute)

        def list_(part, videoId, maxResults, order):
            items = [{"snippet": {"topLevelComment": {"id": "old1", "snippet": {"textOriginal": t}}}} for t in self.existing]
            return types.SimpleNamespace(execute=lambda: {"items": items})

        return types.SimpleNamespace(insert=insert, list=list_)


NOW = datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)


def test_post_comment_sends_a_top_level_comment():
    yt = FakeYouTube()
    assert uploader.post_comment(yt, "vid1", "Which one?") == "c123"
    assert yt.posted[0]["snippet"]["videoId"] == "vid1"
    assert yt.posted[0]["snippet"]["topLevelComment"]["snippet"]["textOriginal"] == "Which one?"
    assert uploader.find_comment(FakeYouTube(existing=["Hi", "Which one?"]), "v", "Which one?") == "old1"
    assert uploader.find_comment(FakeYouTube(existing=["Hi"]), "v", "Which one?") == ""


def test_safety_net_only_touches_old_videos_without_a_comment():
    def e(pid, cid, vid, at, **kw):
        return HistoryEntry(plan_id=pid, channel=cid, topic="t", video_id=vid, publish_at=at, **kw)

    old = e("a", "money", "v1", "2026-11-10T08:00:00Z", comment_text="Q1?")                      # 12 h old: the worker missed it
    fresh = e("b", "money", "v2", "2026-11-10T18:00:00Z", comment_text="Q2?")                    # 2 h old: leave it to the worker
    failing = e("c", "psychology", "v3", "2026-11-10T08:00:00Z", comment_text="Q3?")
    done = e("d", "money", "v4", "2026-11-10T08:00:00Z", comment_text="Q4?", comment_id="x")
    legacy = e("e", "money", "v5", "2026-11-10T08:00:00Z")                                        # video from before this feature
    present = e("f", "money", "v6", "2026-11-10T08:00:00Z", comment_text="Already there?")
    unscheduled = HistoryEntry(plan_id="g", channel="money", topic="t", video_id="v7", comment_text="No time?")
    history = History(entries=[old, fresh, failing, done, legacy, present, unscheduled])
    clients = {"money": FakeYouTube(existing=["Already there?"]), "psychology": FakeYouTube(fail=RuntimeError("boom"))}
    outcome = daily.post_pending_comments(history, Settings(_env_file=None), youtube_for=lambda cid: clients[cid], now=NOW)
    assert [(x.plan_id, err) for x, err in outcome] == [("a", ""), ("c", "RuntimeError"), ("f", "")]
    assert old.comment_id == "c123" and present.comment_id == "old1"          # an existing comment is adopted, never duplicated
    assert failing.comment_id == "" and fresh.comment_id == "" and done.comment_id == "x"
    assert len(clients["money"].posted) == 1


def test_record_keeps_the_comment_fields_in_the_history():
    assert {"comment_text", "comment_id"} <= set(HistoryEntry.model_fields)


def test_telegram_upload_message_announces_the_comment():
    item = {"channel": "money", "publish_at": "2026-11-10T13:00:00Z"}
    res = {"uploaded": True, "title": "T", "url": "https://youtu.be/a", "channel": "money", "comment_text": "Which one?"}
    text = tb.message_for_video(item, res, dry_run=False, run_url="r", notify_tz="Europe/Istanbul", verdict=None)
    assert "yayın saatinde otomatik yazılacak" in text and "Which one?" in text


def test_workflow_retries_comments_before_committing():
    import yaml

    wf = yaml.safe_load((ROOT_WF := Path(__file__).resolve().parent.parent / ".github/workflows/daily.yml").read_text(encoding="utf-8"))
    names = [s.get("name", "") for s in wf["jobs"]["record"]["steps"]]
    run = [s for s in wf["jobs"]["record"]["steps"] if "daily.py comments" in s.get("run", "")]
    assert run and run[0]["continue-on-error"] is True and "YOUTUBE_TOKENS_JSON" in run[0]["env"]
    assert names.index(run[0]["name"]) < next(i for i, n in enumerate(names) if n.startswith("Commit"))
