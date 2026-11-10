"""SFX library (royalty-free placeholders or your own files) and the SFX cue plan.

Visuals are no longer downloaded: every scene is a motion graphic drawn by Remotion code.
"""
from __future__ import annotations

import json
import math
import random
import struct
import wave
from functools import lru_cache
from pathlib import Path

from .audio_engine import SceneTiming
from .config import ROOT_DIR, Settings, get_settings
from .generator import ScriptSpec

SFX_NAMES = ("whoosh", "pop", "riser", "sub_drop")

# Seconds BEFORE the trigger moment that each effect should start, so the
# "impact" of the sound lands on the cut / animation peak (riser builds up, whoosh leads in).
SFX_LEAD = {"whoosh": 0.12, "pop": 0.0, "riser": 1.1, "sub_drop": 0.0}
SFX_VOLUME = {"whoosh": 0.55, "pop": 0.6, "riser": 0.45, "sub_drop": 0.8}

MOTION_TIMING_FILE = ROOT_DIR / "src" / "types" / "motionTiming.json"


# ------------------------------------------------------------------------- SFX
SAMPLE_RATE = 44_100


def _write_wav(path: Path, samples: list[float]) -> None:
    peak = max(1e-9, max(abs(s) for s in samples))
    scale = 0.89 / peak
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(
            b"".join(struct.pack("<h", int(max(-1, min(1, s * scale)) * 32767)) for s in samples)
        )


def _synth_whoosh() -> list[float]:
    rng, n, y, out = random.Random(1), int(SAMPLE_RATE * 0.6), 0.0, []
    for i in range(n):
        t = i / n
        env = math.sin(math.pi * t) ** 1.5
        cutoff = 0.02 + 0.45 * math.sin(math.pi * t)  # low-pass sweeps up then down
        y += cutoff * (rng.uniform(-1, 1) - y)
        out.append(y * env)
    return out


def _synth_pop() -> list[float]:
    n, phase, out = int(SAMPLE_RATE * 0.14), 0.0, []
    for i in range(n):
        t = i / SAMPLE_RATE
        freq = 180 + 700 * math.exp(-t * 40)
        phase += 2 * math.pi * freq / SAMPLE_RATE
        out.append(math.sin(phase) * math.exp(-t * 28))
    return out


def _synth_riser() -> list[float]:
    rng, n, y, phase, out = random.Random(2), int(SAMPLE_RATE * 1.2), 0.0, 0.0, []
    for i in range(n):
        t = i / n
        phase += 2 * math.pi * (180 + 1400 * t * t) / SAMPLE_RATE
        y += (0.03 + 0.4 * t) * (rng.uniform(-1, 1) - y)
        out.append((0.55 * math.sin(phase) + 0.45 * y) * (t**1.8))
    return out


def _synth_sub_drop() -> list[float]:
    n, phase, out = int(SAMPLE_RATE * 1.0), 0.0, []
    for i in range(n):
        t = i / SAMPLE_RATE
        phase += 2 * math.pi * (32 + 70 * math.exp(-t * 5)) / SAMPLE_RATE
        click = math.exp(-t * 400) * 0.5
        out.append(math.sin(phase) * math.exp(-t * 3.2) + click)
    return out


_SYNTH = {
    "whoosh": _synth_whoosh,
    "pop": _synth_pop,
    "riser": _synth_riser,
    "sub_drop": _synth_sub_drop,
}


def ensure_sfx(settings: Settings | None = None) -> dict[str, Path]:
    """Return {name: file}. Uses your own assets/sfx/<name>.mp3|wav|ogg when present,
    otherwise synthesises a royalty-free placeholder .wav so the pipeline always runs."""
    settings = settings or get_settings()
    settings.sfx_dir.mkdir(parents=True, exist_ok=True)
    found: dict[str, Path] = {}
    for name in SFX_NAMES:
        existing = next(
            (settings.sfx_dir / f"{name}{ext}" for ext in (".mp3", ".wav", ".ogg")
             if (settings.sfx_dir / f"{name}{ext}").exists()),
            None,
        )
        if existing is None:
            existing = settings.sfx_dir / f"{name}.wav"
            _write_wav(existing, _SYNTH[name]())
        found[name] = existing
    return found


@lru_cache
def motion_timing() -> dict:
    """Animation event times shared with the Remotion components (src/types/motionTiming.json)."""
    return json.loads(MOTION_TIMING_FILE.read_text(encoding="utf-8"))


def motion_impacts(scene) -> list[tuple[str, float]]:
    """(sfx name, seconds after scene start) for the moments where the animation hits."""
    t, action = motion_timing(), scene.motion_action
    if action.type == "COUNTER":
        return [("pop", t["counter_land_s"])]
    if action.type == "CHART" and action.trend == "DOWN":
        return [("sub_drop", t["chart_start_s"] + t["chart_draw_s"] * t["chart_crash_fraction"])]
    if action.type == "DOCUMENT" and action.is_redacted:
        return [("whoosh", t["document_unredact_s"])]
    return []


def build_sfx_cues(
    script: ScriptSpec, scene_timings: list[SceneTiming], sfx_files: dict[str, Path]
) -> list[dict]:
    """Props-ready SFX cues, sorted by time. `file` is relative to the run's public dir.

    Two sources: the sfx the script asks for at the scene start, plus automatic impact sounds
    synced to each animation's peak (counter landing, chart crash, censor-bar reveal).
    """
    wanted: list[tuple[str, float]] = []
    for scene, timing in zip(script.scenes, scene_timings):
        if scene.sfx != "none":
            wanted.append((scene.sfx, timing.start))
        for name, offset in motion_impacts(scene):
            if timing.start + offset < timing.end - 0.1:  # skip if the scene is too short
                wanted.append((name, timing.start + offset))

    cues = [
        {
            "name": name,
            "file": f"sfx/{sfx_files[name].name}",
            "at": round(max(0.0, at - SFX_LEAD[name]), 3),
            "volume": SFX_VOLUME[name],
        }
        for name, at in wanted
        if name in sfx_files
    ]
    return sorted(cues, key=lambda c: c["at"])
