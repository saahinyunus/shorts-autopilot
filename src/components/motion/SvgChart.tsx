import React, {useMemo} from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {COLORS, DISPLAY_FONT, UI_FONT} from '../../fonts';
import {T} from './timing';

const W = 920;
const H = 560;
const PAD = 24;
const POINTS = 90;

/** Deterministic curve (0..1 = low..high). UP accelerates; DOWN climbs, then collapses. */
const curve = (trend: 'UP' | 'DOWN', x: number): number => {
  const wobble = 0.025 * Math.sin(x * 37) + 0.015 * Math.sin(x * 91 + 1);
  if (trend === 'UP') return 0.1 + 0.82 * x ** 2.2 + wobble * (1 - x);
  const crash = T.chart_crash_fraction;
  if (x <= crash) return 0.42 + 0.4 * (x / crash) ** 1.2 + wobble;
  const k = (x - crash) / (1 - crash);
  return 0.82 - 0.76 * (1 - (1 - k) ** 3); // steep drop that bottoms out
};

/**
 * Line chart that draws itself via strokeDashoffset. UP glows green; DOWN glows red and flashes
 * on impact (T.chart_start_s + T.chart_draw_s * T.chart_crash_fraction).
 */
export const SvgChart: React.FC<{trend: 'UP' | 'DOWN'; label: string; accentColor: string}> = ({
  trend,
  label,
}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  const color = trend === 'UP' ? COLORS.green : COLORS.red;

  const pts = useMemo(
    () =>
      Array.from({length: POINTS}, (_, i) => {
        const x = i / (POINTS - 1);
        return {
          x: PAD + x * (W - PAD * 2),
          y: PAD + (1 - curve(trend, x)) * (H - PAD * 2),
        };
      }),
    [trend],
  );
  const d = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');

  const startF = Math.round(T.chart_start_s * fps);
  const drawF = Math.round(T.chart_draw_s * fps);
  const p = interpolate(frame, [startF, startF + drawF], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // Tip of the line (x is monotonic and evenly spaced, so progress ~ index).
  const f = p * (POINTS - 1);
  const i0 = Math.min(POINTS - 2, Math.floor(f));
  const tip = {
    x: pts[i0].x + (pts[i0 + 1].x - pts[i0].x) * (f - i0),
    y: pts[i0].y + (pts[i0 + 1].y - pts[i0].y) * (f - i0),
  };

  const enter = spring({frame, fps, config: {damping: 18, stiffness: 120}});
  const impactF = Math.round((T.chart_start_s + T.chart_draw_s * T.chart_crash_fraction) * fps);
  const flash =
    trend === 'DOWN'
      ? interpolate(frame, [impactF, impactF + Math.round(fps * 0.5)], [0.35, 0], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        })
      : 0;
  const crashed = trend === 'DOWN' && frame >= impactF;

  return (
    <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 40, opacity: enter}}>
      <div
        style={{
          fontFamily: DISPLAY_FONT,
          fontWeight: 900,
          fontSize: 62,
          lineHeight: 1.08,
          letterSpacing: 2,
          textTransform: 'uppercase',
          color: crashed ? COLORS.red : '#fff',
          textAlign: 'center',
          maxWidth: 920,
        }}
      >
        {label}
      </div>

      <div style={{position: 'relative', width: W, height: H}}>
        {/* impact flash */}
        <div
          style={{
            position: 'absolute',
            inset: -60,
            background: `radial-gradient(closest-side, ${COLORS.red}, transparent)`,
            opacity: flash,
          }}
        />
        <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{position: 'relative', overflow: 'visible'}}>
          <defs>
            <linearGradient id="chartFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.35} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
            <clipPath id="chartClip">
              <rect x={0} y={-40} width={tip.x} height={H + 80} />
            </clipPath>
          </defs>

          {/* axes + guide lines */}
          {[0, 1, 2, 3, 4].map((i) => (
            <line
              key={i}
              x1={PAD}
              x2={W - PAD}
              y1={PAD + ((H - PAD * 2) / 4) * i}
              y2={PAD + ((H - PAD * 2) / 4) * i}
              stroke="rgba(255,255,255,0.1)"
              strokeWidth={2}
              strokeDasharray="6 10"
            />
          ))}
          <line x1={PAD} x2={PAD} y1={PAD} y2={H - PAD} stroke="rgba(255,255,255,0.35)" strokeWidth={3} />
          <line x1={PAD} x2={W - PAD} y1={H - PAD} y2={H - PAD} stroke="rgba(255,255,255,0.35)" strokeWidth={3} />

          {/* area under the curve follows the pen */}
          <path d={`${d} L${W - PAD} ${H - PAD} L${PAD} ${H - PAD} Z`} fill="url(#chartFill)" clipPath="url(#chartClip)" />

          {/* the line: strokeDashoffset 1 -> 0 on a normalised pathLength */}
          <path
            d={d}
            pathLength={1}
            fill="none"
            stroke={color}
            strokeWidth={12}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeDasharray={1}
            strokeDashoffset={1 - p}
            style={{filter: `drop-shadow(0 0 14px ${color})`}}
          />

          {/* pen tip */}
          {p > 0 ? (
            <>
              <circle cx={tip.x} cy={tip.y} r={22} fill={color} opacity={0.25} />
              <circle cx={tip.x} cy={tip.y} r={11} fill="#fff" />
            </>
          ) : null}
        </svg>
      </div>

      <div
        style={{
          fontFamily: UI_FONT,
          fontWeight: 600,
          fontSize: 38,
          letterSpacing: 8,
          color,
          opacity: p > 0.98 ? 1 : 0.0,
        }}
      >
        {trend === 'UP' ? '▲ RISING' : '▼ COLLAPSE'}
      </div>
    </div>
  );
};
