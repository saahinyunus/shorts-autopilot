"""Variety: narrative formats and visual themes, so the videos of a channel do not all look and sound alike.

Every planned video gets
  * a narrative FORMAT (myth vs reality, true story, study, signs, comparison, mistake) that changes the way
    the script is built, and
  * a visual THEME (colour palette + background pattern) chosen from the channel's palettes.
Both rotate: the least recently used comes first and the same one never repeats back to back.
"""
from __future__ import annotations

from dataclasses import dataclass

# ------------------------------------------------------------------------------- formats
FORMATS: dict[str, dict[str, str]] = {
    "myth_vs_reality": {
        "name": "Efsane vs gerçek",
        "description": "Debunk one widespread belief: state the myth, then what the evidence actually shows.",
        "guidance": (
            "MYTH vs REALITY. Open with the belief most people hold. Show it as a CALLOUT (tag MYTH, tone BAD), "
            "then reveal the truth as a CALLOUT (tag FACT, tone GOOD) or a COMPARISON. Explain WHY the myth "
            "feels true, and give one concrete piece of evidence for the real answer."
        ),
    },
    "true_story": {
        "name": "Gerçek hikâye",
        "description": "Tell one real, well documented event with a beginning, a turning point and a lesson.",
        "guidance": (
            "TRUE STORY. Tell ONE real, well documented event or person's story in order: setup, turning point, "
            "consequence, lesson. Use a TIMELINE for the key dates and concrete details (who, where, what). "
            "Only include details you are sure are documented."
        ),
    },
    "study_or_experiment": {
        "name": "Araştırma / deney",
        "description": "Explain one famous study or experiment: setup, result, what it means for the viewer.",
        "guidance": (
            "STUDY OR EXPERIMENT. Describe ONE famous, well replicated study or experiment: how it was set up, "
            "what happened, what it means for the viewer's everyday life. Name the researcher or the study in "
            "`sources`. If the result is not widely replicated, choose a different example."
        ),
    },
    "spot_the_signs": {
        "name": "3 işaret",
        "description": "Teach the viewer to notice something: a short list of concrete signs, each with an example.",
        "guidance": (
            "SPOT THE SIGNS. Teach the viewer to notice something in 2-4 concrete signs, each with a one-line "
            "everyday example. Use a STEPS scene for the list. End with why noticing it matters."
        ),
    },
    "before_after": {
        "name": "Karşılaştırma",
        "description": "Compare two things (before/after, A vs B) and make the difference concrete.",
        "guidance": (
            "COMPARISON. Build the video around a clear contrast: before vs after, A vs B, expectation vs "
            "reality. Use COMPARISON scenes with concrete short values, and say which side wins and why."
        ),
    },
    "common_mistake": {
        "name": "Yaygın hata",
        "description": "Show a mistake almost everybody makes and the better alternative.",
        "guidance": (
            "COMMON MISTAKE. Name a mistake almost everybody makes, explain why it feels right, show what it "
            "costs, then give the better alternative in one clear sentence. STEPS or CALLOUT (WARNING / TIP) "
            "fit well."
        ),
    },
}
FORMAT_IDS = tuple(FORMATS)


def format_brief(format_id: str) -> str:
    """The block appended to the channel brief that tells the script writer how to build THIS video."""
    f = FORMATS.get(format_id)
    if not f:
        return ""
    return (
        "NARRATIVE FORMAT FOR THIS VIDEO (the structure must clearly follow it, so this video does not feel "
        "like the channel's other videos; if the topic does not fit perfectly, adapt the angle, never the "
        f"facts):\n{f['guidance']}"
    )


def _least_recent_first(options: tuple[str, ...], recent: list[str]) -> list[str]:
    """Options ordered by how long ago they were used (never used first)."""
    def last_used(option: str) -> int:
        return max((i for i, r in enumerate(recent) if r == option), default=-1)

    return sorted(options, key=lambda o: (last_used(o), options.index(o)))


def choose_formats(requested: list[str], recent: list[str]) -> list[str]:
    """One format per video. Requested ones (the planner's pick for the topic) are kept unless they would repeat
    the previous video's format or one already used in this batch; then the least recently used one is taken.
    `recent` lists the channel's earlier formats, oldest first."""
    result: list[str] = []
    history = list(recent)
    for want in requested:
        previous = history[-1] if history else ""
        ok = want in FORMATS and want != previous and want not in result[-1:]
        pick = want if ok else next(f for f in _least_recent_first(FORMAT_IDS, history) if f != previous)
        result.append(pick)
        history.append(pick)
    return result


# -------------------------------------------------------------------------------- themes
@dataclass(frozen=True)
class Theme:
    name: str
    bg_style: str  # grid | dots | diagonal | rings | waves
    accent: str
    glow: str
    glow2: str

    def props(self) -> dict:
        """The `theme` object of the Remotion props (src/types/schema.ts VideoTheme)."""
        return {"name": self.name, "bgStyle": self.bg_style, "glow": self.glow, "glow2": self.glow2}


# The first theme of each channel is its brand look; the others stay in the same colour family.
THEMES: dict[str, tuple[Theme, ...]] = {
    "money": (
        Theme("mint-grid", "grid", "#00E58F", "#00E58F", "#22D3EE"),
        Theme("gold-dots", "dots", "#FFC83D", "#FFC83D", "#FB923C"),
        Theme("emerald-waves", "waves", "#34D399", "#34D399", "#38BDF8"),
        Theme("lime-diagonal", "diagonal", "#A3E635", "#A3E635", "#22D3EE"),
        Theme("teal-rings", "rings", "#2DD4BF", "#2DD4BF", "#8B5CF6"),
    ),
    "psychology": (
        Theme("yellow-grid", "grid", "#FFE600", "#FFE600", "#8B5CF6"),
        Theme("violet-rings", "rings", "#C084FC", "#C084FC", "#FF6EC7"),
        Theme("amber-dots", "dots", "#FFB020", "#FFB020", "#F472B6"),
        Theme("rose-waves", "waves", "#FB7185", "#FB7185", "#FACC15"),
        Theme("sky-diagonal", "diagonal", "#38BDF8", "#38BDF8", "#FFE600"),
    ),
}
FALLBACK_THEMES: tuple[Theme, ...] = (
    Theme("cyan-grid", "grid", "#22D3EE", "#22D3EE", "#8B5CF6"),
    Theme("violet-rings", "rings", "#C084FC", "#C084FC", "#FF6EC7"),
    Theme("gold-dots", "dots", "#FFC83D", "#FFC83D", "#FB923C"),
)


def themes_for(channel_id: str) -> tuple[Theme, ...]:
    return THEMES.get(channel_id, FALLBACK_THEMES)


def theme_named(channel_id: str, name: str) -> Theme | None:
    return next((t for t in themes_for(channel_id) if t.name == name), None)


def choose_themes(channel_id: str, count: int, recent: list[str]) -> list[str]:
    """Theme names for `count` videos: least recently used first, never the same as the previous video."""
    options = tuple(t.name for t in themes_for(channel_id))
    history = list(recent)
    picks: list[str] = []
    for _ in range(count):
        previous = history[-1] if history else ""
        pick = next(t for t in _least_recent_first(options, history) if t != previous or len(options) == 1)
        picks.append(pick)
        history.append(pick)
    return picks
