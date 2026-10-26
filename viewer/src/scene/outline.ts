import * as THREE from 'three';

/**
 * Cartoon outline (inverted hull): a back-face copy of each opaque mesh whose vertices are pushed outward along
 * the smoothed normal by a constant number of screen pixels. Skinning is applied before the push, so it follows
 * every pose; it needs no post pass, so transparent PNG exports, the lens shift and the probe renders are unchanged.
 */

/** `tinted`: the line is a dark shade of the surface it hugs (its own albedo x TINT_DARKNESS) instead of one flat colour, like the game's outlines. */
/** `style` 'sprite' = the 3DS battle-sprite look (spriteStyle.ts): low-res render + 1-px screen-space lines, `pixel` px per sprite pixel. */
export interface OutlineState { on: boolean; width: number; color: string; tinted: boolean; style?: 'hull' | 'sprite'; pixel?: number }
export const DEFAULT_OUTLINE: OutlineState = { on: false, width: 2, color: '#14121a', tinted: false, style: 'hull', pixel: 3 };
// FITTED: tinted outline darkness, eyeballed against game outlines, 2026-10-19 (engine-rules debt)
const TINT_DARKNESS = 0.3;

/** Reference viewport height: `width` is in pixels at this height and scales with the canvas (exports match the live view). */
const REF_HEIGHT = 800;

/** x = width in drawing-buffer pixels, y/z = drawing-buffer size. Shared by every outline material. */
export const outlineUniform = { value: new THREE.Vector3(2, 1, 1) };

export function updateOutlineUniform(width: number, bufW: number, bufH: number) {
  outlineUniform.value.set(width * bufH / REF_HEIGHT, bufW, bufH);
}

/**
 * Position-welded vertex normals (split vertices at hard edges would open gaps in the hull), stored as the
 * `outlineNormal` attribute of the geometry once.
 */
export function ensureOutlineNormals(geo: THREE.BufferGeometry): boolean {
  if (geo.getAttribute('outlineNormal')) return true;
  const pos = geo.getAttribute('position'), nrm = geo.getAttribute('normal');
  if (!pos || !nrm) return false;
  const n = pos.count;
  const key = (i: number) => `${Math.round(pos.getX(i) * 1e5)},${Math.round(pos.getY(i) * 1e5)},${Math.round(pos.getZ(i) * 1e5)}`;
  const sum = new Map<string, [number, number, number]>();
  for (let i = 0; i < n; i++) {
    const k = key(i);
    let s = sum.get(k);
    if (!s) { s = [0, 0, 0]; sum.set(k, s); }
    s[0] += nrm.getX(i); s[1] += nrm.getY(i); s[2] += nrm.getZ(i);
  }
  const out = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const s = sum.get(key(i))!;
    const l = Math.hypot(s[0], s[1], s[2]) || 1;
    out[i * 3] = s[0] / l; out[i * 3 + 1] = s[1] / l; out[i * 3 + 2] = s[2] / l;
  }
  geo.setAttribute('outlineNormal', new THREE.BufferAttribute(out, 3));
  return true;
}

export function makeOutlineMaterial(src: THREE.MeshStandardMaterial, color: string, tinted: boolean): THREE.MeshBasicMaterial {
  const cut = src.alphaTest > 0 && !!src.map;
  const mat = new THREE.MeshBasicMaterial({
    color: tinted ? src.color.clone().multiplyScalar(TINT_DARKNESS) : color, side: THREE.BackSide, alphaTest: cut ? src.alphaTest : 0, map: cut || tinted ? src.map : null,
    polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1, toneMapped: false,
  });
  mat.onBeforeCompile = (shader) => {
    shader.uniforms.uOutline = outlineUniform;
    shader.vertexShader = shader.vertexShader
      .replace('void main() {', 'attribute vec3 outlineNormal;\nuniform vec3 uOutline;\nvoid main() {')
      .replace('#include <project_vertex>', `#include <project_vertex>
        vec3 oN = outlineNormal;
        #ifdef USE_SKINNING
          mat4 oSkin = skinWeight.x * boneMatX + skinWeight.y * boneMatY + skinWeight.z * boneMatZ + skinWeight.w * boneMatW;
          oSkin = bindMatrixInverse * oSkin * bindMatrix;
          oN = (oSkin * vec4(oN, 0.0)).xyz;
        #endif
        vec2 oD = (projectionMatrix * vec4(normalize(normalMatrix * oN), 0.0)).xy * uOutline.yz;
        if (dot(oD, oD) > 1e-12) gl_Position.xy += normalize(oD) * (2.0 * uOutline.x) / uOutline.yz * gl_Position.w;`);
  };
  mat.customProgramCacheKey = () => 'outline' + (cut ? '|cut' : '');
  return mat;
}

/** Outline copy of `m` (a child with an identity transform: it follows pose, visibility and mirroring), or null if `m` should have none. */
export function makeOutlineMesh(m: THREE.Mesh, color: string, tinted: boolean): THREE.Mesh | null {
  const srcs = ([] as THREE.Material[]).concat(m.material);
  const ok = (s: THREE.Material): s is THREE.MeshStandardMaterial => {
    const sm = s as THREE.MeshStandardMaterial;
    return !!sm.isMeshStandardMaterial && !sm.transparent && sm.depthWrite && !sm.userData?.gas && !sm.name.includes('~');
  };
  if (!srcs.some(ok) || !ensureOutlineNormals(m.geometry)) return null;
  const mats = srcs.map((s) => {
    if (ok(s)) return makeOutlineMaterial(s, color, tinted);
    const hide = new THREE.MeshBasicMaterial(); hide.visible = false; return hide;
  });
  const mat = Array.isArray(m.material) ? mats : mats[0]; // an array material needs geometry groups, a single one does not
  const sk = m as THREE.SkinnedMesh;
  let p: THREE.Mesh;
  if (sk.isSkinnedMesh) { const q = new THREE.SkinnedMesh(sk.geometry, mat); q.bind(sk.skeleton, sk.bindMatrix); q.bindMode = sk.bindMode; p = q; }
  else p = new THREE.Mesh(m.geometry, mat);
  if (m.morphTargetInfluences) p.morphTargetInfluences = m.morphTargetInfluences;
  p.userData.outline = true;
  p.frustumCulled = false; p.castShadow = false; p.receiveShadow = false;
  p.renderOrder = m.renderOrder;
  p.raycast = () => {};
  return p;
}
