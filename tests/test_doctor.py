"""The key checker: every outcome is reported, no secret ever appears in the report."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from engine import doctor, telegram_bot as tb
from engine.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def resp(status, body=None):
    return types.SimpleNamespace(status_code=status, json=lambda: body or {}, text=str(body or ""))


def test_gemini_daily_quota_is_named_and_keys_are_never_printed():
    s = Settings(_env_file=None, gemini_api_key="SECRET-G")
    quota = resp(429, {"error": {"message": "You exceeded ... per day limit ... Please retry in 2h"}})
    out = doctor.check_gemini(s, post=lambda *a, **k: quota)
    assert all(not ok for _, ok, _ in out) and "daily quota used up" in out[0][2]
    assert "SECRET" not in doctor.format_report(out)


def test_missing_secret_is_reported_as_empty():
    assert "EMPTY" in doctor.check_gemini(Settings(_env_file=None))[0][2]
    assert "EMPTY" in doctor.check_groq(Settings(_env_file=None))[0][2]
    assert "not both set" in doctor.check_telegram(Settings(_env_file=None))[0][2]


def test_groq_failure_shows_status_and_message():
    s = Settings(_env_file=None, groq_api_key="SECRET-Q")
    out = doctor.check_groq(s, post=lambda *a, **k: resp(401, {"error": {"message": "Invalid API Key"}}))
    assert out[0][1] is False and "401" in out[0][2] and "Invalid API Key" in out[0][2]
    ok = doctor.check_groq(s, post=lambda *a, **k: resp(200))
    assert all(good for _, good, _ in ok)


def test_report_verdict_depends_on_any_llm_working():
    good = [("Gemini x", False, "HTTP 429"), ("Groq y", True, "ok")]
    assert "En az bir yapay zekâ çalışıyor" in doctor.format_report(good)
    assert "yedek senaryolardan" in doctor.format_report([("Gemini x", False, "HTTP 429"), ("Groq y", False, "EMPTY")])


def test_one_broken_check_does_not_hide_the_others(monkeypatch):
    monkeypatch.setattr(doctor, "check_gemini", lambda s: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(doctor, "check_groq", lambda s: [("Groq", True, "ok")])
    monkeypatch.setattr(doctor, "check_youtube", lambda s: [])
    results = doctor.run_checks(Settings(_env_file=None))
    assert ("gemini", False, "RuntimeError") in results and ("Groq", True, "ok") in results


def test_workflow_passes_every_secret_the_pipeline_uses():
    wf = yaml.safe_load((ROOT / ".github/workflows/check-keys.yml").read_text(encoding="utf-8"))
    env = wf["jobs"]["doctor"]["steps"][-1]["env"]
    assert {"GEMINI_API_KEY", "GROQ_API_KEY", "YOUTUBE_TOKENS_JSON", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"} <= set(env)


def test_dry_run_message_explains_why_a_reserve_script_was_used():
    item = {"channel": "psychology", "publish_at": "2026-11-11T13:00:00Z"}
    res = {"uploaded": False, "title": "T", "channel": "psychology", "reserve_used": "x.json", "reserve_reason": "All LLM providers failed; HTTP 401"}
    text = tb.message_for_video(item, res, dry_run=True, run_url="r", notify_tz="Europe/Istanbul", verdict=None)
    assert "yedek senaryo kullanıldı" in text and "HTTP 401" in text
