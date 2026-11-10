"""Offline unit tests: no network, no API keys."""
import sys
import wave
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.asset_manager import SFX_NAMES, build_sfx_cues, ensure_sfx, motion_timing
from engine.audio_engine import SceneTiming, WordTiming, align_timeline
from engine.config import Settings
from engine.demo import build_demo
from engine.generator import (
    ChartAction,
    CounterAction,
    DocumentAction,
    MinimalTextAction,
    SceneSpec,
    ScriptSpec,
    _create_message,
    validate_quality,
)
from engine.renderer import build_props
from engine.uploader import build_metadata


def make_script(texts, sfx=None, actions=None):
    sfx = sfx or ["none"] * len(texts)
    actions = actions or [MinimalTextAction(type="MINIMAL_TEXT", headline="H")] * len(texts)
    return ScriptSpec(
        title="T",
        hook=texts[0],
        loop_transition_text="ignored",
        scenes=[
            SceneSpec(id=99, text=t, duration_estimate=2, motion_action=a, sfx=s)
            for t, s, a in zip(texts, sfx, actions)
        ],
    )


def spoken(text, start=0.0, per=0.4):
    out, t = [], start
    for w in text.split():
        out.append(WordTiming(text=w.strip(".,?!").lower(), start=t, end=t + per))
        t += per
    return out, t


# ------------------------------------------------------------------ script + alignment
def test_script_normalises_ids_and_loop_text():
    s = make_script(["Why?", "Because."])
    assert [x.id for x in s.scenes] == [1, 2]
    assert s.loop_transition_text == "Because."


def test_quality_gate_flags_short_script():
    problems = validate_quality(make_script(["Why?", "Because."]))
    assert any("words" in p for p in problems)


def test_alignment_scene_boundaries_and_script_spelling():
    script = make_script(["Why do you blink?", "Your eyes dry out.", "That's fifty percent."])
    words, end = spoken("why do you blink your eyes dry out thats fifty percent")
    tl = align_timeline(script, words, end, tail_seconds=0.3)
    assert [s.start for s in tl.scenes] == pytest.approx([0.0, 1.6, 3.2], abs=1e-3)
    assert tl.scenes[0].end == tl.scenes[1].start
    assert tl.scenes[-1].end == pytest.approx(tl.duration)
    assert tl.duration == pytest.approx(end + 0.3, abs=1e-3)  # last spoken word + tail
    # Captions take the script's casing + punctuation where words matched.
    assert tl.words[0].text == "Why"
    assert tl.words[3].text == "blink?"


def test_alignment_survives_number_spelling_mismatch():
    script = make_script(["Why 50 people stare.", "Nobody knows."])
    words, end = spoken("why fifty people stare nobody knows")  # 50 -> 'fifty'
    tl = align_timeline(script, words, end)
    assert tl.scenes[1].start == pytest.approx(1.6, abs=1e-3)
    assert len(tl.scenes) == 2


def test_alignment_total_mismatch_falls_back_to_proportional():
    script = make_script(["Alpha beta gamma.", "Delta epsilon zeta."])
    words, end = spoken("zzz yyy xxx www vvv uuu")
    tl = align_timeline(script, words, end)
    assert tl.scenes[0].start == 0.0 and 0 < tl.scenes[1].start < tl.duration


# ------------------------------------------------------------------------------- SFX
def test_sfx_generation_and_scene_cues(tmp_path):
    files = ensure_sfx(Settings(sfx_dir=tmp_path / "sfx"))
    assert set(files) == set(SFX_NAMES)
    for f in files.values():
        with wave.open(str(f)) as w:
            assert w.getnframes() > 1000
    script = make_script(["a b", "c d", "e f"], sfx=["whoosh", "riser", "none"])
    timings = [SceneTiming(id=i + 1, start=i * 2.0, end=(i + 1) * 2.0) for i in range(3)]
    cues = build_sfx_cues(script, timings, files)
    assert [c["name"] for c in cues] == ["whoosh", "riser"]
    assert [c["file"] for c in cues] == ["sfx/whoosh.wav", "sfx/riser.wav"]
    assert cues[0]["at"] == 0.0  # clamped, never negative
    assert cues[1]["at"] == pytest.approx(0.9)  # riser leads scene 2 by 1.1s


# ---------------------------------------------------------------------- motion layer
def test_motion_action_discriminated_union():
    sc = SceneSpec.model_validate(
        {"id": 1, "text": "x", "duration_estimate": 1,
         "motion_action": {"type": "COUNTER", "target_number": 87, "suffix": "%", "label": "L"}}
    )
    assert isinstance(sc.motion_action, CounterAction) and sc.motion_action.prefix == ""
    base = {"id": 1, "text": "x", "duration_estimate": 1}
    with pytest.raises(ValidationError):  # CHART needs a trend
        SceneSpec.model_validate({**base, "motion_action": {"type": "CHART"}})
    with pytest.raises(ValidationError):  # unknown type
        SceneSpec.model_validate({**base, "motion_action": {"type": "PHOTO"}})
    with pytest.raises(ValidationError):  # old schema without motion_action
        SceneSpec.model_validate({**base, "visual_prompt": "cinematic photo"})


def test_visual_prompt_is_gone():
    assert "visual_prompt" not in SceneSpec.model_fields
    assert "stock_query" not in SceneSpec.model_fields


def test_quality_gate_motion_variety_and_scene_length():
    long = "one two three four five six seven eight"
    problems = validate_quality(make_script([long] * 5))  # same type everywhere
    assert any("different" in p for p in problems)
    assert any("3 times in a row" in p for p in problems)
    short_doc = make_script(
        ["a b"], actions=[DocumentAction(type="DOCUMENT", title="t", highlight_text="abc", is_redacted=True)]
    )
    assert any("needs >= 6 words" in p for p in validate_quality(short_doc))


def test_motion_synced_sfx_cues(tmp_path):
    t = motion_timing()
    files = ensure_sfx(Settings(sfx_dir=tmp_path / "sfx"))
    script = make_script(
        ["a b c d e f", "g h i j k l", "m n o p q r", "s t u v w x"],
        actions=[
            CounterAction(type="COUNTER", target_number=5),
            ChartAction(type="CHART", trend="DOWN", label="x"),
            DocumentAction(type="DOCUMENT", title="t", highlight_text="abc", is_redacted=True),
            ChartAction(type="CHART", trend="UP", label="x"),  # no impact sound
        ],
    )
    timings = [SceneTiming(id=i + 1, start=i * 4.0, end=(i + 1) * 4.0) for i in range(4)]
    cues = build_sfx_cues(script, timings, files)
    by_name = {c["name"]: c["at"] for c in cues}
    assert len(cues) == 3
    assert by_name["pop"] == pytest.approx(t["counter_land_s"])
    assert by_name["sub_drop"] == pytest.approx(
        4.0 + t["chart_start_s"] + t["chart_draw_s"] * t["chart_crash_fraction"]
    )
    assert by_name["whoosh"] == pytest.approx(8.0 + t["document_unredact_s"] - 0.12)
    assert [c["at"] for c in cues] == sorted(c["at"] for c in cues)

    # The impact sound is dropped when the scene is too short for the animation to reach it.
    short = [SceneTiming(id=1, start=0, end=0.5)]
    one = make_script(["a b c d"], actions=[CounterAction(type="COUNTER", target_number=1)])
    assert build_sfx_cues(one, short, files) == []


def test_demo_props_have_remotion_shape(tmp_path):
    settings = Settings(sfx_dir=tmp_path / "sfx")
    script, tl, audio = build_demo(tmp_path / "run")
    props = build_props(script, tl, ensure_sfx(settings), audio, tmp_path / "run", settings)
    assert set(props) == {
        "title", "fps", "durationInSeconds", "audioFile", "accentColor",
        "seamlessLoop", "scenes", "words", "sfx",
    }
    assert props["fps"] == 60
    assert props["scenes"][0]["motion"]["type"] == "MINIMAL_TEXT"
    assert {s["motion"]["type"] for s in props["scenes"]} == {
        "MINIMAL_TEXT", "COUNTER", "SEARCH", "CHART", "DOCUMENT",
    }
    assert all("image" not in s for s in props["scenes"])
    assert all("name" in c and (tmp_path / "run" / c["file"]).exists() for c in props["sfx"])


# ------------------------------------------------------------------ config + models
def test_env_example_parses_and_models():
    root = Path(__file__).resolve().parent.parent
    s = Settings(_env_file=root / ".env.example")
    assert s.fps == 60 and s.accent_color == "#FFE600" and s.youtube_privacy == "private"
    assert s.anthropic_model == "claude-3-7-sonnet-latest"
    assert s.anthropic_fallback_model == "claude-3-5-sonnet-latest"
    with pytest.raises(Exception):
        Settings(_env_file=None, fps=45)


def test_fallback_model_used_on_404():
    import anthropic

    class FakeResp:
        status_code = 404
        headers = {}
        request = None

    class FakeMessages:
        def __init__(self):
            self.models = []

        def create(self, model, **kw):
            self.models.append(model)
            if model == "primary":
                raise anthropic.NotFoundError("nope", response=FakeResp(), body=None)
            return "ok"

    client = type("C", (), {"messages": FakeMessages()})()
    state = {"i": 0}
    assert _create_message(client, ["primary", "backup"], state) == "ok"
    assert client.messages.models == ["primary", "backup"] and state["i"] == 1
    with pytest.raises(RuntimeError):
        _create_message(client, ["primary"], {"i": 0})


def test_youtube_metadata_keeps_title_and_adds_shorts_hashtag():
    s = make_script(["Why?"])
    s.tags = ["#psychology", "eyes"]
    title, desc, tags = build_metadata(s)
    assert title == "T" and "#Shorts" in desc and tags == ["psychology", "eyes"]


# ------------------------------------------------------------ provider architecture
import types

from engine import audio_engine as ae
from engine.audio_engine import (
    EdgeTTSProvider,
    ElevenLabsProvider,
    FasterWhisperProvider,
    OpenAIWhisperProvider,
    TTSProvider,
    WhisperProvider,
    _clean_words,
    align_words,
    get_tts_provider,
    get_whisper_provider,
    synthesize_audio,
)


def test_defaults_are_free_and_need_no_keys():
    s = Settings(_env_file=None)
    assert (s.tts_provider, s.whisper_provider) == ("edge_tts", "faster_whisper")
    assert s.edge_tts_voice == "en-US-ChristopherNeural" and s.faster_whisper_model == "base.en"
    assert s.elevenlabs_api_key == "" and s.openai_api_key == ""
    tts, whisper = get_tts_provider(s), get_whisper_provider(s)
    assert isinstance(tts, EdgeTTSProvider) and isinstance(whisper, FasterWhisperProvider)
    assert tts.is_free and whisper.is_free
    assert "$0.00 (Free Local Engine)" in tts.cost_summary("hello")
    assert "$0.00 (Free Local Engine)" in whisper.cost_summary(30)


def test_paid_provider_requires_its_key_only_when_selected():
    with pytest.raises(ValidationError, match="ELEVENLABS_API_KEY"):
        Settings(_env_file=None, tts_provider="elevenlabs")
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings(_env_file=None, whisper_provider="openai")
    # key given -> fine; the other provider stays free and key-less
    s = Settings(_env_file=None, tts_provider="elevenlabs", elevenlabs_api_key="k")
    assert isinstance(get_tts_provider(s), ElevenLabsProvider) and s.openai_api_key == ""
    s = Settings(_env_file=None, whisper_provider="openai", openai_api_key="k")
    assert isinstance(get_whisper_provider(s), OpenAIWhisperProvider)
    # a paid key sitting in .env must not matter while the free provider is selected
    assert Settings(_env_file=None, elevenlabs_api_key="k").tts_provider == "edge_tts"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, tts_provider="bark")


def test_paid_provider_costs_and_flags():
    s = Settings(_env_file=None, tts_provider="elevenlabs", elevenlabs_api_key="k",
                 whisper_provider="openai", openai_api_key="k")
    tts, whisper = get_tts_provider(s), get_whisper_provider(s)
    assert not tts.is_free and not whisper.is_free
    assert "5 characters" in tts.cost_summary("hello")
    assert whisper.cost_summary(60) == "~$0.0060 (OpenAI whisper-1)"


def test_providers_follow_the_strategy_interfaces():
    assert all(issubclass(c, TTSProvider) for c in ae.TTS_PROVIDERS.values())
    assert all(issubclass(c, WhisperProvider) for c in ae.WHISPER_PROVIDERS.values())
    with pytest.raises(TypeError):
        TTSProvider(Settings(_env_file=None))  # abstract


def test_synthesize_audio_dispatches_on_provider(tmp_path, monkeypatch):
    calls = []

    class FakeEdge(EdgeTTSProvider):
        def synthesize(self, text, out_path):
            calls.append(("edge", text))
            return out_path

    class FakeEleven(ElevenLabsProvider):
        def synthesize(self, text, out_path):
            calls.append(("eleven", text))
            return out_path

    monkeypatch.setitem(ae.TTS_PROVIDERS, "edge_tts", FakeEdge)
    monkeypatch.setitem(ae.TTS_PROVIDERS, "elevenlabs", FakeEleven)
    synthesize_audio("a", tmp_path / "x.mp3", Settings(_env_file=None))
    synthesize_audio("b", tmp_path / "x.mp3", Settings(_env_file=None, tts_provider="elevenlabs", elevenlabs_api_key="k"))
    assert calls == [("edge", "a"), ("eleven", "b")]


def test_align_words_dispatches_on_provider(tmp_path, monkeypatch):
    class Fake(FasterWhisperProvider):
        def transcribe(self, audio_path):
            return [WordTiming(text="hi", start=0, end=0.2)], 0.5

    monkeypatch.setitem(ae.WHISPER_PROVIDERS, "faster_whisper", Fake)
    words, duration = align_words(tmp_path / "x.mp3", Settings(_env_file=None))
    assert words[0].text == "hi" and duration == 0.5


def test_faster_whisper_output_matches_openai_format(tmp_path, monkeypatch):
    """Fake faster-whisper model: output must be the exact WordTiming list OpenAI would give."""
    word = lambda w, a, b: types.SimpleNamespace(word=w, start=a, end=b)
    segs = [types.SimpleNamespace(words=[word(" Why", 0.0004, 0.31249), word(" can't", 0.31249, 0.6)]),
            types.SimpleNamespace(words=[word("  ", 0.7, 0.8), word(" you?", 0.8, 0.79)])]  # blank + end<start

    class FakeModel:
        def transcribe(self, path, **kw):
            assert kw["word_timestamps"] is True  # word-level timestamps requested
            return iter(segs), types.SimpleNamespace(duration=1.234)

    provider = FasterWhisperProvider(Settings(_env_file=None))
    monkeypatch.setattr(provider, "_model", lambda: FakeModel())
    words, duration = provider.transcribe(tmp_path / "a.mp3")

    assert duration == 1.234
    assert [w.model_dump() for w in words] == [
        {"text": "Why", "start": 0.0, "end": 0.312},      # stripped, rounded to ms
        {"text": "can't", "start": 0.312, "end": 0.6},
        {"text": "you?", "start": 0.8, "end": 0.8},        # blank token dropped, end >= start
    ]
    assert words == _clean_words([("Why", 0.0004, 0.31249), ("can't", 0.31249, 0.6), ("you?", 0.8, 0.79)])


def test_faster_whisper_model_is_cached(monkeypatch):
    created = []
    fake_module = types.SimpleNamespace(WhisperModel=lambda *a, **k: created.append((a, k)) or object())
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    ae._FASTER_WHISPER_MODELS.clear()
    p = FasterWhisperProvider(Settings(_env_file=None, faster_whisper_model="tiny.en"))
    assert p._model() is p._model()
    assert len(created) == 1 and created[0][0] == ("tiny.en",) and created[0][1]["compute_type"] == "int8"
    ae._FASTER_WHISPER_MODELS.clear()


def _fake_edge(audio_bytes=b"ID3fake", boundaries=(("hello", 0.1, 0.4), ("there", 0.5, 0.9)), seen=None):
    """A stand-in for the edge_tts module that streams audio + WordBoundary events."""

    class FakeCommunicate:
        def __init__(self, text, voice, rate="+0%", boundary="SentenceBoundary"):
            if seen is not None:
                seen.update(text=text, voice=voice, rate=rate, boundary=boundary)

        async def stream(self):
            if audio_bytes:
                yield {"type": "audio", "data": audio_bytes}
            for word, start, end in boundaries:  # edge reports 100 ns units
                yield {"type": "WordBoundary", "offset": int(start * 1e7), "duration": int((end - start) * 1e7), "text": word}

    return types.SimpleNamespace(Communicate=FakeCommunicate)


def test_edge_tts_provider_passes_voice_rate_and_word_boundaries(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setitem(sys.modules, "edge_tts", _fake_edge(seen=seen))
    audio = tmp_path / "v.mp3"
    out = EdgeTTSProvider(Settings(_env_file=None, edge_tts_voice="en-GB-RyanNeural", edge_tts_rate="+10%")).synthesize(
        "hello there", audio
    )
    assert out.read_bytes() == b"ID3fake"
    assert seen == {"text": "hello there", "voice": "en-GB-RyanNeural", "rate": "+10%", "boundary": "WordBoundary"}
    words = ae.load_tts_words(audio)  # sidecar written next to the audio
    assert [(w.text, w.start, w.end) for w in words] == [("hello", 0.1, 0.4), ("there", 0.5, 0.9)]


def test_edge_tts_empty_output_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "edge_tts", _fake_edge(audio_bytes=b"", boundaries=()))
    with pytest.raises(RuntimeError, match="empty"):
        EdgeTTSProvider(Settings(_env_file=None)).synthesize("x", tmp_path / "v.mp3")


# ------------------------------------------------------------------ caption / audio sync
def make_wav(path, bursts, seconds=3.0, rate=16000):
    """Digital silence with 200 ms tone bursts starting at the given times (the 'spoken words')."""
    import math
    import struct
    import wave

    samples = [0] * int(seconds * rate)
    for start in bursts:
        for i in range(int(0.2 * rate)):
            samples[int(start * rate) + i] = int(12000 * math.sin(2 * math.pi * 220 * i / rate))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % len(samples), *samples))
    return path


def test_envelope_finds_real_sound_onsets(tmp_path):
    env = ae._load_envelope(make_wav(tmp_path / "a.wav", [0.21, 1.5]))
    assert env is not None and env[1] == pytest.approx(3.0, abs=0.05)
    onsets = ae._onsets(env[0])
    assert onsets[0] == pytest.approx(0.21, abs=0.02) and onsets[1] == pytest.approx(1.5, abs=0.02)


def test_edge_boundaries_are_calibrated_to_the_audio(tmp_path):
    """Edge says the first word starts at 0.10 s but sound begins at 0.21 s: shift everything by 0.11 s."""
    audio = make_wav(tmp_path / "a.wav", [0.21, 1.5])
    raw = [WordTiming(text="why", start=0.10, end=0.35), WordTiming(text="your", start=1.40, end=1.65)]
    words, offset = ae.calibrate_tts_words(raw, ae._load_envelope(audio))
    assert offset == pytest.approx(0.11, abs=0.02)
    assert words[0].start == pytest.approx(0.21, abs=0.02)
    assert words[1].start == pytest.approx(1.5, abs=0.02)  # also lands on the real onset after the pause
    # without analysable audio a conservative default offset is used
    words, offset = ae.calibrate_tts_words(raw, None)
    assert offset == ae.DEFAULT_EDGE_OFFSET and words[0].start == pytest.approx(0.20)


def test_snap_to_onsets_only_touches_words_after_pauses():
    onsets = [0.21, 1.50]
    words = [WordTiming(text="a", start=0.0, end=0.4), WordTiming(text="b", start=0.4, end=0.8),  # whisper: first word at 0.0
             WordTiming(text="c", start=1.30, end=1.9), WordTiming(text="d", start=1.9, end=2.2)]
    out = ae.snap_to_onsets(words, onsets, window=0.25, min_gap=0.25)
    assert [round(w.start, 2) for w in out] == [0.21, 0.4, 1.5, 1.9]  # first word + word after the 0.5 s pause moved
    assert out[1].end <= out[2].start and words[0].start == 0.0  # input not mutated, no overlaps
    far = ae.snap_to_onsets([WordTiming(text="x", start=0.9, end=1.0)], onsets, window=0.25)
    assert far[0].start == 0.9  # no onset within the window: unchanged


def test_align_words_prefers_tts_boundaries_over_whisper(tmp_path, monkeypatch):
    audio = make_wav(tmp_path / "voice.wav", [0.21, 1.5])
    ae.save_tts_words(audio, [WordTiming(text="why", start=0.10, end=0.35), WordTiming(text="your", start=1.40, end=1.65)])

    class Boom(FasterWhisperProvider):
        def transcribe(self, audio_path):
            raise AssertionError("Whisper must not run when the TTS engine supplied word timings")

    monkeypatch.setitem(ae.WHISPER_PROVIDERS, "faster_whisper", Boom)
    words, duration = ae.align_words(audio, Settings(_env_file=None))
    assert [w.text for w in words] == ["why", "your"] and words[0].start == pytest.approx(0.21, abs=0.02)
    assert duration == pytest.approx(3.0, abs=0.05)


def test_align_words_falls_back_to_whisper_and_snaps(tmp_path, monkeypatch):
    audio = make_wav(tmp_path / "voice.wav", [0.21, 1.5])  # no sidecar -> e.g. ElevenLabs audio

    class Fake(FasterWhisperProvider):
        def transcribe(self, audio_path):
            return [WordTiming(text="why", start=0.0, end=0.4), WordTiming(text="your", start=1.3, end=1.7)], 3.0

    monkeypatch.setitem(ae.WHISPER_PROVIDERS, "faster_whisper", Fake)
    words, _ = ae.align_words(audio, Settings(_env_file=None))
    assert [w.start for w in words] == pytest.approx([0.21, 1.5], abs=0.02)  # was 0.0 / 1.3 from "Whisper"


def test_word_timing_source_whisper_ignores_the_sidecar(tmp_path, monkeypatch):
    audio = make_wav(tmp_path / "voice.wav", [0.21])
    ae.save_tts_words(audio, [WordTiming(text="tts", start=0.1, end=0.3)])

    class Fake(FasterWhisperProvider):
        def transcribe(self, audio_path):
            return [WordTiming(text="whisper", start=0.2, end=0.5)], 3.0

    monkeypatch.setitem(ae.WHISPER_PROVIDERS, "faster_whisper", Fake)
    words, _ = ae.align_words(audio, Settings(_env_file=None, word_timing_source="whisper"))
    assert words[0].text == "whisper"


def test_stale_sidecar_is_ignored(tmp_path):
    audio = make_wav(tmp_path / "voice.wav", [0.21])
    ae.save_tts_words(audio, [WordTiming(text="old", start=0.1, end=0.3)])
    assert ae.load_tts_words(audio) is not None
    audio.write_bytes(audio.read_bytes()[:-2000])  # the audio file was replaced
    assert ae.load_tts_words(audio) is None
    assert ae.load_tts_words(tmp_path / "missing.mp3") is None


def test_unreadable_audio_falls_back_to_provider_timings(tmp_path):
    bad = tmp_path / "broken.mp3"
    bad.write_bytes(b"not audio")
    assert ae._load_envelope(bad) is None
    words = [WordTiming(text="a", start=0.0, end=0.2)]
    assert ae.snap_to_onsets(words, [], window=0.2)[0].start == 0.0


def test_env_example_selects_free_providers():
    root = Path(__file__).resolve().parent.parent
    s = Settings(_env_file=root / ".env.example")
    assert (s.tts_provider, s.whisper_provider) == ("edge_tts", "faster_whisper")
    assert s.edge_tts_rate == "-5%" and s.faster_whisper_device == "cpu"


# ------------------------------------------------------- natural pace + clean loop ending
def test_video_ends_shortly_after_last_word_not_after_trailing_silence():
    """TTS files end with up to ~1s of silence; that must not become dead air before the loop."""
    script = make_script(["Why do you blink?", "Your eyes dry out."])
    words, last_end = spoken("why do you blink your eyes dry out")
    tl = align_timeline(script, words, audio_duration=last_end + 1.3, tail_seconds=0.45)
    assert tl.duration == pytest.approx(last_end + 0.45, abs=1e-3)
    assert tl.scenes[-1].end == pytest.approx(tl.duration)


def test_defaults_are_a_natural_pace():
    s = Settings(_env_file=None)
    assert s.edge_tts_rate == "-5%" and s.tail_seconds == 0.45
    from engine.generator import MAX_WORDS, MIN_WORDS, WORDS_PER_SECOND

    assert WORDS_PER_SECOND == 2.5
    assert 28 <= MIN_WORDS / WORDS_PER_SECOND and MAX_WORDS / WORDS_PER_SECOND <= 50  # 30-50 s Shorts


def test_last_scene_must_be_a_lead_in_for_the_loop():
    from engine.generator import validate_quality

    base = valid_script_for_gate()
    assert not any("lead-in" in p for p in validate_quality(base))
    for ending in ("That is the whole story.", "Thanks for watching!"):
        base.scenes[-1].text = ending
        assert any("lead-in" in p for p in validate_quality(base))
    for ending in ("Which brings us back to the question:", "And that is exactly why —", "So ask yourself again…"):
        base.scenes[-1].text = ending
        assert not any("lead-in" in p for p in validate_quality(base))


def valid_script_for_gate():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_llm_providers import valid_script

    return ScriptSpec.model_validate(valid_script())
