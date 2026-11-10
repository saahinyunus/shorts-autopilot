"""Small helpers for running inside GitHub Actions."""
from __future__ import annotations

import os


def annotate_error(message: str, title: str = "Autopilot") -> None:
    """Show `message` as a red annotation on the workflow run (visible without downloading logs).
    Does nothing outside GitHub Actions. Never include secrets in `message`."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    text = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::error title={title}::{text[:1200]}", flush=True)
