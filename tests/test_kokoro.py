"""Kokoro TTS provider (the free local voice used in GitHub Actions). Unit tests are offline; the live test
runs a real synthesis only when RUN_LIVE_TTS=1 (set in CI)."""
import json
import os
import sys
import types
import wave
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import audio_engine as ae
from engine.audio_engine import EdgeTTSProvider, ElevenLabsProvider, KokoroProvider, get_tts_provider
from engine.config import Settings
from test_llm_providers import valid_script


def fake_kokoro_module(created=None, rate=24000, samples=None):
    """Stands in for the kokoro_onnx package: a 1 s tone at 24 kHz and a short voice list."""
    import numpy as np

    tone = (0.5 * np.sin(2 * np.pi * 220 * np.arange(rate) / rate)).astype("float32") if samples is None else samples

    class Kokoro:
        def __init__(self, model, voices, espeak_config=None):
            if created is not None:
                created.append((model, voices, espeak_config))

        def get_voices(self):
            return ["af_heart", "am_adam", "am_michael"]

        def create(self, text, voice, speed, lang):
            self.last = dict(text=text, voice=voice, speed=speed, lang=lang)
            if created is not None:
                created.append(self.last)
            return tone, rate

    return types.SimpleNamespace(Kokoro=Kokoro)


def settings_with_models(tmp_path, **kw):
    models = tmp_path / "models"
    models.mkdir(exist_ok=True)
    for name in ae.KOKORO_FILES:
        (models / name).write_bytes(b"x" * 1_000_001)  # looks like a downloaded model
    return Settings(_env_file=None, tts_provider="kokoro", kokoro_model_dir=models, **kw)


@pytest.fixture(autouse=True)
def clean_engine_cache():
    ae._KOKORO_ENGINES.clear()
    yield
    ae._KOKORO_ENGINES.clear()


# ------------------------------------------------------------------------- selection
def test_kokoro_is_selectable_without_any_key_and_writes_wav():
    s = Settings(_env_file=None, tts_provider="kokoro")
    provider = get_tts_provider(s)
    assert isinstance(provider, KokoroProvider) and provider.is_free and provider.extension == ".wav"
    assert EdgeTTSProvider.extension == ".mp3" and ElevenLabsProvider.extension == ".mp3"
    assert "$0.00" in provider.cost_summary("x") and "am_michael" in provider.cost_summary("x")
    with pytest.raises(Exception):
        Settings(_env_file=None, kokoro_speed=3.0)  # outside 0.5-2.0


# ------------------------------------------------------------------------- synthesis
def test_synthesize_writes_a_wav_with_a_short_lead_in(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "kokoro_onnx", fake_kokoro_module(calls))
    s = settings_with_models(tmp_path, kokoro_voice="am_adam", kokoro_speed=0.92)
    out = KokoroProvider(s).synthesize("Hello there.", tmp_path / "voice.wav")
    with wave.open(str(out)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)
        assert w.getnframes() == 24000 + int(24000 * KokoroProvider.LEAD_SILENCE)  # the 1 s tone + lead-in
        frames = w.readframes(w.getnframes())
    lead = int(24000 * KokoroProvider.LEAD_SILENCE) * 2
    assert frames[:lead] == b"\x00" * lead and any(frames[lead:])  # silence first, then sound
    assert calls[-1] == dict(text="Hello there.", voice="am_adam", speed=0.92, lang="en-us")


def test_unknown_voice_lists_the_available_ones(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "kokoro_onnx", fake_kokoro_module())
    s = settings_with_models(tmp_path, kokoro_voice="zz_nobody")
    with pytest.raises(RuntimeError, match="zz_nobody.*am_adam"):
        KokoroProvider(s).synthesize("Hi", tmp_path / "v.wav")


def test_no_audio_is_an_error_and_stale_word_file_is_removed(tmp_path, monkeypatch):
    import numpy as np

    monkeypatch.setitem(sys.modules, "kokoro_onnx", fake_kokoro_module(samples=np.zeros(0, dtype="float32")))
    out = tmp_path / "v.wav"
    with pytest.raises(RuntimeError, match="no audio"):
        KokoroProvider(settings_with_models(tmp_path)).synthesize("Hi", out)

    ae._KOKORO_ENGINES.clear()  # the engine is cached per model file; start fresh with a working fake
    monkeypatch.setitem(sys.modules, "kokoro_onnx", fake_kokoro_module())
    out.write_bytes(b"old")
    ae.sidecar_path(out).write_text("{}", encoding="utf-8")  # left over from an earlier Edge TTS voice
    KokoroProvider(settings_with_models(tmp_path)).synthesize("Hi", out)
    assert not ae.sidecar_path(out).exists()  # Kokoro has no word boundaries: Whisper times the words


def test_engine_is_loaded_once(tmp_path, monkeypatch):
    created = []
    monkeypatch.setitem(sys.modules, "kokoro_onnx", fake_kokoro_module(created))
    p = KokoroProvider(settings_with_models(tmp_path))
    p.synthesize("a", tmp_path / "a.wav")
    p.synthesize("b", tmp_path / "b.wav")
    assert sum(1 for c in created if isinstance(c, tuple)) == 1  # one Kokoro(...) construction


# ------------------------------------------------------------------------- model files
def test_missing_models_are_downloaded_once(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "kokoro_onnx", fake_kokoro_module())
    urls = []

    def fake_download(url, target):
        urls.append(url)
        target.write_bytes(b"x" * 1_000_001)

    monkeypatch.setattr(ae, "_download", fake_download)
    s = Settings(_env_file=None, tts_provider="kokoro", kokoro_model_dir=tmp_path / "m", kokoro_release_url="https://example.test/rel/")
    p = KokoroProvider(s)
    p.synthesize("x", tmp_path / "a.wav")
    p.synthesize("y", tmp_path / "b.wav")
    assert urls == ["https://example.test/rel/kokoro-v1.0.onnx", "https://example.test/rel/voices-v1.0.bin"]


def test_default_release_is_the_one_compatible_with_kokoro_onnx():
    """model-files-v1.1 ships a model with a different input signature that kokoro-onnx 0.4.x cannot run."""
    assert Settings(_env_file=None).kokoro_release_url.endswith("/model-files-v1.0")


def test_download_is_atomic_and_retried(tmp_path, monkeypatch):
    attempts = []

    class Boom:
        def __enter__(self):
            attempts.append(1)
            raise OSError("connection reset")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ae.httpx, "stream", lambda *a, **k: Boom())
    target = tmp_path / "model.onnx"
    with pytest.raises(RuntimeError, match="Could not download"):
        ae._download("https://example.test/m.onnx", target)
    assert len(attempts) == 3 and not target.exists() and not (tmp_path / "model.onnx.part").exists()


def test_download_success_replaces_the_partial_file(tmp_path, monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def iter_bytes(self, n):
            yield b"abc"
            yield b"def"

    class Ctx:
        def __enter__(self):
            return Response()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ae.httpx, "stream", lambda *a, **k: Ctx())
    target = tmp_path / "m.bin"
    ae._download("https://example.test/m.bin", target)
    assert target.read_bytes() == b"abcdef" and not (tmp_path / "m.bin.part").exists()


# ------------------------------------------- Windows: espeak cannot read non-ASCII folders
def test_espeak_config_is_untouched_off_windows():
    assert ae._ascii_espeak_config(windows=False) is None


def test_espeak_files_are_copied_to_an_ascii_folder_when_the_path_has_non_ascii_letters(tmp_path, monkeypatch):
    pkg = tmp_path / "Masaüstü" / "espeakng_loader"
    (pkg / "espeak-ng-data").mkdir(parents=True)
    (pkg / "espeak-ng-data" / "phontab").write_bytes(b"data")
    (pkg / "espeak-ng.dll").write_bytes(b"dll")
    monkeypatch.setitem(sys.modules, "espeakng_loader", types.SimpleNamespace(
        get_library_path=lambda: str(pkg / "espeak-ng.dll"), get_data_path=lambda: str(pkg / "espeak-ng-data")))
    monkeypatch.setitem(sys.modules, "kokoro_onnx", types.SimpleNamespace(
        EspeakConfig=lambda lib_path, data_path: types.SimpleNamespace(lib_path=lib_path, data_path=data_path)))
    ascii_base = tmp_path / "ProgramData"
    monkeypatch.setenv("PROGRAMDATA", str(ascii_base))
    cfg = ae._ascii_espeak_config(windows=True)
    assert cfg is not None and cfg.lib_path.isascii() and cfg.data_path.isascii()
    assert (Path(cfg.data_path) / "phontab").read_bytes() == b"data" and Path(cfg.lib_path).read_bytes() == b"dll"
    assert ae._ascii_espeak_config(windows=True).data_path == cfg.data_path  # second call reuses the copy

    ascii_pkg = tmp_path / "plain" / "espeakng_loader"  # ASCII path: nothing to do
    (ascii_pkg / "espeak-ng-data").mkdir(parents=True)
    (ascii_pkg / "espeak-ng.dll").write_bytes(b"dll")
    monkeypatch.setitem(sys.modules, "espeakng_loader", types.SimpleNamespace(
        get_library_path=lambda: str(ascii_pkg / "espeak-ng.dll"), get_data_path=lambda: str(ascii_pkg / "espeak-ng-data")))
    assert ae._ascii_espeak_config(windows=True) is None


# ---------------------------------------------------------------------- pipeline + channels
def test_pipeline_uses_the_channels_kokoro_voice_and_a_wav_file(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from engine import pipeline
    from engine.audio_engine import WordTiming
    from engine.config import get_settings
    from engine.generator import ScriptSpec

    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("SFX_DIR", str(tmp_path / "sfx"))
    monkeypatch.setenv("TTS_PROVIDER", "kokoro")
    get_settings.cache_clear()
    seen = {}

    def tts(text, out_path, settings=None):
        seen.update(voice=settings.kokoro_voice, speed=settings.kokoro_speed, file=out_path.name)
        out_path.write_bytes(b"RIFF")
        return out_path

    def words(audio_path, settings=None):
        ws, t = [], 0.0
        for w in ScriptSpec.model_validate(valid_script()).narration.split():
            ws.append(WordTiming(text=w, start=round(t, 3), end=round(t + 0.3, 3)))
            t += 0.33
        return ws, t

    monkeypatch.setattr(pipeline, "synthesize_audio", tts)
    monkeypatch.setattr(pipeline, "align_words", words)
    script_file = tmp_path / "s.json"
    script_file.write_text(json.dumps(valid_script()), encoding="utf-8")

    result = CliRunner().invoke(pipeline.app, ["--channel", "money", "--script-file", str(script_file)])
    get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    assert seen == {"voice": "am_adam", "speed": 0.92, "file": "voice.wav"}  # the money channel's own voice, a .wav
    run_dir = next((tmp_path / "out").iterdir())
    assert json.loads((run_dir / "input-props.json").read_text(encoding="utf-8"))["audioFile"] == "voice.wav"


def test_every_channel_has_a_kokoro_voice_that_exists_and_they_differ():
    from engine.channels import list_channel_ids, load_channel

    voices = {cid: load_channel(cid).kokoro_voice for cid in list_channel_ids()}
    assert all(v and v[:3] in ("am_", "af_", "bm_", "bf_") for v in voices.values())
    assert len(set(voices.values())) == len(voices)  # the two channels sound different


# ------------------------------------------------------------------------- workflows
def test_workflows_use_kokoro_and_cache_the_models():
    daily = yaml.safe_load((ROOT / ".github/workflows/daily.yml").read_text(encoding="utf-8"))
    steps = daily["jobs"]["produce"]["steps"]
    produce = next(s for s in steps if s.get("name") == "Produce the video")
    assert produce["env"]["TTS_PROVIDER"] == "kokoro"
    cache = next(s for s in steps if str(s.get("uses", "")).startswith("actions/cache"))
    assert "models/kokoro" in cache["with"]["path"] and steps.index(cache) < steps.index(produce)
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    test_step = next(s for s in ci["jobs"]["test"]["steps"] if "pytest" in s.get("run", ""))
    assert test_step["env"]["RUN_LIVE_TTS"] == "1"


# ---------------------------------------------------------------------------- live (CI)
@pytest.mark.skipif(os.environ.get("RUN_LIVE_TTS") != "1", reason="real synthesis; set RUN_LIVE_TTS=1 (done in CI)")
def test_live_kokoro_speaks_a_sentence(tmp_path):
    """Downloads the model on first use, then really synthesizes speech with the default settings."""
    ae._KOKORO_ENGINES.clear()
    s = Settings(_env_file=None, tts_provider="kokoro")
    out = KokoroProvider(s).synthesize("Why do you look away the second someone stares back?", tmp_path / "live.wav")
    with wave.open(str(out)) as w:
        seconds = w.getnframes() / w.getframerate()
        assert w.getframerate() == 24000 and 2.0 < seconds < 8.0
        data = w.readframes(w.getnframes())
    assert max(abs(int.from_bytes(data[i:i + 2], "little", signed=True)) for i in range(0, len(data), 400)) > 2000  # audible
