import * as THREE from 'three';
import type { PhysicsNode } from '../types';

/** The game's cloth / hair / belt simulation (the KawaiiPhysics plugin), driven by the settings extracted from the character's anim layer.
 *  Each node roots a chain of bones that the clips leave at their bind pose. Per fixed step (the node's target frame rate): Verlet with
 *  damping and gravity, a pull back to the animated pose (stiffness), collision with the node's capsule / sphere limits, the bone-length
 *  constraint and the limit angle from the pose direction. Bones are then rotated to point at their simulated child. The plugin's world
 *  damping only matters when the actor moves through the world, which the viewer never does, so it is not modelled. */
interface Particle {
  bone: THREE.Object3D;
  parent: number;               // index of the parent particle, -1 = child of the node's root bone
  child: number;                // first child particle, -1 = leaf
  loc: THREE.Vector3; old: THREE.Vector3;
  poseLoc: THREE.Vector3; poseQuat: THREE.Quaternion;
  length: number; ready: boolean;
  newQuat: THREE.Quaternion; delta: THREE.Quaternion;
}
interface Collider { bone: THREE.Object3D; a: THREE.Vector3; b: THREE.Vector3; radius: number; inside: boolean; wa: THREE.Vector3; wb: THREE.Vector3; wr: number }
interface Group { node: PhysicsNode; anchor: THREE.Object3D; parts: Particle[]; colliders: Collider[]; acc: number }

const sane = (n: string) => THREE.PropertyBinding.sanitizeNodeName(n);

export class SpringBones {
  private groups: Group[] = [];
  private tmpA = new THREE.Vector3();
  private tmpB = new THREE.Vector3();
  private tmpC = new THREE.Vector3();
  private tmpD = new THREE.Vector3();
  private qa = new THREE.Quaternion();
  private qb = new THREE.Quaternion();

  constructor(root: THREE.Object3D, nodes: PhysicsNode[]) {
    const byName = new Map<string, THREE.Object3D>();
    root.traverse((o) => { if ((o as THREE.Bone).isBone) byName.set(o.name, o); });
    const find = (n: string) => byName.get(sane(n)) ?? byName.get(sane(n) + '_1');
    for (const node of nodes) {
      const anchor = find(node.root);
      if (!anchor) continue;
      const skip = new Set(node.exclude.map((n) => find(n)).filter(Boolean) as THREE.Object3D[]);
      const parts: Particle[] = [];
      const add = (bone: THREE.Object3D, parent: number) => {
        if (skip.has(bone) || !(bone as THREE.Bone).isBone) return;
        const idx = parts.length;
        parts.push({
          bone, parent, child: -1, loc: new THREE.Vector3(), old: new THREE.Vector3(), poseLoc: new THREE.Vector3(), poseQuat: new THREE.Quaternion(),
          length: 0, ready: false, newQuat: new THREE.Quaternion(), delta: new THREE.Quaternion(),
        });
        if (parent >= 0 && parts[parent].child < 0) parts[parent].child = idx;
        for (const c of bone.children) add(c, idx);
      };
      for (const r of [anchor, ...(node.extraRoots.map(find).filter(Boolean) as THREE.Object3D[])]) for (const c of r.children) add(c, -1);
      if (!parts.length) continue;
      const colliders: Collider[] = [];
      const mk = (bone: string, a: number[], b: number[], radius: number, inside: boolean) => {
        const o = find(bone);
        if (o) colliders.push({ bone: o, a: new THREE.Vector3(...a), b: new THREE.Vector3(...b), radius, inside, wa: new THREE.Vector3(), wb: new THREE.Vector3(), wr: 0 });
      };
      for (const c of node.capsules) mk(c.bone, c.a, c.b, c.radius, false);
      for (const s of node.spheres) mk(s.bone, s.center, s.center, s.radius, s.inside);
      this.groups.push({ node, anchor, parts, colliders, acc: 0 });
    }
  }

  get count() { return this.groups.reduce((n, g) => n + g.parts.length, 0); }

  update(dt: number) {
    if (!this.groups.length || dt <= 0) return;
    for (const g of this.groups) this.updateGroup(g, Math.min(dt, 0.1));
  }

  private updateGroup(g: Group, dt: number) {
    const { node, parts, anchor } = g;
    // 1. the animated pose (the mixer has just written it): positions / rotations before the simulation touches anything
    anchor.updateWorldMatrix(true, true);
    for (const p of parts) p.bone.matrixWorld.decompose(p.poseLoc, p.poseQuat, this.tmpA);
    const worldScale = anchor.matrixWorld.getMaxScaleOnAxis();
    const anchorPos = this.tmpD.setFromMatrixPosition(anchor.matrixWorld);
    for (const p of parts) {
      const pp = p.parent >= 0 ? parts[p.parent].poseLoc : anchorPos;
      p.length = p.poseLoc.distanceTo(pp);
      if (!p.ready || p.loc.distanceTo(p.poseLoc) > 2 * worldScale) { p.loc.copy(p.poseLoc); p.old.copy(p.poseLoc); p.ready = true; }
    }
    for (const c of g.colliders) {
      c.bone.updateWorldMatrix(true, false);
      c.wa.copy(c.a).applyMatrix4(c.bone.matrixWorld);
      c.wb.copy(c.b).applyMatrix4(c.bone.matrixWorld);
      c.wr = c.radius * c.bone.matrixWorld.getMaxScaleOnAxis();
    }

    // 2. fixed-rate steps
    const h = 1 / (node.fps || 60);
    g.acc = Math.min(g.acc + dt, h * 4);
    const damp = 1 - Math.min(Math.max(node.damping, 0), 1);
    const stiff = Math.min(Math.max(node.stiffness, 0), 1);
    const pr = node.radius * worldScale;
    const gx = node.gravity[0] * h * h, gy = node.gravity[1] * h * h, gz = node.gravity[2] * h * h;
    const limit = THREE.MathUtils.degToRad(node.limitAngle || 0);
    while (g.acc >= h) {
      g.acc -= h;
      for (const p of parts) {
        const parentLoc = p.parent >= 0 ? parts[p.parent].loc : anchorPos;
        const parentPose = p.parent >= 0 ? parts[p.parent].poseLoc : anchorPos;
        // inertia (+ gravity) and the pull back to the animated pose
        const vx = (p.loc.x - p.old.x) * damp, vy = (p.loc.y - p.old.y) * damp, vz = (p.loc.z - p.old.z) * damp;
        p.old.copy(p.loc);
        p.loc.x += vx + gx; p.loc.y += vy + gy; p.loc.z += vz + gz;
        p.loc.addScaledVector(this.tmpA.copy(p.poseLoc).sub(p.loc), stiff);
        for (const c of g.colliders) this.collide(p.loc, pr, c);
        // limit angle from the pose direction (relative to the parent), then the bone length
        const dir = this.tmpA.copy(p.loc).sub(parentLoc);
        const pose = this.tmpB.copy(p.poseLoc).sub(parentPose);
        if (limit > 0 && dir.lengthSq() > 1e-12 && pose.lengthSq() > 1e-12) {
          const ang = dir.angleTo(pose);
          if (ang > limit) {
            const axis = this.tmpC.crossVectors(pose, dir);
            if (axis.lengthSq() > 1e-14) dir.copy(pose).applyAxisAngle(axis.normalize(), limit);
          }
        }
        if (dir.lengthSq() < 1e-14) dir.copy(pose);
        p.loc.copy(parentLoc).addScaledVector(dir.normalize(), p.length);
      }
    }

    // 3. rotate every bone to point at its simulated child (leaves follow their parent's rotation); parents come first
    anchor.getWorldQuaternion(this.qa);
    for (const p of parts) {
      const parentQ = p.parent >= 0 ? parts[p.parent].newQuat : this.qa;
      if (p.child >= 0) {
        const c = parts[p.child];
        const poseDir = this.tmpA.copy(c.poseLoc).sub(p.poseLoc);
        const simDir = this.tmpB.copy(c.loc).sub(p.loc);
        if (poseDir.lengthSq() > 1e-14 && simDir.lengthSq() > 1e-14) p.delta.setFromUnitVectors(poseDir.normalize(), simDir.normalize());
        else p.delta.identity();
      } else if (p.parent >= 0) p.delta.copy(parts[p.parent].delta);
      else p.delta.identity();
      p.newQuat.copy(p.delta).multiply(p.poseQuat);
      p.bone.quaternion.copy(this.qb.copy(parentQ).invert().multiply(p.newQuat));
    }
    anchor.updateWorldMatrix(false, true);
  }

  private collide(loc: THREE.Vector3, pr: number, c: Collider) {
    const ab = this.tmpA.copy(c.wb).sub(c.wa);
    const l2 = ab.lengthSq();
    const t = l2 > 1e-12 ? THREE.MathUtils.clamp(this.tmpB.copy(loc).sub(c.wa).dot(ab) / l2, 0, 1) : 0;
    const closest = this.tmpB.copy(c.wa).addScaledVector(ab, t);
    const d = this.tmpC.copy(loc).sub(closest);
    const dist = d.length();
    const target = c.inside ? Math.max(c.wr - pr, 0) : c.wr + pr;
    if (c.inside ? dist > target : dist < target) {
      if (dist < 1e-9) return;
      loc.copy(closest).addScaledVector(d.multiplyScalar(1 / dist), target);
    }
  }
}
