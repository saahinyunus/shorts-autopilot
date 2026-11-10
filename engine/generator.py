"""LLM script + motion-graphics storyboard generator (strict JSON) with swappable providers:
gemini (free, default), groq (free), anthropic (paid)."""
from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from typing import Annotated, Literal, Union

import anthropic
import httpx
from pydantic import BaseModel, Field, ValidationError, model_validator

from .config import Settings, get_settings

SfxLiteral = Literal["whoosh", "pop", "riser", "sub_drop", "none"]

# Narration pace of a "high energy" ElevenLabs voice at speed ~1.08.
WORDS_PER_SECOND = 2.5  # natural narration incl. pauses between sentences ~2.2-2.5
MIN_WORDS, MAX_WORDS = 70, 110  # ~30s .. ~48s of speech
MIN_SCENES, MAX_SCENES = 8, 16

# A scene must be on screen long enough for its animation to finish (see motionTiming.json).
MIN_WORDS_PER_SCENE = {"COUNTER": 4, "CHART": 6, "DOCUMENT": 6, "SEARCH": 6, "MINIMAL_TEXT": 1}


# --------------------------------------------------------------------- motion actions
class CounterAction(BaseModel):
    type: Literal["COUNTER"]
    target_number: float
    prefix: str = Field("", max_length=4)
    suffix: str = Field("", max_length=8)
    label: str = Field("", max_length=48)


class ChartAction(BaseModel):
    type: Literal["CHART"]
    trend: Literal["UP", "DOWN"]
    label: str = Field("", max_length=48)


class DocumentAction(BaseModel):
    type: Literal["DOCUMENT"]
    title: str = Field(max_length=40)
    highlight_text: str = Field(min_length=3, max_length=90)
    is_redacted: bool = False


class SearchAction(BaseModel):
    type: Literal["SEARCH"]
    query_text: str = Field(min_length=2, max_length=60)
    result_headline: str = Field(min_length=2, max_length=80)


class MinimalTextAction(BaseModel):
    type: Literal["MINIMAL_TEXT"]
    headline: str = Field(min_length=1, max_length=40)
    subtext: str = Field("", max_length=80)


MotionAction = Annotated[
    Union[CounterAction, ChartAction, DocumentAction, SearchAction, MinimalTextAction],
    Field(discriminator="type"),
]
MOTION_TYPES = ("COUNTER", "CHART", "DOCUMENT", "SEARCH", "MINIMAL_TEXT")


# ------------------------------------------------------------------------------ script
class SceneSpec(BaseModel):
    id: int
    text: str = Field(min_length=1)
    duration_estimate: float = Field(gt=0)
    motion_action: MotionAction
    sfx: SfxLiteral = "none"


class ScriptSpec(BaseModel):
    title: str
    hook: str
    scenes: list[SceneSpec]
    loop_transition_text: str
    # Extras used by the uploader.
    description: str = ""
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "ScriptSpec":
        if not self.scenes:
            raise ValueError("script has no scenes")
        # Normalise ids to 1..N so downstream references are predictable.
        for i, scene in enumerate(self.scenes, start=1):
            scene.id = i
        # The last scene IS the loop transition (it is spoken, so it must be in the audio).
        self.loop_transition_text = self.scenes[-1].text
        return self

    @property
    def narration(self) -> str:
        return " ".join(s.text.strip() for s in self.scenes)

    @property
    def word_count(self) -> int:
        return len(self.narration.split())


def validate_quality(script: ScriptSpec) -> list[str]:
    """Rules that Pydantic types cannot express. Returned as feedback for Claude's retry."""
    problems: list[str] = []
    wc = script.word_count
    if not MIN_WORDS <= wc <= MAX_WORDS:
        problems.append(
            f"Narration has {wc} words; it must be between {MIN_WORDS} and {MAX_WORDS} "
            f"(30-50 seconds when spoken)."
        )
    if not MIN_SCENES <= len(script.scenes) <= MAX_SCENES:
        problems.append(
            f"There are {len(script.scenes)} scenes; use between {MIN_SCENES} and {MAX_SCENES}."
        )
    if script.scenes[0].text.strip() != script.hook.strip():
        problems.append("scenes[0].text must be exactly the same string as `hook`.")
    if len(script.hook.split()) > 14:
        problems.append(f"The hook is {len(script.hook.split())} words; keep it under 14.")
    if script.hook.strip() and script.hook.strip()[-1] not in "?!.":
        problems.append("The hook must end with punctuation.")
    if script.scenes[-1].text.strip()[-1] not in ":-\u2014\u2013\u2026":
        problems.append(
            "The last scene must be a lead-in that ends with a colon (:) or a dash so the hook "
            "completes it when the video loops, e.g. 'Which brings us back to the question:'."
        )

    types = [s.motion_action.type for s in script.scenes]
    if len(set(types)) < 3:
        problems.append(f"Only {len(set(types))} motion type(s) used; use at least 3 different ones.")
    for i in range(len(types) - 2):
        if types[i] == types[i + 1] == types[i + 2]:
            problems.append(f"Scenes {i + 1}-{i + 3} all use {types[i]}; never repeat a type 3 times in a row.")
            break
    for scene in script.scenes:
        need = MIN_WORDS_PER_SCENE[scene.motion_action.type]
        if len(scene.text.split()) < need:
            problems.append(
                f"Scene {scene.id} uses {scene.motion_action.type} but has only "
                f"{len(scene.text.split())} words; that animation needs >= {need} words of narration."
            )
    return problems


SYSTEM_PROMPT_BASE = f"""You are a viral YouTube Shorts scriptwriter and motion-graphics director in the style of
Vox, Polymatter and Magnates Media. You write faceless, high-tempo English narration (30-50 seconds) and,
for every sentence, choose the on-screen motion graphic that visualises it. There are NO photos or stock
footage: every visual is an animated graphic built from code.

HARD RULES
1. HOOK: The very first sentence (scene 1) must create instant curiosity in under 1.5 seconds of
   speech: a direct question or a bold, slightly controversial claim. No greetings, no "Did you
   know", no "In this video". Under 12 words.
2. PACE: {MIN_WORDS}-{MAX_WORDS} words of narration in total. Short, punchy sentences. Zero filler
   (no "basically", "actually", "so", "well"). Every sentence adds a fact, raises tension or pays
   something off. Use a pattern interrupt every 2-3 sentences.
3. SEAMLESS LOOP: The video restarts right after the last sentence, so the LAST sentence must be a
   spoken LEAD-IN that the hook then completes or answers. End it with a colon or a dash so the voice
   hangs in the air, e.g. "Which brings us right back to the question:" -> hook "Why do you look
   away the second someone stares back?" Other lead-ins: "And that is exactly why:", "So ask yourself
   again:", "Now you know the real reason:". The last scene must NOT be a conclusion or summary and
   must not end with a full stop. Never write a goodbye or call to action ("subscribe", "follow",
   "thanks for watching").
4. SCENES: {MIN_SCENES}-{MAX_SCENES} scenes, each one sentence (occasionally two short ones).
5. MOTION_ACTION: every scene gets exactly one `motion_action` object with a `type` and the
   parameters of that type. The animation is drawn live, so keep all on-screen text SHORT.
   - COUNTER: a number rockets from 0 to `target_number`. Params: target_number (number), prefix
     (e.g. "$", max 4 chars), suffix (e.g. "%", "x", "M", " SEC", max 8 chars), label (<= 48 chars,
     says what the number measures). Use when the sentence states a statistic or quantity.
     The narration for this scene needs >= 4 words.
   - CHART: a line chart draws itself. Params: trend ("UP" for growth/rise, "DOWN" for a crash/decline/
     collapse) and label (<= 48 chars, e.g. "TRUST IN INSTITUTIONS"). Narration >= 6 words.
   - DOCUMENT: a classified-style report; `highlight_text` (<= 90 chars) is the key phrase that gets
     highlighted in yellow. Params: title (<= 40 chars, e.g. "CASE FILE: GAZE"), highlight_text,
     is_redacted (true = the phrase is hidden under a black censor bar and revealed at the dramatic
     moment; use for secrets / reveals). Narration >= 6 words.
   - SEARCH: a dark search box types a query then shows a result. Params: query_text (<= 60 chars,
     what a curious person would type), result_headline (<= 80 chars, the answer). Use for questions
     and "what people ask". Narration >= 6 words.
   - MINIMAL_TEXT: kinetic headline text. Params: headline (<= 40 chars, 1-5 words, ALL the punch),
     subtext (<= 80 chars, optional). Use for the hook, transitions and the final line.
   Variety matters: use at least 3 different types, never the same type 3 scenes in a row, and make
   scene 1 (the hook) MINIMAL_TEXT, SEARCH or COUNTER. On-screen text must complement the narration,
   not repeat it word for word. NEVER invent statistics: only use numbers that are real and widely
   documented, or approximate ones that you phrase carefully in the narration ("about", "roughly").
   If you have no defensible number, use MINIMAL_TEXT or SEARCH instead of COUNTER.
6. SFX per scene (placed at the scene's first word): one of "whoosh" (transition / new idea),
   "pop" (a punchy fact), "riser" (tension before a reveal), "sub_drop" (the big reveal / shocking
   line), or "none". Use sfx on at most ~60% of scenes; scene 1 should use "whoosh" or "sub_drop".
   Never put the same sfx on two consecutive scenes. (COUNTER, DOCUMENT-redacted and CHART-DOWN
   scenes already trigger their own impact sounds at the animation's peak.)
7. `duration_estimate` = word_count / {WORDS_PER_SECOND} seconds, rounded to 0.1.
8. SEO (this is how the Short gets found):
   - `title`: <= 70 characters. Put the main search keyword in the first 40 characters, then a curiosity
     gap. No ALL CAPS, no emoji spam, no false promises. The title must match what the video delivers.
   - `description`: line 1 is a single sentence (<= 120 chars) containing the main keyword that restates
     the hook; line 2-3 are two short sentences with secondary keywords that a viewer would search;
     then ONE blank line and 3-5 hashtags, the first being #Shorts.
   - `tags`: 8-12 lowercase search phrases (2-4 words each), most important first, no hashtags.
9. Facts must be defensible. If a claim is contested, phrase it carefully ("studies suggest").
   Never state a precise number or study result unless you are sure it is real and widely documented.
10. The narration is read aloud by a text-to-speech engine: write numbers, years, money and symbols the way
    they are SPOKEN ("nineteen twenty", "four dollars and fifty cents", "three percent"), never as digits or
    symbols, and avoid abbreviations.
11. A CHANNEL BRIEF may follow the topic. Follow its tone and obey its rules and exclusions."""


SUBMIT_SCRIPT_TOOL = {
    "name": "submit_script",
    "description": "Submit the finished Shorts script and its motion-graphics storyboard.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "hook": {"type": "string", "description": "Identical to scenes[0].text"},
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "text": {"type": "string", "description": "Sentence spoken in this scene"},
                        "duration_estimate": {"type": "number"},
                        "motion_action": {
                            "type": "object",
                            "description": "The animation for this scene. Fill only the fields of the chosen type.",
                            "properties": {
                                "type": {"type": "string", "enum": list(MOTION_TYPES)},
                                "target_number": {"type": "number", "description": "COUNTER"},
                                "prefix": {"type": "string", "description": "COUNTER, e.g. $"},
                                "suffix": {"type": "string", "description": "COUNTER, e.g. %"},
                                "label": {"type": "string", "description": "COUNTER or CHART"},
                                "trend": {"type": "string", "enum": ["UP", "DOWN"], "description": "CHART"},
                                "title": {"type": "string", "description": "DOCUMENT"},
                                "highlight_text": {"type": "string", "description": "DOCUMENT"},
                                "is_redacted": {"type": "boolean", "description": "DOCUMENT"},
                                "query_text": {"type": "string", "description": "SEARCH"},
                                "result_headline": {"type": "string", "description": "SEARCH"},
                                "headline": {"type": "string", "description": "MINIMAL_TEXT"},
                                "subtext": {"type": "string", "description": "MINIMAL_TEXT"},
                            },
                            "required": ["type"],
                        },
                        "sfx": {
                            "type": "string",
                            "enum": ["whoosh", "pop", "riser", "sub_drop", "none"],
                        },
                    },
                    "required": ["id", "text", "duration_estimate", "motion_action", "sfx"],
                },
            },
            "loop_transition_text": {
                "type": "string",
                "description": "Identical to the last scene's text; it leads back into the hook",
            },
            "description": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "hook", "scenes", "loop_transition_text"],
    },
}


def _create_message(client: anthropic.Anthropic, models: list[str], state: dict, **kwargs):
    """messages.create with automatic fallback to the next model when one returns 404."""
    while True:
        model = models[state["i"]]
        try:
            return client.messages.create(model=model, **kwargs)
        except anthropic.NotFoundError as exc:
            if state["i"] + 1 >= len(models):
                raise RuntimeError(
                    f"Model '{model}' was not found and no fallback is left. "
                    f"Set ANTHROPIC_MODEL to a model your account can use."
                ) from exc
            print(f"[generator] model '{model}' unavailable ({exc}) -> falling back to '{models[state['i'] + 1]}'")
            state["i"] += 1

# ================================================================== LLM providers
# One strict-JSON script, three interchangeable back-ends:
#   gemini (default, free tier) | groq (free tier) | anthropic (paid)
# Each provider keeps its own conversation so a rejected script can be revised with feedback.

TOOL_SUFFIX = "\n\nReturn the result ONLY by calling the `submit_script` tool."
JSON_SUFFIX = (
    "\n\nReturn the result ONLY as one JSON object (no markdown fences, no commentary) that "
    "matches the schema."
)
SYSTEM_PROMPT = SYSTEM_PROMPT_BASE + TOOL_SUFFIX  # tool-calling providers (anthropic)
SCRIPT_SCHEMA = SUBMIT_SCRIPT_TOOL["input_schema"]

# Which parameters each motion type takes, and which are mandatory.
MOTION_FIELDS = {
    "COUNTER": (["target_number", "prefix", "suffix", "label"], ["target_number", "label"]),
    "CHART": (["trend", "label"], ["trend", "label"]),
    "DOCUMENT": (["title", "highlight_text", "is_redacted"], ["title", "highlight_text", "is_redacted"]),
    "SEARCH": (["query_text", "result_headline"], ["query_text", "result_headline"]),
    "MINIMAL_TEXT": (["headline", "subtext"], ["headline"]),
}


def _build_strict_schema() -> dict:
    """Same script schema, but `motion_action` is a per-type `anyOf` with the right REQUIRED fields.
    The flat tool schema only requires `type`, so schema-enforcing models (Gemini) would otherwise
    omit e.g. DOCUMENT.highlight_text. Used for Gemini (responseSchema) and Groq (prompt)."""
    schema = json.loads(json.dumps(SCRIPT_SCHEMA))
    scene = schema["properties"]["scenes"]["items"]
    flat = scene["properties"]["motion_action"]["properties"]
    variants = []
    for motion_type, (fields, required) in MOTION_FIELDS.items():
        props = {"type": {"type": "string", "enum": [motion_type]}}
        props.update({f: {k: v for k, v in flat[f].items() if k != "description"} for f in fields})
        variants.append({"type": "object", "properties": props, "required": ["type", *required]})
    scene["properties"]["motion_action"] = {"anyOf": variants}
    return schema


STRICT_SCRIPT_SCHEMA = _build_strict_schema()


class LLMOutputError(Exception):
    """The provider answered, but not with a usable script object (retried with feedback)."""


def _user_prompt(topic: str, tool: bool, brief: str = "") -> str:
    ask = "Call `submit_script`." if tool else "Return the JSON object."
    brief_block = f"\n\nCHANNEL BRIEF:\n{brief}" if brief else ""
    return f"Topic: {topic}{brief_block}\n\nWrite the Short now. {ask}"


def _feedback_prompt(problems: list[str]) -> str:
    return "Rejected. Fix these problems and resubmit the FULL script:\n- " + "\n- ".join(problems)


def _drop_nulls(obj):
    """Some models emit `null` for fields that do not apply to the chosen motion type."""
    if isinstance(obj, dict):
        return {k: _drop_nulls(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_drop_nulls(v) for v in obj]
    return obj


def _parse_json_object(text: str) -> dict:
    cleaned = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip())
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMOutputError(f"The reply was not valid JSON ({exc}). Reply with a single JSON object.") from exc
    if not isinstance(data, dict):
        raise LLMOutputError("The reply must be a single JSON object, not a list or string.")
    return data


class ModelNotFound(Exception):
    """The model cannot be used (HTTP 404, or its daily free quota is used up): try the next model."""


class DailyQuotaExhausted(ModelNotFound):
    """HTTP 429 for a per-day quota. Waiting is pointless (it resets hours later); another model has its own quota."""


RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_DELAYS = (4.0, 12.0, 30.0)  # free tiers rate-limit hard; wait and retry politely


def _post_json(url: str, headers: dict, body: dict, provider: str) -> dict:
    """POST JSON with retry on rate limits / transient errors. Raises ModelNotFound on 404."""
    for attempt in range(len(RETRY_DELAYS) + 1):
        response = httpx.post(url, headers=headers, json=body, timeout=180)
        if response.status_code == 200:
            return response.json()
        if response.status_code == 404:
            raise ModelNotFound(_error_message(response))
        if response.status_code == 429 and re.search(r"per ?day", response.text, re.IGNORECASE):
            raise DailyQuotaExhausted("daily free quota used up")
        if response.status_code in RETRY_STATUS and attempt < len(RETRY_DELAYS):
            try:
                wait = min(60.0, float(response.headers.get("retry-after", "")))
            except ValueError:
                wait = RETRY_DELAYS[attempt]
            print(f"[generator] {provider} HTTP {response.status_code}; retrying in {wait:.0f}s")
            time.sleep(wait)
            continue
        raise RuntimeError(f"{provider} API error {response.status_code}: {_error_message(response)}")
    raise RuntimeError(f"{provider} API kept failing after retries.")  # pragma: no cover


def _error_message(response: httpx.Response) -> str:
    try:
        err = response.json().get("error", {})
        return str(err.get("message") if isinstance(err, dict) else err)[:300]
    except Exception:  # noqa: BLE001 - body may not be JSON
        return response.text[:300]


def _with_fallback(models: list[str], state: dict, call):
    """Run `call(model)`; on a 404 move to the next model in `models` (sticky for later calls)."""
    while True:
        model = models[state["i"]]
        try:
            return call(model)
        except ModelNotFound as exc:
            if state["i"] + 1 >= len(models):
                raise RuntimeError(
                    f"Model '{model}' is unavailable ({exc}) and no fallback is left. "
                    f"Check the model name in .env or the quota of your API key."
                ) from exc
            print(f"[generator] model '{model}' unavailable ({exc}) -> falling back to '{models[state['i'] + 1]}'")
            state["i"] += 1


class LLMProvider(ABC):
    name: str
    is_free: bool

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    @abstractmethod
    def model_label(self) -> str: ...

    @abstractmethod
    def propose(self, topic: str, brief: str = "") -> dict:
        """First attempt: return the raw script object (may raise LLMOutputError)."""

    @abstractmethod
    def revise(self, problems: list[str]) -> dict:
        """Next attempt: same conversation plus feedback on what was wrong."""

    @abstractmethod
    def cost_summary(self) -> str: ...

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        """One-shot structured answer for helper tasks (topic planning). Raises LLMOutputError."""
        raise NotImplementedError(f"{self.name} does not support complete_json")


class AnthropicProvider(LLMProvider):
    """Claude via forced tool use (strict JSON). Paid."""

    name = "anthropic"
    is_free = False

    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None):
        super().__init__(settings)
        settings.require("anthropic_api_key")
        self.client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.models = [settings.anthropic_model]
        if settings.anthropic_fallback_model and settings.anthropic_fallback_model not in self.models:
            self.models.append(settings.anthropic_fallback_model)
        self.state = {"i": 0}
        self.messages: list[dict] = []
        self._pending: tuple | None = None  # (assistant content, tool_use id) awaiting feedback

    @property
    def model_label(self) -> str:
        return f"{self.settings.anthropic_model} (fallback: {self.settings.anthropic_fallback_model})"

    def cost_summary(self) -> str:
        return "paid: ~2,400 input + ~2,800 output tokens per attempt (max 3 attempts)"

    def _call(self) -> dict:
        response = _create_message(
            self.client,
            self.models,
            self.state,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=[SUBMIT_SCRIPT_TOOL],
            tool_choice={"type": "tool", "name": "submit_script"},
            messages=self.messages,
        )
        tool_use = next((b for b in response.content if b.type == "tool_use"), None)
        if tool_use is None:
            self._pending = None
            raise LLMOutputError("You did not call the `submit_script` tool. Call it with the script.")
        self._pending = (response.content, tool_use.id)
        return tool_use.input

    def propose(self, topic: str, brief: str = "") -> dict:
        self.messages = [{"role": "user", "content": _user_prompt(topic, True, brief)}]
        return self._call()

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        tool = {"name": "submit_json", "description": "Submit the answer.", "input_schema": schema}
        response = _create_message(
            self.client, self.models, self.state, max_tokens=4096, system=system, tools=[tool],
            tool_choice={"type": "tool", "name": "submit_json"}, messages=[{"role": "user", "content": user}],
        )
        block = next((b for b in response.content if b.type == "tool_use"), None)
        if block is None:
            raise LLMOutputError("The model did not call submit_json.")
        return block.input

    def revise(self, problems: list[str]) -> dict:
        if self._pending is not None:
            content, tool_id = self._pending
            self.messages.append({"role": "assistant", "content": content})
            self.messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "is_error": True,
                            "content": _feedback_prompt(problems),
                        }
                    ],
                }
            )
        else:  # the previous reply had no tool call; just nudge again
            self.messages.append({"role": "user", "content": _feedback_prompt(problems)})
        return self._call()


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _gemini_schema(node):
    """Gemini's responseSchema uses upper-case OpenAPI type names."""
    if isinstance(node, dict):
        return {k: (v.upper() if k == "type" and isinstance(v, str) else _gemini_schema(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [_gemini_schema(v) for v in node]
    return node


class GeminiProvider(LLMProvider):
    """Google Gemini over REST with a response schema (server-side enforced JSON). Free tier."""

    name = "gemini"
    is_free = True

    def __init__(self, settings: Settings):
        super().__init__(settings)
        settings.require("gemini_api_key")
        self.models = [settings.gemini_model]
        if settings.gemini_fallback_model and settings.gemini_fallback_model not in self.models:
            self.models.append(settings.gemini_fallback_model)
        self.state = {"i": 0}
        self.contents: list[dict] = []
        self._last_text: str | None = None

    @property
    def model_label(self) -> str:
        return f"{self.settings.gemini_model} (fallback: {self.settings.gemini_fallback_model})"

    def cost_summary(self) -> str:
        return "$0.00 (Gemini free tier; rate-limited, requests are retried automatically)"

    def _call(self) -> dict:
        return self._generate(SYSTEM_PROMPT_BASE + JSON_SUFFIX, self.contents, STRICT_SCRIPT_SCHEMA)

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        return self._generate(system, [{"role": "user", "parts": [{"text": user}]}], schema)

    def _generate(self, system: str, contents: list[dict], schema: dict) -> dict:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": _gemini_schema(schema),
                "maxOutputTokens": 16384,  # thinking models spend part of this budget
            },
        }
        headers = {"x-goog-api-key": self.settings.gemini_api_key, "Content-Type": "application/json"}
        data = _with_fallback(
            self.models,
            self.state,
            lambda model: _post_json(GEMINI_URL.format(model=model), headers, body, "Gemini"),
        )

        self._last_text = None
        block = (data.get("promptFeedback") or {}).get("blockReason")
        if block:
            raise LLMOutputError(f"Gemini blocked the prompt ({block}).")
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMOutputError("Gemini returned no candidates.")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if candidates[0].get("finishReason") == "MAX_TOKENS" and not text.strip().endswith("}"):
            raise LLMOutputError("The reply was cut off (token limit). Write a more compact script.")
        self._last_text = text
        return _parse_json_object(text)

    def propose(self, topic: str, brief: str = "") -> dict:
        self.contents = [{"role": "user", "parts": [{"text": _user_prompt(topic, False, brief)}]}]
        return self._call()

    def revise(self, problems: list[str]) -> dict:
        if self._last_text:
            self.contents.append({"role": "model", "parts": [{"text": self._last_text}]})
        self.contents.append({"role": "user", "parts": [{"text": _feedback_prompt(problems)}]})
        return self._call()


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider(LLMProvider):
    """Groq's OpenAI-compatible chat API in JSON mode. Free tier."""

    name = "groq"
    is_free = True

    def __init__(self, settings: Settings):
        super().__init__(settings)
        settings.require("groq_api_key")
        self.state = {"i": 0}
        self.messages: list[dict] = []
        self._last_text: str | None = None

    @property
    def model_label(self) -> str:
        return self.settings.groq_model

    def cost_summary(self) -> str:
        return "$0.00 (Groq free tier; rate-limited, requests are retried automatically)"

    def _call(self) -> dict:
        return self._chat(self.messages)

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        system = system + JSON_SUFFIX + "\n\nJSON schema:\n" + json.dumps(schema)
        return self._chat([{"role": "system", "content": system}, {"role": "user", "content": user}])

    def _chat(self, messages: list[dict]) -> dict:
        body = {
            "model": self.settings.groq_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.8,
            "max_tokens": 6000,
        }
        headers = {"Authorization": f"Bearer {self.settings.groq_api_key}", "Content-Type": "application/json"}
        data = _with_fallback([self.settings.groq_model], self.state, lambda _m: _post_json(GROQ_URL, headers, body, "Groq"))
        choices = data.get("choices") or []
        if not choices:
            raise LLMOutputError("Groq returned no choices.")
        self._last_text = (choices[0].get("message") or {}).get("content") or ""
        return _parse_json_object(self._last_text)

    def propose(self, topic: str, brief: str = "") -> dict:
        system = SYSTEM_PROMPT_BASE + JSON_SUFFIX + "\n\nJSON schema:\n" + json.dumps(STRICT_SCRIPT_SCHEMA)
        self.messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": _user_prompt(topic, False, brief)},
        ]
        return self._call()

    def revise(self, problems: list[str]) -> dict:
        if self._last_text:
            self.messages.append({"role": "assistant", "content": self._last_text})
        self.messages.append({"role": "user", "content": _feedback_prompt(problems)})
        return self._call()


LLM_PROVIDERS: dict[str, type[LLMProvider]] = {
    GeminiProvider.name: GeminiProvider,
    GroqProvider.name: GroqProvider,
    AnthropicProvider.name: AnthropicProvider,
}


class FailoverProvider(LLMProvider):
    """Tries the providers in order. When one is unreachable (quota, outage, bad key) the next one takes over
    and restarts the conversation, so a single provider's rate limit never costs a video."""

    def __init__(self, providers: list[LLMProvider]):
        if not providers:
            raise ValueError("FailoverProvider needs at least one provider")
        super().__init__(providers[0].settings)
        self.providers = providers
        self.index = 0
        self._topic = ""
        self._brief = ""

    @property
    def name(self) -> str:  # type: ignore[override]
        return self.providers[self.index].name

    @property
    def is_free(self) -> bool:  # type: ignore[override]
        return all(p.is_free for p in self.providers)

    @property
    def model_label(self) -> str:
        return " -> ".join(f"{p.name}:{p.model_label}" for p in self.providers)

    def cost_summary(self) -> str:
        return " | ".join(f"{p.name}: {p.cost_summary()}" for p in self.providers)

    def _failover(self, exc: Exception) -> None:
        if self.index + 1 >= len(self.providers):
            raise RuntimeError(f"All LLM providers failed; the last error ({self.name}): {exc}") from exc
        print(f"[generator] {self.name} failed ({str(exc)[:160]}); switching to {self.providers[self.index + 1].name}")
        self.index += 1

    def propose(self, topic: str, brief: str = "") -> dict:
        self._topic, self._brief = topic, brief
        while True:
            try:
                return self.providers[self.index].propose(topic, brief)
            except LLMOutputError:
                raise
            except Exception as exc:  # noqa: BLE001 - any API/network failure moves on to the next provider
                self._failover(exc)

    def revise(self, problems: list[str]) -> dict:
        try:
            return self.providers[self.index].revise(problems)
        except LLMOutputError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._failover(exc)
        return self.propose(self._topic, self._brief)  # the new provider has no conversation yet

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        while True:
            try:
                return self.providers[self.index].complete_json(system, user, schema)
            except LLMOutputError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._failover(exc)


def _build_one(name: str, settings: Settings, client: anthropic.Anthropic | None) -> LLMProvider:
    cls = LLM_PROVIDERS[name]
    return AnthropicProvider(settings, client) if cls is AnthropicProvider else cls(settings)


def get_llm_provider(settings: Settings | None = None, client: anthropic.Anthropic | None = None) -> LLMProvider:
    """The provider selected by LLM_PROVIDER, backed by every other provider whose API key is set
    (order: selected, then gemini, groq, anthropic), so one provider's outage or quota never stops a video."""
    settings = settings or get_settings()
    if settings.llm_provider not in LLM_PROVIDERS:
        raise RuntimeError(f"Unknown LLM_PROVIDER '{settings.llm_provider}'. Options: {', '.join(LLM_PROVIDERS)}")
    primary = _build_one(settings.llm_provider, settings, client)
    if not settings.llm_failover:
        return primary
    backups = []
    for name in ("gemini", "groq", "anthropic"):
        if name != settings.llm_provider and getattr(settings, f"{name}_api_key", ""):
            backups.append(_build_one(name, settings, client if name == "anthropic" else None))
    return FailoverProvider([primary, *backups]) if backups else primary


def generate_script(
    topic: str,
    settings: Settings | None = None,
    max_attempts: int = 3,
    client: anthropic.Anthropic | None = None,
    provider: LLMProvider | None = None,
    brief: str = "",
) -> ScriptSpec:
    """Ask the selected LLM for a script (`brief` = channel context: niche, tone, rules); validate it and retry with feedback if it breaks the rules."""
    settings = settings or get_settings()
    provider = provider or get_llm_provider(settings, client)
    last_error = "unknown"
    problems: list[str] = []

    for attempt in range(1, max_attempts + 1):
        script = None
        try:
            raw = provider.propose(topic, brief) if attempt == 1 else provider.revise(problems)
            try:
                script = ScriptSpec.model_validate(_drop_nulls(raw))
                problems = validate_quality(script)
            except ValidationError as exc:
                problems = [
                    f"Schema error at {'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                    for e in exc.errors()[:5]
                ]
        except LLMOutputError as exc:
            problems = [str(exc)]

        if script is not None and not problems:
            return script
        last_error = "; ".join(problems)
        print(f"[generator] attempt {attempt}/{max_attempts} rejected ({provider.name}): {last_error}")

    raise RuntimeError(f"{provider.name} failed to produce a valid script: {last_error}")


def slugify(text: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].strip("-") or "short"
