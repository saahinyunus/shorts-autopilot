import React from 'react';
import {interpolate, useCurrentFrame} from 'remotion';
import type {MotionAction} from '../../types/schema';
import {DocumentReveal} from './DocumentReveal';
import {DynamicCounter} from './DynamicCounter';
import {MinimalText} from './MinimalText';
import {SearchMockup} from './SearchMockup';
import {SvgChart} from './SvgChart';

/**
 * Chooses the React animation for a scene from its `motion_action`.
 * Rendered inside a <Sequence>, so useCurrentFrame() is relative to the scene start.
 */
const renderMotion = (m: MotionAction, accentColor: string): React.ReactNode => {
  switch (m.type) {
    case 'COUNTER':
      return (
        <DynamicCounter
          target={m.target_number}
          prefix={m.prefix}
          suffix={m.suffix}
          label={m.label}
          accentColor={accentColor}
        />
      );
    case 'CHART':
      return <SvgChart trend={m.trend} label={m.label} accentColor={accentColor} />;
    case 'DOCUMENT':
      return (
        <DocumentReveal
          title={m.title}
          highlightText={m.highlight_text}
          isRedacted={m.is_redacted}
          accentColor={accentColor}
        />
      );
    case 'SEARCH':
      return <SearchMockup queryText={m.query_text} resultHeadline={m.result_headline} />;
    case 'MINIMAL_TEXT':
    default:
      return (
        <MinimalText
          headline={(m as Extract<MotionAction, {type: 'MINIMAL_TEXT'}>).headline ?? ''}
          subtext={(m as Extract<MotionAction, {type: 'MINIMAL_TEXT'}>).subtext ?? ''}
          accentColor={accentColor}
        />
      );
  }
};

export const MotionScene: React.FC<{
  motion: MotionAction;
  accentColor: string;
  /** Length of this scene in frames (used for the loop fade-out). */
  sceneFrames: number;
  /** Fade this scene out over its last frames (the final scene, for a seamless loop). */
  fadeOut: boolean;
}> = ({motion, accentColor, sceneFrames, fadeOut}) => {
  const frame = useCurrentFrame();
  const fadeFrames = 14;
  const opacity = fadeOut
    ? interpolate(frame, [sceneFrames - fadeFrames, sceneFrames - 1], [1, 0], {
        extrapolateLeft: 'clamp',
        extrapolateRight: 'clamp',
      })
    : 1;

  // Visual area: upper ~60% of the frame; the lower part is reserved for captions + Shorts UI.
  return (
    <div
      style={{
        position: 'absolute',
        left: 60,
        right: 60,
        top: 150,
        height: 1000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        opacity,
      }}
    >
      {renderMotion(motion, accentColor)}
    </div>
  );
};
