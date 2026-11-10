"""Publish worker/index.js to Cloudflare (Worker code, KV binding and cron triggers) through the API.

    python worker/deploy.py              # deploy / update
    python worker/deploy.py --check      # only verify the token and show what exists

Needs CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in .env (token template "Edit Cloudflare Workers").
Existing Worker secrets (bot token, tokens JSON, ...) are kept; nothing secret is ever printed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import typer

from engine.config import get_settings

API = "https://api.cloudflare.com/client/v4"
SCRIPT_NAME = "shorts-bot"
KV_TITLE = "shorts-bot-kv"
CRONS = ["*/10 * * * *", "0 21 * * *"]  # first comments every 10 min; daily summary at 00:00 Istanbul (21:00 UTC)
SOURCE = Path(__file__).resolve().parent / "index.js"

app = typer.Typer(add_completion=False)


class CloudflareError(RuntimeError):
    pass


class Cloudflare:
    def __init__(self, token: str, account: str, client: httpx.Client | None = None):
        self.account = account
        self.http = client or httpx.Client(timeout=60, headers={"Authorization": f"Bearer {token}"})

    def call(self, method: str, path: str, **kwargs) -> dict:
        response = self.http.request(method, f"{API}{path}", **kwargs)
        try:
            data = response.json()
        except ValueError:
            raise CloudflareError(f"{method} {path}: HTTP {response.status_code} (not JSON)") from None
        if not data.get("success"):
            errors = "; ".join(f"{e.get('code')}: {e.get('message')}" for e in data.get("errors", [])) or f"HTTP {response.status_code}"
            raise CloudflareError(f"{method} {path}: {errors}")
        return data

    def verify(self) -> str:
        for path in (f"/accounts/{self.account}/tokens/verify", "/user/tokens/verify"):
            try:
                return self.call("GET", path)["result"]["status"]
            except CloudflareError:
                continue
        raise CloudflareError("The API token was rejected (wrong token, or missing permissions).")

    def kv_namespace(self, title: str) -> str:
        """Id of the KV namespace with this title; created if it does not exist."""
        listing = self.call("GET", f"/accounts/{self.account}/storage/kv/namespaces", params={"per_page": 100})["result"]
        for ns in listing:
            if ns["title"] == title:
                return ns["id"]
        return self.call("POST", f"/accounts/{self.account}/storage/kv/namespaces", json={"title": title})["result"]["id"]

    def upload(self, source: str, kv_id: str) -> None:
        metadata = {
            "main_module": "index.js",
            "compatibility_date": "2025-01-01",
            "bindings": [{"type": "kv_namespace", "name": "KV", "namespace_id": kv_id}],
            # keep the secrets/variables that were added in the dashboard
            "keep_bindings": ["secret_text", "plain_text", "json"],
        }
        self.call(
            "PUT",
            f"/accounts/{self.account}/workers/scripts/{SCRIPT_NAME}",
            files={
                "metadata": (None, json.dumps(metadata), "application/json"),
                "index.js": ("index.js", source.encode("utf-8"), "application/javascript+module"),
            },
        )

    def schedules(self, crons: list[str]) -> list[str]:
        result = self.call("PUT", f"/accounts/{self.account}/workers/scripts/{SCRIPT_NAME}/schedules", json=[{"cron": c} for c in crons])
        return [s["cron"] for s in result["result"]["schedules"]]

    def enable_subdomain(self) -> None:
        self.call("POST", f"/accounts/{self.account}/workers/scripts/{SCRIPT_NAME}/subdomain", json={"enabled": True, "previews_enabled": False})

    def bindings(self) -> list[dict]:
        return self.call("GET", f"/accounts/{self.account}/workers/scripts/{SCRIPT_NAME}/settings")["result"].get("bindings", [])


@app.command()
def deploy(check: bool = typer.Option(False, "--check", help="Only verify the token and list what exists.")) -> None:
    settings = get_settings()
    if not (settings.cloudflare_api_token and settings.cloudflare_account_id):
        raise typer.BadParameter("Set CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID in .env first.")
    cf = Cloudflare(settings.cloudflare_api_token, settings.cloudflare_account_id)
    try:
        typer.echo(f"token: {cf.verify()}")
        kv_id = cf.kv_namespace(KV_TITLE)
        typer.echo(f"KV namespace '{KV_TITLE}': ready")
        if check:
            names = sorted({f"{b['name']} ({b['type']})" for b in cf.bindings()})
            typer.echo("worker bindings: " + (", ".join(names) or "none"))
            return
        cf.upload(SOURCE.read_text(encoding="utf-8"), kv_id)
        typer.echo(f"worker '{SCRIPT_NAME}': uploaded ({SOURCE.stat().st_size} bytes)")
        typer.echo("cron triggers: " + ", ".join(cf.schedules(CRONS)))
        try:
            cf.enable_subdomain()
        except CloudflareError as exc:  # already enabled accounts may answer with a harmless error
            typer.echo(f"(workers.dev subdomain: {exc})")
        names = sorted({f"{b['name']} ({b['type']})" for b in cf.bindings()})
        typer.echo("worker bindings: " + ", ".join(names))
    except CloudflareError as exc:
        typer.secho(f"Cloudflare error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None


if __name__ == "__main__":
    app()
