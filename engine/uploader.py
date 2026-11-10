"""YouTube Data API v3 uploader: several channels, scheduled publishing, SEO metadata.

Credentials per channel come from (first match wins):
  1. env YOUTUBE_TOKENS_JSON = {"<channel id>": <authorized-user token json>, ...}   (GitHub Actions secret)
  2. file token_<channel id>.json in the project root                              (made by youtube_auth.py)
  3. for the single-channel legacy mode (no channel id): YOUTUBE_TOKEN_FILE
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from .channels import ChannelConfig
from .config import ROOT_DIR, Settings, get_settings
from .generator import ScriptSpec

# Requested at login. "upload" publishes videos; "force-ssl" additionally lets the bot read statistics, verify
# that uploads are scheduled and post the first comment. Tokens made before still work for uploading.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
MAX_TAG_CHARS = 450  # YouTube allows 500 in total; stay safely below
MAX_HASHTAGS = 5  # the first three are shown above the title; many more look spammy


class UploadResult(BaseModel):
    video_id: str
    url: str
    privacy: str
    publish_at: str = ""
    scheduled: bool = False
    comment_text: str = ""
    comment_id: str = ""


# --------------------------------------------------------------------------- metadata
def _hashtags(text: str) -> list[str]:
    return re.findall(r"#\w+", text)


def _cut(text: str, limit: int) -> str:
    """Shorten at a word boundary."""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:-") if " " in text[:limit] else text[:limit]


def build_metadata(script: ScriptSpec, channel: ChannelConfig | None = None) -> tuple[str, str, list[str]]:
    """Title (<=100), description (<=5000, with hashtags + footer) and tags (<=450 chars)."""
    title = _cut(script.title.strip(), 100)

    body = script.description.strip() or script.title
    existing = _hashtags(body)
    # Hashtags are rebuilt as ONE final line (max 5): #Shorts, the video's own 2 best, the channel's
    # (consistent branding / discovery), then any remaining ones from the LLM.
    own = [h for h in existing if h.lower() != "#shorts"]
    wanted = ["#Shorts"] + own[:2] + (channel.hashtags if channel else []) + own[2:]
    unique: dict[str, str] = {}
    for h in wanted:
        unique.setdefault(h.lower(), h)
    tags_line = list(unique.values())[:MAX_HASHTAGS]
    body = re.sub(r"[ \t]*#\w+", "", body)  # remove them from the text itself
    body = re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", body)).strip()
    parts = [body, " ".join(tags_line)]
    if channel and channel.description_footer:
        parts.insert(1, channel.description_footer)
    description = "\n\n".join(p for p in parts if p)[:4900]

    raw_tags = [t.lstrip("#").strip() for t in script.tags] + (channel.tags if channel else [])
    tags, total = [], 0
    seen: set[str] = set()
    for tag in raw_tags:
        key = tag.lower()
        if not tag or key in seen or total + len(tag) + 1 > MAX_TAG_CHARS:
            continue
        seen.add(key)
        tags.append(tag)
        total += len(tag) + 1
    return title, description, tags


def build_body(
    script: ScriptSpec,
    settings: Settings,
    channel: ChannelConfig | None = None,
    publish_at: datetime | None = None,
) -> dict:
    title, description, tags = build_metadata(script, channel)
    language = channel.language if channel else "en"
    status: dict = {"privacyStatus": settings.youtube_privacy, "selfDeclaredMadeForKids": False}
    if settings.youtube_synthetic_disclosure:
        status["containsSyntheticMedia"] = True
    if publish_at is not None:
        # YouTube publishes the video itself at this time; it must be uploaded as private first.
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": channel.category_id if channel else settings.youtube_category_id,
            "defaultLanguage": language,
            "defaultAudioLanguage": language,
        },
        "status": status,
    }


# ---------------------------------------------------------------------------- first comment
MAX_COMMENT_CHARS = 200
DEFAULT_QUESTION = "What do you think? Tell us in the comments."


def comment_text_for(script: ScriptSpec) -> str:
    """The question to post as the first comment: the script's own, else the hook if it is a question,
    else a generic one. Links and hashtags are stripped (they get comments held for review)."""
    text = script.comment_question.strip()
    if not text and script.hook.strip().endswith("?"):
        text = script.hook.strip()
    text = re.sub(r"https?://\S+|www\.\S+", "", text)
    text = re.sub(r"#\w+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 8:
        return DEFAULT_QUESTION
    return _cut(text, MAX_COMMENT_CHARS)


def find_comment(youtube, video_id: str, text: str) -> str:
    """Id of the channel's own comment with exactly this text on the video, or ''."""
    response = youtube.commentThreads().list(part="snippet", videoId=video_id, maxResults=50, order="time").execute()
    for item in response.get("items", []):
        top = item["snippet"]["topLevelComment"]
        if top["snippet"].get("textOriginal") == text:
            return top["id"]
    return ""


def post_comment(youtube, video_id: str, text: str) -> str:
    """Post a top-level comment as the channel owner; returns the comment id. Raises on failure."""
    body = {"snippet": {"videoId": video_id, "topLevelComment": {"snippet": {"textOriginal": text}}}}
    response = youtube.commentThreads().insert(part="snippet", body=body).execute()
    return response["snippet"]["topLevelComment"]["id"]


# ------------------------------------------------------------------------ credentials
def token_info(settings: Settings, channel_id: str | None) -> dict | None:
    """Authorized-user token dict for a channel, or None."""
    if channel_id and settings.youtube_tokens_json.strip():
        try:
            tokens = json.loads(settings.youtube_tokens_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"YOUTUBE_TOKENS_JSON is not valid JSON: {exc}") from exc
        if channel_id in tokens:
            info = tokens[channel_id]
            return json.loads(info) if isinstance(info, str) else info
    candidates = [ROOT_DIR / f"token_{channel_id}.json"] if channel_id else [settings.youtube_token_file]
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def get_credentials(settings: Settings, channel_id: str | None = None):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    info = token_info(settings, channel_id)
    if info is None:
        who = f"channel '{channel_id}'" if channel_id else "the YouTube account"
        raise RuntimeError(
            f"No YouTube login found for {who}. Run once on your computer: "
            f"python engine/youtube_auth.py login --channel {channel_id or '<id>'}"
        )
    creds = Credentials.from_authorized_user_info(info)  # refresh with exactly the scopes the login granted
    if not creds.valid:
        if creds.refresh_token:
            creds.refresh(Request())
        else:
            raise RuntimeError(f"The saved YouTube login for '{channel_id}' has no refresh token; log in again.")
    return creds


# ----------------------------------------------------------------------------- upload
def upload_video(
    video_path: Path,
    script: ScriptSpec,
    settings: Settings | None = None,
    channel: ChannelConfig | None = None,
    publish_at: datetime | None = None,
) -> UploadResult:
    """Upload `video_path`; with `publish_at` it is scheduled and goes public by itself."""
    settings = settings or get_settings()
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    youtube = build("youtube", "v3", credentials=get_credentials(settings, channel.id if channel else None), cache_discovery=False)
    body = build_body(script, settings, channel, publish_at)
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    try:
        response = None
        while response is None:
            progress, response = request.next_chunk()
            if progress:
                print(f"[upload] {int(progress.progress() * 100)}%")
    except HttpError as exc:
        raise RuntimeError(f"YouTube upload failed: {exc}") from exc

    status = response.get("status", {})
    result = UploadResult(
        video_id=response["id"],
        url=f"https://www.youtube.com/shorts/{response['id']}",
        privacy=status.get("privacyStatus", body["status"]["privacyStatus"]),
        publish_at=status.get("publishAt", ""),
        scheduled=bool(status.get("publishAt")),
    )
    if publish_at is not None and not result.scheduled:
        print("[upload] WARNING: YouTube did not keep the schedule (publishAt missing). The video may have been "
              "locked as private; check YouTube Studio and the API audit status of your Google Cloud project.")
    # YouTube refuses comments on a video that is not public yet, so the comment is posted at publish time
    # (by the Telegram worker's timer; daily.py comments is the safety net). Only the text is stored now.
    result.comment_text = comment_text_for(script)
    label = f"scheduled for {result.publish_at}" if result.scheduled else result.privacy
    print(f"[upload] done ({label}): {result.url}")
    return result
