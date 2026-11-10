import React from 'react';
import {spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {DISPLAY_FONT, UI_FONT} from '../../fonts';

/** A short title and 2-4 numbered cards that slide up one after another. */
export const Steps: React.FC<{title: string; steps: string[]; accentColor: string}> = ({title, steps, accentColor}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const stagger = Math.round(fps * 0.55);
  const titleIn = spring({frame, fps, config: {damping: 16, stiffness: 140}});
  const cardH = steps.length > 3 ? 168 : 200;

  return (
    <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 36, width: 920}}>
      {title ? (
        <div
          style={{
            fontFamily: DISPLAY_FONT,
            fontWeight: 900,
            fontSize: title.length > 20 ? 68 : 86,
            letterSpacing: -1,
            textTransform: 'uppercase',
            textAlign: 'center',
            color: accentColor,
            textShadow: `0 0 40px ${accentColor}66`,
            opacity: Math.min(1, titleIn * 1.6),
            transform: `translateY(${(1 - titleIn) * 40}px)`,
          }}
        >
          {title}
        </div>
      ) : null}
      {steps.map((text, i) => {
        const s = spring({frame: frame - Math.round(fps * 0.35) - i * stagger, fps, config: {damping: 14, stiffness: 160, mass: 0.7}});
        return (
          <div
            key={`${text}-${i}`}
            style={{
              width: '100%',
              height: cardH,
              boxSizing: 'border-box',
              borderRadius: 32,
              padding: '0 40px',
              display: 'flex',
              alignItems: 'center',
              gap: 36,
              background: 'rgba(255,255,255,0.06)',
              border: '3px solid rgba(255,255,255,0.14)',
              opacity: Math.min(1, s * 1.6),
              transform: `translateY(${(1 - s) * 110}px)`,
            }}
          >
            <div
              style={{
                width: 96,
                height: 96,
                flex: '0 0 96px',
                borderRadius: 48,
                background: accentColor,
                color: '#07080B',
                fontFamily: DISPLAY_FONT,
                fontWeight: 900,
                fontSize: 60,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                boxShadow: `0 0 28px ${accentColor}88`,
              }}
            >
              {i + 1}
            </div>
            <div style={{fontFamily: UI_FONT, fontWeight: 600, fontSize: text.length > 28 ? 50 : 58, lineHeight: 1.12, color: '#fff'}}>{text}</div>
          </div>
        );
      })}
    </div>
  );
};
