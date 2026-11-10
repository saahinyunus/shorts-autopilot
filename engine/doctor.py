"""Checks every credential the autopilot depends on with one tiny real request each, and reports the result.

    python engine/doctor.py            # prints a report (and sends it to Telegram if configured)

Run it in GitHub Actions (workflow "Check keys") to see what the CI environment really has: a secret that is
missing, mistyped or out of quota shows up here instead of silently pushing the pipeline to its fallbacks.
Only status codes and short error texts are reported, never a key.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "engine"

import httpx
import typer

from engine.channels import list_channel_ids
from engine.config import Settings, get_settings

app = typer.Typer(add_completion=False, help="Check the API keys and logins the autopilot needs.")

Check = tuple[str, bool, str]  # (name, ok, detail)


def _short(response: httpx.Response) -> str:
    try:
        err = response.json().get("error", {})
        text = err.get("message") if isinstance(err, dict) else str(err)
    except Exception:  # noqa: BLE001
        text = response.text
    text = " ".join(str(text).split())
    for marker in ("Please retry", "For more information"):  # cut the long help text
        text = text.split(marker)[0]
    return text[:180].strip()


def check_gemini(s: Settings, post: Callable = httpx.post) -> list[Check]:
    if not s.gemini_api_key:
        return [("Gemini", False, "secret is EMPTY (GEMINI_API_KEY not set)")]
    out: list[Check] = []
    for model in dict.fromkeys([s.gemini_model, s.gemini_fallback_model]):
        r = post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": s.gemini_api_key},
            json={"contents": [{"parts": [{"text": "Say OK"}]}], "generationConfig": {"maxOutputTokens": 8}},
            timeout=60,
        )
        quota = "daily quota used up" if r.status_code == 429 and "per day" in r.text.lower() else ""
        out.append((f"Gemini {model}", r.status_code == 200, "ok" if r.status_code == 200 else f"HTTP {r.status_code} {quota or _short(r)}"))
    return out


def check_groq(s: Settings, post: Callable = httpx.post) -> list[Check]:
    if not s.groq_api_key:
        return [("Groq", False, "secret is EMPTY (GROQ_API_KEY not set)")]
    out: list[Check] = []
    for model in dict.fromkeys([s.groq_model, s.groq_fallback_model]):
        r = post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {s.groq_api_key}"},
            json={"model": model, "messages": [{"role": "user", "content": "Say OK"}], "max_tokens": 8},
            timeout=60,
        )
        out.append((f"Groq {model}", r.status_code == 200, "ok" if r.status_code == 200 else f"HTTP {r.status_code} {_short(r)}"))
    return out


def check_anthropic(s: Settings) -> list[Check]:
    return [("Anthropic", True, "key present (not tested: paid)")] if s.anthropic_api_key else []


def check_youtube(s: Settings) -> list[Check]:
    out: list[Check] = []
    for cid in list_channel_ids():
        try:
            from engine.uploader import get_credentials

            creds = get_credentials(s, cid)
            scopes = sorted(x.rsplit("/", 1)[-1] for x in (creds.scopes or []))
            out.append((f"YouTube login ({cid})", True, "refreshed ok, scopes: " + ", ".join(scopes)))
        except Exception as exc:  # noqa: BLE001
            out.append((f"YouTube login ({cid})", False, f"{type(exc).__name__}: {str(exc)[:140]}"))
    return out


def check_telegram(s: Settings, post: Callable = httpx.post) -> list[Check]:
    if not (s.telegram_bot_token and s.telegram_chat_id):
        return [("Telegram", False, "secrets TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not both set")]
    try:
        r = post(f"https://api.telegram.org/bot{s.telegram_bot_token}/getMe", json={}, timeout=30)
        ok = r.status_code == 200 and r.json().get("ok")
        return [("Telegram bot", bool(ok), "ok" if ok else f"HTTP {r.status_code}")]
    except Exception as exc:  # noqa: BLE001 - never include the URL (it holds the token)
        return [("Telegram bot", False, type(exc).__name__)]


def run_checks(s: Settings) -> list[Check]:
    results: list[Check] = []
    for fn in (check_gemini, check_groq, check_anthropic, check_youtube, check_telegram):
        try:
            results.extend(fn(s))
        except Exception as exc:  # noqa: BLE001 - one broken check must not hide the others
            results.append((fn.__name__.removeprefix("check_"), False, type(exc).__name__))
    return results


def format_report(results: list[Check]) -> str:
    lines = ["🔧 <b>Anahtar kontrolü</b> (GitHub Actions ortamı)"]
    for name, ok, detail in results:
        lines.append(f"{'✅' if ok else '❌'} {name}: {detail}")
    llm = [ok for name, ok, _ in results if name.startswith(("Gemini", "Groq", "Anthropic"))]
    lines.append("")
    lines.append(
        "🟢 En az bir yapay zekâ çalışıyor" if any(llm) else "🔴 Hiçbir yapay zekâ çalışmıyor: videolar yedek senaryolardan çıkar"
    )
    return "\n".join(lines)


@app.command()
def main(send: bool = typer.Option(True, "--send/--no-send", help="Also send the report to Telegram.")) -> None:
    settings = get_settings()
    results = run_checks(settings)
    text = format_report(results)
    typer.echo(text.replace("<b>", "").replace("</b>", ""))
    for name, ok, detail in results:  # annotations are readable on the run page without opening logs
        print(f"::{'notice' if ok else 'error'} title=Key check::{name}: {detail}")
    if send:
        from engine.telegram_bot import get_telegram

        tg = get_telegram(settings)
        if tg:
            try:
                tg.send(text)
            except Exception as exc:  # noqa: BLE001
                typer.echo(f"(Telegram not reachable: {type(exc).__name__})")


if __name__ == "__main__":
    app()
