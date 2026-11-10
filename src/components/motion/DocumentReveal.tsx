import React from 'react';
import {Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {COLORS, TYPEWRITER_FONT} from '../../fonts';
import {T} from './timing';

const SkeletonLine: React.FC<{width: string; redacted?: boolean}> = ({width, redacted}) => (
  <div
    style={{
      width,
      height: redacted ? 26 : 18,
      borderRadius: redacted ? 3 : 9,
      background: redacted ? '#0A0A0A' : '#CFC8B5',
    }}
  />
);

/**
 * Classified-report card. The key sentence (`highlight_text`) is either always visible or hidden
 * under a black censor bar that is ripped away at T.document_unredact_s; a yellow highlighter
 * then sweeps across it.
 */
export const DocumentReveal: React.FC<{
  title: string;
  highlightText: string;
  isRedacted: boolean;
  accentColor: string;
}> = ({title, highlightText, isRedacted, accentColor}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  const cardIn = spring({frame, fps, config: {damping: 15, stiffness: 130}});
  const stampFrame = frame - Math.round(0.35 * fps);
  const stamp = stampFrame >= 0 ? spring({frame: stampFrame, fps, config: {damping: 9, stiffness: 240}}) : 0;

  const unredactF = Math.round(T.document_unredact_s * fps);
  const censorX = isRedacted
    ? interpolate(frame, [unredactF, unredactF + Math.round(0.18 * fps)], [0, 1], {
        easing: Easing.in(Easing.cubic),
        extrapolateLeft: 'clamp',
        extrapolateRight: 'clamp',
      })
    : 1;

  const hlStartF = Math.round((isRedacted ? T.document_unredact_s + 0.15 : 0.7) * fps);
  const hlProgress = interpolate(frame, [hlStartF, hlStartF + Math.round(T.document_highlight_s * fps)], [0, 1], {
    easing: Easing.out(Easing.cubic),
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  const fontSize = highlightText.length > 60 ? 42 : highlightText.length > 36 ? 50 : 60;

  return (
    <div
      style={{
        width: 900,
        padding: '56px 60px 64px',
        background: COLORS.paper,
        borderRadius: 10,
        boxShadow: '0 40px 90px rgba(0,0,0,0.65), 0 0 0 1px rgba(255,255,255,0.08)',
        transform: `translateY(${(1 - cardIn) * 160}px) rotate(${-1.6 + (1 - cardIn) * -6}deg)`,
        opacity: Math.min(1, cardIn * 1.4),
        fontFamily: TYPEWRITER_FONT,
        color: COLORS.ink,
        position: 'relative',
      }}
    >
      {/* header */}
      <div style={{display: 'flex', justifyContent: 'space-between', fontSize: 24, letterSpacing: 3, opacity: 0.55}}>
        <span>FILE NO. 0472-B</span>
        <span>EYES ONLY</span>
      </div>
      <div
        style={{
          marginTop: 22,
          fontSize: 50,
          fontWeight: 700,
          textTransform: 'uppercase',
          borderBottom: '5px solid #121212',
          paddingBottom: 18,
          lineHeight: 1.1,
          maxWidth: 640,
        }}
      >
        {title}
      </div>

      {/* body */}
      <div style={{display: 'flex', flexDirection: 'column', gap: 18, marginTop: 36}}>
        <SkeletonLine width="94%" />
        <SkeletonLine width="78%" />
        <SkeletonLine width="86%" redacted />
      </div>

      <div style={{position: 'relative', margin: '40px 0'}}>
        <div style={{fontSize, fontWeight: 700, lineHeight: 1.3, padding: '4px 6px'}}>
          <span
            style={{
              backgroundImage: `linear-gradient(${accentColor}, ${accentColor})`,
              backgroundRepeat: 'no-repeat',
              backgroundPosition: '0 70%',
              backgroundSize: `${hlProgress * 100}% 86%`,
              boxDecorationBreak: 'clone',
              WebkitBoxDecorationBreak: 'clone',
              padding: '0 6px',
            }}
          >
            {highlightText}
          </span>
        </div>
        {/* censor bar rips off to the right */}
        {isRedacted ? (
          <div
            style={{
              position: 'absolute',
              inset: '-4px -8px',
              background: '#0A0A0A',
              borderRadius: 4,
              transformOrigin: 'right center',
              transform: `scaleX(${1 - censorX}) translateX(${censorX * 40}px)`,
              opacity: censorX > 0.98 ? 0 : 1,
            }}
          />
        ) : null}
      </div>

      <div style={{display: 'flex', flexDirection: 'column', gap: 18}}>
        <SkeletonLine width="90%" />
        <SkeletonLine width="64%" />
      </div>

      {/* stamp slams in */}
      <div
        style={{
          position: 'absolute',
          right: 40,
          bottom: 70,
          border: `7px solid ${COLORS.red}`,
          color: COLORS.red,
          padding: '4px 18px',
          fontSize: 44,
          fontWeight: 700,
          letterSpacing: 4,
          borderRadius: 6,
          opacity: stamp > 0 ? 0.88 : 0,
          transform: `rotate(9deg) scale(${stamp > 0 ? 2.4 - 1.4 * stamp : 2.4})`,
        }}
      >
        CONFIDENTIAL
      </div>
    </div>
  );
};
