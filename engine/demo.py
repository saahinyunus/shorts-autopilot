"""Offline demo job: a hand-written storyboard + synthetic audio, no API keys needed.

Used by `pipeline.py --demo` to verify the Remotion layer and the whole render path.
The statistics shown are PLACEHOLDERS for layout testing, not real data.
"""
from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path

from .asset_manager import SAMPLE_RATE
from .audio_engine import SceneTiming, Timeline, WordTiming
from .generator import (
    ChartAction,
    CounterAction,
    DocumentAction,
    MinimalTextAction,
    SceneSpec,
    ScriptSpec,
    SearchAction,
)

DEMO_STORYBOARD = [
    ("Why can't you look away?", "whoosh",
     MinimalTextAction(type="MINIMAL_TEXT", headline="Why can't you look away", subtext="the 3-second rule")),
    ("Your brain locks on to eyes almost instantly.", "pop",
     CounterAction(type="COUNTER", target_number=87, suffix="%", label="DEMO STAT: LOCK-ON RATE")),
    ("People secretly type this into search every day.", "none",
     SearchAction(type="SEARCH", query_text="why do i hate eye contact", result_headline="Gaze aversion explained")),
    ("Hold a stare and tension builds fast.", "riser",
     ChartAction(type="CHART", trend="UP", label="Social tension")),
    ("Researchers keep one finding tightly under wraps.", "none",
     DocumentAction(type="DOCUMENT", title="Case file: gaze", highlight_text="Three seconds changes everything", is_redacted=True)),
    ("Then comfort collapses, and the urge to look away wins.", "none",
     ChartAction(type="CHART", trend="DOWN", label="Comfort level")),
    ("It takes just three seconds.", "pop",
     CounterAction(type="COUNTER", target_number=3, suffix=" SEC", label="DEMO STAT: TIME TO TENSION")),
    ("And that is exactly why", "sub_drop",
     MinimalTextAction(type="MINIMAL_TEXT", headline="Look away", subtext="")),
]


def _babble(path: Path, seconds: float) -> None:
    """Speech-ish amplitude-modulated tones so the audio track is audible in the demo."""
    rng = random.Random(7)
    n = int(SAMPLE_RATE * seconds)
    phase, freq, out = 0.0, 140.0, []
    for i in range(n):
        if i % (SAMPLE_RATE // 6) == 0:
            freq = rng.uniform(110, 220)
        t = i / SAMPLE_RATE
        phase += 2 * math.pi * freq / SAMPLE_RATE
        env = 0.5 + 0.5 * math.sin(2 * math.pi * 3.1 * t)
        out.append(0.25 * env * (math.sin(phase) + 0.4 * math.sin(2 * phase)))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"".join(struct.pack("<h", int(s * 32767)) for s in out))


def build_demo(run_dir: Path, tail: float = 0.35) -> tuple[ScriptSpec, Timeline, str]:
    """Create the demo audio in `run_dir`; return (script, timeline, audio file name)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    scenes = [
        SceneSpec(
            id=i,
            text=text,
            duration_estimate=round(len(text.split()) / 2.9, 1),
            motion_action=action,
            sfx=sfx,  # type: ignore[arg-type]
        )
        for i, (text, sfx, action) in enumerate(DEMO_STORYBOARD, start=1)
    ]
    script = ScriptSpec(
        title="Demo: The Power of Eye Contact",
        hook=scenes[0].text,
        scenes=scenes,
        loop_transition_text=scenes[-1].text,
        description="Offline demo render.\n\n#Shorts",
        tags=["demo"],
    )

    # Demo pace: slower than real speech (2.2 words/s) so each animation has time to play out.
    per_word = 1 / 2.2
    words: list[WordTiming] = []
    timings: list[SceneTiming] = []
    cursor = 0.0
    for scene in scenes:
        timings.append(SceneTiming(id=scene.id, start=round(cursor, 3), end=0))
        for tok in scene.text.split():
            words.append(WordTiming(text=tok, start=round(cursor, 3), end=round(cursor + per_word, 3)))
            cursor += per_word
        cursor += 0.4  # beat between sentences (lets the animation finish)
    total = cursor + tail
    for i, t in enumerate(timings):
        t.end = round(timings[i + 1].start if i + 1 < len(timings) else total, 3)

    _babble(run_dir / "voice.wav", total)
    return script, Timeline(duration=round(total, 3), words=words, scenes=timings), "voice.wav"
