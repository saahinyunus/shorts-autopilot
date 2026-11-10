"""What has been published (state/history.json). Committed back to the repo by the workflow, so it
survives between runs, prevents duplicate topics/slots and keeps the repository 'active'."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from .config import ROOT_DIR

HISTORY_FILE = ROOT_DIR / "state" / "history.json"


class HistoryEntry(BaseModel):
    plan_id: str
    channel: str
    topic: str
    title: str = ""
    video_id: str = ""
    url: str = ""
    publish_at: str = ""


class History(BaseModel):
    entries: list[HistoryEntry] = Field(default_factory=list)

    def has_plan(self, plan_id: str) -> bool:
        return any(e.plan_id == plan_id for e in self.entries)

    def recent(self, channel: str, limit: int = 80) -> list[HistoryEntry]:
        return [e for e in self.entries if e.channel == channel][-limit:]

    def add(self, entry: HistoryEntry) -> bool:
        """Append unless this plan slot is already recorded. Returns True if added."""
        if self.has_plan(entry.plan_id):
            return False
        self.entries.append(entry)
        return True


def load_history(path: Path = HISTORY_FILE) -> History:
    if not path.exists():
        return History()
    return History.model_validate_json(path.read_text(encoding="utf-8"))


def save_history(history: History, path: Path = HISTORY_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history.model_dump(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
