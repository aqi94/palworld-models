import * as THREE from 'three';
import type { GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js';

interface Mapping { material: number; variants: number[] }
interface Entry {
  mesh: THREE.Mesh; defaultMat: THREE.Material | THREE.Material[]; mappings: Mapping[];
  /** The last picked material that changes more than the texture transform (a skin), and the last picked transform-only one
   * (an expression-atlas cell). The mesh shows the skin with the cell's offset applied, whichever was picked last. */
  base?: THREE.Material; cell?: THREE.Material;
}

/** Texture slots that carry the atlas cell offset (KHR_texture_transform). */
const MAP_SLOTS = ['map', 'emissiveMap', 'alphaMap', 'normalMap', 'roughnessMap', 'metalnessMap', 'aoMap'] as const;
type Slotted = Record<(typeof MAP_SLOTS)[number], THREE.Texture | null>;

/** True when `m` is `ref` or differs from it only by its texture transforms (same images and factors): an atlas-cell variant. */
function transformOnly(m: THREE.Material, ref: THREE.Material): boolean {
  const a = m as unknown as Slotted, b = ref as unknown as Slotted;
  if (m === ref) return true;
  if (!MAP_SLOTS.every((k) => (a[k]?.image ?? null) === (b[k]?.image ?? null))) return false;
  // a skin that keeps the textures but changes a factor (an emissive glow) is still a skin
  const x = m as THREE.MeshStandardMaterial, y = ref as THREE.MeshStandardMaterial;
  return x.color.equals(y.color) && x.emissive.equals(y.emissive) && x.emissiveIntensity === y.emissiveIntensity
    && x.roughness === y.roughness && x.metalness === y.metalness && x.opacity === y.opacity && x.alphaTest === y.alphaTest
    && x.transparent === y.transparent;
}

/** Minimal KHR_materials_variants support (three's GLTFLoader does not implement it). */
export class VariantSwitcher {
  private entries: Entry[] = [];
  private materialCache = new Map<number, THREE.Material>();
  readonly names: string[];
  current = -1;
  onMaterialsChanged?: () => void;

  constructor(private gltf: GLTF) {
    const ext = (gltf.parser.json.extensions ?? {}).KHR_materials_variants;
    this.names = (ext?.variants ?? []).map((v: { name: string }) => v.name);
    const parser = gltf.parser;
    gltf.scene.traverse((o) => {
      const mesh = o as THREE.Mesh;
      if (!mesh.isMesh) return;
      const assoc = parser.associations.get(mesh) as { meshes?: number; primitives?: number } | undefined;
      if (assoc?.meshes === undefined || assoc.primitives === undefined) return;
      const prim = parser.json.meshes[assoc.meshes]?.primitives?.[assoc.primitives];
      const mappings: Mapping[] | undefined = prim?.extensions?.KHR_materials_variants?.mappings;
      if (mappings?.length) this.entries.push({ mesh, defaultMat: mesh.material, mappings });
    });
  }

  get available() { return this.names.length > 0 && this.entries.length > 0; }

  private async material(idx: number): Promise<THREE.Material> {
    let m = this.materialCache.get(idx);
    if (!m) {
      m = (await this.gltf.parser.getDependency('material', idx)) as THREE.Material;
      this.materialCache.set(idx, m);
    }
    return m;
  }

  private kinds = new Map<Entry, Map<string, boolean>>();

  private async isCellGroup(e: Entry, group?: number[]): Promise<boolean> {
    if (!group || group.length < 2 || Array.isArray(e.defaultMat)) return false;
    const key = group.join(',');
    let per = this.kinds.get(e);
    if (!per) this.kinds.set(e, (per = new Map()));
    let v = per.get(key);
    if (v === undefined) {
      const def = e.defaultMat as THREE.Material;
      const mats = await Promise.all(group.map((g) => {
        const map = e.mappings.find((x) => x.variants.includes(g));
        return map ? this.material(map.material) : Promise.resolve(def);
      }));
      v = mats.every((m) => transformOnly(m, def));
      per.set(key, v);
    }
    return v;
  }

  private composed = new Map<string, THREE.Material>();

  /** `base` (the picked skin) wearing the texture transform of `cell` (the picked atlas cell). */
  private compose(base: THREE.Material, cell: THREE.Material): THREE.Material {
    const key = `${base.uuid}|${cell.uuid}`;
    let m = this.composed.get(key);
    if (!m) {
      m = base.clone();
      const out = m as unknown as Slotted, src = cell as unknown as Slotted;
      for (const k of MAP_SLOTS) {
        const t = out[k], c = src[k];
        if (!t || !c) continue;
        const tt = t.clone();
        tt.offset.copy(c.offset); tt.repeat.copy(c.repeat); tt.rotation = c.rotation; tt.center.copy(c.center);
        tt.matrixAutoUpdate = true; tt.needsUpdate = true;
        out[k] = tt;
      }
      this.composed.set(key, m);
    }
    return m;
  }

  private gens = new Map<string, number>();

  /** Entries a variant group recolours: their material differs between the group's options. Other groups' meshes
   * (and meshes no option changes) are left alone, so groups combine instead of resetting each other (a skin-tone
   * pick used to put the coloring group's meshes back to their default). Without `group`, every entry. */
  private owned(group?: number[]): Entry[] {
    if (!group || group.length < 2) return this.entries;
    return this.entries.filter((e) => {
      const mats = new Set(group.map((v) => e.mappings.find((m) => m.variants.includes(v))?.material ?? -1));
      return mats.size > 1;
    });
  }

  /** Loads every material of the variant first, then swaps the group's meshes in one synchronous pass. A select that
   * a newer one in the same group overtook while loading is dropped (returns false): interleaved selects used to
   * leave each mesh on whichever variant reached it last (half the hair one colour, half the other). */
  async select(variantIndex: number, group?: number[]): Promise<boolean> {
    const key = group ? group.join(',') : '*';
    const gen = (this.gens.get(key) ?? 0) + 1;
    this.gens.set(key, gen);
    const entries = this.owned(group);
    const mats = await Promise.all(entries.map((e) => {
      const map = e.mappings.find((m) => m.variants.includes(variantIndex));
      return map ? this.material(map.material) : Promise.resolve(e.defaultMat);
    }));
    if (this.gens.get(key) !== gen) return false;
    // An atlas group (every option of it only moves the texture transform) keeps the skin picked in another group; a skin
    // group keeps the cell. The kind is a property of the group, not of one material: cell 1 looks like the default skin.
    const cellKind = await Promise.all(entries.map((e) => this.isCellGroup(e, group)));
    entries.forEach((e, i) => {
      const def = e.defaultMat as THREE.Material, m = mats[i];
      if (Array.isArray(e.defaultMat) || Array.isArray(m)) { e.mesh.material = m; return; }
      if (cellKind[i]) e.cell = m; else { e.base = m; if (m === def) e.base = undefined; }
      const base = e.base ?? def;
      e.mesh.material = !e.cell ? base : base === def ? e.cell : this.compose(base, e.cell);
    });
    this.current = variantIndex;
    this.onMaterialsChanged?.();
    return true;
  }

  /** Average opaque colour of the largest base-colour texture of a variant (for the swatch). */
  async sampleColor(variantIndex: number, group?: number[]): Promise<string | null> {
    try {
      let best: { img: CanvasImageSource | null; area: number; tint: THREE.Color } | null = null;
      for (const e of this.owned(group)) {
        const map = e.mappings.find((m) => m.variants.includes(variantIndex));
        if (!map) continue;
        const mat = (await this.material(map.material)) as THREE.MeshStandardMaterial;
        const img = mat.map?.image as (CanvasImageSource & { width: number; height: number }) | undefined;
        const area = img ? img.width * img.height : 0;
        if (!best || area > best.area) best = { img: img ?? null, area, tint: mat.color };
      }
      if (!best) return null;
      // Variants that only change the material colour factor (skin tones) share one texture: apply the factor.
      if (!best.img) return best.tint.getStyle();
      const c = document.createElement('canvas');
      c.width = c.height = 32;
      const ctx = c.getContext('2d', { willReadFrequently: true })!;
      ctx.drawImage(best.img, 0, 0, 32, 32);
      const d = ctx.getImageData(0, 0, 32, 32).data;
      let r = 0, g = 0, b = 0, n = 0;
      for (let i = 0; i < d.length; i += 4) {
        if (d[i + 3] < 200) continue;
        r += d[i]; g += d[i + 1]; b += d[i + 2]; n++;
      }
      if (!n) return null;
      const avg = new THREE.Color().setRGB(r / n / 255, g / n / 255, b / n / 255, THREE.SRGBColorSpace);
      return avg.multiply(best.tint).getStyle();
    } catch {
      return null;
    }
  }
}
