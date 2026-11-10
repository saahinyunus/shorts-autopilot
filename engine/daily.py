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

from engine.ci import annotate_error

from engine.channels import list_channel_ids, load_channel, plan_id, upcoming_slots
from engine.config import get_settings
from engine.generator import get_llm_provider
from engine.history import HistoryEntry, load_history, save_history
from engine.planner import PlannedTopic, plan_topics_detailed
from engine.reserve import available, prune, relpath
from engine.variety import choose_formats, choose_themes

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
            # Nothing may repeat: not this channel's whole history, not the other channels' videos
            # (e.g. "decoy pricing" must not appear on both), not the channel's already_covered list,
            # not another topic planned in this run. The channel's own newest entries come last, so
            # they are the ones the prompt shows when the list is very long.
            others = [t for e in history.entries if e.channel != cid for t in (e.topic, e.title) if t]
            own = [t for e in history.entries if e.channel == cid for t in (e.topic, e.title) if t]
            # a topic waiting in another channel's reserve counts as taken too
            other_reserve = [
                t
                for other in channel_ids + [c for c in list_channel_ids() if c not in channel_ids]
                if other != cid
                for _, t in available(other, history, root=settings.state_dir / "reserve")
            ]
            # this channel's own reserve scripts are future videos too: a fresh topic must not be the same idea
            own_reserve = [t for _, t in available(cid, history, root=settings.state_dir / "reserve")]
            previous = others + other_reserve + planned_topics + channel.already_covered + own + own_reserve
            recent = [e for e in history.entries if e.channel == cid]
            recent_formats = [e.format for e in recent if e.format]
            planned: list[PlannedTopic] = []
            try:
                provider = provider or provider_factory()
                planned = plan_topics_detailed(channel, len(slots), previous, provider, recent_formats=recent_formats)
            except Exception as exc:  # noqa: BLE001 - the reserve below keeps every slot filled
                print(f"[plan] {cid}: the LLM could not plan topics ({str(exc)[:200]}); using the reserve")
            topics = [p.topic for p in planned]
            wanted_formats = [p.format for p in planned]
            planned_topics.extend(topics)

            # Reserve scripts: the topic source if the planner failed, otherwise a backup for the producer.
            pool = [
                (path, topic)
                for path, topic in available(cid, history, avoid=planned_topics, root=settings.state_dir / "reserve")
            ]
            channel_items: list[dict] = []
            wanted_formats_for_items: list[str] = []
            unfilled = 0
            for n, slot in enumerate(slots):
                reserve = ""
                if n < len(topics):
                    topic = topics[n]
                elif pool:
                    path, topic = pool.pop(0)
                    reserve = relpath(path)
                    planned_topics.append(topic)
                else:
                    unfilled += 1
                    continue
                channel_items.append(
                    {
                        "channel": cid,
                        "topic": topic,
                        "publish_at": slot.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "plan_id": plan_id(cid, slot),
                        "reserve": reserve,
                    }
                )
                wanted_formats_for_items.append(wanted_formats[n] if n < len(wanted_formats) else "")
            for item in channel_items:  # every other slot gets a backup script, if one is left
                if not item["reserve"] and pool:
                    item["reserve"] = relpath(pool.pop(0)[0])
            # variety: a narrative format and a visual theme per video, rotating and never the same twice in a row
            formats = choose_formats(wanted_formats_for_items, recent_formats)
            themes = choose_themes(cid, len(channel_items), [e.theme for e in recent if e.theme])
            for item, fmt, theme in zip(channel_items, formats, themes):
                item["format"], item["theme"] = fmt, theme
            items.extend(channel_items)
            if unfilled:
                errors.append(f"{cid}: no topic and no reserve script for {unfilled} slot(s)")
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
    removed = prune(history, settings.state_dir / "reserve")  # reserve scripts whose topic is now published
    if removed:
        typer.echo(f"reserve: removed {len(removed)} used script(s)")
    typer.echo(f"history: {added} added, {skipped} skipped, {len(history.entries)} total")


def post_pending_comments(
    history, settings, youtube_for=None, now=None, min_age_hours: float = 6.0, max_comments: int = 20
) -> list[tuple[HistoryEntry, str]]:
    """Safety net: post the first comment under videos that went public at least `min_age_hours` ago and
    still have none (the Telegram worker normally does it within minutes). Returns [(entry, error or "")]."""
    from engine.uploader import find_comment, post_comment

    now = now or datetime.now(timezone.utc)

    if youtube_for is None:
        from googleapiclient.discovery import build

        from engine.uploader import get_credentials

        cache: dict = {}

        def youtube_for(channel_id):  # noqa: E306
            if channel_id not in cache:
                cache[channel_id] = build("youtube", "v3", credentials=get_credentials(settings, channel_id), cache_discovery=False)
            return cache[channel_id]

    outcome: list[tuple[HistoryEntry, str]] = []
    for entry in history.entries:
        if not entry.comment_text or entry.comment_id or not entry.publish_at or len(outcome) >= max_comments:
            continue
        if (now - datetime.fromisoformat(entry.publish_at.replace("Z", "+00:00"))).total_seconds() < min_age_hours * 3600:
            continue
        try:
            yt = youtube_for(entry.channel)
            existing = find_comment(yt, entry.video_id, entry.comment_text)  # the worker may have posted it already
            entry.comment_id = existing or post_comment(yt, entry.video_id, entry.comment_text)
            outcome.append((entry, ""))
        except Exception as exc:  # noqa: BLE001
            outcome.append((entry, type(exc).__name__))
    return outcome


@app.command()
def comments() -> None:
    """Safety net for the first comment (normally posted by the Telegram worker at publish time)."""
    settings = get_settings()
    path = settings.state_dir / "history.json"
    history = load_history(path)
    outcome = post_pending_comments(history, settings)
    save_history(history, path)
    for entry, error in outcome:
        typer.echo(f"comment {entry.plan_id}: {'posted' if not error else 'failed (' + error + ')'}")
    if not outcome:
        typer.echo("no pending comments")
    try:  # tell Telegram about late comments (best effort)
        from engine.telegram_bot import get_telegram

        tg = get_telegram(settings)
        posted = [e for e, err in outcome if not err]
        if tg and posted:
            lines = [f"• {e.title or e.topic}: {e.comment_text}" for e in posted]
            tg.send("💬 <b>Yorum yazıldı</b> (önceki denemede yazılamamıştı)\n" + "\n".join(lines))
    except Exception:  # noqa: BLE001
        pass


@app.command()
def heartbeat() -> None:
    """Write state/status.json: what the last run did. The Telegram worker reads it (no GitHub API needed)."""
    settings = get_settings()
    history = load_history(settings.state_dir / "history.json")
    env = os.environ
    status = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_url": env.get("RUN_URL", ""),
        "plan_count": int(env.get("PLAN_COUNT") or 0),
        "plan_result": env.get("PLAN_RESULT", ""),
        "produce_result": env.get("PRODUCE_RESULT", ""),
        "reserve": {
            cid: len(available(cid, history, root=settings.state_dir / "reserve")) for cid in list_channel_ids()
        },
        "llms": [n for n, k in (("Gemini", settings.gemini_api_key), ("Groq", settings.groq_api_key), ("Anthropic", settings.anthropic_api_key)) if k],
    }
    (settings.state_dir / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    typer.echo(f"status.json written ({status['produce_result'] or 'no produce result'})")


def main() -> None:
    try:
        app()
    except ValidationError as exc:
        for err in exc.errors():
            typer.secho(f"\nConfig error: {err['msg'].removeprefix('Value error, ')}", fg=typer.colors.RED, err=True)
        raise SystemExit(1) from None
    except RuntimeError as exc:
        typer.secho(f"\nError: {exc}", fg=typer.colors.RED, err=True)
        annotate_error(str(exc))
        raise SystemExit(1) from None
    except Exception as exc:  # noqa: BLE001
        annotate_error(f"{type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    main()
