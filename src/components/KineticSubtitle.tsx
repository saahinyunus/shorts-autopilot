import React, {useMemo} from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {loadFont} from '@remotion/google-fonts/Montserrat';
import type {WordTiming} from '../types/schema';

const {fontFamily} = loadFont('normal', {weights: ['900'], subsets: ['latin']});

const MAX_WORDS_PER_CHUNK = 4;
const MAX_CHARS_PER_CHUNK = 15;
const ACTIVE_SCALE = 1.2;

interface Chunk {
  words: WordTiming[];
  start: number;
  end: number;
}

const endsSentence = (t: string) => /[.!?…]["')\]]*$/.test(t);
const endsClause = (t: string) => /[,;:]["')\]]*$/.test(t);

/** Group the word stream into short, punchy caption chunks. */
export const buildChunks = (words: WordTiming[]): Chunk[] => {
  const chunks: Chunk[] = [];
  let current: WordTiming[] = [];
  let chars = 0;

  const flush = () => {
    if (current.length === 0) return;
    chunks.push({
      words: current,
      start: current[0].start,
      end: current[current.length - 1].end,
    });
    current = [];
    chars = 0;
  };

  for (const w of words) {
    if (current.length > 0 && chars + w.text.length + 1 > MAX_CHARS_PER_CHUNK) flush();
    current.push(w);
    chars += w.text.length + 1;
    if (
      endsSentence(w.text) ||
      (endsClause(w.text) && current.length >= 2) ||
      current.length >= MAX_WORDS_PER_CHUNK
    ) {
      flush();
    }
  }
  flush();
  return chunks;
};

export const KineticSubtitle: React.FC<{
  words: WordTiming[];
  accentColor: string;
  /** Fade the captions out over the last N frames (seamless loop). */
  endFadeFrames?: number;
}> = ({words, accentColor, endFadeFrames = 0}) => {
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const t = frame / fps;

  const chunks = useMemo(() => buildChunks(words), [words]);

  // Active chunk: the last chunk that has started. It stays visible until the next one
  // begins (with a short hold after the final word so the last caption doesn't blink out).
  let idx = -1;
  for (let i = 0; i < chunks.length; i++) {
    if (chunks[i].start <= t + 0.02) idx = i;
    else break;
  }
  if (idx < 0) return null;
  const chunk = chunks[idx];
  const isLastChunk = idx === chunks.length - 1;
  if (isLastChunk && t > chunk.end + 0.6) return null;

  // Active word: the last word that has started (holds through tiny gaps between words).
  let activeWord = 0;
  for (let i = 0; i < chunk.words.length; i++) {
    if (chunk.words[i].start <= t + 0.02) activeWord = i;
  }

  // Pop-in of the whole chunk.
  const chunkFrame = frame - Math.round(chunk.start * fps);
  const chunkIn = interpolate(chunkFrame, [0, 4], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    // Captions sit in the lower third (centre ~73%): below the motion graphics, above the Shorts UI.
    <AbsoluteFill className="items-center justify-center" style={{top: '60%', bottom: '12%', height: 'auto'}}>
      <div
        className="flex flex-wrap items-center justify-center px-14 text-center"
        style={{
          // Wide column gap leaves room for the 1.2x pop of the active word.
          columnGap: 58,
          rowGap: 14,
          fontFamily,
          opacity:
            chunkIn *
            (endFadeFrames > 0
              ? interpolate(frame, [durationInFrames - endFadeFrames, durationInFrames - 1], [1, 0], {
                  extrapolateLeft: 'clamp',
                  extrapolateRight: 'clamp',
                })
              : 1),
          transform: `translateY(${(1 - chunkIn) * 24}px)`,
        }}
      >
        {chunk.words.map((w, i) => {
          const isActive = i === activeWord;
          const wordFrame = frame - Math.round(w.start * fps);
          const pop = isActive
            ? spring({
                frame: Math.max(0, wordFrame),
                fps,
                config: {damping: 11, stiffness: 220, mass: 0.6},
              })
            : 0;
          const scale = isActive ? 1 + (ACTIVE_SCALE - 1) * pop : 1;
          return (
            <span
              key={`${w.start}-${i}`}
              className="inline-block uppercase"
              style={{
                fontSize: 92,
                fontWeight: 900,
                lineHeight: 1.05,
                letterSpacing: -1,
                color: isActive ? accentColor : '#FFFFFF',
                transform: `scale(${scale})`,
                transformOrigin: 'center bottom',
                WebkitTextStroke: '10px #000',
                paintOrder: 'stroke fill',
                textShadow: isActive
                  ? `0 0 28px ${accentColor}AA, 0 8px 0 #000`
                  : '0 8px 0 #000',
              }}
            >
              {w.text}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
