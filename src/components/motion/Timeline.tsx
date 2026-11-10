import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {DISPLAY_FONT, UI_FONT} from '../../fonts';

export type TimelineEvent = {label: string; text: string};

/** A vertical line draws itself downwards; each event pops in on its node (year/label + one line of text). */
export const Timeline: React.FC<{events: TimelineEvent[]; accentColor: string}> = ({events, accentColor}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const count = Math.max(1, events.length);
  const rowH = count > 3 ? 218 : 270;
  const stagger = Math.round(fps * 0.6);
  const total = rowH * (count - 1);
  const drawn = interpolate(frame, [0, stagger * (count - 1) + Math.round(fps * 0.4)], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <div style={{position: 'relative', width: 920, height: total + 150}}>
      {/* spine */}
      <div style={{position: 'absolute', left: 70, top: 75, width: 8, height: total * drawn, borderRadius: 4, background: accentColor, boxShadow: `0 0 26px ${accentColor}99`}} />
      {events.map((e, i) => {
        const s = spring({frame: frame - i * stagger, fps, config: {damping: 14, stiffness: 170, mass: 0.7}});
        return (
          <div
            key={`${e.label}-${i}`}
            style={{
              position: 'absolute',
              left: 0,
              top: i * rowH,
              height: 150,
              display: 'flex',
              alignItems: 'center',
              gap: 44,
              opacity: Math.min(1, s * 1.6),
              transform: `translateX(${(1 - s) * 120}px)`,
            }}
          >
            <div
              style={{
                width: 148,
                height: 148,
                borderRadius: 74,
                flex: '0 0 148px',
                background: '#07080B',
                border: `7px solid ${accentColor}`,
                boxShadow: `0 0 34px ${accentColor}77`,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontFamily: DISPLAY_FONT,
                fontWeight: 900,
                fontSize: e.label.length > 6 ? 30 : e.label.length > 4 ? 34 : e.label.length === 4 ? 40 : 54,
                color: accentColor,
                textAlign: 'center',
                lineHeight: 1,
                transform: `scale(${0.7 + 0.3 * s})`,
              }}
            >
              {e.label}
            </div>
            <div
              style={{
                fontFamily: UI_FONT,
                fontWeight: 600,
                fontSize: e.text.length > 26 ? 52 : 62,
                lineHeight: 1.1,
                color: '#fff',
                textShadow: '0 6px 30px rgba(0,0,0,0.6)',
                maxWidth: 680,
              }}
            >
              {e.text}
            </div>
          </div>
        );
      })}
    </div>
  );
};
