import React from 'react';
import {Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {COLORS, DISPLAY_FONT, UI_FONT} from '../../fonts';
import {T} from './timing';

/**
 * Number rockets from 0 to `target` (fast start, long settle) and "lands" exactly at
 * T.counter_land_s, the same instant the engine places a pop SFX and a screen shake.
 */
export const DynamicCounter: React.FC<{
  target: number;
  prefix: string;
  suffix: string;
  label: string;
  accentColor: string;
}> = ({target, prefix, suffix, label, accentColor}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const landFrame = Math.round(T.counter_land_s * fps);

  const progress = interpolate(frame, [0, landFrame], [0, 1], {
    easing: Easing.out(Easing.exp),
    extrapolateRight: 'clamp',
  });
  const decimals = Number.isInteger(target) ? 0 : 1;
  const format = (n: number) =>
    n.toLocaleString('en-US', {minimumFractionDigits: decimals, maximumFractionDigits: decimals});
  const shown = format(target * progress);

  // Impact: scale slams from 1.14 to 1 and the digits flash the accent colour.
  const pop = frame >= landFrame ? spring({frame: frame - landFrame, fps, config: {damping: 9, stiffness: 260}}) : 0;
  const settle = interpolate(frame, [0, landFrame], [0.88, 1], {extrapolateRight: 'clamp'});
  const scale = frame >= landFrame ? 1.14 - 0.14 * pop : settle;
  const flash = interpolate(frame, [landFrame, landFrame + Math.round(fps * 0.35)], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Size to the FINAL string so the layout never jumps while counting.
  const chars = prefix.length * 0.6 + format(target).length + suffix.length * 0.6;
  const fontSize = Math.min(300, 880 / (0.66 * Math.max(chars, 1)));

  const enter = spring({frame, fps, config: {damping: 18, stiffness: 120}});
  const labelIn = spring({frame: frame - Math.round(fps * 0.25), fps, config: {damping: 16, stiffness: 120}});

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 36,
        opacity: enter,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'center',
          fontFamily: DISPLAY_FONT,
          fontWeight: 900,
          lineHeight: 1,
          transform: `scale(${scale})`,
          textShadow: `0 0 ${40 + flash * 60}px ${accentColor}${flash > 0.05 ? '99' : '33'}`,
        }}
      >
        {prefix ? (
          <span style={{fontSize: fontSize * 0.6, color: accentColor, marginRight: 8}}>{prefix}</span>
        ) : null}
        <span
          style={{
            fontSize,
            color: flash > 0.02 ? accentColor : '#fff',
            fontVariantNumeric: 'tabular-nums',
            letterSpacing: -4,
          }}
        >
          {shown}
        </span>
        {suffix ? (
          <span style={{fontSize: fontSize * 0.6, color: accentColor, marginLeft: 8}}>{suffix}</span>
        ) : null}
      </div>

      {/* progress rail */}
      <div style={{width: 760, height: 8, borderRadius: 4, background: 'rgba(255,255,255,0.12)'}}>
        <div
          style={{
            width: `${progress * 100}%`,
            height: '100%',
            borderRadius: 4,
            background: `linear-gradient(90deg, ${COLORS.cyan}, ${accentColor})`,
            boxShadow: `0 0 18px ${accentColor}88`,
          }}
        />
      </div>

      <div
        style={{
          fontFamily: UI_FONT,
          fontWeight: 600,
          fontSize: 44,
          letterSpacing: 6,
          textTransform: 'uppercase',
          color: 'rgba(255,255,255,0.78)',
          textAlign: 'center',
          maxWidth: 900,
          opacity: labelIn,
          transform: `translateY(${(1 - labelIn) * 24}px)`,
        }}
      >
        {label}
      </div>
    </div>
  );
};
