"""Telegram bot: notifications from the autopilot + commands to check it.

Setup: create a bot with @BotFather, put TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env (locally) and in
the GitHub secrets. Then

    python engine/telegram_bot.py whoami          # prints your chat id after you sent /start to the bot
    python engine/telegram_bot.py test            # sends a test message

Used by the workflows:
    notify-plan / notify-video / notify-run       # push messages during the daily run
    poll                                          # answers /status /queue /stats /health /help (telegram.yml)

Only messages from TELEGRAM_CHAT_ID are answered. Nothing here may ever break the autopilot: the notify
commands swallow every error, and no message ever contains a secret.
"""
from __future__ import annotations

import html
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "engine"

import httpx
import typer

from engine.channels import list_channel_ids, load_channel, upcoming_slots
from engine.config import Settings, get_settings
from engine.history import History, HistoryEntry, load_history
from engine.reserve import available as reserve_available

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 3900  # Telegram's hard limit is 4096 characters per message

app = typer.Typer(add_completion=False, help="Telegram notifications and commands for the autopilot.")


class TelegramError(RuntimeError):
    """Never carries the request URL: it contains the bot token."""


# ---------------------------------------------------------------------------------------- API
class Telegram:
    def __init__(self, token: str, chat_id: str, post: Callable = httpx.post):
        self.token, self.chat_id, self._post = token, str(chat_id), post

    def call(self, method: str, **params) -> object:
        try:
            response = self._post(API.format(token=self.token, method=method), json=params, timeout=70)
            data = response.json()
        except Exception as exc:  # noqa: BLE001 - httpx errors can mention the URL (= the token)
            raise TelegramError(f"Telegram {method}: {type(exc).__name__}") from None
        if not data.get("ok"):
            raise TelegramError(f"Telegram {method}: {data.get('description', 'unknown error')}")
        return data["result"]

    def send(self, text: str, chat_id: str | None = None) -> None:
        for chunk in split_message(text):
            self.call(
                "sendMessage",
                chat_id=chat_id or self.chat_id,
                text=chunk,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    """Cut at line breaks so no message exceeds Telegram's limit."""
    chunks, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single endless line
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit and current:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks or [""]


def get_telegram(settings: Settings | None = None) -> Telegram | None:
    settings = settings or get_settings()
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return None
    return Telegram(settings.telegram_bot_token, settings.telegram_chat_id)


# ------------------------------------------------------------------------------- formatting
esc = html.escape


def num(value) -> str:
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "-"


def fmt_slot(iso: str, channel_tz: str, notify_tz: str) -> str:
    """'10.11 08:00 NY · 16:00 TR' for an ISO UTC time."""
    if not iso:
        return "-"
    when = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    a, b = when.astimezone(ZoneInfo(channel_tz)), when.astimezone(ZoneInfo(notify_tz))
    city = {"America/New_York": "NY", "Europe/Istanbul": "TR"}
    return f"{a:%d.%m %H:%M} {city.get(channel_tz, channel_tz)} · {b:%H:%M} {city.get(notify_tz, notify_tz)}"


def ago(then: datetime, now: datetime) -> str:
    minutes = int((now - then).total_seconds() // 60)
    if minutes < 90:
        return f"{max(minutes, 0)} dk önce"
    if minutes < 48 * 60:
        return f"{minutes // 60} sa önce"
    return f"{minutes // 1440} gün önce"


def channel_tz(channel_id: str) -> str:
    try:
        return load_channel(channel_id).timezone
    except Exception:  # noqa: BLE001
        return "America/New_York"


# --------------------------------------------------------------------------------- context
@dataclass
class Context:
    """Everything the commands need; every external call is injectable so tests need no network."""

    settings: Settings
    history: History
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    github_runs: Callable[[], list[dict]] | None = None  # recent runs of the daily workflow
    youtube: Callable[[str], object] | None = None  # channel id -> YouTube API client

    def yt(self, channel_id: str):
        if self.youtube is None:
            raise RuntimeError("YouTube access is not configured")
        return self.youtube(channel_id)


def default_github_runs() -> list[dict]:
    repo = os.environ.get("GITHUB_REPOSITORY", "saahinyunus/shorts-autopilot")
    headers = {"User-Agent": "shorts-autopilot-bot", "Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    r = httpx.get(f"https://api.github.com/repos/{repo}/actions/workflows/daily.yml/runs?per_page=5", headers=headers, timeout=30)
    r.raise_for_status()
    return r.json().get("workflow_runs", [])


def default_youtube(settings: Settings) -> Callable[[str], object]:
    def factory(channel_id: str):
        from googleapiclient.discovery import build

        from engine.uploader import get_credentials

        return build("youtube", "v3", credentials=get_credentials(settings, channel_id), cache_discovery=False)

    return factory


def explain_youtube_error(exc: Exception) -> str:
    text = str(exc)
    if "insufficient" in text.lower() and "scope" in text.lower() or "insufficientPermissions" in text:
        return "YouTube izni eksik: bu kanalda yeniden giriş yapılmalı (youtube.readonly izni)"
    if "No YouTube login" in text:
        return "bu kanal için YouTube girişi yok"
    return f"YouTube'a ulaşılamadı ({type(exc).__name__})"


def video_info(ctx: Context, channel_id: str, ids: list[str]) -> dict[str, dict]:
    """videos.list for up to 50 ids at a time -> {video id: item}."""
    found: dict[str, dict] = {}
    yt = ctx.yt(channel_id)
    for i in range(0, len(ids), 50):
        batch = ids[i : i + 50]
        for item in yt.videos().list(part="snippet,statistics,status", id=",".join(batch)).execute().get("items", []):
            found[item["id"]] = item
    return found


def check_scheduled(item: dict | None, publish_at: str) -> tuple[bool, str]:
    """Is this video really scheduled for `publish_at` (or already live)? -> (ok, short description)."""
    if not item:
        return False, "video YouTube'da bulunamadı (silinmiş olabilir)"
    status = item.get("status", {})
    privacy = status.get("privacyStatus", "?")
    if privacy == "public":
        return True, "yayında"
    if privacy == "private" and status.get("publishAt"):
        planned = datetime.fromisoformat(status["publishAt"].replace("Z", "+00:00"))
        wanted = datetime.fromisoformat(publish_at.replace("Z", "+00:00")) if publish_at else planned
        if abs((planned - wanted).total_seconds()) < 120:
            return True, "zamanlanmış"
        return False, f"zamanlama farklı: {status['publishAt']}"
    return False, f"zamanlanmamış, durum: {privacy} (kilitlenmiş olabilir)"


# ---------------------------------------------------------------------------------- commands
HELP = (
    "<b>Komutlar</b>\n"
    "/status: otopilotun genel durumu\n"
    "/queue: yayını bekleyen videolar (YouTube'dan doğrulanır)\n"
    "/stats: kanal ve video istatistikleri\n"
    "/health: sistem sağlık kontrolü\n"
    "/help: bu liste\n\n"
    "Cevaplar birkaç dakika gecikebilir (bot 5 dakikada bir kontrol eder)."
)


def cmd_status(ctx: Context) -> str:
    s = ctx.settings
    lines = ["<b>📊 Otopilot durumu</b>"]
    try:
        runs = (ctx.github_runs or default_github_runs)()
        if runs:
            r = runs[0]
            when = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
            icon = {"success": "✅", "failure": "❌", "cancelled": "⚪"}.get(r.get("conclusion") or "", "⏳")
            lines.append(f"{icon} Son çalıştırma: {r.get('conclusion') or r.get('status')} ({ago(when, ctx.now)}) <a href=\"{r['html_url']}\">detay</a>")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"⚠️ Çalıştırma geçmişi okunamadı ({type(exc).__name__})")
    for cid in list_channel_ids():
        entries = [e for e in ctx.history.entries if e.channel == cid]
        waiting = [e for e in entries if e.publish_at and _utc(e.publish_at) > ctx.now]
        nxt = min(waiting, key=lambda e: e.publish_at, default=None)
        tz = channel_tz(cid)
        line = f"\n<b>{esc(cid)}</b>: {len(entries)} video yüklendi, {len(waiting)} yayın bekliyor"
        if nxt:
            line += f"\n   ⏭ Sıradaki: {fmt_slot(nxt.publish_at, tz, s.notify_timezone)}"
        try:
            ch = load_channel(cid)
            free = [x for x in upcoming_slots(ch, ctx.now, 0.0, 30.0) if not ctx.history.has_plan(f"{cid}-{x:%Y%m%dT%H%MZ}")]
            if free:
                line += f"\n   🕓 Henüz üretilmemiş slot: {len(free)} (sıradaki otomatik çalışmada planlanır)"
        except Exception:  # noqa: BLE001
            pass
        lines.append(line)
    reserve = ", ".join(
        f"{cid} {len(reserve_available(cid, ctx.history, root=s.state_dir / 'reserve'))}" for cid in list_channel_ids()
    )
    lines.append(f"\n🛟 Yedek senaryo: {reserve}")
    return "\n".join(lines)


def _utc(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def cmd_queue(ctx: Context) -> str:
    upcoming = sorted(
        (e for e in ctx.history.entries if e.publish_at and _utc(e.publish_at) > ctx.now), key=lambda e: e.publish_at
    )
    if not upcoming:
        return "📭 Yayın bekleyen video yok."
    lines = [f"<b>🗓 Yayın bekleyen {len(upcoming)} video</b>"]
    for cid in dict.fromkeys(e.channel for e in upcoming):
        group = [e for e in upcoming if e.channel == cid]
        try:
            info = video_info(ctx, cid, [e.video_id for e in group])
            note = None
        except Exception as exc:  # noqa: BLE001
            info, note = {}, explain_youtube_error(exc)
        lines.append(f"\n<b>{esc(cid)}</b>" + (f" <i>({esc(note)})</i>" if note else ""))
        for e in group:
            when = fmt_slot(e.publish_at, channel_tz(cid), ctx.settings.notify_timezone)
            if note:
                mark = "•"
            else:
                ok, why = check_scheduled(info.get(e.video_id), e.publish_at)
                mark = "✅" if ok else "⚠️"
                if not ok:
                    when += f" ({why})"
            lines.append(f"{mark} {when}\n   <a href=\"{esc(e.url)}\">{esc(e.title or e.topic)}</a>")
    return "\n".join(lines)


def cmd_stats(ctx: Context) -> str:
    lines = ["<b>📈 İstatistikler</b>"]
    for cid in list_channel_ids():
        entries = [e for e in ctx.history.entries if e.channel == cid]
        try:
            yt = ctx.yt(cid)
            ch = yt.channels().list(part="snippet,statistics", mine=True).execute()["items"][0]
            st = ch["statistics"]
            lines.append(
                f"\n<b>{esc(ch['snippet']['title'])}</b> ({esc(cid)})\n"
                f"👥 {num(st.get('subscriberCount'))} abone · 👁 {num(st.get('viewCount'))} toplam izlenme · 🎞 {num(st.get('videoCount'))} video"
            )
            published = [e for e in entries if e.publish_at and _utc(e.publish_at) <= ctx.now]
            info = video_info(ctx, cid, [e.video_id for e in published[-8:]]) if published else {}
            for e in reversed(published[-5:]):
                v = info.get(e.video_id, {}).get("statistics", {})
                lines.append(
                    f"• {esc((e.title or e.topic)[:48])}: 👁 {num(v.get('viewCount'))} · 👍 {num(v.get('likeCount'))} · 💬 {num(v.get('commentCount'))}"
                )
            if not published:
                lines.append("(henüz yayına girmiş video yok)")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"\n<b>{esc(cid)}</b>: {esc(explain_youtube_error(exc))}")
    return "\n".join(lines)


def cmd_health(ctx: Context) -> str:
    s = ctx.settings
    problems: list[str] = []
    lines = ["<b>🩺 Sağlık kontrolü</b>"]
    try:
        runs = (ctx.github_runs or default_github_runs)()
        if runs:
            last = _utc(runs[0]["created_at"])
            hours = (ctx.now - last).total_seconds() / 3600
            ok = runs[0].get("conclusion") in (None, "success") and hours < 30
            lines.append(f"{'✅' if ok else '❌'} Günlük çalıştırma: son {ago(last, ctx.now)}, sonuç {runs[0].get('conclusion') or 'sürüyor'}")
            if not ok:
                problems.append("günlük çalıştırma")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"⚠️ GitHub okunamadı ({type(exc).__name__})")
    for cid in list_channel_ids():
        try:
            ctx.yt(cid)
            lines.append(f"✅ YouTube girişi ({cid}): geçerli")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"❌ YouTube girişi ({cid}): {esc(explain_youtube_error(exc))}")
            problems.append(f"{cid} girişi")
        n = len(reserve_available(cid, ctx.history, root=s.state_dir / "reserve"))
        lines.append(f"{'✅' if n >= 3 else '⚠️'} Yedek senaryo ({cid}): {n}")
    llms = [n for n, k in (("Gemini", s.gemini_api_key), ("Groq", s.groq_api_key), ("Anthropic", s.anthropic_api_key)) if k]
    lines.append(f"{'✅' if len(llms) >= 2 else '⚠️'} Yapay zekâ anahtarları: {', '.join(llms) or 'yok'}")
    lines.append("\n" + ("🟢 Her şey yolunda" if not problems else "🔴 Dikkat: " + ", ".join(problems)))
    return "\n".join(lines)


COMMANDS: dict[str, Callable[[Context], str]] = {
    "status": cmd_status,
    "queue": cmd_queue,
    "stats": cmd_stats,
    "health": cmd_health,
}


def reply_for(text: str, ctx: Context) -> str | None:
    """The answer to one incoming message, or None for messages that are not commands."""
    word = (text or "").strip().split(" ")[0].lower()
    if not word.startswith("/"):
        return None
    name = word[1:].split("@")[0]
    if name in ("start", "help"):
        return HELP
    handler = COMMANDS.get(name)
    if handler is None:
        return "Bilinmeyen komut. /help yazın."
    try:
        return handler(ctx)
    except Exception as exc:  # noqa: BLE001 - a broken command must not stop the others
        return f"⚠️ /{name} çalışmadı: {esc(type(exc).__name__)}"


def handle_updates(tg: Telegram, updates: list[dict], ctx: Context) -> int:
    """Answer every update from the allowed chat. Returns the next offset (confirms them all)."""
    offset = 0
    for update in updates:
        offset = max(offset, update["update_id"] + 1)
        message = update.get("message") or {}
        chat = str((message.get("chat") or {}).get("id", ""))
        if chat != tg.chat_id:
            continue  # strangers get no answer at all
        answer = reply_for(message.get("text", ""), ctx)
        if answer:
            tg.send(answer, chat_id=chat)
    return offset


def build_context(settings: Settings) -> Context:
    return Context(
        settings=settings,
        history=load_history(settings.state_dir / "history.json"),
        github_runs=default_github_runs,
        youtube=default_youtube(settings),
    )


# ------------------------------------------------------------------------------ notifications
def _env_item() -> dict:
    return {k: os.environ.get(k.upper(), "") for k in ("channel", "topic", "publish_at", "plan_id")}


def message_for_video(
    item: dict, result: dict | None, *, dry_run: bool, run_url: str, notify_tz: str, verdict: tuple[bool, str] | None
) -> str:
    cid = item.get("channel") or (result or {}).get("channel", "?")
    when = fmt_slot(item.get("publish_at") or (result or {}).get("publish_at", ""), channel_tz(cid), notify_tz)
    if result and result.get("uploaded"):
        lines = [
            "✅ <b>Video yüklendi ve planlandı</b>",
            f"📺 {esc(cid)} · 🕒 {when}",
            f"🎬 {esc(result.get('title', ''))}",
            f"🔗 {esc(result.get('url', ''))}",
        ]
        if verdict is not None:
            lines.append(("✔️ YouTube doğruladı: " if verdict[0] else "⚠️ ") + esc(verdict[1]))
        if result.get("reserve_used"):
            lines.append("ℹ️ Yapay zekâ çalışmadığı için yedek senaryo kullanıldı")
        return "\n".join(lines)
    if result and dry_run:
        return f"🧪 <b>Deneme videosu hazır</b> (yüklenmedi)\n📺 {esc(cid)} · 🕒 {when}\n🎬 {esc(result.get('title', ''))}\n🔗 {esc(run_url)}"
    return (
        f"❌ <b>Video üretilemedi</b>\n📺 {esc(cid)} · 🕒 {when}\n📝 {esc(item.get('topic', ''))}\n"
        f"🔎 Ayrıntı: {esc(run_url)}"
    )


def _find_result(output_dir: Path) -> dict | None:
    files = sorted(output_dir.glob("*/result.json"), key=lambda p: p.stat().st_mtime)
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


@app.command("notify-video")
def notify_video(output_dir: Path = typer.Option(Path("output"), "--output-dir")) -> None:
    """After each produce job: one message about the video (never fails the job)."""
    try:
        settings = get_settings()
        tg = get_telegram(settings)
        if tg is None:
            return
        item, result = _env_item(), _find_result(output_dir)
        verdict = None
        if result and result.get("uploaded"):
            try:
                ctx = build_context(settings)
                verdict = check_scheduled(video_info(ctx, result["channel"], [result["video_id"]]).get(result["video_id"]), result.get("publish_at", ""))
            except Exception as exc:  # noqa: BLE001 - older tokens cannot read status
                verdict = (False, f"doğrulanamadı: {explain_youtube_error(exc)}") if "izni eksik" not in explain_youtube_error(exc) else None
        tg.send(
            message_for_video(
                item, result, dry_run=os.environ.get("DRY_RUN") == "true", run_url=os.environ.get("RUN_URL", ""),
                notify_tz=settings.notify_timezone, verdict=verdict,
            )
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[telegram] notification skipped: {type(exc).__name__}")


@app.command("notify-plan")
def notify_plan() -> None:
    """After planning: what is about to be produced."""
    try:
        settings = get_settings()
        tg = get_telegram(settings)
        if tg is None:
            return
        items = json.loads(os.environ.get("PLAN_MATRIX") or "{}").get("include", [])
        errors = os.environ.get("PLAN_ERRORS", "")
        dry = os.environ.get("DRY_RUN") == "true"
        if not items and not errors:
            return  # nothing to say on a day without work
        lines = [f"📋 <b>{'[DENEME] ' if dry else ''}Plan: {len(items)} video üretilecek</b>"]
        for it in items:
            lines.append(f"• {esc(it['channel'])} · {fmt_slot(it['publish_at'], channel_tz(it['channel']), settings.notify_timezone)}\n   {esc(it['topic'])}")
        if errors:
            lines.append(f"\n⚠️ Planlama sorunu: {esc(errors[:500])}")
        tg.send("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        print(f"[telegram] notification skipped: {type(exc).__name__}")


@app.command("notify-run")
def notify_run() -> None:
    """End of the daily run: one summary line."""
    try:
        settings = get_settings()
        tg = get_telegram(settings)
        if tg is None:
            return
        env = os.environ
        count = env.get("PLAN_COUNT", "0") or "0"
        produce, record = env.get("PRODUCE_RESULT", ""), env.get("RECORD_RESULT", "")
        if count == "0" and not env.get("PLAN_ERRORS"):
            return
        bad = [n for n, r in (("planlama", env.get("PLAN_RESULT", "")), ("üretim", produce), ("kayıt", record)) if r == "failure"]
        head = "🟢 Günlük çalıştırma tamamlandı" if not bad and not env.get("PLAN_ERRORS") else "🔴 Günlük çalıştırmada sorun var"
        text = f"{head}\nPlanlanan video: {count}" + (f"\nSorunlu adım: {', '.join(bad)}" if bad else "")
        tg.send(text + f"\n🔎 {esc(env.get('RUN_URL', ''))}")
    except Exception as exc:  # noqa: BLE001
        print(f"[telegram] notification skipped: {type(exc).__name__}")


# --------------------------------------------------------------------------- poll / helpers
@app.command()
def poll(minutes: float = typer.Option(0.0, "--minutes", help="0 = answer what is waiting and exit; >0 = keep listening this long.")) -> None:
    """Answer waiting commands (run by telegram.yml every 5 minutes)."""
    import time

    settings = get_settings()
    tg = get_telegram(settings)
    if tg is None:
        typer.echo("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; nothing to do.")
        return
    ctx = build_context(settings)
    deadline = time.time() + minutes * 60
    offset = None
    handled = 0
    while True:
        params = {"timeout": 50 if minutes else 0, "allowed_updates": ["message"]}
        if offset:
            params["offset"] = offset
        updates = tg.call("getUpdates", **params)
        if updates:
            ctx.now = datetime.now(timezone.utc)
            offset = handle_updates(tg, updates, ctx)
            handled += len(updates)
        if time.time() >= deadline or not minutes:
            break
    if offset:  # confirm, so the same messages are never answered twice
        tg.call("getUpdates", offset=offset, timeout=0)
    typer.echo(f"handled {handled} update(s)")


@app.command()
def test() -> None:
    """Send a test message."""
    tg = get_telegram()
    if tg is None:
        raise typer.BadParameter("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first.")
    tg.send("✅ Telegram bağlantısı çalışıyor. /help yazarak komutları görebilirsiniz.")
    typer.echo("sent")


@app.command()
def whoami() -> None:
    """Prints the chat ids that wrote to the bot: send /start to your bot first, then run this."""
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise typer.BadParameter("Set TELEGRAM_BOT_TOKEN in .env first.")
    updates = Telegram(settings.telegram_bot_token, "").call("getUpdates", timeout=0)
    seen = {}
    for u in updates:
        chat = (u.get("message") or {}).get("chat") or {}
        if chat.get("id"):
            seen[chat["id"]] = chat.get("first_name") or chat.get("title") or "?"
    if not seen:
        typer.echo("No messages yet. Open your bot in Telegram, press Start (or send /start), then run this again.")
    for cid, name in seen.items():
        typer.echo(f"TELEGRAM_CHAT_ID={cid}   ({name})")


if __name__ == "__main__":
    app()
