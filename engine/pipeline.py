"""Main entrypoint.

    python engine/pipeline.py --topic "The Dark Psychology of Eye Contact" --render --upload
    python engine/pipeline.py --channel money --topic "..." --publish-at 2026-11-12T13:00:00Z --upload
    python engine/pipeline.py --demo --render          # offline smoke test, no API keys
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow `python engine/pipeline.py` (script mode) as well as `python -m engine.pipeline`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "engine"

import typer
from pydantic import ValidationError

from engine.ci import annotate_error

from engine.asset_manager import ensure_sfx
from engine.channels import ChannelConfig, load_channel
from engine.audio_engine import Timeline, align_timeline, align_words, get_tts_provider, synthesize_audio
from engine.config import get_settings
from engine.generator import ScriptSpec, generate_script, slugify, validate_quality
from engine.renderer import build_props, render_video, write_props
from engine.uploader import upload_video

app = typer.Typer(add_completion=False, help="YouTube Shorts automation pipeline.")


def _step(n: int, total: int, msg: str) -> None:
    typer.secho(f"\n[{n}/{total}] {msg}", fg=typer.colors.CYAN, bold=True)


def _load_script_file(path: Path) -> ScriptSpec:
    """Read a ready-made script JSON. Needs no LLM and no API key.
    Invalid files stop with a readable message; quality-rule problems are only warnings."""
    try:
        script = ScriptSpec.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:4]
        )
        raise RuntimeError(
            f"{path} is not a valid script file ({details}). Expected the format of "
            f"script.json written by this pipeline (title, hook, scenes[] with text, "
            f"motion_action and sfx)."
        ) from exc
    for problem in validate_quality(script):
        typer.secho(f"  warning: {problem}", fg=typer.colors.YELLOW)
    return script


@app.command()
def run(
    topic: str = typer.Option(None, "--topic", "-t", help="Video topic (required unless --script-file/--resume/--demo)."),
    render: bool = typer.Option(False, "--render", help="Render the MP4 with Remotion."),
    upload: bool = typer.Option(False, "--upload", help="Upload the MP4 to YouTube (renders first if needed)."),
    resume: Path = typer.Option(None, "--resume", help="Re-use an existing output/<run> folder; finished steps are skipped."),
    script_file: Path = typer.Option(None, "--script-file", exists=True, dir_okay=False, readable=True, help="Use this script.json instead of calling the LLM (no API key needed)."),
    voice_id: str = typer.Option(None, "--voice-id", help="Override the voice of the active TTS provider (EDGE_TTS_VOICE or ELEVENLABS_VOICE_ID)."),
    privacy: str = typer.Option(None, "--privacy", help="private | unlisted | public"),
    channel: str = typer.Option(None, "--channel", "-c", help="Channel id from channels/ (voice, accent colour, tone, SEO and the YouTube login come from it)."),
    publish_at: str = typer.Option(None, "--publish-at", help="Schedule the upload: ISO time, e.g. 2026-11-12T13:00:00Z (uploaded as private, YouTube publishes it then)."),
    plan_id: str = typer.Option(None, "--plan-id", help="Id of the planned slot (written to result.json for the history)."),
    reserve_file: Path = typer.Option(None, "--reserve-file", help="Backup script (state/reserve/...): used only if generating a fresh script fails, so the slot is never left empty."),
    demo: bool = typer.Option(False, "--demo", help="Offline demo with placeholder assets (no API keys)."),
) -> None:
    base_settings = get_settings()
    voice_field = "edge_tts_voice" if base_settings.tts_provider == "edge_tts" else "elevenlabs_voice_id"
    overrides = {
        k: v
        for k, v in {
            voice_field: voice_id,
            "youtube_privacy": privacy,
        }.items()
        if v
    }
    channel_cfg: ChannelConfig | None = load_channel(channel) if channel else None
    if channel_cfg:  # the channel's own voice / colour, unless overridden on the command line
        if channel_cfg.edge_tts_voice and base_settings.tts_provider == "edge_tts":
            overrides.setdefault("edge_tts_voice", channel_cfg.edge_tts_voice)
        if base_settings.tts_provider == "kokoro":
            if channel_cfg.kokoro_voice:
                overrides.setdefault("kokoro_voice", channel_cfg.kokoro_voice)
            if channel_cfg.kokoro_speed:
                overrides.setdefault("kokoro_speed", channel_cfg.kokoro_speed)
        if channel_cfg.accent_color:
            overrides.setdefault("accent_color", channel_cfg.accent_color)
        overrides.setdefault("youtube_category_id", channel_cfg.category_id)
    settings = base_settings.model_copy(update=overrides)

    publish_dt: datetime | None = None
    if publish_at:
        try:
            publish_dt = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
        except ValueError:
            raise typer.BadParameter(f"--publish-at '{publish_at}' is not an ISO time like 2026-11-12T13:00:00Z") from None
        if publish_dt.tzinfo is None:
            publish_dt = publish_dt.replace(tzinfo=timezone.utc)
        if publish_dt <= datetime.now(timezone.utc):
            typer.secho("  warning: --publish-at is in the past; YouTube will publish the video immediately.", fg=typer.colors.YELLOW)

    if not (topic or script_file or resume or demo):
        raise typer.BadParameter("Provide --topic, --script-file, --resume or --demo.")

    # ------------------------------------------------------------------ run folder
    if resume:
        run_dir = resume if resume.is_absolute() else Path.cwd() / resume
        if not run_dir.exists():
            run_dir = settings.output_dir / resume.name
    else:
        label = "demo" if demo else slugify(topic or (script_file.stem if script_file else "short"))
        if channel:
            label = f"{channel}-{label}"
        run_dir = settings.output_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{label}"
    run_dir.mkdir(parents=True, exist_ok=True)
    typer.echo(f"Run folder: {run_dir}")

    reserve_used = ""
    base_steps = 1 if demo else 3
    total_steps = base_steps + int(render or upload) + int(upload)
    sfx_files = ensure_sfx(settings)
    script_path = run_dir / "script.json"
    timeline_path = run_dir / "timeline.json"

    # ------------------------------------------------------------ build the assets
    if demo:
        from engine.demo import build_demo

        _step(1, total_steps, "Building offline demo (motion graphics, synthetic audio)")
        script, timeline, audio_name = build_demo(run_dir, settings.tail_seconds)
        script_path.write_text(script.model_dump_json(indent=2), encoding="utf-8")
        timeline_path.write_text(timeline.model_dump_json(indent=2), encoding="utf-8")
    else:
        # 1) script ----------------------------------------------------------------
        _step(1, total_steps, "Script (from file)" if script_file else f"Script ({settings.llm_provider})")
        if script_file:
            script = _load_script_file(script_file)
        elif script_path.exists():
            typer.echo("  reusing script.json")
            script = _load_script_file(script_path)
        else:
            try:
                script = generate_script(topic, settings, brief=channel_cfg.brief() if channel_cfg else "")
            except RuntimeError as exc:
                if not (reserve_file and reserve_file.exists()):
                    raise
                from engine.reserve import load_item

                item = load_item(reserve_file)
                typer.secho(f"  script generation failed ({str(exc)[:160]}); using the reserve script '{item.topic}'", fg=typer.colors.YELLOW)
                script, topic, reserve_used = item.script, item.topic, str(reserve_file)
        script_path.write_text(script.model_dump_json(indent=2), encoding="utf-8")
        typer.echo(f"  \"{script.title}\" - {len(script.scenes)} scenes, {script.word_count} words")

        # 2) voice -----------------------------------------------------------------
        _step(2, total_steps, f"Voice-over ({settings.tts_provider})")
        # reuse a voice file from an earlier run of this folder, else the type the provider writes
        audio_name = next(
            (n for n in ("voice.mp3", "voice.wav") if (run_dir / n).exists()),
            f"voice{get_tts_provider(settings).extension}",
        )
        audio_path = run_dir / audio_name
        if audio_path.exists():
            typer.echo(f"  reusing {audio_name}")
        else:
            synthesize_audio(script.narration, audio_path, settings)

        # 3) word timing -------------------------------------------------------------
        _step(3, total_steps, f"Word timestamps ({settings.whisper_provider}) + scene alignment")
        if timeline_path.exists() and audio_path.exists():
            typer.echo("  reusing timeline.json")
            timeline = Timeline.model_validate_json(timeline_path.read_text(encoding="utf-8"))
        else:
            whisper_words, audio_duration = align_words(audio_path, settings)
            (run_dir / "whisper_words.json").write_text(
                json.dumps([w.model_dump() for w in whisper_words], indent=2), encoding="utf-8"
            )
            timeline = align_timeline(script, whisper_words, audio_duration, settings.tail_seconds)
            timeline_path.write_text(timeline.model_dump_json(indent=2), encoding="utf-8")
        typer.echo(f"  video length {timeline.duration:.1f}s, {len(timeline.words)} words")

    # --------------------------------------------------------------------- props
    props = build_props(script, timeline, sfx_files, audio_name, run_dir, settings)
    props_path = write_props(props, run_dir)
    typer.echo(f"\nProps written: {props_path}")

    final_path = run_dir / "final.mp4"
    if render or upload:
        _step(base_steps + 1, total_steps, "Render (Remotion)")
        final_path = render_video(run_dir, props_path)
        typer.secho(f"  {final_path}", fg=typer.colors.GREEN)

    uploaded = None
    if upload:
        _step(total_steps, total_steps, "Upload (YouTube)")
        uploaded = upload_video(final_path, script, settings, channel_cfg, publish_dt)
        (run_dir / "youtube_url.txt").write_text(uploaded.url, encoding="utf-8")
        typer.secho(f"  {uploaded.url}", fg=typer.colors.GREEN)

    if render or upload:
        # Machine-readable summary; the daily workflow turns these into state/history.json.
        result = {
            "plan_id": plan_id or "",
            "channel": channel or "",
            "topic": topic or "",
            "title": script.title,
            "uploaded": uploaded is not None,
            "video_id": uploaded.video_id if uploaded else "",
            "url": uploaded.url if uploaded else "",
            "publish_at": (uploaded.publish_at if uploaded and uploaded.publish_at else (publish_at or "")),
            "reserve_used": reserve_used,
        }
        (run_dir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    typer.secho("\nDone.", fg=typer.colors.GREEN, bold=True)


def main() -> None:
    try:
        app()
    except ValidationError as exc:  # invalid .env values (e.g. paid provider without its key)
        for err in exc.errors():
            message = err["msg"].removeprefix("Value error, ")
            typer.secho(f"\nConfig error: {message}", fg=typer.colors.RED, err=True)
            annotate_error(f"Config error: {message}")
        raise SystemExit(1) from None
    except RuntimeError as exc:  # config/API/render problems: short message, no traceback
        typer.secho(f"\nError: {exc}", fg=typer.colors.RED, err=True)
        annotate_error(str(exc))
        raise SystemExit(1) from None
    except Exception as exc:  # noqa: BLE001 - unexpected crash: make it visible in the run summary, then re-raise
        annotate_error(f"{type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    main()
