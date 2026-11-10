import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';

export type ShakeTrigger = {
  /** Global time in seconds. */
  at: number;
  /** 0..1 */
  intensity: number;
};

const SHAKE_SECONDS = 0.4;
const MAX_OFFSET_PX = 34;

/**
 * Jolts everything inside it for ~0.4s after each trigger (decaying amplitude, deterministic
 * so renders are reproducible). Overlapping triggers do not add up: the strongest wins.
 */
export const ScreenShake: React.FC<{triggers: ShakeTrigger[]; children: React.ReactNode}> = ({
  triggers,
  children,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;

  let amp = 0;
  for (const trigger of triggers) {
    const dt = t - trigger.at;
    if (dt >= 0 && dt < SHAKE_SECONDS) {
      amp = Math.max(amp, trigger.intensity * (1 - dt / SHAKE_SECONDS) ** 2);
    }
  }

  const x = Math.sin(frame * 2.3 + 1) * amp * MAX_OFFSET_PX;
  const y = Math.cos(frame * 3.1) * amp * MAX_OFFSET_PX * 0.8;
  const rotate = Math.sin(frame * 1.7) * amp * 1.4;
  // Slight zoom hides the edges that the offset would otherwise reveal.
  const scale = 1 + amp * 0.05;

  return (
    <AbsoluteFill
      style={{transform: `translate(${x}px, ${y}px) rotate(${rotate}deg) scale(${scale})`}}
    >
      {children}
    </AbsoluteFill>
  );
};
