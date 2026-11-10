import React from 'react';
import {Composition, type CalculateMetadataFunction} from 'remotion';
import {ShortsComposition} from './Composition';
import {VIDEO_HEIGHT, VIDEO_WIDTH, type VideoProps} from './types/schema';

/** Placeholder props so `npm run studio` opens without a pipeline run (one scene of each type). */
const defaultProps: VideoProps = {
  title: 'Sample',
  fps: 60,
  durationInSeconds: 15,
  audioFile: 'voice.mp3',
  accentColor: '#FFE600',
  seamlessLoop: true,
  scenes: [
    {id: 1, text: 'Sample.', start: 0, end: 3, motion: {type: 'MINIMAL_TEXT', headline: 'Sample headline', subtext: 'sub text'}},
    {id: 2, text: 'Sample.', start: 3, end: 6, motion: {type: 'COUNTER', target_number: 87, prefix: '', suffix: '%', label: 'Sample stat'}},
    {id: 3, text: 'Sample.', start: 6, end: 9, motion: {type: 'CHART', trend: 'DOWN', label: 'Sample crash'}},
    {id: 4, text: 'Sample.', start: 9, end: 12, motion: {type: 'DOCUMENT', title: 'Case file', highlight_text: 'The key sentence goes here', is_redacted: true}},
    {id: 5, text: 'Sample.', start: 12, end: 15, motion: {type: 'SEARCH', query_text: 'why do we do this', result_headline: 'The surprising answer'}},
  ],
  words: [
    {text: 'Sample', start: 0.2, end: 0.6},
    {text: 'words.', start: 0.6, end: 1.0},
  ],
  sfx: [],
};

const calculateMetadata: CalculateMetadataFunction<VideoProps> = ({props}) => ({
  durationInFrames: Math.max(1, Math.round(props.durationInSeconds * props.fps)),
  fps: props.fps,
});

export const RemotionRoot: React.FC = () => (
  <Composition
    id="ShortsVideo"
    component={ShortsComposition}
    width={VIDEO_WIDTH}
    height={VIDEO_HEIGHT}
    fps={60}
    durationInFrames={900}
    defaultProps={defaultProps}
    calculateMetadata={calculateMetadata}
  />
);
