import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {UI_FONT} from '../../fonts';

const SURFACE = '#303134';
const MUTED = '#9AA0A6';

const Magnifier: React.FC<{size?: number}> = ({size = 40}) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={MUTED} strokeWidth={2.4} strokeLinecap="round">
    <circle cx="10.5" cy="10.5" r="6.5" />
    <path d="M15.5 15.5L21 21" />
  </svg>
);

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/**
 * Dark-mode search UI: the query is typed char by char with a live autocomplete dropdown,
 * then the dropdown collapses and the result headline slides in.
 */
export const SearchMockup: React.FC<{queryText: string; resultHeadline: string}> = ({
  queryText,
  resultHeadline,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps;

  const typeStart = 0.35;
  const typeDur = clamp(queryText.length / 16, 0.9, 1.6);
  const typed = Math.floor(
    interpolate(t, [typeStart, typeStart + typeDur], [0, queryText.length], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    }),
  );
  const typing = t >= typeStart && typed < queryText.length;
  const resultAt = typeStart + typeDur + 0.35;

  const enter = spring({frame, fps, config: {damping: 16, stiffness: 140}});
  const caretOn = typing || Math.floor(t / 0.5) % 2 === 0;

  const dropdownOpacity =
    typed >= 2 ? interpolate(t, [resultAt - 0.15, resultAt], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}) : 0;
  const submitGlow = interpolate(t, [resultAt - 0.2, resultAt, resultAt + 0.4], [0, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  const prefix = queryText.slice(0, typed);
  const rest = queryText.slice(typed);
  const suggestions = [rest, `${rest} explained`, `${rest} reddit`];

  const resultIn = t >= resultAt ? spring({frame: frame - Math.round(resultAt * fps), fps, config: {damping: 15, stiffness: 150}}) : 0;

  return (
    <div
      style={{
        width: 920,
        display: 'flex',
        flexDirection: 'column',
        gap: 28,
        fontFamily: UI_FONT,
        opacity: enter,
        transform: `translateY(${(1 - enter) * 50}px)`,
      }}
    >
      {/* search bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 24,
          height: 124,
          padding: '0 40px',
          borderRadius: 62,
          background: SURFACE,
          boxShadow: `0 10px 50px rgba(0,0,0,0.6), 0 0 ${submitGlow * 50}px rgba(138,180,248,${0.7 * submitGlow})`,
          border: `2px solid rgba(138,180,248,${0.12 + 0.6 * submitGlow})`,
        }}
      >
        <Magnifier size={46} />
        <div style={{fontSize: 46, fontWeight: 500, color: '#fff', whiteSpace: 'nowrap', overflow: 'hidden'}}>
          {prefix}
          <span
            style={{
              display: 'inline-block',
              width: 3,
              height: 52,
              marginLeft: 3,
              verticalAlign: 'middle',
              background: '#8AB4F8',
              opacity: caretOn ? 1 : 0,
            }}
          />
        </div>
      </div>

      {/* autocomplete dropdown */}
      {typed >= 2 && dropdownOpacity > 0 ? (
        <div
          style={{
            marginTop: -12,
            padding: '14px 40px 18px',
            borderRadius: 36,
            background: SURFACE,
            opacity: dropdownOpacity,
            display: 'flex',
            flexDirection: 'column',
            gap: 6,
          }}
        >
          {suggestions.map((s, i) => (
            <div key={i} style={{display: 'flex', alignItems: 'center', gap: 22, height: 62, fontSize: 36, color: '#E8EAED'}}>
              <Magnifier size={30} />
              <span style={{whiteSpace: 'nowrap', overflow: 'hidden'}}>
                {prefix}
                <b style={{fontWeight: 600}}>{s}</b>
              </span>
            </div>
          ))}
        </div>
      ) : null}

      {/* result card */}
      {t >= resultAt ? (
        <div
          style={{
            padding: '36px 44px 40px',
            borderRadius: 28,
            background: '#1E1F22',
            border: '2px solid rgba(255,255,255,0.08)',
            opacity: resultIn,
            transform: `translateY(${(1 - resultIn) * 70}px)`,
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
          }}
        >
          <div style={{fontSize: 28, color: MUTED}}>journal.research › article</div>
          <div style={{fontSize: 56, fontWeight: 600, lineHeight: 1.15, color: '#8AB4F8'}}>{resultHeadline}</div>
          <div style={{display: 'flex', flexDirection: 'column', gap: 12, marginTop: 6}}>
            <div style={{height: 18, width: '96%', borderRadius: 9, background: 'rgba(255,255,255,0.12)'}} />
            <div style={{height: 18, width: '72%', borderRadius: 9, background: 'rgba(255,255,255,0.12)'}} />
          </div>
        </div>
      ) : null}
    </div>
  );
};
