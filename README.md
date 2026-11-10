# shorts-automation-engine

One command turns a topic into a 30-50 second, vertical (1080x1920, 60 FPS), English YouTube Short in the
style of Vox / Polymatter / Magnates Media: curiosity hook, AI voice-over, word-by-word kinetic subtitles,
**code-drawn motion graphics** (counters, self-drawing charts, classified documents, search bars), sound
effects and a seamless-loop ending. Optionally it uploads the result.

**No image or stock-footage APIs are used.** Every visual is a React/SVG animation rendered by Remotion.

```
topic ──LLM (Gemini)──▶ script.json (+ motion_action per scene) ──TTS provider──▶ voice.mp3 ──Whisper provider──▶ word timings
                                                                                         │
                                  input-props.json ◀────────────────────────────────────┘
                                         │
                                         └──Remotion (React)──▶ final.mp4 ──YouTube API──▶ upload
```

## Requirements

- Node.js 18+ and npm
- Python 3.11+ (tested on 3.14)
- A free **Gemini API key** (https://aistudio.google.com/apikey) to generate scripts; or none at all if you bring your own `--script-file`
- Script, voice and timestamps are free by default (Gemini + Edge TTS + local faster-whisper); Anthropic / ElevenLabs / OpenAI are optional paid upgrades
- For uploads: a Google Cloud OAuth client
- First render downloads a headless Chromium (Remotion does this automatically)

## Setup

```bash
npm install
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # Windows: copy .env.example .env   -> fill in the keys
```

### Smoke test without any API key

```bash
python engine/pipeline.py --demo --render
```

Builds a hand-written storyboard (one of every animation type) with synthetic audio and renders
`output/<run>/final.mp4`. The numbers in the demo are placeholders. A 30 s video takes roughly 5 minutes to
render at 60 FPS on a laptop CPU; set `FPS=30` for faster drafts.

## Free vs paid providers

Script, voice-over and word timestamps sit behind swappable providers. Defaults are **free**; switch to the paid
services by changing one line in `.env`:

| Setting | Free (default) | Paid |
|---|---|---|
| `LLM_PROVIDER` | `gemini` (`GEMINI_API_KEY`, free tier) or `groq` (`GROQ_API_KEY`, free tier) | `anthropic` (`ANTHROPIC_API_KEY`) |
| `TTS_PROVIDER` | `edge_tts` - Microsoft neural voices via the `edge-tts` package, no key (needs internet) | `elevenlabs` (+ `ELEVENLABS_API_KEY`) |
| `WHISPER_PROVIDER` | `faster_whisper` - runs locally, no key | `openai` (+ `OPENAI_API_KEY`) |
| LLM model | `GEMINI_MODEL=gemini-3.6-flash`, `GROQ_MODEL=llama-3.3-70b-versatile` | `ANTHROPIC_MODEL` |
| Voice | `EDGE_TTS_VOICE=en-US-ChristopherNeural` (list: `python -m edge_tts --list-voices`) | `ELEVENLABS_VOICE_ID` |
| Model | `FASTER_WHISPER_MODEL=base.en` (`tiny.en` faster, `small.en` more accurate) | `WHISPER_MODEL=whisper-1` |

- Only the key of the **selected** provider is needed; leave the others empty. A missing TTS / Whisper key stops at
  startup; a missing LLM key is reported only when a script is actually generated, so `--script-file` and `--demo`
  run with no LLM key at all.
- Gemini and Groq are called over plain HTTPS (no extra SDK). Gemini uses a server-enforced response schema, Groq JSON
  mode; every script is validated and sent back with feedback if it breaks the rules. Free tiers are rate-limited, so
  429/503 answers are retried with backoff, and a retired Gemini model id falls back to `GEMINI_FALLBACK_MODEL`.
- Google retired `gemini-1.5-flash` and `gemini-2.0-flash`; the defaults are the current 3.x Flash models. If Google
  renames them again, change `GEMINI_MODEL` in `.env`.
- faster-whisper downloads its model (base.en is roughly 140 MB) the first time it runs, then works offline.
- Both whisper providers return the same word format, so `whisper_words.json`, alignment and the video are
  provider-independent. The code lives in `engine/audio_engine.py` (`TTSProvider` / `WhisperProvider` classes,
  entry points `synthesize_audio()` and `align_words()`).
- `faster-whisper` needs `av` below version 17 (pinned in `requirements.txt`).

## Usage

```bash
# script + voice + timings + props only (cheap to inspect)
python engine/pipeline.py --topic "The Dark Psychology of Eye Contact"

# full pipeline
python engine/pipeline.py --topic "The Dark Psychology of Eye Contact" --render --upload
```

| Flag | Meaning |
|---|---|
| `--topic, -t` | Video topic |
| `--render` | Render the MP4 with Remotion |
| `--upload` | Upload to YouTube (renders first if needed). Default privacy is **private** |
| `--resume output/<run>` | Re-use a run folder; steps whose files exist are skipped (saves API credits) |
| `--script-file path.json` | Skip the LLM and use a ready-made script (needs no LLM key). Invalid files stop with a clear message; quality-rule problems are printed as warnings |
| `--voice-id`, `--privacy` | Override `.env` for one run |
| `--demo` | Offline demo (no keys) |

Typical iteration: run once without `--render`, check `output/<run>/script.json`, then
`--resume output/<run> --render` (or `--upload`).

### Check spend module by module

`engine/test_modules.py` runs each stage alone, shows a cost estimate and asks before spending:

```bash
python engine/test_modules.py script --topic "..."     # LLM only (provider from .env)
python engine/test_modules.py estimate                 # offline cost preview for output/_modtest/script.json
python engine/test_modules.py audio [--skip-tts]       # TTS + word timestamps + alignment (selected providers)
python engine/test_modules.py assets                   # SFX library + storyboard check + SFX plan (free)
```

Outputs go to `output/_modtest/`. `audio` prints the selected providers; with the free defaults it needs no API
key, shows `$0.00 (Free Local Engine)` and runs without a confirmation prompt. Paid providers show their cost and ask
first (`--yes` skips). Visuals cost nothing.

## Motion actions

Claude gives every scene a `motion_action`; `src/components/motion/MotionScene.tsx` maps it to a component.

| `type` | Parameters | Animation |
|---|---|---|
| `COUNTER` | `target_number`, `prefix`, `suffix`, `label` | Number rockets from 0 and lands with a pop SFX + screen shake (`DynamicCounter`) |
| `CHART` | `trend` (`UP`/`DOWN`), `label` | Line draws itself via `strokeDashoffset`; DOWN crashes with a red flash, sub-drop and shake (`SvgChart`) |
| `DOCUMENT` | `title`, `highlight_text`, `is_redacted` | Classified report; black censor bar is ripped off, yellow highlighter sweeps the key phrase (`DocumentReveal`) |
| `SEARCH` | `query_text`, `result_headline` | Dark search bar types the query with autocomplete, then the result slides in (`SearchMockup`) |
| `MINIMAL_TEXT` | `headline`, `subtext` | Kinetic headline (`MinimalText`) |

Other layers: `GridBackground` (drifting blueprint grid, neon glows, film grain, all loop-safe) and
`ScreenShake` (jolts background + graphics on impacts; captions stay still).

Animation event times (counter landing, chart crash, censor-bar reveal) live in
`src/types/motionTiming.json`, which **both** Remotion and `engine/asset_manager.py` read, so SFX and shakes land
exactly on the visual impact. Change a timing there and both sides follow.

## Autopilot: 2 channels x 3 videos a day on GitHub Actions

`channels/<id>.json` defines a channel (niche, tone, rules, hashtags, voice, colour, time zone and the three daily
slots). The workflow `.github/workflows/daily.yml` runs every day and, **with nobody at the computer**:

1. **plans** the next ~28 h of slots per channel and asks Gemini for fresh topics that do not repeat anything in
   `state/history.json` (`engine/daily.py plan`);
2. **produces** one video per slot in parallel (script with the channel's brief and SEO rules, voice, captions,
   render) and **uploads it scheduled**: private first, public at the slot time, with title, description,
   hashtags, tags, category, language and the AI-content disclosure (`engine/pipeline.py --channel ... --publish-at ...`);
3. **records** the result in `state/history.json` and commits it; opens an issue if anything failed.

Step-by-step setup (Google Cloud, YouTube login, GitHub secrets, first dry run) is in
[docs/OTOPILOT_KURULUM.md](docs/OTOPILOT_KURULUM.md) (Turkish). Commands:

```bash
python engine/youtube_auth.py login --channel psychology   # one-time browser login per channel
python engine/youtube_auth.py export                        # value for the GitHub secret YOUTUBE_TOKENS_JSON
python engine/daily.py plan                                 # what would be produced right now (calls Gemini)
python engine/pipeline.py --channel money --topic "..." --publish-at 2026-11-12T13:00:00Z --render   # one video, no upload
```

## Output layout

Every run gets its own folder (it is also Remotion's *public dir*, so props use relative paths):

```
output/20261110-120000-the-dark-psychology/
├── script.json          # Claude's script + motion storyboard
├── voice.mp3
├── whisper_words.json   # raw Whisper word timestamps
├── timeline.json        # aligned scene + word timings used for rendering
├── sfx/                 # only the SFX the video uses
├── input-props.json     # Remotion props
└── final.mp4
```

## How the pieces work

- **Script (`generator.py`)** - the selected LLM answers in strict JSON (tool call for Claude, response schema for
  Gemini, JSON mode for Groq). The result is
  validated with Pydantic (discriminated union for `motion_action`) plus quality rules: hook, 70-110 words,
  8-16 scenes, at least 3 motion types, no type 3x in a row, and enough narration per animation. On failure Claude
  is re-prompted with the exact problems. The last scene is the *loop transition* and leads grammatically into
  the hook. If a model returns 404 the engine falls back to the provider's `*_FALLBACK_MODEL`.
- **Alignment (`audio_engine.py`)** - the whole script is voiced in one request (smooth prosody).
  The whisper provider returns word timestamps; they are diffed against the script so scene boundaries snap to the first
  spoken word of each scene even when Whisper spells "50" as "fifty". Captions use the script's spelling.
- **Seamless loop** - the last sentence is a spoken *lead-in* ending in `:` or a dash that the hook completes. The video
  ends `TAIL_SECONDS` after the last spoken word (TTS trailing silence is cut, so there is no dead air), the voice, captions
  and last scene fade out over the grid background (which is periodic over the video length), then the hook starts again.
- **Natural pace** - `EDGE_TTS_RATE=-5%` (+8% sounded rushed); scripts are sized for ~2.5 words/s, so 70-110 words = 30-48 s.
- **SFX (`assets/sfx/`)** - drop in your own `whoosh|pop|riser|sub_drop` as `.mp3`/`.wav`/`.ogg`. Missing files
  are replaced by synthesised placeholder `.wav`s. Sync is frame-accurate: 16 ms at 60 FPS.

## YouTube upload setup

1. Google Cloud Console → create a project → enable **YouTube Data API v3**.
2. OAuth consent screen: add your Google account as a *test user*.
3. Credentials → *Create OAuth client ID* → **Desktop app** → download JSON → save as `client_secret.json` in the project root.
4. First `--upload` opens a browser for consent and stores `token.json`.

Notes:
- Whether Google locks uploads from unverified API projects to private is not stated clearly in the current docs; test it with the first real upload. If videos end up private and unscheduled, request a YouTube API audit or publish them from YouTube Studio.
- Quota: `videos.insert` costs 1 unit and is limited to 100 uploads per day, so 6 videos a day is far below the limit.
- Set the OAuth consent screen to *In production*, otherwise refresh tokens expire after 7 days.
- `YOUTUBE_SYNTHETIC_DISCLOSURE=true` marks the video as containing AI-generated media. Keep this on.

## Customising

| Want to change | Where |
|---|---|
| Script style, hook rules, motion guidance | `SYSTEM_PROMPT` in `engine/generator.py` |
| Voice / speed | `.env` (`EDGE_TTS_*` or `ELEVENLABS_*`) |
| Subtitle look, chunk size | `src/components/KineticSubtitle.tsx` |
| Animation look and timing | `src/components/motion/*`, `src/types/motionTiming.json` |
| New animation type | add a pydantic model in `generator.py`, a case in `MotionScene.tsx`, a type in `src/types/schema.ts` |
| Live preview | `npm run studio` |

## Tests

```bash
python -m pytest tests          # offline unit tests (alignment, SFX, motion schema, props, metadata)
npm run typecheck               # TypeScript
```

## Cost per video (rough)

With the default providers nothing costs money: script (Gemini free tier), voice, timestamps and visuals are free;
the price is render time. ElevenLabs (~600 characters of credit) and OpenAI Whisper (< $0.01) are optional.
