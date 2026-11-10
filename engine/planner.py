"""Topic planner: asks the LLM for fresh, non-repeating video topics for a channel."""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .channels import ChannelConfig
from .generator import LLMOutputError, LLMProvider
from .variety import FORMAT_IDS, FORMATS

TOPICS_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "A specific, searchable video topic (5-14 words)"},
                    "format": {
                        "type": "string",
                        "enum": list(FORMAT_IDS),
                        "description": "The narrative format that suits this topic best",
                    },
                },
                "required": ["topic", "format"],
            },
        }
    },
    "required": ["topics"],
}

PLANNER_SYSTEM = """You plan topics for faceless 30-45 second YouTube Shorts channels.
Each topic must be specific (one clear idea a viewer could search for), surprising, defensible with
well-established facts, and answerable in ~40 seconds. Spread the topics across different sub-areas of the
niche; no two topics may cover the same idea. Respect the channel's rules and exclusions."""

_STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "why", "how", "what", "is", "are", "you", "your", "do", "does", "that", "this", "it", "with"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP}


def is_duplicate(a: str, b: str) -> bool:
    """Near-duplicate topics: similar wording or most of the meaningful words shared."""
    if SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.75:
        return True
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= 0.7


@dataclass
class PlannedTopic:
    topic: str
    format: str = ""  # one of variety.FORMAT_IDS, or "" when the planner gave none


def _format_block(recent_formats: list[str]) -> str:
    lines = [f"- {fid}: {f['description']}" for fid, f in FORMATS.items()]
    used = ", ".join(recent_formats[-6:]) or "(none yet)"
    return (
        "\n\nGive every topic the narrative `format` that suits it best. Available formats:\n"
        + "\n".join(lines)
        + f"\nRecently used on this channel (oldest first): {used}. Spread the formats: do not give two topics "
        "in this list the same format and avoid the most recent ones."
    )


def plan_topics(
    channel: ChannelConfig, count: int, previous: list[str], provider: LLMProvider, max_attempts: int = 3
) -> list[str]:
    """Return exactly `count` new topics, none repeating `previous` or each other."""
    return [p.topic for p in plan_topics_detailed(channel, count, previous, provider, max_attempts)]


def plan_topics_detailed(
    channel: ChannelConfig,
    count: int,
    previous: list[str],
    provider: LLMProvider,
    max_attempts: int = 3,
    recent_formats: list[str] | None = None,
) -> list[PlannedTopic]:
    """Like plan_topics, but every topic comes with the narrative format the planner chose for it."""
    seed = "\n".join(f"- {t}" for t in channel.topic_ideas)
    feedback = ""
    for attempt in range(1, max_attempts + 1):
        user = (
            f"CHANNEL BRIEF:\n{channel.brief()}\n\n"
            f"Example topics in this niche (for style only, do not reuse):\n{seed or '- (none)'}\n\n"
            "Already published (do NOT repeat or rephrase any of these):\n"
            + ("\n".join(f"- {t}" for t in previous[-300:]) or "- (nothing yet)")
            + f"\n\nSuggest {count + 2} NEW topics.{_format_block(recent_formats or [])}{feedback}"
        )
        try:
            raw = provider.complete_json(PLANNER_SYSTEM, user, TOPICS_SCHEMA)
            candidates = [
                (str(t.get("topic", "")).strip(), str(t.get("format", "")).strip())
                for t in raw.get("topics", [])
                if isinstance(t, dict)
            ]
        except LLMOutputError as exc:
            feedback = f"\n\nYour last answer was unusable: {exc}"
            continue

        chosen: list[PlannedTopic] = []
        rejected: list[str] = []
        for topic, fmt in candidates:
            taken = previous + [c.topic for c in chosen]
            if 12 <= len(topic) <= 140 and not any(is_duplicate(topic, other) for other in taken):
                chosen.append(PlannedTopic(topic, fmt if fmt in FORMATS else ""))
            else:
                rejected.append(topic)
        if len(chosen) >= count:
            return chosen[:count]
        feedback = (
            f"\n\nOnly {len(chosen)} of your last topics were new and valid. "
            "These were rejected as repeats or malformed: " + "; ".join(rejected[:6])
        )
    raise RuntimeError(f"The planner could not find {count} new topics for '{channel.id}' after {max_attempts} attempts.")
