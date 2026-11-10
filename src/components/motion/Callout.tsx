import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {COLORS, DISPLAY_FONT} from '../../fonts';

/** A big statement card with a tag pill (MYTH / FACT / TIP ...). Tone sets the colour: BAD red, GOOD green. */
export const Callout: React.FC<{
  tag: string;
  statement: string;
  tone: 'NEUTRAL' | 'BAD' | 'GOOD';
  accentColor: string;
}> = ({tag, statement, tone, accentColor}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const color = tone === 'BAD' ? COLORS.red : tone === 'GOOD' ? COLORS.green : accentColor;

  const card = spring({frame, fps, config: {damping: 15, stiffness: 150, mass: 0.8}});
  const pill = spring({frame: frame - Math.round(fps * 0.15), fps, config: {damping: 9, stiffness: 210, mass: 0.6}});
  const words = statement.trim().split(/\s+/);
  const stagger = Math.max(1, Math.round(fps * 0.07));
  const size = statement.length > 70 ? 70 : statement.length > 44 ? 84 : 104;
  const pulse = 0.85 + 0.15 * Math.sin(interpolate(frame, [0, fps * 2], [0, Math.PI * 2], {extrapolateRight: 'extend'}));

  return (
    <div style={{position: 'relative', width: 920, display: 'flex', flexDirection: 'column', alignItems: 'center'}}>
      <div
        style={{
          width: '100%',
          boxSizing: 'border-box',
          borderRadius: 44,
          padding: '120px 56px 80px',
          background: 'rgba(255,255,255,0.05)',
          border: `5px solid ${color}`,
          boxShadow: `0 0 ${70 * pulse}px ${color}55`,
          opacity: Math.min(1, card * 1.5),
          transform: `translateY(${(1 - card) * 120}px) scale(${0.94 + 0.06 * card})`,
          display: 'flex',
          flexWrap: 'wrap',
          justifyContent: 'center',
          columnGap: 22,
          rowGap: 8,
        }}
      >
        {words.map((w, i) => {
          const s = spring({frame: frame - Math.round(fps * 0.35) - i * stagger, fps, config: {damping: 14, stiffness: 190, mass: 0.7}});
          return (
            <span
              key={`${w}-${i}`}
              style={{
                display: 'inline-block',
                fontFamily: DISPLAY_FONT,
                fontWeight: 900,
                fontSize: size,
                lineHeight: 1.08,
                letterSpacing: -1,
                color: '#fff',
                opacity: Math.min(1, s * 1.6),
                transform: `translateY(${(1 - s) * 40}px)`,
                textShadow: '0 6px 30px rgba(0,0,0,0.6)',
              }}
            >
              {w}
            </span>
          );
        })}
      </div>
      <div
        style={{
          position: 'absolute',
          top: -52,
          padding: '14px 54px',
          borderRadius: 60,
          background: color,
          color: '#07080B',
          fontFamily: DISPLAY_FONT,
          fontWeight: 900,
          fontSize: 66,
          letterSpacing: 6,
          textTransform: 'uppercase',
          boxShadow: `0 0 40px ${color}99`,
          transform: `scale(${Math.max(0, pill)})`,
        }}
      >
        {tag}
      </div>
    </div>
  );
};
