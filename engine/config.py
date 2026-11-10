"""Central configuration (Pydantic Settings). Values come from `.env` / environment."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Script LLM: free Gemini by default. Only the SELECTED provider's key is needed, and
    #     only when a script is actually generated (--script-file / --demo need none). ---
    llm_provider: Literal["gemini", "anthropic", "groq"] = "gemini"

    # Gemini (free tier). gemini-2.0-flash / 1.5-flash were retired by Google; 3.x Flash is current.
    llm_failover: bool = True  # fall back to every other LLM whose API key is set
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    gemini_fallback_model: str = "gemini-3.8-flash"  # used if the primary returns 404

    # Groq (free tier, OpenAI-compatible)
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_fallback_model: str = "qwen/qwen3.8-27b"

    # Anthropic (paid; only used when LLM_PROVIDER=anthropic)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-3-7-sonnet-latest"
    anthropic_fallback_model: str = "claude-3-5-sonnet-latest"  # used if the primary returns 404

    # --- Provider selection: free local engines by default, paid APIs by changing one line ---
    tts_provider: Literal["edge_tts", "kokoro", "elevenlabs"] = "edge_tts"
    whisper_provider: Literal["faster_whisper", "openai"] = "faster_whisper"

    # --- Edge TTS (free, Microsoft neural voices, no API key) ---
    edge_tts_voice: str = "en-US-ChristopherNeural"
    edge_tts_rate: str = "-5%"  # speaking speed, e.g. "+0%", "+10%", "-8%"

    # Where caption timings come from: "auto" = the TTS engine's own word boundaries when it provides
    # them (Edge TTS; most accurate, no Whisper needed), else Whisper. "whisper" forces Whisper.
    word_timing_source: Literal["auto", "whisper"] = "auto"

    # --- Kokoro (free, open-source TTS that runs locally; the voice used in GitHub Actions) ---
    kokoro_voice: str = "am_michael"  # am_* male, af_* female (US), bm_/bf_* British
    kokoro_speed: float = Field(1.0, ge=0.5, le=2.0)
    kokoro_model_dir: Path = ROOT_DIR / "models" / "kokoro"  # ~350 MB, downloaded on first use
    kokoro_release_url: str = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

    # --- faster-whisper (free, runs locally; model downloads on first use) ---
    faster_whisper_model: str = "base.en"  # tiny.en | base.en | small.en (or any faster-whisper model)
    faster_whisper_device: str = "cpu"  # "cpu", "cuda" or "auto"
    faster_whisper_compute_type: str = "int8"

    # --- ElevenLabs (paid voice-over; only used when TTS_PROVIDER=elevenlabs) ---
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "JBFqnCBsd6RMkjVDRZzb"  # "George"; override with any voice
    elevenlabs_model_id: str = "eleven_multilingual_v2"
    elevenlabs_stability: float = Field(0.35, ge=0, le=1)  # lower = more expressive
    elevenlabs_similarity: float = Field(0.8, ge=0, le=1)
    elevenlabs_style: float = Field(0.55, ge=0, le=1)
    elevenlabs_speed: float = Field(1.08, ge=0.7, le=1.2)  # slightly fast = high tempo

    # --- OpenAI Whisper API (paid; only used when WHISPER_PROVIDER=openai) ---
    openai_api_key: str = ""
    whisper_model: str = "whisper-1"

    # --- YouTube upload ---
    youtube_client_secrets: Path = ROOT_DIR / "client_secret.json"
    youtube_token_file: Path = ROOT_DIR / "token.json"
    youtube_privacy: Literal["private", "unlisted", "public"] = "private"
    youtube_category_id: str = "27"  # 27 = Education, 24 = Entertainment, 28 = Sci/Tech
    youtube_synthetic_disclosure: bool = True  # AI-generated media disclosure
    # All channels' tokens as one JSON object {"<channel id>": {...}}; used in GitHub Actions
    # (secret YOUTUBE_TOKENS_JSON). Locally the files token_<channel>.json are used instead.
    youtube_tokens_json: str = ""

    # --- Telegram bot (notifications + commands); both empty = disabled ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    notify_timezone: str = "Europe/Istanbul"  # second clock shown next to the channel's own time

    # --- Autopilot (daily.py) ---
    plan_lead_hours: float = 3.0  # a slot must be at least this far ahead so there is time to render
    plan_window_hours: float = 28.0  # a little over 24 h: late cron runs leave no gap, duplicates are filtered

    # --- Video ---
    fps: int = 60  # 30 or 60 (motion graphics are designed for 60)
    accent_color: str = "#FFE600"
    tail_seconds: float = 0.45  # pause after the last word, then the loop restarts
    seamless_loop: bool = True

    # --- Paths ---
    output_dir: Path = ROOT_DIR / "output"
    state_dir: Path = ROOT_DIR / "state"
    sfx_dir: Path = ROOT_DIR / "assets" / "sfx"

    @field_validator("fps")
    @classmethod
    def _fps_ok(cls, v: int) -> int:
        if v not in (30, 60):
            raise ValueError("FPS must be 30 or 60")
        return v

    @model_validator(mode="after")
    def _provider_keys(self) -> "Settings":
        """An API key is only mandatory for the paid provider that is actually selected."""
        if self.tts_provider == "elevenlabs" and not self.elevenlabs_api_key:
            raise ValueError("TTS_PROVIDER=elevenlabs requires ELEVENLABS_API_KEY (or use TTS_PROVIDER=edge_tts).")
        if self.whisper_provider == "openai" and not self.openai_api_key:
            raise ValueError("WHISPER_PROVIDER=openai requires OPENAI_API_KEY (or use WHISPER_PROVIDER=faster_whisper).")
        return self

    def require(self, *names: str) -> None:
        """Fail early with a readable message when a needed key is missing."""
        missing = [n.upper() for n in names if not getattr(self, n)]
        if missing:
            raise RuntimeError(
                f"Missing required setting(s): {', '.join(missing)}. "
                f"Copy .env.example to .env and fill them in."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
