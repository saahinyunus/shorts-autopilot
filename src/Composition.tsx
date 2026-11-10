import React, {useMemo} from 'react';
import {AbsoluteFill, Audio, Sequence, interpolate, staticFile, useVideoConfig} from 'remotion';
import {KineticSubtitle} from './components/KineticSubtitle';
import {SoundEffects} from './components/SoundEffects';
import {VignetteOverlay} from './components/VignetteOverlay';
import {GridBackground} from './components/motion/GridBackground';
import {MotionScene} from './components/motion/MotionScene';
import {ScreenShake} from './components/motion/ScreenShake';
import {buildShakeTriggers} from './components/motion/timing';
import type {VideoProps} from './types/schema';

const LOOP_FADE_FRAMES = 14; // visual fade-out before the loop restarts

/**
 * 100% code-rendered video: a looping blueprint background, one motion-graphic per scene
 * (chosen by `scene.motion.type`), kinetic captions, voice-over and SFX. The background and
 * the motion layer shake together on impacts; captions stay readable.
 */
export const ShortsComposition: React.FC<VideoProps> = ({
  audioFile,
  scenes,
  words,
  sfx,
  accentColor,
  seamlessLoop,
}) => {
  const {fps, durationInFrames} = useVideoConfig();
  const shakes = useMemo(() => buildShakeTriggers(scenes, sfx), [scenes, sfx]);

  return (
    <AbsoluteFill style={{backgroundColor: '#07080B'}}>
      <ScreenShake triggers={shakes}>
        <GridBackground />
        {scenes.map((scene, i) => {
          const isLast = i === scenes.length - 1;
          const from = Math.round(scene.start * fps);
          const to = isLast ? durationInFrames : Math.round(scenes[i + 1].start * fps);
          const length = Math.max(1, to - from);
          return (
            <Sequence key={scene.id} from={from} durationInFrames={length} layout="none">
              <MotionScene
                motion={scene.motion}
                accentColor={accentColor}
                sceneFrames={length}
                fadeOut={isLast && seamlessLoop}
              />
            </Sequence>
          );
        })}
      </ScreenShake>

      <VignetteOverlay grain={0} />
      <KineticSubtitle words={words} accentColor={accentColor} endFadeFrames={seamlessLoop ? LOOP_FADE_FRAMES : 0} />
      {/* Tiny fades at both ends: no click when the Short loops from the last frame to the first. */}
      <Audio
        src={staticFile(audioFile)}
        volume={(f) =>
          seamlessLoop
            ? interpolate(f, [0, 2, durationInFrames - 8, durationInFrames - 1], [0, 1, 1, 0], {
                extrapolateLeft: 'clamp',
                extrapolateRight: 'clamp',
              })
            : 1
        }
      />
      <SoundEffects cues={sfx} fps={fps} totalFrames={durationInFrames} />
    </AbsoluteFill>
  );
};
