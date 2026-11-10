"""Run each stage on its own, with a cost estimate and a confirmation before anything is spent.

    python engine/test_modules.py estimate --script-file output/_modtest/script.json
    python engine/test_modules.py script --topic "The Dark Psychology of Eye Contact"   # LLM_PROVIDER from .env
    python engine/test_modules.py audio                 # uses <workdir>/script.json
    python engine/test_modules.py audio --skip-tts      # re-run only word timestamps + alignment
    python engine/test_modules.py assets                # SFX library + motion/SFX plan (free)

Everything lands in `output/_modtest/` (override with --workdir). Nothing is rendered or uploaded.
With the default free providers (edge_tts + faster_whisper) `audio` needs no API key and costs $0.
Paid-provider costs are rough estimates from public list prices.
Visuals are rendered locally by Remotion, so there is no image-generation cost any more.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "engine"

import typer
from pydantic import ValidationError

from engine.asset_manager import build_sfx_cues, ensure_sfx, motion_impacts
from engine.audio_engine import SceneTiming, align_timeline, align_words, get_tts_provider, get_whisper_provider
from engine.config import ROOT_DIR, Settings, get_settings
from engine.generator import WORDS_PER_SECOND, ScriptSpec, generate_script, get_llm_provider, validate_quality

app = typer.Typer(add_completion=False, help="Run pipeline stages one at a time, with cost previews.")

DEFAULT_WORKDIR = ROOT_DIR / "output" / "_modtest"



def estimate_costs(script: ScriptSpec, settings: Settings) -> dict:
    """Pure function (no network): size of the audio job. Dollar amounts come from the selected
    providers (`provider.cost_summary`), because free local engines cost nothing."""
    return {
        "words": script.word_count,
        "spoken_seconds": round(script.word_count / WORDS_PER_SECOND, 1),
        "characters": len(script.narration),
    }


def _load_script(path: Path) -> ScriptSpec:
    if not path.exists():
        raise RuntimeError(f"{path} not found. Run the `script` command first or pass --script-file.")
    return ScriptSpec.model_validate_json(path.read_text(encoding="utf-8"))


def _confirm(yes: bool) -> None:
    if not yes:
        typer.confirm("Spend this and continue?", abort=True)


def _banner(title: str, rows: list[str]) -> None:
    typer.secho(f"\n== {title} ==", fg=typer.colors.CYAN, bold=True)
    for row in rows:
        typer.echo(f"  {row}")


@app.command()
def estimate(
    script_file: Path = typer.Option(None, "--script-file", help="Default: <workdir>/script.json"),
    workdir: Path = typer.Option(DEFAULT_WORKDIR, "--workdir"),
) -> None:
    """Offline: print what the audio stage would cost with the selected providers. No API calls."""
    settings = get_settings()
    script = _load_script(script_file or workdir / "script.json")
    c = estimate_costs(script, settings)
    tts, whisper = get_tts_provider(settings), get_whisper_provider(settings)
    _banner(
        "Cost preview (no calls made)",
        [
            f"Script:  {c['words']} words, ~{c['spoken_seconds']}s spoken, {len(script.scenes)} scenes",
            f"TTS      [{tts.name}]: {tts.cost_summary(script.narration)}",
            f"Whisper  [{whisper.name}]: {whisper.cost_summary(c['spoken_seconds'])}",
            "Visuals: $0 (rendered locally by Remotion)",
        ],
    )


@app.command()
def script(
    topic: str = typer.Option(..., "--topic", "-t"),
    workdir: Path = typer.Option(DEFAULT_WORKDIR, "--workdir"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """generator.py only: one LLM call with the provider from .env (retried up to 3x if the script
    breaks the rules). Only the selected provider's key is needed; free providers don't ask."""
    settings = get_settings()
    provider = get_llm_provider(settings)  # checks only the selected provider's key
    _banner(
        "Script + motion storyboard",
        [
            f"LLM provider: {provider.name}",
            f"Model:        {provider.model_label}",
            f"Cost:         {provider.cost_summary()}",
        ],
    )
    if not provider.is_free:
        _confirm(yes)

    started = time.time()
    result = generate_script(topic, settings, provider=provider)
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / "script.json"
    out.write_text(result.model_dump_json(indent=2), encoding="utf-8")

    typer.secho(f"\nOK in {time.time() - started:.1f}s -> {out}", fg=typer.colors.GREEN)
    typer.echo(f"Title: {result.title}")
    typer.echo(f"{result.word_count} words (~{result.word_count / WORDS_PER_SECOND:.0f}s), {len(result.scenes)} scenes")
    for s in result.scenes:
        typer.echo(f"  {s.id:>2} [{s.motion_action.type:<12}|{s.sfx:<8}] {s.text}")
    typer.echo(f"Loop: \"{result.scenes[-1].text}\"  ->  \"{result.hook}\"")


@app.command()
def audio(
    script_file: Path = typer.Option(None, "--script-file", help="Default: <workdir>/script.json"),
    workdir: Path = typer.Option(DEFAULT_WORKDIR, "--workdir"),
    skip_tts: bool = typer.Option(False, "--skip-tts", help="Reuse <workdir>/voice.mp3; only run word timestamps + alignment."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt (only shown for paid providers)."),
) -> None:
    """audio_engine.py only: TTS -> word timestamps -> scene alignment, with the providers from .env.

    Free local providers (edge_tts + faster_whisper) need no API key and no confirmation."""
    settings = get_settings()
    script_obj = _load_script(script_file or workdir / "script.json")
    voice = next(
        (workdir / n for n in ("voice.mp3", "voice.wav") if (workdir / n).exists()),
        workdir / f"voice{get_tts_provider(settings).extension}",
    )
    c = estimate_costs(script_obj, settings)

    if skip_tts and not voice.exists():
        raise RuntimeError(f"--skip-tts needs an existing {voice}")
    tts = None if skip_tts else get_tts_provider(settings)  # paid providers check their key here
    whisper = get_whisper_provider(settings)

    rows = [
        f"TTS provider:     {'skipped (reusing ' + voice.name + ')' if tts is None else tts.name}",
        f"Whisper provider: {whisper.name} (only used when the TTS engine gives no word timings)",
    ]
    if tts is not None:
        rows.append(f"TTS cost:         {tts.cost_summary(script_obj.narration)}")
    rows.append(f"Whisper cost:     {whisper.cost_summary(c['spoken_seconds'])}")
    _banner("Voice + word timing", rows)

    spends_money = (tts is not None and not tts.is_free) or not whisper.is_free
    if spends_money:
        _confirm(yes)

    workdir.mkdir(parents=True, exist_ok=True)
    if tts is not None:
        started = time.time()
        tts.synthesize(script_obj.narration, voice)
        typer.echo(f"voice-over -> {voice} ({voice.stat().st_size / 1024:.0f} KB, {time.time() - started:.1f}s)")

    started = time.time()
    words, duration = align_words(voice, settings)  # same path as the pipeline (TTS boundaries, else Whisper)
    typer.echo(f"{len(words)} word timestamps in {time.time() - started:.1f}s")
    (workdir / "whisper_words.json").write_text(
        json.dumps([w.model_dump() for w in words], indent=2), encoding="utf-8"
    )
    timeline = align_timeline(script_obj, words, duration, settings.tail_seconds)
    (workdir / "timeline.json").write_text(timeline.model_dump_json(indent=2), encoding="utf-8")

    typer.echo("")
    typer.secho(f"OK: audio {duration:.1f}s, video would be {timeline.duration:.1f}s", fg=typer.colors.GREEN)
    if not 25 <= timeline.duration <= 60:
        typer.secho("  WARNING: outside the 30-60s Shorts target.", fg=typer.colors.YELLOW)
    # Rough alignment health: spoken words vs script words.
    ratio = len(words) / max(1, script_obj.word_count)
    if not 0.9 <= ratio <= 1.1:
        typer.secho(f"  WARNING: heard {len(words)} words vs {script_obj.word_count} in the script.", fg=typer.colors.YELLOW)
    for scene, t in zip(script_obj.scenes, timeline.scenes):
        typer.echo(f"  {t.start:6.2f}-{t.end:6.2f}s  {scene.text}")


@app.command()
def assets(
    script_file: Path = typer.Option(None, "--script-file", help="Default: <workdir>/script.json"),
    workdir: Path = typer.Option(DEFAULT_WORKDIR, "--workdir"),
) -> None:
    """asset_manager.py only (free): prepare the SFX library, check the motion storyboard against
    the quality rules, and print the SFX cue plan using the script's estimated timings."""
    settings = get_settings()
    script_obj = _load_script(script_file or workdir / "script.json")

    sfx = ensure_sfx(settings)
    typer.echo("SFX ready: " + ", ".join(f"{k}={v.name}" for k, v in sfx.items()))

    problems = validate_quality(script_obj)
    if problems:
        typer.secho("\nStoryboard problems:", fg=typer.colors.YELLOW)
        for p in problems:
            typer.echo(f"  - {p}")
    else:
        typer.secho("\nStoryboard passes all quality rules.", fg=typer.colors.GREEN)

    # Rough timeline from duration estimates (real timings come from the `audio` command).
    timings, cursor = [], 0.0
    for scene in script_obj.scenes:
        timings.append(
            SceneTiming(id=scene.id, start=round(cursor, 2), end=round(cursor + scene.duration_estimate, 2))
        )
        cursor += scene.duration_estimate

    _banner("Motion plan (estimated times)", [])
    for scene, t in zip(script_obj.scenes, timings):
        impacts = ", ".join(f"{n}@+{o:.2f}s" for n, o in motion_impacts(scene)) or "-"
        typer.echo(f"  {t.start:6.1f}s  {scene.motion_action.type:<12} sfx={scene.sfx:<8} impacts: {impacts}")

    cues = build_sfx_cues(script_obj, timings, sfx)
    _banner(f"{len(cues)} SFX cues", [f"{c['at']:6.2f}s  {c['name']}" for c in cues])


def main() -> None:
    try:
        app()
    except ValidationError as exc:  # invalid .env values (e.g. paid provider without its key)
        for err in exc.errors():
            message = err["msg"].removeprefix("Value error, ")
            typer.secho(f"\nConfig error: {message}", fg=typer.colors.RED, err=True)
        raise SystemExit(1) from None
    except RuntimeError as exc:
        typer.secho(f"\nError: {exc}", fg=typer.colors.RED, err=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
