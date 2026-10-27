export interface IndexModel {
  game: string;
  id: string;
  kind: 'pal' | 'human' | 'object' | string;
  category?: string; // top-level picker tab: pals | weapons | humans | furniture | other
  subcategory?: string | null;
  displayName: string;
  clips: number;
  variantGroups: string[];
  path: string;
  dex?: number | null;
  form?: string | null;
  thumbnail?: string | null; // relative to out/ (served at /models/)
  item?: string | null;
  dexSuffix?: string | null;
  elements?: string[] | null;
  species?: string | null;
  skin?: string | null;
  codeName?: string | null;
}
export interface CategoryInfo { id: string; label: string; count: number; subs: { id: string; label: string; count: number }[] }
export interface GameIndex {
  indexVersion: number;
  game: string;
  name: string;
  categories?: CategoryInfo[];
  models: IndexModel[];
}
export interface GameSummary { name: string; count: number; kinds: Record<string, number> }
export interface VariantOption { variant: number; name: string; label: string; default: boolean }
export interface VariantGroup { name: string; label: string; options: VariantOption[] }
export interface ClipInfo {
  name: string; index: number; duration: number; frameRate: number; frameCount: number;
  loop: boolean; category: string; mountOk?: boolean; default: boolean; visibility?: VisibilityTracks; uvOffset?: UvOffsetTracks; uvOffsetMode?: 'gltf_step';
}
/** Per-material texture offset animation (BDSP eye gaze): material name -> keys in seconds, offsets in UV units (x right, y up). */
export type UvOffsetTracks = Record<string, { times: number[]; values: [number, number][] }>;
export type VisibilityTracks = Record<string, [number, number][]>;
export interface PartInfo { node: string; label: string; lod: number; visible: boolean; group?: string; option?: string; accessory?: boolean }
export interface PaletteEntry { label: string; materials: string[]; divide: number; indices: number[]; layers: number[]; zones: string[];
  /** Colours the model did not ship with (eyes of the Common NPC faces): the UI labels the row as custom. */
  custom?: boolean;
  /** One swatch per (layer 2, layer 3) zone pair, zones 2j+2 / 2j+3 for pair j (labels in `pairLabels`). */
  pairs?: boolean; pairLabels?: string[];
  /** Single-layer custom rows: label per zone (zone 1 = the model's own base layer, not offered). */
  zoneLabels?: string[];
  /** The palette's layer 1 is the skin: the viewer drives its zone from the selected skin tone variant. */
  skinLink?: boolean;
  /** 1-based mask layers (besides 1) that point at a skin zone and follow the skin tone too. */
  skinLayers?: number[];
  /** Offered zones in order (0 = same zone as the skin, labelled `noneLabel`); default = every zone but the first. */
  offer?: number[]; noneLabel?: string;
  /** Zones 1..own are the model's own table; later zones are borrowed colours (the player's hair colours), labelled in `zoneLabels`. */
  own?: number }
export interface PartOption { id: string; label: string; default: boolean; nodes: string[]; palettes?: PaletteEntry[];
  /** Hidden-by-default beard meshes of this face: the Appearance section offers a Facial hair toggle. */
  facialHair?: string[];
  /** Variant (by name) this option follows: picking that variant selects this option (brow set of an eye cell). */
  variant?: string }
export interface PartGroup { name: string; label: string; options: PartOption[] }
export interface PropInfo { bone: string; side: string; clips: string[]; always?: boolean }
/** A default held item (Palworld: the weapon a Pal carries) with its local offset on the prop bone (glTF metres, xyzw). */
export interface HeldItemDefault { item: string; bone: string; position: number[]; rotation: number[]; scale?: number[]; always?: boolean }
/** A tower boss riding this Pal: the boss model, the Pal bone it sits on, the offset in that bone's space, and the Pal's in-game size. */
/** rotationSpace 'model': the rotation is in the Pal model's frame and ignores the bone's (the seat has bAbsoluteRotation); only `position` follows the bone. */
export interface BossRide { boss: string; bone: string; position: number[]; rotation: number[]; rotationSpace?: 'model'; palScale: number; pairs?: ClipPair[] }
export interface MountInfo { pal: string; bone: string; position: number[]; rotation: number[]; rotationSpace?: 'model'; palScale: number; scale?: number[]; pairs?: ClipPair[] }
/** While the boss plays `boss`, the Pal plays `pal` in order (montage play `rate`; `loop` segments share the time the others leave). From the
 *  game's tables (marker WazaMontage / Anim List <-> the Pal's waza action montages), see PalExtract BossClipPairs. */
export interface ClipPair { boss: string; pal: { clip: string; rate?: number; loop?: boolean; from?: number; to?: number }[] }
/** One KawaiiPhysics node of the game's anim layer: a bone chain below `root` simulated as cloth / hair (glTF metres, bone-local colliders). */
export interface PhysicsNode {
  root: string; extraRoots: string[]; exclude: string[]; damping: number; stiffness: number; radius: number; limitAngle: number; gravity: number[]; fps: number;
  capsules: { bone: string; a: number[]; b: number[]; radius: number }[];
  spheres: { bone: string; center: number[]; radius: number; inside: boolean }[];
}
export interface Manifest {
  manifestVersion: number;
  heldItem?: HeldItemDefault;
  /** Every held item (heldItem first); a pair of weapons has one per hand. */
  heldItems?: HeldItemDefault[];
  game: string;
  id: string;
  kind: string;
  displayName: string;
  files: { model: string };
  variantGroups: VariantGroup[];
  clips: ClipInfo[];
  defaultClip?: string;
  parts: PartInfo[];
  partGroups?: PartGroup[];
  props?: PropInfo[];
  /** Z-A Holocast material sets (`holo`, `holo_red`): hologram presets from the data */
  holocast?: HolocastPreset[];
  stats: { joints: number; meshes: number; triangles: number; textures: number; clips: number; glbBytes: number };
  extras?: { dex?: number; dexSuffix?: string; form?: string; species?: string; elements?: string[]; skin?: string; codeName?: string; rarity?: number; bossRide?: BossRide; physics?: PhysicsNode[]; auras?: { color: number[]; intensity: number; width: number; curve: number[][] }[]; mount?: MountInfo; /** ride clips carry the seat in the mount's frame: attach at its model origin, at tower size */ seatInClip?: boolean };
}

/** A calibrated gameplay screenshot (refs/<game>/index.json, written by tools/lightrefs/build_presets.py). */
export interface RefEntry {
  scene: string; sceneLabel: string; preset: string; slug: string; model: string; name: string; context: string;
  clip: string; frame: number; camera: { fov: number; position: [number, number, number]; target: [number, number, number] };
  image: string; width: number; height: number; dE: number | null; video?: string | null; timecode?: string | null;
}
export interface RefIndex { game: string; builtAt: string; refs: RefEntry[] }

export interface HolocastPreset {
  name: string;
  label: string;
  color: [number, number, number];
  scanlineSpeed: number;
  scanlineDepth: number;
  scanlineTiling: number;
  scanGradationTiling: number;
}
