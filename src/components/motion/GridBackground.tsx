import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {COLORS} from '../../fonts';

const MINOR = 54;
const MAJOR = MINOR * 5;

/**
 * Dark blueprint background: drifting grid, two orbiting neon glows and film grain.
 * Every motion is a whole number of cycles over the video length, so the last frame flows
 * into the first (seamless Shorts loop).
 */
export const GridBackground: React.FC<{glow?: string; glow2?: string}> = ({
  glow = COLORS.cyan,
  glow2 = '#8B5CF6',
}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const loop = frame / durationInFrames; // 0..1
  const theta = loop * Math.PI * 2;

  // Whole multiples of the cell size => the pattern at loop=1 equals loop=0.
  const shiftX = loop * MAJOR * 2;
  const shiftY = loop * MAJOR;

  const g1 = {x: 50 + 30 * Math.cos(theta), y: 28 + 12 * Math.sin(theta)};
  const g2 = {x: 50 - 32 * Math.cos(theta), y: 64 - 10 * Math.sin(theta)};
  const pulse = 0.8 + 0.2 * Math.sin(theta * 8);

  const line = (alpha: number) => `rgba(120, 200, 255, ${alpha})`;
  const mask = 'radial-gradient(ellipse at 50% 42%, #000 25%, transparent 82%)';

  return (
    <AbsoluteFill style={{backgroundColor: COLORS.bg}}>
      {/* neon glows */}
      <AbsoluteFill
        style={{
          background: `radial-gradient(620px circle at ${g1.x}% ${g1.y}%, ${glow}33, transparent 70%),
                       radial-gradient(760px circle at ${g2.x}% ${g2.y}%, ${glow2}2B, transparent 72%)`,
          opacity: pulse,
        }}
      />
      {/* minor + major blueprint grid */}
      <AbsoluteFill
        style={{
          backgroundImage: `linear-gradient(${line(0.07)} 1px, transparent 1px),
                            linear-gradient(90deg, ${line(0.07)} 1px, transparent 1px),
                            linear-gradient(${line(0.16)} 2px, transparent 2px),
                            linear-gradient(90deg, ${line(0.16)} 2px, transparent 2px)`,
          backgroundSize: `${MINOR}px ${MINOR}px, ${MINOR}px ${MINOR}px, ${MAJOR}px ${MAJOR}px, ${MAJOR}px ${MAJOR}px`,
          backgroundPosition: `${shiftX}px ${shiftY}px`,
          maskImage: mask,
          WebkitMaskImage: mask,
        }}
      />
      {/* film grain: computed at half resolution (4x cheaper), scaled up, re-seeded ~20x/s */}
      <AbsoluteFill style={{opacity: 0.1, mixBlendMode: 'overlay'}}>
        <svg
          width={540}
          height={960}
          viewBox="0 0 540 960"
          style={{width: '100%', height: '100%'}}
          xmlns="http://www.w3.org/2000/svg"
        >
          <filter id="grid-grain">
            <feTurbulence
              type="fractalNoise"
              baseFrequency="0.9"
              numOctaves="2"
              seed={Math.floor(frame / 3)}
              stitchTiles="stitch"
            />
            <feColorMatrix type="saturate" values="0" />
          </filter>
          <rect width="540" height="960" filter="url(#grid-grain)" />
        </svg>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
