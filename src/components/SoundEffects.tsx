import React from 'react';
import {Audio, Sequence, staticFile} from 'remotion';
import type {SfxCue} from '../types/schema';

/**
 * Fires every SFX cue on its frame. Remotion audio is frame-accurate: at 30 fps that is
 * 33 ms resolution (16 ms at 60 fps). Set FPS=60 in .env for finer sync.
 */
export const SoundEffects: React.FC<{cues: SfxCue[]; fps: number; totalFrames: number}> = ({
  cues,
  fps,
  totalFrames,
}) => (
  <>
    {cues.map((cue, i) => {
      const from = Math.max(0, Math.round(cue.at * fps));
      if (from >= totalFrames) return null;
      return (
        <Sequence key={`${cue.file}-${i}`} from={from} layout="none">
          <Audio src={staticFile(cue.file)} volume={cue.volume ?? 0.6} />
        </Sequence>
      );
    })}
  </>
);
