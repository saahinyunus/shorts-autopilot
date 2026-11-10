/**
 * Props contract between the Python engine (renderer.py -> input-props.json)
 * and the Remotion composition. All file paths are relative to the
 * Remotion public dir (the per-run output folder).
 */

export type SfxName = 'whoosh' | 'pop' | 'riser' | 'sub_drop';

export type WordTiming = {
  /** Display text (script spelling + punctuation when it could be aligned). */
  text: string;
  /** Seconds from start of the audio. */
  start: number;
  end: number;
};

/** What each scene animates. Mirrors the pydantic models in engine/generator.py. */
export type MotionAction =
  | {
      type: 'COUNTER';
      target_number: number;
      prefix: string;
      suffix: string;
      label: string;
    }
  | {type: 'CHART'; trend: 'UP' | 'DOWN'; label: string}
  | {type: 'DOCUMENT'; title: string; highlight_text: string; is_redacted: boolean}
  | {type: 'SEARCH'; query_text: string; result_headline: string}
  | {type: 'MINIMAL_TEXT'; headline: string; subtext: string};

export type SceneProps = {
  id: number;
  text: string;
  /** Seconds. Scenes are contiguous: scene[i].end === scene[i+1].start. */
  start: number;
  end: number;
  motion: MotionAction;
};

export type SfxCue = {
  name: SfxName;
  /** File path relative to the public dir, e.g. "sfx/whoosh.wav". */
  file: string;
  /** Trigger time in seconds (may be fractional; rounded to a frame). */
  at: number;
  volume?: number;
};

export type VideoProps = {
  title: string;
  fps: number;
  /** Total length in seconds (voice-over + short tail). */
  durationInSeconds: number;
  /** Voice-over path relative to the public dir. */
  audioFile: string;
  scenes: SceneProps[];
  words: WordTiming[];
  sfx: SfxCue[];
  /** Highlight colour (active subtitle word, highlighter, accents). */
  accentColor: string;
  /** Fade the last scene out so the Short loops seamlessly into scene 1. */
  seamlessLoop: boolean;
};

export const VIDEO_WIDTH = 1080;
export const VIDEO_HEIGHT = 1920;
