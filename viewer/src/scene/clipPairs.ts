import type { ClipPair } from '../types';

/** One Pal clip of a pair laid out on the boss clip's timeline (seconds). `from` / `clipDur`: the part of the clip the montage plays. */
export interface PairSeg { clip: string; rate: number; loop: boolean; start: number; len: number; from: number; clipDur: number }

/** Lay a pair's Pal clips out along the boss clip. The boss clip was authored against the Pal's montages back to back at their own speed:
 *  - one-shots last their clip (the montage's PlayRate is a runtime rate, not part of the authoring: Sorajima VortexRage 6.77 s = Start 3.67 +
 *    End 3.0; with End x1.45 Auri leaves Shaolong's head, at x1 she stays within 0.09 m of her seat);
 *  - loops take the time the one-shots leave, in whole cycles, in order (World Tree LaserGliding 9.0 s = Start 2.83 + 2 x Loop 1.33 + End 3.5;
 *    Supernova 40.17 = 2 + 26 x 1.33 + 3.5); less than a cycle left is the boss holding its end (VortexRage: 0.1 s);
 *  - a loop montage that plays a single frame (SnowImpact_01_DownLoop: 0..0.005 s of SnowImpact_01) is a hold.
 *  A pair of one clip that loops by itself (Idle under Ride_Idle, TackleLaser_Loop under Victor's UniqueSkill loop) runs the whole boss clip. */
export function layoutPair(pair: ClipPair, bossDur: number, dur: (clip: string) => number, loops?: (clip: string) => boolean): PairSeg[] {
  const single = pair.pal.length === 1;
  const segs = pair.pal.map((s) => {
    const full = dur(s.clip), from = Math.min(s.from ?? 0, full), to = s.to != null && s.to >= 0 ? Math.min(s.to, full) : full;
    return { clip: s.clip, rate: 1, loop: !!s.loop || (single && !!loops?.(s.clip)), from, clipDur: Math.max(0, to - from), full };
  }).filter((s) => s.full > 0);
  let left = bossDur - segs.filter((s) => !s.loop).reduce((a, s) => a + s.clipDur, 0);
  const loopLen = new Map<number, number>();
  segs.forEach((s, i) => {
    if (!s.loop || single) return;
    const n = s.clipDur > 0 ? Math.max(0, Math.floor(left / s.clipDur + 0.05)) : 0;
    loopLen.set(i, n * s.clipDur); left -= n * s.clipDur;
  });
  let t = 0;
  return segs.map((s, i) => {
    const len = single ? Math.max(bossDur, s.clipDur) : s.loop ? loopLen.get(i) ?? 0 : s.clipDur;
    const seg = { clip: s.clip, rate: s.rate, loop: s.loop, from: s.from, clipDur: s.clipDur, start: t, len };
    t += len;
    return seg;
  });
}

/** The Pal clip and its local time at boss time `t`. A looping part wraps (a one-frame loop holds that frame); a one-shot holds its end. */
export function palAt(segs: PairSeg[], t: number): { clip: string; time: number } | null {
  const live = segs.filter((s) => s.len > 1e-4);
  if (!live.length) return null;
  const s = live.find((x) => t < x.start + x.len) ?? live[live.length - 1];
  const local = Math.max(0, t - s.start) * s.rate;
  const inPart = s.clipDur <= 0 ? 0 : s.loop ? local % s.clipDur : Math.min(local, s.clipDur);
  return { clip: s.clip, time: s.from + inPart };
}

/** Inverse, for the Pal page: the boss clip time matching the Pal's `clip` at its own `palTime` (first segment using that clip). */
export function bossAt(segs: PairSeg[], clip: string, palTime: number): number | null {
  const s = segs.find((x) => x.clip === clip && x.len > 1e-4 && x.clipDur > 0.05);
  if (!s) return null;
  const local = Math.max(0, palTime - s.from);
  return s.start + Math.min(s.len, (s.loop ? local % s.clipDur : local) / s.rate);
}

/** The pair a Pal clip belongs to: one where it is the whole sequence first (Idle), else the first that uses it. */
export function pairForPalClip(pairs: ClipPair[] | undefined, clip: string | null): ClipPair | undefined {
  if (!pairs || !clip) return undefined;
  return pairs.find((p) => p.pal.length === 1 && p.pal[0].clip === clip) ?? pairs.find((p) => p.pal.some((s) => s.clip === clip));
}
