import timing from '../../types/motionTiming.json';
import type {MotionAction, SceneProps, SfxCue} from '../../types/schema';
import type {ShakeTrigger} from './ScreenShake';

export const T = timing;

/** Seconds (relative to scene start) at which a scene's animation "hits". */
export const motionImpacts = (m: MotionAction): ShakeTrigger[] => {
  switch (m.type) {
    case 'COUNTER':
      return [{at: T.counter_land_s, intensity: 0.55}];
    case 'CHART':
      return m.trend === 'DOWN'
        ? [{at: T.chart_start_s + T.chart_draw_s * T.chart_crash_fraction, intensity: 1}]
        : [];
    case 'DOCUMENT':
      return m.is_redacted ? [{at: T.document_unredact_s, intensity: 0.5}] : [];
    default:
      return [];
  }
};

/** Global shake triggers: visual impacts + punchy SFX (sub_drop / pop). */
export const buildShakeTriggers = (scenes: SceneProps[], sfx: SfxCue[]): ShakeTrigger[] => {
  const triggers: ShakeTrigger[] = [];
  for (const scene of scenes) {
    for (const impact of motionImpacts(scene.motion)) {
      const at = scene.start + impact.at;
      if (at < scene.end - 0.1) triggers.push({at, intensity: impact.intensity});
    }
  }
  for (const cue of sfx) {
    if (cue.name === 'sub_drop') triggers.push({at: cue.at, intensity: 1});
    else if (cue.name === 'pop') triggers.push({at: cue.at, intensity: 0.35});
  }
  return triggers;
};
