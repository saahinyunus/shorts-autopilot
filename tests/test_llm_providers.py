"""LLM provider tests (Gemini / Groq / Anthropic) and the --script-file flow. No network, no keys."""
import copy
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import generator as gen
from engine.config import Settings
from engine.generator import (
    AnthropicProvider,
    GeminiProvider,
    GroqProvider,
    ScriptSpec,
    _gemini_schema,
    generate_script,
    get_llm_provider,
    validate_quality,
)

SENTENCE = "this sentence has plenty of spoken words inside"  # 8 words


def valid_script() -> dict:
    """A script that passes every quality rule (8 scenes, 3+ motion types, 80+ words)."""
    actions = [
        {"type": "MINIMAL_TEXT", "headline": "Why", "subtext": "sub"},
        {"type": "COUNTER", "target_number": 87, "suffix": "%", "label": "stat"},
        {"type": "SEARCH", "query_text": "why do we", "result_headline": "answer"},
        {"type": "CHART", "trend": "DOWN", "label": "crash"},
    ]
    scenes = []
    for i in range(8):
        text = f"{SENTENCE} number {i} and more words here"
        if i == 0:
            text = "Why does this happen to you?"
        elif i == 7:
            text = "Which brings us right back to the question:"  # lead-in: the hook completes it on loop
        scenes.append(
            {"id": i + 1, "text": text, "duration_estimate": 3.0,
             "motion_action": dict(actions[i % 4]), "sfx": "whoosh" if i % 2 == 0 else "none"}
        )
    return {
        "title": "T", "hook": "Why does this happen to you?", "scenes": scenes,
        "loop_transition_text": scenes[-1]["text"], "description": "d #Shorts", "tags": ["a"],
    }


def test_fixture_script_is_valid():
    script = ScriptSpec.model_validate(valid_script())
    assert validate_quality(script) == []


class FakeResp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body if body is not None else {}, headers or {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


def gemini_reply(payload, finish="STOP"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}]})


class Recorder:
    """Stands in for httpx.post; replies come from a queue and every request is recorded."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "body": json})
        return self.replies.pop(0)


@pytest.fixture
def no_sleep(monkeypatch):
    waited = []
    monkeypatch.setattr(gen.time, "sleep", waited.append)
    return waited


def gemini_settings(**kw):
    return Settings(_env_file=None, gemini_api_key="g-key", **kw)


# ------------------------------------------------------------------------- selection
def test_gemini_is_default_and_no_anthropic_key_needed():
    s = Settings(_env_file=None)
    assert s.llm_provider == "gemini" and s.anthropic_api_key == ""
    assert s.gemini_model == "gemini-3.6-flash"
    provider = get_llm_provider(gemini_settings())
    assert isinstance(provider, GeminiProvider) and provider.is_free
    assert provider.cost_summary().startswith("$0.00")


def test_only_the_selected_providers_key_is_required():
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        get_llm_provider(Settings(_env_file=None))
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        get_llm_provider(Settings(_env_file=None, llm_provider="groq", gemini_api_key="x"))
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        get_llm_provider(Settings(_env_file=None, llm_provider="anthropic", gemini_api_key="x"))
    assert isinstance(get_llm_provider(Settings(_env_file=None, llm_provider="groq", groq_api_key="k")), GroqProvider)
    anth = get_llm_provider(Settings(_env_file=None, llm_provider="anthropic", anthropic_api_key="k"))
    assert isinstance(anth, AnthropicProvider) and not anth.is_free


def test_settings_do_not_demand_any_llm_key_up_front():
    """--script-file / --demo must start without any LLM key, whatever LLM_PROVIDER is."""
    for provider in ("gemini", "groq", "anthropic"):
        assert Settings(_env_file=None, llm_provider=provider).llm_provider == provider


def test_env_example_selects_gemini():
    s = Settings(_env_file=Path(__file__).resolve().parent.parent / ".env.example")
    assert (s.llm_provider, s.gemini_model, s.groq_model) == ("gemini", "gemini-3.6-flash", "openai/gpt-oss-120b")


# --------------------------------------------------------------------------- Gemini
def test_gemini_request_shape_and_script(monkeypatch):
    rec = Recorder(gemini_reply(valid_script()))
    monkeypatch.setattr(gen.httpx, "post", rec)
    script = generate_script("eye contact", gemini_settings())
    assert len(script.scenes) == 8

    call = rec.calls[0]
    assert call["url"].endswith("/models/gemini-3.6-flash:generateContent")
    assert call["headers"]["x-goog-api-key"] == "g-key" and "g-key" not in call["url"]  # key never in the URL
    cfg = call["body"]["generationConfig"]
    assert cfg["responseMimeType"] == "application/json"
    schema = cfg["responseSchema"]
    assert schema["type"] == "OBJECT" and schema["properties"]["scenes"]["type"] == "ARRAY"
    motion = schema["properties"]["scenes"]["items"]["properties"]["motion_action"]
    # one variant per motion type, each with its own REQUIRED fields (flat schemas let Gemini omit them)
    variants = {v["properties"]["type"]["enum"][0]: v for v in motion["anyOf"]}
    assert list(variants) == ["COUNTER", "CHART", "DOCUMENT", "SEARCH", "MINIMAL_TEXT"]
    assert variants["DOCUMENT"]["required"] == ["type", "title", "highlight_text", "is_redacted"]
    assert variants["CHART"]["properties"]["trend"]["enum"] == ["UP", "DOWN"]
    assert variants["MINIMAL_TEXT"]["required"] == ["type", "headline"]
    assert "motion_action" in call["body"]["systemInstruction"]["parts"][0]["text"]
    assert "submit_script" not in call["body"]["systemInstruction"]["parts"][0]["text"]  # no tool talk for JSON mode
    assert call["body"]["contents"][0]["role"] == "user" and "eye contact" in call["body"]["contents"][0]["parts"][0]["text"]


def test_gemini_schema_uppercases_types_only():
    out = _gemini_schema({"type": "object", "properties": {"x": {"type": "string", "enum": ["a"], "description": "string"}}})
    assert out == {"type": "OBJECT", "properties": {"x": {"type": "STRING", "enum": ["a"], "description": "string"}}}


def test_gemini_retries_with_feedback_after_bad_script(monkeypatch):
    bad = valid_script()
    bad["scenes"] = bad["scenes"][:2]  # too few scenes / words
    rec = Recorder(gemini_reply(bad), gemini_reply(valid_script()))
    monkeypatch.setattr(gen.httpx, "post", rec)
    script = generate_script("t", gemini_settings())
    assert len(script.scenes) == 8 and len(rec.calls) == 2
    second = rec.calls[1]["body"]["contents"]
    assert [c["role"] for c in second] == ["user", "model", "user"]
    assert "Rejected" in second[2]["parts"][0]["text"] and "scenes" in second[2]["parts"][0]["text"]


def test_gemini_accepts_fenced_json_and_null_fields(monkeypatch):
    data = valid_script()
    data["scenes"][0]["motion_action"].update(prefix=None, target_number=None, trend=None)  # irrelevant fields as null
    data["tags"] = None
    fenced = "```json\n" + json.dumps(data) + "\n```"
    monkeypatch.setattr(gen.httpx, "post", Recorder(gemini_reply(fenced)))
    assert len(generate_script("t", gemini_settings()).scenes) == 8


def test_gemini_falls_back_to_second_model_on_404(monkeypatch):
    rec = Recorder(FakeResp(404, {"error": {"message": "model gone"}}), gemini_reply(valid_script()))
    monkeypatch.setattr(gen.httpx, "post", rec)
    generate_script("t", gemini_settings(gemini_model="old-flash", gemini_fallback_model="new-flash"))
    assert rec.calls[0]["url"].count("old-flash") == 1 and "new-flash" in rec.calls[1]["url"]


def test_gemini_both_models_missing_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(gen.httpx, "post", Recorder(FakeResp(404, {"error": {"message": "x"}}), FakeResp(404, {"error": {"message": "x"}})))
    with pytest.raises(RuntimeError, match="no fallback is left"):
        generate_script("t", gemini_settings())


def test_gemini_rate_limit_is_retried_with_backoff(monkeypatch, no_sleep):
    rec = Recorder(
        FakeResp(429, {"error": {"message": "quota"}}, headers={"retry-after": "7"}),
        FakeResp(503, {"error": {"message": "busy"}}),
        gemini_reply(valid_script()),
    )
    monkeypatch.setattr(gen.httpx, "post", rec)
    assert len(generate_script("t", gemini_settings()).scenes) == 8
    assert no_sleep == [7.0, 12.0]  # honours Retry-After, then the default backoff


def test_gemini_auth_error_is_reported_without_retry(monkeypatch, no_sleep):
    rec = Recorder(FakeResp(400, {"error": {"message": "API key not valid. Please pass a valid API key."}}))
    monkeypatch.setattr(gen.httpx, "post", rec)
    with pytest.raises(RuntimeError, match="API key not valid"):
        generate_script("t", gemini_settings())
    assert len(rec.calls) == 1 and no_sleep == []


def test_gemini_blocked_or_truncated_replies_exhaust_attempts(monkeypatch):
    blocked = FakeResp(200, {"promptFeedback": {"blockReason": "SAFETY"}})
    cut = gemini_reply('{"title": "x", "scenes": [', finish="MAX_TOKENS")
    monkeypatch.setattr(gen.httpx, "post", Recorder(blocked, cut, gemini_reply("not json at all")))
    with pytest.raises(RuntimeError, match="gemini failed to produce a valid script"):
        generate_script("t", gemini_settings())


# ----------------------------------------------------------------------------- Groq
def test_groq_request_shape_and_script(monkeypatch):
    rec = Recorder(FakeResp(200, {"choices": [{"message": {"content": json.dumps(valid_script())}}]}))
    monkeypatch.setattr(gen.httpx, "post", rec)
    script = generate_script("t", Settings(_env_file=None, llm_provider="groq", groq_api_key="q-key"))
    assert len(script.scenes) == 8
    call = rec.calls[0]
    assert call["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer q-key"
    assert call["body"]["model"] == "openai/gpt-oss-120b"
    assert call["body"]["response_format"] == {"type": "json_object"}
    system = call["body"]["messages"][0]
    assert system["role"] == "system" and '"motion_action"' in system["content"]  # schema embedded for JSON mode


# -------------------------------------------------------------------------- Anthropic
def test_anthropic_provider_still_works_via_tool_use():
    calls = []

    class Msgs:
        def create(self, model, **kw):
            calls.append((model, kw))
            block = types.SimpleNamespace(type="tool_use", id="tu1", input=valid_script())
            return types.SimpleNamespace(content=[block])

    client = types.SimpleNamespace(messages=Msgs())
    s = Settings(_env_file=None, llm_provider="anthropic", anthropic_api_key="k")
    script = generate_script("t", s, client=client)
    assert len(script.scenes) == 8
    model, kw = calls[0]
    assert model == "claude-3-7-sonnet-latest"
    assert kw["tool_choice"] == {"type": "tool", "name": "submit_script"}
    assert "submit_script" in kw["system"]


def test_anthropic_feedback_uses_tool_result():
    replies = []
    bad = valid_script()
    bad["scenes"] = bad["scenes"][:2]

    class Msgs:
        def __init__(self):
            self.queue = [bad, valid_script()]

        def create(self, model, **kw):
            replies.append(copy.deepcopy(kw["messages"]))
            return types.SimpleNamespace(content=[types.SimpleNamespace(type="tool_use", id="tu", input=self.queue.pop(0))])

    client = types.SimpleNamespace(messages=Msgs())
    generate_script("t", Settings(_env_file=None, llm_provider="anthropic", anthropic_api_key="k"), client=client)
    last = replies[1][-1]["content"][0]
    assert last["type"] == "tool_result" and last["is_error"] and last["tool_use_id"] == "tu"


# ------------------------------------------------------------------ --script-file flow
@pytest.fixture
def cli(tmp_path, monkeypatch):
    """Run the real pipeline CLI offline: fake TTS + fake whisper, output into tmp_path."""
    from typer.testing import CliRunner

    from engine import pipeline
    from engine.audio_engine import WordTiming
    from engine.config import get_settings

    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("SFX_DIR", str(tmp_path / "sfx"))
    for var in ("GEMINI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()

    def boom(*a, **k):
        raise AssertionError("the LLM must not be called when --script-file is given")

    def fake_tts(text, out_path, settings=None):
        out_path.write_bytes(b"ID3")
        return out_path

    def fake_words(audio_path, settings=None):
        words, t = [], 0.0
        for w in ScriptSpec.model_validate(valid_script()).narration.split():
            words.append(WordTiming(text=w, start=round(t, 3), end=round(t + 0.3, 3)))
            t += 0.33
        return words, t

    monkeypatch.setattr(pipeline, "generate_script", boom)
    monkeypatch.setattr(pipeline, "synthesize_audio", fake_tts)
    monkeypatch.setattr(pipeline, "align_words", fake_words)
    yield CliRunner(), pipeline.app, tmp_path
    get_settings.cache_clear()


def test_script_file_runs_without_any_llm_key(cli):
    runner, app, tmp = cli
    path = tmp / "my_script.json"
    path.write_text(json.dumps(valid_script()), encoding="utf-8")
    result = runner.invoke(app, ["--script-file", str(path)])
    assert result.exit_code == 0, result.output
    assert "Script (from file)" in result.output and "8 scenes" in result.output
    run_dir = next((tmp / "out").iterdir())
    props = json.loads((run_dir / "input-props.json").read_text(encoding="utf-8"))
    assert [s["motion"]["type"] for s in props["scenes"][:4]] == ["MINIMAL_TEXT", "COUNTER", "SEARCH", "CHART"]
    assert {"script.json", "voice.mp3", "timeline.json", "whisper_words.json"} <= {p.name for p in run_dir.iterdir()}


def test_script_file_warns_about_quality_but_still_runs(cli):
    runner, app, tmp = cli
    data = valid_script()
    for s in data["scenes"]:
        s["motion_action"] = {"type": "MINIMAL_TEXT", "headline": "H"}  # one type everywhere
    path = tmp / "mono.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    result = runner.invoke(app, ["--script-file", str(path)])
    assert result.exit_code == 0 and "warning:" in result.output and "different" in result.output


def test_script_file_missing_or_invalid_gives_clean_errors(cli):
    runner, app, tmp = cli
    missing = runner.invoke(app, ["--script-file", str(tmp / "nope.json")])
    # click validates the path before the command runs: usage error (exit 2), no traceback, no LLM call
    assert missing.exit_code == 2 and isinstance(missing.exception, SystemExit)
    assert "Error" in missing.output and "Traceback" not in missing.output

    old_format = tmp / "old.json"  # an old-style script: visual_prompt instead of motion_action
    old_format.write_text(json.dumps({"title": "t", "hook": "h", "loop_transition_text": "x",
                                      "scenes": [{"id": 1, "text": "a", "duration_estimate": 1, "visual_prompt": "photo"}]}), encoding="utf-8")
    invalid = runner.invoke(app, ["--script-file", str(old_format)])
    assert invalid.exit_code != 0 and "not a valid script file" in str(invalid.exception) + invalid.output
    assert "motion_action" in str(invalid.exception) + invalid.output

    garbage = tmp / "garbage.json"
    garbage.write_text("{not json", encoding="utf-8")
    assert runner.invoke(app, ["--script-file", str(garbage)]).exit_code != 0
