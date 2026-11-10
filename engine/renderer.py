"""Turns generated assets into Remotion input props and renders the vertical MP4."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .asset_manager import build_sfx_cues
from .audio_engine import Timeline
from .config import ROOT_DIR, Settings, get_settings
from .generator import ScriptSpec

COMPOSITION_ID = "ShortsVideo"


def build_props(
    script: ScriptSpec,
    timeline: Timeline,
    sfx_files: dict[str, Path],
    audio_file: str,
    run_dir: Path,
    settings: Settings | None = None,
) -> dict:
    """Create the Remotion `VideoProps` dict and copy the SFX files the video uses into
    `<run_dir>/sfx` (the run folder doubles as Remotion's public dir)."""
    settings = settings or get_settings()
    cues = build_sfx_cues(script, timeline.scenes, sfx_files)

    sfx_dir = run_dir / "sfx"
    sfx_dir.mkdir(parents=True, exist_ok=True)
    for cue in cues:
        name = Path(cue["file"]).name
        source = next(p for p in sfx_files.values() if p.name == name)
        shutil.copyfile(source, sfx_dir / name)

    return {
        "title": script.title,
        "fps": settings.fps,
        "durationInSeconds": timeline.duration,
        "audioFile": audio_file,
        "accentColor": settings.accent_color,
        "seamlessLoop": settings.seamless_loop,
        "scenes": [
            {
                "id": scene.id,
                "text": spec.text,
                "start": scene.start,
                "end": scene.end,
                "motion": spec.motion_action.model_dump(),
            }
            for scene, spec in zip(timeline.scenes, script.scenes)
        ],
        "words": [w.model_dump() for w in timeline.words],
        "sfx": cues,
    }


def write_props(props: dict, run_dir: Path) -> Path:
    path = run_dir / "input-props.json"
    path.write_text(json.dumps(props, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def render_video(run_dir: Path, props_path: Path, out_name: str = "final.mp4") -> Path:
    """Run `npx remotion render` with the run folder as the public dir."""
    npx = shutil.which("npx")
    if npx is None:
        raise RuntimeError("`npx` not found. Install Node.js 18+ and run `npm install`.")
    if not (ROOT_DIR / "node_modules").exists():
        raise RuntimeError("node_modules missing. Run `npm install` in the project root first.")

    def rel(p: Path) -> str:
        return os.path.relpath(p, ROOT_DIR).replace("\\", "/")

    out_path = run_dir / out_name
    cmd = [
        npx,
        "remotion",
        "render",
        "src/index.ts",
        COMPOSITION_ID,
        rel(out_path),
        f"--props={rel(props_path)}",
        f"--public-dir={rel(run_dir)}",
    ]
    print(f"[render] {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=ROOT_DIR)
    if result.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"Remotion render failed (exit code {result.returncode}).")
    return out_path
