"""One-time YouTube login per channel, done on YOUR computer (opens a browser).

    python engine/youtube_auth.py login --channel psychology
    python engine/youtube_auth.py login --channel money
    python engine/youtube_auth.py export          # prints the value for the GitHub secret YOUTUBE_TOKENS_JSON

Sign in with the Google account that owns the channel and, if asked, pick the right channel
(brand account). Tokens are stored in token_<channel>.json (git-ignored).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "engine"

import typer

from engine.channels import list_channel_ids, load_channel
from engine.config import ROOT_DIR, get_settings
from engine.uploader import SCOPES

app = typer.Typer(add_completion=False, help="Log in to YouTube for each channel and export the tokens.")


@app.command()
def login(
    channel: str = typer.Option(..., "--channel", "-c", help="Channel id (file name in channels/)"),
    client_secret: Path = typer.Option(None, "--client-secret", help="OAuth 'Desktop app' client JSON (default: client_secret.json)"),
) -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    load_channel(channel)  # validates the id
    secrets = client_secret or get_settings().youtube_client_secrets
    if not Path(secrets).exists():
        raise typer.BadParameter(
            f"{secrets} not found. Create an OAuth client of type 'Desktop app' in Google Cloud Console "
            "(YouTube Data API v3 enabled) and save its JSON there."
        )
    typer.echo(f"A browser opens. Sign in with the Google account that owns the '{channel}' channel.")
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")  # prompt=consent => always returns a refresh token
    if not creds.refresh_token:
        raise typer.Exit("Google returned no refresh token. Remove the app at myaccount.google.com/permissions and retry.")
    out = ROOT_DIR / f"token_{channel}.json"
    out.write_text(creds.to_json(), encoding="utf-8")
    typer.secho(f"Saved {out.name}. Now run: python engine/youtube_auth.py export", fg=typer.colors.GREEN)


@app.command()
def export() -> None:
    """Print all saved tokens as one JSON object: paste it into the GitHub secret YOUTUBE_TOKENS_JSON."""
    tokens = {}
    for channel_id in list_channel_ids():
        path = ROOT_DIR / f"token_{channel_id}.json"
        if path.exists():
            tokens[channel_id] = json.loads(path.read_text(encoding="utf-8"))
    missing = [c for c in list_channel_ids() if c not in tokens]
    if missing:
        typer.secho(f"Not logged in yet: {', '.join(missing)}", fg=typer.colors.YELLOW, err=True)
    if not tokens:
        raise typer.Exit("No tokens found. Run the login command first.")
    typer.echo(json.dumps(tokens, separators=(",", ":")))


if __name__ == "__main__":
    app()
