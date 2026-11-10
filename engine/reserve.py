"""Reserve of ready-made scripts (state/reserve/<channel>/*.json).

Every slot must get a video, even when every LLM is down or out of quota. So the repository keeps a few
finished, validated scripts per channel. The daily plan hands each slot one of them as a backup; the
pipeline only uses it when generating a fresh script fails. The planner also falls back to it when it
cannot invent topics. Scripts are topped up automatically while the LLMs work.

    python engine/reserve.py status
    python engine/reserve.py fill [--channel money] [--target 8] [--max-new 3]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "engine"

import typer
from pydantic import BaseModel

from engine.channels import list_channel_ids, load_channel
from engine.config import ROOT_DIR, get_settings
from engine.generator import ScriptSpec, generate_script, get_llm_provider, slugify
from engine.history import History, load_history
from engine.planner import is_duplicate, plan_topics

RESERVE_DIR = ROOT_DIR / "state" / "reserve"

app = typer.Typer(add_completion=False, help="Manage the reserve of ready-made scripts.")


class ReserveItem(BaseModel):
    topic: str
    script: ScriptSpec


def channel_dir(channel_id: str, root: Path = RESERVE_DIR) -> Path:
    return root / channel_id


def load_item(path: Path) -> ReserveItem:
    return ReserveItem.model_validate_json(path.read_text(encoding="utf-8"))


def save_item(channel_id: str, item: ReserveItem, root: Path = RESERVE_DIR) -> Path:
    folder = channel_dir(channel_id, root)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{slugify(item.topic)}.json"
    path.write_text(item.model_dump_json(indent=2), encoding="utf-8")
    return path


def _topics_of(history: History) -> list[str]:
    return [t for e in history.entries for t in (e.topic, e.title) if t]


def available(channel_id: str, history: History, avoid: list[str] | None = None, root: Path = RESERVE_DIR) -> list[tuple[Path, str]]:
    """Reserve files of this channel whose topic is still new (not in the history, not in `avoid`)."""
    blocked = _topics_of(history) + list(avoid or [])
    found: list[tuple[Path, str]] = []
    folder = channel_dir(channel_id, root)
    for path in sorted(folder.glob("*.json")) if folder.exists() else []:
        try:
            item = load_item(path)
        except Exception:  # noqa: BLE001 - a broken file must never break a run
            continue
        if any(is_duplicate(item.topic, other) for other in blocked):
            continue
        found.append((path, item.topic))
    return found


def prune(history: History, root: Path = RESERVE_DIR) -> list[Path]:
    """Delete reserve files whose topic was published in the meantime."""
    removed: list[Path] = []
    for folder in sorted(root.glob("*")) if root.exists() else []:
        if not folder.is_dir():
            continue
        keep = {p for p, _ in available(folder.name, history, root=root)}
        for path in sorted(folder.glob("*.json")):
            if path not in keep:
                path.unlink()
                removed.append(path)
    return removed


def relpath(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        return str(path)


@app.command()
def status() -> None:
    settings = get_settings()
    history = load_history(settings.state_dir / "history.json")
    for cid in list_channel_ids():
        typer.echo(f"{cid}: {len(available(cid, history, root=settings.state_dir / 'reserve'))} reserve script(s)")


@app.command()
def fill(
    channel: str = typer.Option("", "--channel", help="Channel id (default: all)."),
    target: int = typer.Option(8, "--target", help="Keep this many reserve scripts per channel."),
    max_new: int = typer.Option(3, "--max-new", help="Create at most this many per channel per call (protects the free quota)."),
) -> None:
    """Top up the reserve. Failures are reported but never fatal: this is housekeeping."""
    settings = get_settings()
    history = load_history(settings.state_dir / "history.json")
    ids = [channel] if channel else list_channel_ids()
    provider = None
    for cid in ids:
        ch = load_channel(cid)
        root = settings.state_dir / "reserve"
        have = available(cid, history, root=root)
        need = min(max(0, target - len(have)), max_new)
        if need == 0:
            typer.echo(f"{cid}: {len(have)} in reserve, nothing to do")
            continue
        try:
            provider = provider or get_llm_provider(settings)
            others = [t for e in history.entries if e.channel != cid for t in (e.topic, e.title) if t]
            own = [t for e in history.entries if e.channel == cid for t in (e.topic, e.title) if t]
            elsewhere = [t for o in list_channel_ids() if o != cid for _, t in available(o, history, root=root)]
            previous = others + elsewhere + ch.already_covered + own + [t for _, t in have]
            topics = plan_topics(ch, need, previous, provider)
        except Exception as exc:  # noqa: BLE001
            typer.secho(f"{cid}: could not plan reserve topics: {exc}", fg=typer.colors.YELLOW)
            continue
        for topic in topics:
            try:
                script = generate_script(topic, settings, provider=provider, brief=ch.brief())
            except Exception as exc:  # noqa: BLE001
                typer.secho(f"{cid}: script for '{topic}' failed: {exc}", fg=typer.colors.YELLOW)
                continue
            path = save_item(cid, ReserveItem(topic=topic, script=script), root)
            typer.echo(f"{cid}: reserve += {relpath(path)}")


if __name__ == "__main__":
    app()
