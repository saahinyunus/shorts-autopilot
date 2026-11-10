"""Daily autopilot helpers used by .github/workflows/daily.yml.

    python engine/daily.py plan [--channels psychology,money] [--github-output]
    python engine/daily.py record --results-dir artifacts

`plan` decides WHAT to produce: for every channel, which publishing slots fall in the next ~28 hours
(and are not published yet) and a fresh topic for each. `record` stores finished uploads in
state/history.json so the next run never repeats a topic or a slot.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "engine"

import typer
from pydantic import ValidationError

from engine.channels import list_channel_ids, load_channel, plan_id, upcoming_slots
from engine.config import get_settings
from engine.generator import get_llm_provider
from engine.history import HistoryEntry, load_history, save_history
from engine.planner import plan_topics

app = typer.Typer(add_completion=False, help="Daily planning and bookkeeping for the autopilot.")


def build_plan(channel_ids: list[str], now: datetime, settings, history, provider_factory) -> tuple[list[dict], list[str]]:
    """Returns (items to produce, error messages). A failing channel never blocks the others."""
    items: list[dict] = []
    errors: list[str] = []
    provider = None
    planned_topics: list[str] = []  # topics chosen earlier in this very run (all channels)
    for cid in channel_ids:
        try:
            channel = load_channel(cid)
            slots = [
                s
                for s in upcoming_slots(channel, now, settings.plan_lead_hours, settings.plan_window_hours)
                if not history.has_plan(plan_id(cid, s))
            ]
            if not slots:
                continue
            provider = provider or provider_factory()
            # Nothing may repeat: not this channel's whole history, not the other channels' videos
            # (e.g. "decoy pricing" must not appear on both), not the channel's already_covered list,
            # not another topic planned in this run. The channel's own newest entries come last, so
            # they are the ones the prompt shows when the list is very long.
            others = [t for e in history.entries if e.channel != cid for t in (e.topic, e.title) if t]
            own = [t for e in history.entries if e.channel == cid for t in (e.topic, e.title) if t]
            previous = others + planned_topics + channel.already_covered + own
            topics = plan_topics(channel, len(slots), previous, provider)
            planned_topics.extend(topics)
            for slot, topic in zip(slots, topics):
                items.append(
                    {
                        "channel": cid,
                        "topic": topic,
                        "publish_at": slot.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "plan_id": plan_id(cid, slot),
                    }
                )
        except RuntimeError as exc:
            errors.append(f"{cid}: {exc}")
    return items, errors


@app.command()
def plan(
    channels: str = typer.Option("", "--channels", help="Comma-separated channel ids (default: all in channels/)."),
    now: str = typer.Option(None, "--now", help="Pretend it is this ISO time (testing)."),
    github_output: bool = typer.Option(False, "--github-output", help="Write matrix/count/errors to $GITHUB_OUTPUT."),
) -> None:
    settings = get_settings()
    ids = [c.strip() for c in channels.split(",") if c.strip()] or list_channel_ids()
    now_dt = datetime.fromisoformat(now.replace("Z", "+00:00")) if now else datetime.now(timezone.utc)
    history = load_history(settings.state_dir / "history.json")

    items, errors = build_plan(ids, now_dt, settings, history, lambda: get_llm_provider(settings))

    typer.echo(json.dumps(items, indent=2, ensure_ascii=False))
    for message in errors:
        typer.secho(f"::error::planning failed for {message}", fg=typer.colors.RED, err=True)

    if github_output and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"matrix={json.dumps({'include': items}, ensure_ascii=False)}\n")
            fh.write(f"count={len(items)}\n")
            fh.write(f"errors={' | '.join(errors).replace(chr(10), ' ')}\n")
    if errors and not items:
        raise SystemExit(1)


@app.command()
def record(
    results_dir: Path = typer.Option(..., "--results-dir", exists=True, file_okay=False, help="Folder with result.json files (searched recursively)."),
) -> None:
    settings = get_settings()
    path = settings.state_dir / "history.json"
    history = load_history(path)
    added = skipped = 0
    for file in sorted(results_dir.rglob("result.json")):
        data = json.loads(file.read_text(encoding="utf-8"))
        if not data.get("uploaded") or not data.get("plan_id"):
            skipped += 1
            continue
        entry = HistoryEntry(**{k: data.get(k, "") for k in HistoryEntry.model_fields})
        added += int(history.add(entry))
    save_history(history, path)
    typer.echo(f"history: {added} added, {skipped} skipped, {len(history.entries)} total")


def main() -> None:
    try:
        app()
    except ValidationError as exc:
        for err in exc.errors():
            typer.secho(f"\nConfig error: {err['msg'].removeprefix('Value error, ')}", fg=typer.colors.RED, err=True)
        raise SystemExit(1) from None
    except RuntimeError as exc:
        typer.secho(f"\nError: {exc}", fg=typer.colors.RED, err=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
