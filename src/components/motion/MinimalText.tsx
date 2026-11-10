import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {DISPLAY_FONT, UI_FONT} from '../../fonts';

/** Bold kinetic headline (words spring in one by one, last word in the accent colour) + subtext. */
export const MinimalText: React.FC<{headline: string; subtext: string; accentColor: string}> = ({
  headline,
  subtext,
  accentColor,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  const words = headline.trim().split(/\s+/);
  const fontSize = headline.length > 30 ? 96 : headline.length > 18 ? 124 : 156;
  const stagger = Math.round(fps * 0.09);
  const wordsDone = words.length * stagger + Math.round(fps * 0.25);

  const lineW = interpolate(frame, [wordsDone - 6, wordsDone + Math.round(fps * 0.3)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const subIn = spring({frame: frame - wordsDone, fps, config: {damping: 16, stiffness: 120}});

  return (
    <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 44, maxWidth: 940}}>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          justifyContent: 'center',
          columnGap: 28,
          rowGap: 6,
          fontFamily: DISPLAY_FONT,
          fontWeight: 900,
          fontSize,
          lineHeight: 1.04,
          letterSpacing: -2,
          textTransform: 'uppercase',
          textAlign: 'center',
        }}
      >
        {words.map((w, i) => {
          const s = spring({frame: frame - i * stagger, fps, config: {damping: 13, stiffness: 190, mass: 0.7}});
          const isLast = i === words.length - 1 && words.length > 1;
          return (
            <span
              key={`${w}-${i}`}
              style={{
                display: 'inline-block',
                color: isLast ? accentColor : '#fff',
                opacity: Math.min(1, s * 1.6),
                transform: `translateY(${(1 - s) * 90}px) scale(${0.85 + 0.15 * s})`,
                textShadow: isLast ? `0 0 44px ${accentColor}66` : '0 6px 30px rgba(0,0,0,0.6)',
              }}
            >
              {w}
            </span>
          );
        })}
      </div>

      <div style={{width: 520 * lineW, height: 8, borderRadius: 4, background: accentColor, boxShadow: `0 0 24px ${accentColor}99`}} />

      {subtext ? (
        <div
          style={{
            fontFamily: UI_FONT,
            fontWeight: 500,
            fontSize: 46,
            letterSpacing: 3,
            color: 'rgba(255,255,255,0.75)',
            textAlign: 'center',
            opacity: Math.max(0, subIn),
            transform: `translateY(${(1 - Math.max(0, subIn)) * 26}px)`,
          }}
        >
          {subtext}
        </div>
      ) : null}
    </div>
  );
};
