import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig} from 'remotion';
import {COLORS} from '../../fonts';
import type {BgStyle} from '../../types/schema';

const MINOR = 54;
const MAJOR = MINOR * 5;

/**
 * Dark animated background in five patterns (grid, dots, diagonal, rings, waves), two orbiting neon glows
 * and film grain. Every motion is a whole number of cycles over the video length, so the last frame
 * flows into the first (seamless Shorts loop). The pattern and glow colours come from the video's theme.
 */
export const GridBackground: React.FC<{glow?: string; glow2?: string; bgStyle?: BgStyle}> = ({
  glow = COLORS.cyan,
  glow2 = '#8B5CF6',
  bgStyle = 'grid',
}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const loop = frame / durationInFrames; // 0..1
  const theta = loop * Math.PI * 2;

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

      {bgStyle === 'grid' && (
        <AbsoluteFill
          style={{
            // whole multiples of the cell size => the pattern at loop=1 equals loop=0
            backgroundImage: `linear-gradient(${line(0.07)} 1px, transparent 1px),
                              linear-gradient(90deg, ${line(0.07)} 1px, transparent 1px),
                              linear-gradient(${line(0.16)} 2px, transparent 2px),
                              linear-gradient(90deg, ${line(0.16)} 2px, transparent 2px)`,
            backgroundSize: `${MINOR}px ${MINOR}px, ${MINOR}px ${MINOR}px, ${MAJOR}px ${MAJOR}px, ${MAJOR}px ${MAJOR}px`,
            backgroundPosition: `${loop * MAJOR * 2}px ${loop * MAJOR}px`,
            maskImage: mask,
            WebkitMaskImage: mask,
          }}
        />
      )}

      {bgStyle === 'dots' && (
        <AbsoluteFill
          style={{
            backgroundImage: `radial-gradient(circle, ${line(0.34)} 3px, transparent 4px)`,
            backgroundSize: '60px 60px',
            backgroundPosition: `${loop * 60 * 4}px ${loop * 60 * 2}px`,
            maskImage: mask,
            WebkitMaskImage: mask,
          }}
        />
      )}

      {bgStyle === 'diagonal' && (
        <AbsoluteFill
          style={{
            backgroundImage: `linear-gradient(135deg, ${line(0.14)} 0 4px, transparent 4px 50%, ${line(0.14)} 50% calc(50% + 4px), transparent calc(50% + 4px))`,
            backgroundSize: '96px 96px',
            backgroundPosition: `${loop * 96 * 2}px 0px`,
            maskImage: mask,
            WebkitMaskImage: mask,
          }}
        />
      )}

      {bgStyle === 'rings' && (
        <AbsoluteFill style={{maskImage: mask, WebkitMaskImage: mask}}>
          <svg width="100%" height="100%" viewBox="0 0 1080 1920" xmlns="http://www.w3.org/2000/svg">
            {Array.from({length: 17}, (_, i) => {
              // one full ring spacing per loop => identical first and last frame; rings fade in near the centre
              const r = (i + loop) * 96;
              const fade = Math.min(1, r / 96) * Math.max(0, 1 - r / 1500);
              return <circle key={i} cx={540} cy={800} r={r} fill="none" stroke={line(0.34 * fade)} strokeWidth={3} />;
            })}
          </svg>
        </AbsoluteFill>
      )}

      {bgStyle === 'waves' && (
        <AbsoluteFill style={{maskImage: mask, WebkitMaskImage: mask}}>
          <svg width="100%" height="100%" viewBox="0 0 1080 1920" xmlns="http://www.w3.org/2000/svg">
            {Array.from({length: 11}, (_, i) => {
              const y0 = 160 + i * 150;
              const phase = theta * (i % 2 === 0 ? 1 : -1) + i * 0.9;
              const pts = Array.from({length: 28}, (_, k) => {
                const x = (k / 27) * 1080;
                const y = y0 + Math.sin((x / 1080) * Math.PI * 4 + phase) * 46;
                return `${k === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`;
              }).join(' ');
              return <path key={i} d={pts} fill="none" stroke={line(0.2)} strokeWidth={3} />;
            })}
          </svg>
        </AbsoluteFill>
      )}

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
