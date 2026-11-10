import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {DISPLAY_FONT, UI_FONT} from '../../fonts';

type Side = {label: string; value: string};

const Card: React.FC<{
  side: Side;
  from: 'left' | 'right';
  delay: number;
  highlight: boolean;
  dim: boolean;
  accentColor: string;
}> = ({side, from, delay, highlight, dim, accentColor}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const s = spring({frame: frame - delay, fps, config: {damping: 15, stiffness: 140, mass: 0.8}});
  const dir = from === 'left' ? -1 : 1;
  const glow = highlight ? interpolate(frame, [fps * 1.3, fps * 1.7], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}) : 0;
  const color = highlight ? accentColor : '#fff';
  const valueSize = side.value.length > 14 ? 78 : side.value.length > 8 ? 104 : 136;

  return (
    <div
      style={{
        width: 900,
        height: 400,
        borderRadius: 36,
        padding: '0 48px',
        boxSizing: 'border-box',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        gap: 18,
        background: 'rgba(255,255,255,0.05)',
        border: `4px solid ${highlight ? accentColor : 'rgba(255,255,255,0.16)'}`,
        boxShadow: highlight ? `0 0 ${60 * glow}px ${accentColor}88` : 'none',
        opacity: Math.min(1, s * 1.5) * (dim ? 0.62 : 1),
        transform: `translateX(${dir * (1 - s) * 900}px) scale(${highlight ? 1 + 0.03 * glow : 1})`,
      }}
    >
      <div style={{fontFamily: UI_FONT, fontWeight: 600, fontSize: 44, letterSpacing: 6, textTransform: 'uppercase', color: 'rgba(255,255,255,0.7)', textAlign: 'center'}}>
        {side.label}
      </div>
      <div
        style={{
          fontFamily: DISPLAY_FONT,
          fontWeight: 900,
          fontSize: valueSize,
          lineHeight: 1.02,
          letterSpacing: -2,
          textAlign: 'center',
          color,
          textShadow: highlight ? `0 0 40px ${accentColor}66` : '0 6px 30px rgba(0,0,0,0.6)',
        }}
      >
        {side.value}
      </div>
    </div>
  );
};

/** Two things side by side (stacked in the vertical frame) with a VS badge; the winner glows in the accent. */
export const Comparison: React.FC<{
  left: Side;
  right: Side;
  winner: 'LEFT' | 'RIGHT' | 'NONE';
  accentColor: string;
}> = ({left, right, winner, accentColor}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const vs = spring({frame: frame - Math.round(fps * 0.7), fps, config: {damping: 9, stiffness: 200, mass: 0.6}});

  return (
    <div style={{position: 'relative', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 56}}>
      <Card side={left} from="left" delay={0} highlight={winner === 'LEFT'} dim={winner === 'RIGHT'} accentColor={accentColor} />
      <div
        style={{
          position: 'absolute',
          top: '50%',
          left: '50%',
          width: 150,
          height: 150,
          marginLeft: -75,
          marginTop: -75,
          borderRadius: 75,
          background: '#07080B',
          border: `5px solid ${accentColor}`,
          boxShadow: `0 0 36px ${accentColor}77`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontFamily: DISPLAY_FONT,
          fontWeight: 900,
          fontSize: 62,
          color: accentColor,
          zIndex: 3,
          transform: `scale(${Math.max(0, vs)})`,
        }}
      >
        VS
      </div>
      <Card side={right} from="right" delay={Math.round(fps * 0.25)} highlight={winner === 'RIGHT'} dim={winner === 'LEFT'} accentColor={accentColor} />
    </div>
  );
};
