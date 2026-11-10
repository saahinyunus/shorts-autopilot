"""Telegram bot: notifications and commands. No network, no real token."""
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import telegram_bot as tb
from engine.config import Settings
from engine.history import History, HistoryEntry

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
TOKEN = "123456:SECRET-TOKEN"


def entry(cid, n, publish_at, title="A title"):
    return HistoryEntry(plan_id=f"{cid}-{n}", channel=cid, topic=f"topic {n}", title=title, video_id=f"vid{n}",
                        url=f"https://youtu.be/vid{n}", publish_at=publish_at)


class FakeYT:
    """Minimal YouTube client: channels().list / videos().list return canned data."""

    def __init__(self, videos=None, error=None):
        self.videos_data, self.error = videos or {}, error

    def _exec(self, payload):
        if self.error:
            raise self.error
        return types.SimpleNamespace(execute=lambda: payload)

    def channels(self):
        return types.SimpleNamespace(list=lambda **k: self._exec({"items": [
            {"snippet": {"title": "Money Channel"}, "statistics": {"subscriberCount": "1234", "viewCount": "56789", "videoCount": "7"}}]}))

    def videos(self):
        def lst(part, id):
            return self._exec({"items": [{"id": i, **self.videos_data[i]} for i in id.split(",") if i in self.videos_data]})

        return types.SimpleNamespace(list=lst)


def ctx_for(tmp_path, entries=(), yt=None, runs=None):
    return tb.Context(
        settings=Settings(_env_file=None, state_dir=tmp_path / "state", gemini_api_key="g", groq_api_key="q"),
        history=History(entries=list(entries)), now=NOW,
        github_runs=(lambda: runs if runs is not None else [{"created_at": "2026-11-10T04:12:00Z", "conclusion": "success", "status": "completed", "html_url": "https://x/run"}]),
        youtube=(lambda cid: yt) if yt is not None else None,
    )


# ---------------------------------------------------------------------------- basics
def test_split_message_never_exceeds_the_limit_and_keeps_all_text():
    text = "\n".join(f"line {i} " + "x" * 200 for i in range(60)) + "\n" + "y" * 9000
    parts = tb.split_message(text, 4000)
    assert all(len(p) <= 4000 for p in parts) and "".join(parts).replace("\n", "") == text.replace("\n", "")


def test_slot_is_shown_in_channel_and_viewer_time():
    assert tb.fmt_slot("2026-11-10T13:00:00Z", "America/New_York", "Europe/Istanbul") == "10.11 08:00 NY · 16:00 TR"
    assert tb.num(1234567) == "1.234.567" and tb.num(None) == "-"


def test_api_errors_never_contain_the_token():
    def boom(url, **k):
        raise RuntimeError(f"connection failed for {url}")

    with pytest.raises(tb.TelegramError) as err:
        tb.Telegram(TOKEN, "1", post=boom).call("getMe")
    assert TOKEN not in str(err.value) and "SECRET" not in str(err.value)
    bad = lambda url, **k: types.SimpleNamespace(json=lambda: {"ok": False, "description": "Forbidden"})
    with pytest.raises(tb.TelegramError, match="Forbidden"):
        tb.Telegram(TOKEN, "1", post=bad).call("sendMessage")


def test_not_configured_means_disabled():
    assert tb.get_telegram(Settings(_env_file=None)) is None
    assert tb.get_telegram(Settings(_env_file=None, telegram_bot_token="t", telegram_chat_id="5")).chat_id == "5"


# ------------------------------------------------------------------- who may talk to it
def test_only_the_owner_gets_answers_and_everything_is_confirmed():
    sent = []
    tg = tb.Telegram(TOKEN, "42", post=lambda url, json, timeout: (sent.append(json), types.SimpleNamespace(json=lambda: {"ok": True, "result": True}))[1])
    updates = [
        {"update_id": 10, "message": {"chat": {"id": 999}, "text": "/status"}},  # a stranger
        {"update_id": 11, "message": {"chat": {"id": 42}, "text": "/help"}},
        {"update_id": 12, "message": {"chat": {"id": 42}, "text": "just chatting"}},  # not a command
    ]
    offset = tb.handle_updates(tg, updates, ctx_for(Path(".")))
    assert offset == 13
    assert len(sent) == 1 and sent[0]["chat_id"] == "42" and "/queue" in sent[0]["text"]


def test_unknown_and_broken_commands_answer_politely():
    ctx = ctx_for(Path("."))
    assert "Bilinmeyen komut" in tb.reply_for("/nope", ctx)
    assert tb.reply_for("hello", ctx) is None
    assert tb.reply_for("/stats@my_bot", ctx) is not None  # group-style suffix
    boom = lambda c: (_ for _ in ()).throw(KeyError("x"))
    tb.COMMANDS["status"], saved = boom, tb.COMMANDS["status"]
    try:
        assert "çalışmadı" in tb.reply_for("/status", ctx)
    finally:
        tb.COMMANDS["status"] = saved


# --------------------------------------------------------------------- scheduled check
def test_check_scheduled_cases():
    ok = {"status": {"privacyStatus": "private", "publishAt": "2026-11-10T13:00:00Z"}}
    assert tb.check_scheduled(ok, "2026-11-10T13:00:00Z") == (True, "zamanlanmış")
    assert tb.check_scheduled({"status": {"privacyStatus": "public"}}, "2026-11-10T13:00:00Z")[0] is True
    assert tb.check_scheduled({"status": {"privacyStatus": "private"}}, "2026-11-10T13:00:00Z")[0] is False  # locked private
    assert "farklı" in tb.check_scheduled(ok, "2026-11-10T18:00:00Z")[1]
    assert tb.check_scheduled(None, "x")[0] is False


# --------------------------------------------------------------------------- commands
def test_queue_verifies_each_waiting_video_against_youtube(tmp_path):
    videos = {
        "vid1": {"status": {"privacyStatus": "private", "publishAt": "2026-11-10T13:00:00Z"}},
        "vid2": {"status": {"privacyStatus": "private"}},  # NOT scheduled
    }
    entries = [entry("money", 1, "2026-11-10T13:00:00Z"), entry("money", 2, "2026-11-10T18:00:00Z"),
               entry("money", 3, "2026-11-09T13:00:00Z")]  # already past: not listed
    text = tb.cmd_queue(ctx_for(tmp_path, entries, FakeYT(videos)))
    assert "2 video" in text and "✅" in text and "⚠️" in text and "zamanlanmamış" in text and "vid3" not in text


def test_queue_still_works_without_the_readonly_permission(tmp_path):
    err = Exception("Request had insufficient authentication scopes. insufficientPermissions")
    text = tb.cmd_queue(ctx_for(tmp_path, [entry("money", 1, "2026-11-10T13:00:00Z")], FakeYT(error=err)))
    assert "izni eksik" in text and "vid1" in text
    assert tb.cmd_queue(ctx_for(tmp_path)) == "📭 Yayın bekleyen video yok."


def test_stats_shows_channel_and_video_numbers(tmp_path):
    videos = {"vid1": {"statistics": {"viewCount": "1500", "likeCount": "30", "commentCount": "2"}}}
    text = tb.cmd_stats(ctx_for(tmp_path, [entry("money", 1, "2026-11-09T13:00:00Z", "Great video")], FakeYT(videos)))
    assert "Money Channel" in text and "1.234 abone" in text and "56.789" in text and "1.500" in text and "Great video" in text


def test_stats_explains_a_missing_permission(tmp_path):
    err = Exception("insufficientPermissions: Insufficient Permission")
    assert "yeniden giriş" in tb.cmd_stats(ctx_for(tmp_path, [], FakeYT(error=err)))


def test_status_and_health_report_run_results(tmp_path):
    ctx = ctx_for(tmp_path, [entry("money", 1, "2026-11-10T18:00:00Z")], FakeYT())
    status = tb.cmd_status(ctx)
    assert "Son çalıştırma: success" in status and "money" in status and "1 yayın bekliyor" in status
    assert "Yedek senaryo" in status
    health = tb.cmd_health(ctx)
    assert "Günlük çalıştırma" in health and "Yapay zekâ anahtarları: Gemini, Groq" in health
    stale = tb.cmd_health(ctx_for(tmp_path, [], FakeYT(), runs=[{"created_at": "2026-11-05T04:12:00Z", "conclusion": "success", "status": "completed", "html_url": "u"}]))
    assert "Dikkat" in stale
    failed = tb.cmd_health(ctx_for(tmp_path, [], FakeYT(), runs=[{"created_at": "2026-11-10T04:12:00Z", "conclusion": "failure", "status": "completed", "html_url": "u"}]))
    assert "🔴" in failed


# ----------------------------------------------------------------------- notifications
def test_video_messages():
    item = {"channel": "money", "topic": "Why <x> & y", "publish_at": "2026-11-10T13:00:00Z"}
    res = {"uploaded": True, "title": "Nice <b>", "url": "https://youtu.be/a", "channel": "money", "reserve_used": ""}
    ok = tb.message_for_video(item, res, dry_run=False, run_url="https://run", notify_tz="Europe/Istanbul", verdict=(True, "zamanlanmış"))
    assert "✅" in ok and "10.11 08:00 NY" in ok and "doğruladı" in ok and "&lt;b&gt;" in ok  # HTML-escaped
    bad = tb.message_for_video(item, None, dry_run=False, run_url="https://run", notify_tz="Europe/Istanbul", verdict=None)
    assert "❌" in bad and "Why &lt;x&gt; &amp; y" in bad and "https://run" in bad
    dry = tb.message_for_video(item, {**res, "uploaded": False}, dry_run=True, run_url="r", notify_tz="Europe/Istanbul", verdict=None)
    assert "Deneme" in dry
    with_q = tb.message_for_video(item, {**res, "uploaded": False, "comment_text": "Which one?"}, dry_run=True, run_url="r", notify_tz="Europe/Istanbul", verdict=None)
    assert "İlk yorum sorusu: Which one?" in with_q  # a dry run shows the comment before anything is posted
    assert "yedek senaryo" in tb.message_for_video(item, {**res, "reserve_used": "x"}, dry_run=False, run_url="r", notify_tz="Europe/Istanbul", verdict=None)


def test_notify_commands_never_fail_and_do_nothing_when_disabled(monkeypatch, tmp_path):
    from typer.testing import CliRunner

    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(var, raising=False)
    from engine.config import get_settings

    get_settings.cache_clear()
    for cmd in (["notify-video", "--output-dir", str(tmp_path)], ["notify-plan"], ["notify-run"]):
        assert CliRunner().invoke(tb.app, cmd).exit_code == 0
    # configured but the network is down: still exit code 0, token not printed
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("PLAN_COUNT", "3")
    get_settings.cache_clear()
    monkeypatch.setattr(tb.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(f"down {a}")))
    result = CliRunner().invoke(tb.app, ["notify-run"])
    get_settings.cache_clear()
    assert result.exit_code == 0 and "SECRET" not in result.output


def test_notify_plan_lists_the_topics(monkeypatch):
    sent = []
    monkeypatch.setattr(tb.Telegram, "send", lambda self, text, chat_id=None: sent.append(text))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("PLAN_MATRIX", json.dumps({"include": [{"channel": "money", "topic": "Topic A", "publish_at": "2026-11-10T13:00:00Z", "plan_id": "p"}]}))
    from typer.testing import CliRunner

    from engine.config import get_settings

    get_settings.cache_clear()
    CliRunner().invoke(tb.app, ["notify-plan"])
    get_settings.cache_clear()
    assert len(sent) == 1 and "Topic A" in sent[0] and "1 video" in sent[0]


# --------------------------------------------------------------------------- workflows
def load(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


def test_daily_workflow_notifies_without_ever_failing_the_run():
    wf = load("daily.yml")
    for job_name, marker in (("plan", "notify-plan"), ("produce", "notify-video"), ("notify", "notify-run")):
        step = next(s for s in wf["jobs"][job_name]["steps"] if marker in s.get("run", ""))
        assert step.get("continue-on-error") is True and "TELEGRAM_BOT_TOKEN" in step["env"]
    assert "RUN_URL" in wf["env"]
    notify = wf["jobs"]["notify"]
    assert "always()" in notify["if"] and set(notify["needs"]) == {"plan", "produce", "record"}


def test_telegram_poll_workflow_is_safe():
    wf = load("telegram.yml")
    text = json.dumps(wf)
    triggers = wf[True if True in wf else "on"]  # PyYAML parses `on` as True
    assert "schedule" not in triggers and "workflow_dispatch" in triggers  # the Worker answers instantly; this is a fallback
    assert wf["concurrency"]["cancel-in-progress"] is False and wf["permissions"] == {"contents": "read", "actions": "read"}
    assert "${{" not in " ".join(s.get("run", "") for s in wf["jobs"]["poll"]["steps"])
    assert "poll" in text


def test_login_asks_for_the_upload_and_force_ssl_scopes():
    from engine.uploader import SCOPES

    assert any(s.endswith("youtube.upload") for s in SCOPES) and any(s.endswith("youtube.force-ssl") for s in SCOPES)


# ------------------------------------------------------------- heartbeat + webhook coexistence
def test_heartbeat_writes_the_status_file_for_the_worker(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from engine import daily
    from engine.config import get_settings

    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("GROQ_API_KEY", "q")
    monkeypatch.setenv("PLAN_COUNT", "3")
    monkeypatch.setenv("PRODUCE_RESULT", "success")
    monkeypatch.setenv("RUN_URL", "https://run")
    get_settings.cache_clear()
    result = CliRunner().invoke(daily.app, ["heartbeat"])
    get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    status = json.loads((tmp_path / "state" / "status.json").read_text(encoding="utf-8"))
    assert status["plan_count"] == 3 and status["produce_result"] == "success" and status["llms"] == ["Gemini", "Groq"]
    assert set(status["reserve"]) == {"money", "psychology"} and status["run_url"] == "https://run"
    assert "TOKEN" not in json.dumps(status).upper().replace("TOKENS", "")  # nothing secret in a public file


def test_polling_steps_aside_when_the_webhook_is_active(monkeypatch):
    from typer.testing import CliRunner

    from engine.config import get_settings

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    get_settings.cache_clear()
    monkeypatch.setattr(tb, "build_context", lambda s: object())
    def conflict(self, method, **params):
        raise tb.TelegramError("Telegram getUpdates: Conflict: can't use getUpdates method while webhook is active")

    monkeypatch.setattr(tb.Telegram, "call", conflict)
    result = CliRunner().invoke(tb.app, ["poll"])
    get_settings.cache_clear()
    assert result.exit_code == 0 and "webhook is active" in result.output.lower()


def test_ci_runs_the_worker_tests_and_the_worker_has_no_secrets_inside():
    assert "worker/test.mjs" in (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    code = (ROOT / "worker" / "index.js").read_text(encoding="utf-8")
    assert "env.TELEGRAM_BOT_TOKEN" in code and "AAF" not in code  # secrets come from the environment only
