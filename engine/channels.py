"""Channel definitions (channels/<id>.json) and publishing-slot maths."""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator

from .config import ROOT_DIR

CHANNELS_DIR = ROOT_DIR / "channels"


class ChannelConfig(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    display_name: str
    niche: str
    audience: str = "English-speaking viewers, mostly in the US."
    tone: str = "Intriguing, confident, conversational."
    topic_ideas: list[str] = Field(default_factory=list)
    # Titles/topics already published on this channel by something else (older videos, another
    # automation). They are treated like history: new topics may not repeat them.
    already_covered: list[str] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=lambda: ["#Shorts"])
    tags: list[str] = Field(default_factory=list)
    description_footer: str = ""
    category_id: str = "27"
    language: str = "en"
    timezone: str = "America/New_York"
    slots: list[str] = Field(default_factory=lambda: ["08:00", "13:00", "19:00"])
    edge_tts_voice: str | None = None
    kokoro_voice: str | None = None  # voice used when TTS_PROVIDER=kokoro (GitHub Actions)
    kokoro_speed: float | None = None
    accent_color: str | None = None

    @field_validator("slots")
    @classmethod
    def _slots_ok(cls, v: list[str]) -> list[str]:
        for slot in v:
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", slot):
                raise ValueError(f"slot '{slot}' must look like HH:MM (24h)")
        return sorted(set(v))

    @field_validator("timezone")
    @classmethod
    def _tz_ok(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except Exception as exc:  # ZoneInfoNotFoundError / ValueError for malformed keys
            raise ValueError(f"unknown time zone '{v}' (use an IANA name like America/New_York)") from exc
        return v

    @field_validator("hashtags")
    @classmethod
    def _hashtags_ok(cls, v: list[str]) -> list[str]:
        return [h if h.startswith("#") else f"#{h}" for h in v]

    def brief(self) -> str:
        """Channel context handed to the LLM together with the topic."""
        lines = [
            f"Channel niche: {self.niche}",
            f"Audience: {self.audience}",
            f"Tone: {self.tone}",
        ]
        if self.rules:
            lines.append("Rules you MUST follow:\n" + "\n".join(f"- {r}" for r in self.rules))
        if self.avoid:
            lines.append("Never cover:\n" + "\n".join(f"- {a}" for a in self.avoid))
        lines.append("Use these hashtags in the description (first one first): " + " ".join(self.hashtags))
        return "\n".join(lines)


def list_channel_ids(directory: Path | None = None) -> list[str]:
    directory = directory or CHANNELS_DIR  # looked up at call time so tests can point it elsewhere
    return sorted(p.stem for p in directory.glob("*.json"))


def load_channel(channel_id: str, directory: Path | None = None) -> ChannelConfig:
    directory = directory or CHANNELS_DIR
    path = directory / f"{channel_id}.json"
    if not path.exists():
        raise RuntimeError(f"Unknown channel '{channel_id}'. Available: {', '.join(list_channel_ids(directory)) or '(none)'}")
    channel = ChannelConfig.model_validate_json(path.read_text(encoding="utf-8"))
    if channel.id != channel_id:
        raise RuntimeError(f"{path.name}: id '{channel.id}' must equal the file name '{channel_id}'")
    return channel


def upcoming_slots(
    channel: ChannelConfig, now: datetime, lead_hours: float = 3.0, window_hours: float = 28.0
) -> list[datetime]:
    """UTC datetimes of the channel's publishing slots that fall in [now+lead, now+lead+window).

    The window is a little longer than 24 h so that a daily run that starts late (GitHub schedules
    can be delayed) never leaves a gap; overlaps are removed by the plan-id check in history.
    Slots are interpreted in the channel's own time zone, so daylight-saving changes are handled."""
    tz = ZoneInfo(channel.timezone)
    start = now.astimezone(timezone.utc) + timedelta(hours=lead_hours)
    end = start + timedelta(hours=window_hours)
    first_day = (start.astimezone(tz) - timedelta(days=1)).date()
    found: list[datetime] = []
    for offset in range(0, 4):
        day = first_day + timedelta(days=offset)
        for slot in channel.slots:
            hour, minute = map(int, slot.split(":"))
            local = datetime.combine(day, time(hour, minute), tzinfo=tz)
            utc = local.astimezone(timezone.utc)
            if start <= utc < end:
                found.append(utc)
    return sorted(found)


def plan_id(channel_id: str, publish_at: datetime) -> str:
    return f"{channel_id}-{publish_at.astimezone(timezone.utc):%Y%m%dT%H%MZ}"
