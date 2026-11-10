"""Make test failures readable in GitHub Actions without downloading logs: each failed test is also
printed as a red workflow annotation (the public API exposes annotations)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def pytest_runtest_logreport(report):
    if report.failed and os.environ.get("GITHUB_ACTIONS") == "true":
        from engine.ci import annotate_error

        annotate_error(f"{report.nodeid} [{report.when}]: {str(report.longreprtext)[-700:]}", title="pytest failure")


import pytest


@pytest.fixture(autouse=True)
def _never_talk_to_real_services(monkeypatch):
    """The developer's .env holds REAL tokens (Telegram, Cloudflare). Tests must never use them: an empty
    environment value beats the .env file, so no test can send a real message or call a real API by accident.
    A test that needs a (fake) value sets it itself afterwards."""
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"):
        monkeypatch.setenv(name, "")
    from engine.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _fixed_channel_definitions(monkeypatch):
    """Tests run against frozen copies of the channel files (tests/fixtures/channels), so changing the real
    channels/*.json (slots, topics, voices) never breaks them. The real files have their own test."""
    import engine.channels as channels

    monkeypatch.setattr(channels, "CHANNELS_DIR", Path(__file__).resolve().parent / "fixtures" / "channels")
