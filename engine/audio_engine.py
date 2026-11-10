"""Voice-over + word-level timestamps behind swappable providers (Strategy pattern).

    TTS_PROVIDER      edge_tts (free, default)      | elevenlabs (paid)
    WHISPER_PROVIDER  faster_whisper (free, local)  | openai (paid)

Both whisper providers return the same `list[WordTiming]` (+ duration), so everything downstream
(alignment, whisper_words.json, Remotion props) is provider-independent. Use the two entry points
`synthesize_audio()` and `align_words()`; they pick the provider from the settings.

Caption/voice sync: with Edge TTS the engine's own word boundaries are the primary timing source
(calibrated against the real audio, see "caption / audio sync" below); Whisper is the fallback and
the source for every other TTS provider.
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterable
from difflib import SequenceMatcher
from pathlib import Path

import httpx
from pydantic import BaseModel

from .config import Settings, get_settings
from .generator import ScriptSpec


class WordTiming(BaseModel):
    text: str
    start: float
    end: float


class SceneTiming(BaseModel):
    id: int
    start: float
    end: float


class Timeline(BaseModel):
    duration: float  # total video length incl. tail (seconds)
    words: list[WordTiming]
    scenes: list[SceneTiming]


FREE_LABEL = "$0.00 (Free Local Engine)"
WHISPER_API_PER_MINUTE = 0.006  # OpenAI whisper-1 list price (USD)


def _clean_words(raw: Iterable[tuple[str, float, float]]) -> list[WordTiming]:
    """One normalisation for every whisper provider => identical output format:
    stripped text, no empty tokens, seconds rounded to milliseconds, end >= start."""
    words: list[WordTiming] = []
    for text, start, end in raw:
        text = text.strip()
        if not text:
            continue
        start, end = round(float(start), 3), round(float(end), 3)
        words.append(WordTiming(text=text, start=start, end=max(start, end)))
    return words


def _run_async(coro):
    """asyncio.run that also works when an event loop is already running (e.g. notebooks)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict = {}

    def target() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller's thread
            box["error"] = exc

    thread = threading.Thread(target=target)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


# ====================================================== caption / audio sync (onsets)
# Word timestamps are only as good as their source. Measured on real files: Whisper is 60-300 ms
# EARLY on average (and reports 0.0 s for the first word), Edge TTS word boundaries are consistently
# ~100 ms early. Captions that lead the voice look out of sync, so we anchor timings to the audio:
#   * Edge boundaries are shifted by an offset measured from the file (first audible sound vs the
#     first boundary), then words that start after a pause are snapped to the real sound onset.
#   * Whisper words after pauses / sentence starts are snapped to the real sound onset.
ONSET_HOP = 0.005  # seconds per envelope frame
ONSET_RISE_PCT = 0.01  # a sound "starts" when RMS exceeds 1 % of the peak ...
ONSET_QUIET_FRAMES = 6  # ... after at least 30 ms below it
DEFAULT_EDGE_OFFSET = 0.10  # used only when the audio cannot be analysed
MAX_EDGE_OFFSET = 0.30
SIDECAR_SUFFIX = ".tts_words.json"


def _load_envelope(audio_path: Path):
    """RMS envelope of the audio (10 ms window, 5 ms hop) -> (numpy array, duration) or None."""
    try:
        import av
        import numpy as np
    except ImportError:
        return None
    try:
        container = av.open(str(audio_path))
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        chunks = []
        for frame in container.decode(audio=0):
            chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(frame))
        chunks.extend(f.to_ndarray().reshape(-1) for f in (resampler.resample(None) or []))
        container.close()
        samples = np.concatenate(chunks).astype(np.float32) / 32768.0
    except Exception:  # noqa: BLE001 - undecodable file: skip refinement, keep provider timings
        return None
    if len(samples) < 320:
        return None
    hop, win = int(16000 * ONSET_HOP), 160
    rms = np.sqrt(np.array([np.mean(samples[i:i + win] ** 2) for i in range(0, len(samples) - win, hop)]))
    return rms, len(samples) / 16000


def _onsets(rms) -> list[float]:
    """Times (s) where sound starts after >= 30 ms of near-silence (word/sentence onsets)."""
    peak = float(rms.max()) if len(rms) else 0.0
    if peak <= 0:
        return []
    thr = peak * ONSET_RISE_PCT
    loud = rms > thr
    out = []
    for i in range(len(loud)):
        quiet_before = i == 0 or not loud[max(0, i - ONSET_QUIET_FRAMES):i].any()
        if loud[i] and quiet_before and (i == 0 or not loud[i - 1]):
            out.append(i * ONSET_HOP)
    return out


def snap_to_onsets(
    words: list[WordTiming], onsets: list[float], window: float, min_gap: float = 0.0
) -> list[WordTiming]:
    """Move each word start to the nearest real sound onset within +-`window` seconds.
    Only words preceded by a pause of >= `min_gap` s (and the first word) are touched."""
    out = [w.model_copy() for w in words]
    for i, w in enumerate(out):
        if i and w.start - out[i - 1].end < min_gap:
            continue
        near = [o for o in onsets if abs(o - w.start) <= window]
        if not near:
            continue
        onset = min(near, key=lambda o: abs(o - w.start))
        w.start = round(onset, 3)
        w.end = round(max(w.end, w.start + 0.04), 3)
        if i and out[i - 1].end > w.start:
            out[i - 1].end = max(out[i - 1].start, w.start)
    return out


def calibrate_tts_words(raw: list[WordTiming], envelope) -> tuple[list[WordTiming], float]:
    """Edge boundaries -> audible timings. Returns (words, applied offset in seconds)."""
    offset = DEFAULT_EDGE_OFFSET
    onsets: list[float] = []
    if envelope is not None:
        onsets = _onsets(envelope[0])
        if onsets and raw:
            offset = min(MAX_EDGE_OFFSET, max(0.0, onsets[0] - raw[0].start))
    shifted = [WordTiming(text=w.text, start=round(w.start + offset, 3), end=round(w.end + offset, 3)) for w in raw]
    return snap_to_onsets(shifted, onsets, window=0.12), offset


def sidecar_path(audio_path: Path) -> Path:
    return audio_path.with_name(audio_path.name + SIDECAR_SUFFIX)


def save_tts_words(audio_path: Path, words: list[WordTiming]) -> None:
    """Remember the TTS engine's own word boundaries next to the audio file."""
    sidecar = sidecar_path(audio_path)
    if not words:
        sidecar.unlink(missing_ok=True)
        return
    payload = {"audio_bytes": audio_path.stat().st_size, "words": [w.model_dump() for w in words]}
    sidecar.write_text(json.dumps(payload, indent=1), encoding="utf-8")


def load_tts_words(audio_path: Path) -> list[WordTiming] | None:
    """Sidecar words, or None if absent / written for a different audio file."""
    sidecar = sidecar_path(audio_path)
    if not sidecar.exists() or not audio_path.exists():
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        if data["audio_bytes"] != audio_path.stat().st_size:
            return None
        words = [WordTiming(**w) for w in data["words"]]
    except (KeyError, ValueError, TypeError):
        return None
    return words or None


# =========================================================================== TTS
class TTSProvider(ABC):
    name: str
    is_free: bool

    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    def synthesize(self, text: str, out_path: Path) -> Path:
        """Render `text` to an MP3 at `out_path` and return the path."""

    @abstractmethod
    def cost_summary(self, text: str) -> str:
        """Human-readable cost of synthesising `text`."""


class EdgeTTSProvider(TTSProvider):
    """Microsoft Edge neural voices through the `edge-tts` package. No key, no cost.
    (Uses Microsoft's online endpoint, so it needs an internet connection.)"""

    name = "edge_tts"
    is_free = True

    def synthesize(self, text: str, out_path: Path) -> Path:
        try:
            import edge_tts
        except ImportError as exc:
            raise RuntimeError("edge-tts is not installed. Run: pip install edge-tts") from exc

        out_path.parent.mkdir(parents=True, exist_ok=True)

        async def render() -> list[tuple[str, float, float]]:
            communicate = edge_tts.Communicate(
                text, self.settings.edge_tts_voice, rate=self.settings.edge_tts_rate, boundary="WordBoundary"
            )
            boundaries: list[tuple[str, float, float]] = []
            with out_path.open("wb") as fh:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        fh.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":  # offsets are in 100 ns units
                        start = chunk["offset"] / 1e7
                        boundaries.append((chunk["text"], start, start + chunk["duration"] / 1e7))
            return boundaries

        try:
            boundaries = _run_async(render())
        except Exception as exc:  # noqa: BLE001 - network/voice errors vary
            raise RuntimeError(
                f"Edge TTS failed ({exc}). Check your internet connection and that "
                f"EDGE_TTS_VOICE='{self.settings.edge_tts_voice}' exists "
                f"(python -m edge_tts --list-voices)."
            ) from exc
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError("Edge TTS produced an empty file.")
        save_tts_words(out_path, _clean_words(boundaries))  # exact word timing for align_words()
        return out_path

    def cost_summary(self, text: str) -> str:
        return f"{FREE_LABEL} - Edge TTS voice {self.settings.edge_tts_voice}"


class ElevenLabsProvider(TTSProvider):
    """ElevenLabs text-to-speech. The whole script goes in one request so prosody flows
    across sentences (per-scene requests sound choppy)."""

    name = "elevenlabs"
    is_free = False

    def __init__(self, settings: Settings):
        super().__init__(settings)
        settings.require("elevenlabs_api_key")

    def synthesize(self, text: str, out_path: Path) -> Path:
        s = self.settings
        response = httpx.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{s.elevenlabs_voice_id}",
            params={"output_format": "mp3_44100_128"},
            headers={"xi-api-key": s.elevenlabs_api_key, "Content-Type": "application/json"},
            json={
                "text": text,
                "model_id": s.elevenlabs_model_id,
                "voice_settings": {
                    "stability": s.elevenlabs_stability,
                    "similarity_boost": s.elevenlabs_similarity,
                    "style": s.elevenlabs_style,
                    "use_speaker_boost": True,
                    "speed": s.elevenlabs_speed,
                },
            },
            timeout=180,
        )
        if response.status_code != 200:
            raise RuntimeError(f"ElevenLabs error {response.status_code}: {response.text[:300]}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(response.content)
        return out_path

    def cost_summary(self, text: str) -> str:
        return f"{len(text)} characters of ElevenLabs credit (voice {self.settings.elevenlabs_voice_id})"


TTS_PROVIDERS: dict[str, type[TTSProvider]] = {
    EdgeTTSProvider.name: EdgeTTSProvider,
    ElevenLabsProvider.name: ElevenLabsProvider,
}


def get_tts_provider(settings: Settings | None = None) -> TTSProvider:
    settings = settings or get_settings()
    try:
        return TTS_PROVIDERS[settings.tts_provider](settings)
    except KeyError:
        raise RuntimeError(f"Unknown TTS_PROVIDER '{settings.tts_provider}'. Options: {', '.join(TTS_PROVIDERS)}") from None


def synthesize_audio(text: str, out_path: Path, settings: Settings | None = None) -> Path:
    """Text -> MP3 with the provider selected by TTS_PROVIDER."""
    return get_tts_provider(settings).synthesize(text, out_path)


# ===================================================================== Whisper
class WhisperProvider(ABC):
    name: str
    is_free: bool

    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    def transcribe(self, audio_path: Path) -> tuple[list[WordTiming], float]:
        """Return (word timings in seconds, audio duration in seconds)."""

    @abstractmethod
    def cost_summary(self, audio_seconds: float) -> str:
        """Human-readable cost of transcribing `audio_seconds` of audio."""


_FASTER_WHISPER_MODELS: dict[tuple[str, str, str], object] = {}


class FasterWhisperProvider(WhisperProvider):
    """Local faster-whisper (CTranslate2). The model is downloaded on first use and cached
    in memory for later calls; no key and no cost."""

    name = "faster_whisper"
    is_free = True

    def _model(self):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("faster-whisper is not installed. Run: pip install faster-whisper") from exc

        s = self.settings
        key = (s.faster_whisper_model, s.faster_whisper_device, s.faster_whisper_compute_type)
        if key not in _FASTER_WHISPER_MODELS:
            print(f"[whisper] loading faster-whisper '{key[0]}' on {key[1]} ({key[2]}); the first run downloads the model...")
            _FASTER_WHISPER_MODELS[key] = WhisperModel(key[0], device=key[1], compute_type=key[2])
        return _FASTER_WHISPER_MODELS[key]

    def transcribe(self, audio_path: Path) -> tuple[list[WordTiming], float]:
        segments, info = self._model().transcribe(
            str(audio_path),
            language="en",
            word_timestamps=True,
            beam_size=5,
            condition_on_previous_text=False,
        )
        raw = [(w.word, w.start, w.end) for seg in segments for w in (seg.words or [])]
        words = _clean_words(raw)
        if not words:
            raise RuntimeError("faster-whisper returned no word timestamps.")
        duration = float(getattr(info, "duration", 0) or 0) or words[-1].end
        return words, duration

    def cost_summary(self, audio_seconds: float) -> str:
        return f"{FREE_LABEL} - faster-whisper '{self.settings.faster_whisper_model}'"


class OpenAIWhisperProvider(WhisperProvider):
    """OpenAI Whisper API with word-level timestamps."""

    name = "openai"
    is_free = False

    def __init__(self, settings: Settings):
        super().__init__(settings)
        settings.require("openai_api_key")

    def transcribe(self, audio_path: Path) -> tuple[list[WordTiming], float]:
        from openai import OpenAI

        client = OpenAI(api_key=self.settings.openai_api_key)
        with audio_path.open("rb") as fh:
            result = client.audio.transcriptions.create(
                model=self.settings.whisper_model,
                file=fh,
                response_format="verbose_json",
                timestamp_granularities=["word"],
                language="en",
            )
        words = _clean_words((w.word, w.start, w.end) for w in (result.words or []))
        if not words:
            raise RuntimeError("Whisper returned no word timestamps.")
        duration = float(getattr(result, "duration", 0) or 0) or words[-1].end
        return words, duration

    def cost_summary(self, audio_seconds: float) -> str:
        return f"~${audio_seconds / 60 * WHISPER_API_PER_MINUTE:.4f} (OpenAI {self.settings.whisper_model})"


WHISPER_PROVIDERS: dict[str, type[WhisperProvider]] = {
    FasterWhisperProvider.name: FasterWhisperProvider,
    OpenAIWhisperProvider.name: OpenAIWhisperProvider,
}


def get_whisper_provider(settings: Settings | None = None) -> WhisperProvider:
    settings = settings or get_settings()
    try:
        return WHISPER_PROVIDERS[settings.whisper_provider](settings)
    except KeyError:
        raise RuntimeError(
            f"Unknown WHISPER_PROVIDER '{settings.whisper_provider}'. Options: {', '.join(WHISPER_PROVIDERS)}"
        ) from None


def align_words(audio_path: Path, settings: Settings | None = None) -> tuple[list[WordTiming], float]:
    """Audio -> (word timestamps in seconds, duration), anchored to the real audio.

    1. If the TTS engine saved its own word boundaries (Edge TTS) and WORD_TIMING_SOURCE=auto,
       use them, calibrated against the audio file. No Whisper needed.
    2. Otherwise run the Whisper provider (WHISPER_PROVIDER) and snap words after pauses to the
       real sound onsets."""
    settings = settings or get_settings()
    envelope = _load_envelope(audio_path)

    raw = load_tts_words(audio_path) if settings.word_timing_source == "auto" else None
    if raw:
        words, offset = calibrate_tts_words(raw, envelope)
        duration = envelope[1] if envelope else words[-1].end
        print(f"[sync] word timing from the TTS engine ({len(words)} words, calibrated +{offset * 1000:.0f} ms)")
        return words, duration

    words, duration = get_whisper_provider(settings).transcribe(audio_path)
    if envelope is not None:
        words = snap_to_onsets(words, _onsets(envelope[0]), window=0.25, min_gap=0.25)
    print(f"[sync] word timing from Whisper ({settings.whisper_provider}), sentence starts snapped to the audio")
    return words, duration


# ---------------------------------------------------------------------- Alignment
def _norm(token: str) -> str:
    return re.sub(r"[^a-z0-9]", "", token.lower())


def _fill_gaps(values: list[float | None], first: float, last: float) -> list[float]:
    """Linearly interpolate None entries between known neighbours."""
    vals = list(values)
    vals[0] = first if vals[0] is None else vals[0]
    known = [i for i, v in enumerate(vals) if v is not None]
    known_end = len(vals)  # virtual anchor at the end
    result: list[float] = []
    for i, v in enumerate(vals):
        if v is not None:
            result.append(v)
            continue
        prev_i = max(k for k in known if k < i)
        next_candidates = [k for k in known if k > i]
        next_i = next_candidates[0] if next_candidates else known_end
        prev_v = vals[prev_i]
        next_v = vals[next_i] if next_candidates else last
        frac = (i - prev_i) / (next_i - prev_i)
        result.append(prev_v + (next_v - prev_v) * frac)  # type: ignore[operator]
    return result


def align_timeline(
    script: ScriptSpec,
    whisper_words: list[WordTiming],
    audio_duration: float,
    tail_seconds: float = 0.35,
) -> Timeline:
    """Map the spoken words back onto the script's scenes.

    Whisper may spell numbers/names differently from the script, so we diff the two word
    sequences (difflib) instead of assuming a 1:1 count. Matched words take the script's
    original spelling + punctuation (good for captions); scene boundaries snap to the first
    spoken word of each scene.
    """
    script_tokens: list[tuple[int, str, str]] = []  # (scene index, raw, normalised)
    for si, scene in enumerate(script.scenes):
        for raw in scene.text.split():
            n = _norm(raw)
            if n:
                script_tokens.append((si, raw, n))

    matcher = SequenceMatcher(
        None,
        [t[2] for t in script_tokens],
        [_norm(w.text) for w in whisper_words],
        autojunk=False,
    )
    script_to_whisper: dict[int, int] = {}
    whisper_to_script: dict[int, int] = {}
    for a, b, size in matcher.get_matching_blocks():
        for k in range(size):
            script_to_whisper[a + k] = b + k
            whisper_to_script[b + k] = a + k

    # End shortly after the last spoken word: TTS files carry up to ~1 s of trailing silence,
    # which would play as dead air right before the loop restarts. (`audio_duration` is kept
    # for API compatibility and sanity checks by callers.)
    total = whisper_words[-1].end + tail_seconds

    starts: list[float | None] = []
    for si in range(len(script.scenes)):
        idxs = [i for i, t in enumerate(script_tokens) if t[0] == si]
        first_hit = next((script_to_whisper[i] for i in idxs if i in script_to_whisper), None)
        starts.append(whisper_words[first_hit].start if first_hit is not None else None)

    if all(s is None for s in starts):
        # Total mismatch: fall back to proportional-by-characters timing.
        weights = [max(1, len(s.text)) for s in script.scenes]
        acc, starts = 0.0, []
        for w in weights:
            starts.append(acc / sum(weights) * (total - tail_seconds))
            acc += w

    filled = _fill_gaps(starts, 0.0, total - tail_seconds)
    filled[0] = 0.0
    for i in range(1, len(filled)):  # enforce monotonic order
        filled[i] = max(filled[i], filled[i - 1] + 0.05)

    scenes = [
        SceneTiming(
            id=scene.id,
            start=round(filled[i], 3),
            end=round(filled[i + 1] if i + 1 < len(filled) else total, 3),
        )
        for i, scene in enumerate(script.scenes)
    ]

    words: list[WordTiming] = []
    for j, w in enumerate(whisper_words):
        text = w.text
        if j in whisper_to_script:
            text = script_tokens[whisper_to_script[j]][1]
        if not _norm(text):
            continue
        words.append(WordTiming(text=text, start=w.start, end=w.end))
    for k in range(len(words) - 1):  # remove overlaps between consecutive words
        if words[k].end > words[k + 1].start > words[k].start:
            words[k].end = words[k + 1].start

    return Timeline(duration=round(total, 3), words=words, scenes=scenes)
