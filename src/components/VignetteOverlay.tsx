import React from 'react';
import {AbsoluteFill, useCurrentFrame} from 'remotion';

/** Vignette + top/bottom scrims; optional extra film grain (the grid background has its own). */
export const VignetteOverlay: React.FC<{grain?: number}> = ({grain = 0}) => {
  const frame = useCurrentFrame();

  return (
    <>
      <AbsoluteFill
        style={{
          background:
            'radial-gradient(ellipse at center, rgba(0,0,0,0) 45%, rgba(0,0,0,0.55) 100%)',
        }}
      />
      <AbsoluteFill
        style={{
          background:
            'linear-gradient(to bottom, rgba(0,0,0,0.25) 0%, rgba(0,0,0,0) 20%, rgba(0,0,0,0) 55%, rgba(0,0,0,0.45) 100%)',
        }}
      />
      {grain > 0 ? (
        <AbsoluteFill style={{opacity: grain, mixBlendMode: 'overlay'}}>
          <svg width="100%" height="100%" xmlns="http://www.w3.org/2000/svg">
            <filter id="grain">
              <feTurbulence
                type="fractalNoise"
                baseFrequency="0.8"
                numOctaves="2"
                seed={Math.floor(frame / 3)}
                stitchTiles="stitch"
              />
              <feColorMatrix type="saturate" values="0" />
            </filter>
            <rect width="100%" height="100%" filter="url(#grain)" />
          </svg>
        </AbsoluteFill>
      ) : null}
    </>
  );
};
