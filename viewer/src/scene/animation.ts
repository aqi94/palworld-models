import * as THREE from 'three';
import type { ClipInfo } from '../types';

export interface PlaybackState {
  clip: string | null;
  time: number;
  duration: number;
  frame: number;
  frameCount: number;
  paused: boolean;
  timeScale: number;
  loop: boolean;
}

export const TIME_SCALES = [0.25, 0.5, 1, 2];

/** Animation pack manifest extras.retarget: bones whose translation depends on the target mesh -> mode + source translation per clip. */
export type PackRetarget = Record<string, { mode: 'AnimationScaled' | 'AnimationRelative'; sources: Record<string, [number, number, number]> }>;

export class AnimationController {
  mixer: THREE.AnimationMixer;
  private clips = new Map<string, THREE.AnimationClip>();
  private infos = new Map<string, ClipInfo>();
  private action: THREE.AnimationAction | null = null;
  current: string | null = null;
  paused = false;
  timeScale = 1;
  /** Whether clips repeat. Shared by every controller (main, rider, mount): the clips carry no loop flag we can trust. */
  static loop = true;

  /** node name -> bind-pose position, captured before anything plays (pack clips are fitted to these) */
  private bind = new Map<string, THREE.Vector3>();

  constructor(root: THREE.Object3D, clips: THREE.AnimationClip[], infos: ClipInfo[]) {
    this.mixer = new THREE.AnimationMixer(root);
    root.traverse((o) => { if (!this.bind.has(o.name)) this.bind.set(o.name, o.position.clone()); });
    clips.forEach((c) => this.clips.set(c.name, c));
    infos.forEach((i) => this.infos.set(i.name, i));
  }

  has(name: string) { return this.clips.has(name); }

  private nodeNames?: Set<string>;
  /** Register clips from another GLB (a shared animation pack). Tracks bind by node name; tracks for bones this model lacks are dropped
   *  (the human skeleton has every outfit's cloth bones). Clips are rebuilt per model, so a cached pack can serve several models.
   *  The first clip registered under a name wins. */
  addClips(root: THREE.Object3D, clips: THREE.AnimationClip[], infos: ClipInfo[], retarget?: PackRetarget) {
    if (!this.nodeNames) { const n = new Set<string>(); root.traverse((o) => { n.add(o.name); }); this.nodeNames = n; }
    const names = this.nodeNames;
    for (const c of clips) {
      if (this.clips.has(c.name)) continue;
      const tracks = c.tracks.filter((t) => names.has(THREE.PropertyBinding.parseTrackName(t.name).nodeName)).map((t) => this.fit(t, c.name, retarget));
      this.clips.set(c.name, new THREE.AnimationClip(c.name, c.duration, tracks));
    }
    for (const i of infos) if (!this.infos.has(i.name)) this.infos.set(i.name, i);
  }

  /** The engine's translation retargeting for a pack track (Skeleton-mode bones carry no translation track at all, so they keep the
   *  mesh's offset): AnimationScaled = scaled by this mesh's bone length over the source's; AnimationRelative = shifted by the difference. */
  private fit(t: THREE.KeyframeTrack, clip: string, retarget?: PackRetarget): THREE.KeyframeTrack {
    const p = THREE.PropertyBinding.parseTrackName(t.name);
    const r = retarget?.[p.nodeName];
    if (!r || p.propertyName !== 'position') return t;
    const src = r.sources[clip], dst = this.bind.get(p.nodeName);
    if (!src || !dst) return t;
    const v = Float32Array.from(t.values as ArrayLike<number>);
    if (r.mode === 'AnimationScaled') {
      const ls = Math.hypot(src[0], src[1], src[2]), k = ls > 1e-6 ? dst.length() / ls : 1;
      for (let i = 0; i < v.length; i++) v[i] *= k;
    } else if (r.mode === 'AnimationRelative') {
      const d = [dst.x - src[0], dst.y - src[1], dst.z - src[2]];
      for (let i = 0; i < v.length; i++) v[i] += d[i % 3];
    }
    const out = t.clone();
    out.values = v;
    return out;
  }

  play(name: string) {
    const clip = this.clips.get(name);
    if (!clip) return;
    const prev = this.action;
    const next = this.mixer.clipAction(clip);
    next.reset();
    next.enabled = true;
    this.applyLoop(next);
    if (prev && prev !== next) prev.stop();
    next.paused = this.paused;
    next.play();
    this.action = next;
    this.current = name;
    this.mixer.update(0);
  }

  private applyLoop(a: THREE.AnimationAction) {
    if (AnimationController.loop) {
      a.setLoop(THREE.LoopRepeat, Infinity);
      a.clampWhenFinished = false;
    } else {
      a.setLoop(THREE.LoopOnce, 1);
      a.clampWhenFinished = true;
    }
  }

  setLoop(on: boolean) {
    AnimationController.loop = on;
    const a = this.action;
    if (!a) return;
    const done = a.loop === THREE.LoopOnce && a.time >= a.getClip().duration - 1e-4;
    this.applyLoop(a);
    if (on && done) { a.reset(); a.play(); }
  }

  update(dt: number) {
    this.mixer.timeScale = this.timeScale;
    this.mixer.update(dt);
  }

  setPaused(p: boolean) {
    this.paused = p;
    const a = this.action;
    if (!a) return;
    if (!p && a.loop === THREE.LoopOnce && a.time >= a.getClip().duration - 1e-4) {
      a.reset(); // replay a finished one-shot
      a.play();
    }
    a.paused = p;
  }

  setTimeScale(s: number) { this.timeScale = s; }

  scrubToFrame(frame: number) {
    const a = this.action;
    if (!a) return;
    const info = this.infos.get(this.current!);
    const dur = a.getClip().duration;
    const fc = Math.max(1, (info?.frameCount ?? 1) - 1);
    a.enabled = true;
    a.paused = true;
    a.time = Math.min(dur, (frame / fc) * dur);
    this.paused = true;
    const ts = this.mixer.timeScale;
    this.mixer.update(0);
    this.mixer.timeScale = ts;
  }

  /** Current clip's duration (s) and whether it plays once; null without a clip. */
  clipTiming(): { duration: number; once: boolean } | null {
    const a = this.action;
    return a ? { duration: a.getClip().duration, once: a.loop === THREE.LoopOnce } : null;
  }

  /** A clip's duration in seconds (0 if unknown). */
  duration(name: string) { return this.clips.get(name)?.duration ?? 0; }

  /** Pose clip `name` at `t` seconds, switching to it if needed; the action stays paused (driven from outside, e.g. by a rider's clip). */
  poseAt(name: string, t: number) {
    if (this.current !== name) this.play(name);
    const a = this.action;
    if (!a) return;
    a.enabled = true;
    a.paused = true;
    a.time = Math.min(a.getClip().duration, Math.max(0, t));
    const ts = this.mixer.timeScale;
    this.mixer.update(0);
    this.mixer.timeScale = ts;
  }

  /** Pause and pose the current clip at `t` seconds (frame-exact exports). */
  seekTime(t: number) {
    const a = this.action;
    if (!a) return;
    a.enabled = true;
    a.paused = true;
    a.time = Math.min(a.getClip().duration, Math.max(0, t));
    this.paused = true;
    const ts = this.mixer.timeScale;
    this.mixer.update(0);
    this.mixer.timeScale = ts;
  }

  state(): PlaybackState {
    const a = this.action;
    const info = this.current ? this.infos.get(this.current) : undefined;
    const dur = a?.getClip().duration ?? 0;
    const time = a ? Math.min(dur, Math.max(0, a.time)) : 0;
    const fcount = info?.frameCount ?? 0;
    const frame = dur > 0 ? Math.round((time / dur) * Math.max(0, fcount - 1)) : 0;
    return { clip: this.current, time, duration: dur, frame, frameCount: fcount, paused: this.paused, timeScale: this.timeScale, loop: AnimationController.loop };
  }
}
