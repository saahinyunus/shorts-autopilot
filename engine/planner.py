"""Topic planner: asks the LLM for fresh, non-repeating video topics for a channel."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from .channels import ChannelConfig
from .generator import LLMOutputError, LLMProvider

TOPICS_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "A specific, searchable video topic (5-14 words)"},
                },
                "required": ["topic"],
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


def plan_topics(
    channel: ChannelConfig, count: int, previous: list[str], provider: LLMProvider, max_attempts: int = 3
) -> list[str]:
    """Return exactly `count` new topics, none repeating `previous` or each other."""
    seed = "\n".join(f"- {t}" for t in channel.topic_ideas)
    feedback = ""
    for attempt in range(1, max_attempts + 1):
        user = (
            f"CHANNEL BRIEF:\n{channel.brief()}\n\n"
            f"Example topics in this niche (for style only, do not reuse):\n{seed or '- (none)'}\n\n"
            "Already published (do NOT repeat or rephrase any of these):\n"
            + ("\n".join(f"- {t}" for t in previous[-300:]) or "- (nothing yet)")
            + f"\n\nSuggest {count + 2} NEW topics.{feedback}"
        )
        try:
            raw = provider.complete_json(PLANNER_SYSTEM, user, TOPICS_SCHEMA)
            candidates = [str(t.get("topic", "")).strip() for t in raw.get("topics", []) if isinstance(t, dict)]
        except LLMOutputError as exc:
            feedback = f"\n\nYour last answer was unusable: {exc}"
            continue

        chosen: list[str] = []
        rejected: list[str] = []
        for topic in candidates:
            ok = 12 <= len(topic) <= 140 and not any(is_duplicate(topic, other) for other in previous + chosen)
            (chosen if ok else rejected).append(topic)
        if len(chosen) >= count:
            return chosen[:count]
        feedback = (
            f"\n\nOnly {len(chosen)} of your last topics were new and valid. "
            "These were rejected as repeats or malformed: " + "; ".join(rejected[:6])
        )
    raise RuntimeError(f"The planner could not find {count} new topics for '{channel.id}' after {max_attempts} attempts.")
