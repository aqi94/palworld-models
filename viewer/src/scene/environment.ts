// Lighting follows the usual game-engine model (UE/Unity HDRP/Frostbite):
// light colour as colour temperature, key + back (rim) light + sky/ground
// ambient (the L1 approximation of a light probe), exposure + filmic tone
// mapping, and named profiles that swap the whole set (post-process volumes).


export type ToneMap = 'linear' | 'aces' | 'agx' | 'neutral' | 'hable';

/** John Hable's piecewise filmic curve ("Filmic Worlds" user params); Game Freak's `PfxTonemapMode 2` stores exactly these. */
export interface HableParams {
  toeStrength: number;     // 0..1
  toeLength: number;       // 0..1
  shoulderStrength: number; // F-stops of extra white point, 0..1 in the UI space
  shoulderLength: number;  // 0..1
  shoulderAngle: number;   // 0..1
  gamma: number;
}

export interface Environment {
  ambient: number;     // hemisphere intensity
  key: number;         // key light intensity
  azimuth: number;     // degrees, 0 = straight in front of the model (+Z), positive toward +X
  elevation: number;   // degrees above the horizon
  keyTemp: number;     // key colour temperature, Kelvin
  back: number;        // back/rim light intensity (window, street lamp, neon)
  backAzimuth: number; // degrees, 180 = straight behind the model
  backElevation: number;
  backTemp: number;
  skyTemp: number;     // ambient sky colour temperature
  ground: string;      // ambient ground (bounce) colour
  warmth: number;      // -1 cool .. 1 warm, white-balance shift on top of everything
  shadow: number;      // 0..1 strength of the shadow colour
  rim: number;         // multiplier on the material rim term
  detail: number;      // normal map / AO strength
  exposure: number;
  toneMapping: ToneMap;
  contrast: number;
  saturation: number;
  gi: number;          // ambient tint toward the material's shadow colours (x ShadowingGIGain)
  // Optional explicit light colours (sRGB hex). When present they replace the Kelvin fields, which the
  // game-data presets cannot express (a pink GI tint has no blackbody temperature). Moving a Kelvin slider clears them.
  keyColor?: string;
  skyColor?: string;
  backColor?: string;
  hable?: HableParams; // only read when toneMapping === 'hable'
  /** Per material category (`.trmtr` CategoryLabel, glTF `extras.category`) light adjustments, like the game's
   *  `dir_N` / `GILightCommon.ColorN` / `rim_N` sets. Absent or missing category = the base lights unchanged. */
  categories?: Record<string, CategoryLight>;
}

/** Multipliers on the base key / ambient for one material category, plus that category's own rim light.
 *  Colours are sRGB hex; a scale of 1 and white is neutral. */
export interface CategoryLight {
  key?: number; keyColor?: string;
  ambient?: number; ambientColor?: string;
  rim?: number; rimColor?: string; rimPower?: number;
}

/** Z-A `CategoryLabel` values (verified over every Z-A .trmtr). SV numbers its categories differently (trainer skin 5). */
export const CATEGORY_LABELS: Record<string, string> = { '4': 'Body (skin, hair, eyes)', '5': 'Clothing', '6': 'Pokémon', '0': 'Props' };

/** Tuned against the VS screenshots (neutral white light, linear output). */
export const DEFAULT_ENV: Environment = {
  ambient: 1.9, key: 1.2, azimuth: -26.6, elevation: 38.8, keyTemp: 6500,
  back: 0, backAzimuth: 160, backElevation: 25, backTemp: 6500, skyTemp: 6500, ground: '#f6ecd8',
  warmth: 0, shadow: 0.65, rim: 1, detail: 0.5, exposure: 1, toneMapping: 'linear', contrast: 1, saturation: 1, gi: 1,
};

/** Scarlet/Violet's master post-process curve (romfs/env/master/master.trlgt Global.Pfx*), the default when 'hable' is picked by hand. */
export const DEFAULT_HABLE: HableParams = { toeStrength: 0.085, toeLength: 0.5, shoulderStrength: 0.9999, shoulderLength: 0.5, shoulderAngle: 0, gamma: 0.967 };

/** `game` records which game's data a preset came from (provenance only; every preset is offered for every model). */
export interface LightingPreset { id: string; label: string; env: Environment; game?: string }

const p = (id: string, label: string, e: Partial<Environment>, game?: string): LightingPreset => ({ id, label, env: { ...DEFAULT_ENV, ...e }, game });

// Temperatures are the standard reference values: sun 5500-6500 K, overcast
// sky ~7000 K, incandescent / warm LED 2700-3000 K, fluorescent ~4300 K,
// sodium street lamp ~2000 K, moonlight rendered ~4100 K at low exposure.
// Calibrated entries say so in their comment.
export const LIGHTING_PRESETS: LightingPreset[] = [
  p('studio', 'Studio', {}),
  // Uncalibrated looks (not fitted to Palworld screenshots): a bright outdoor day and a warm evening.
  p('daylight', 'Daylight', { ambient: 2.2, key: 1.6, azimuth: -35, elevation: 50, keyTemp: 5800, skyTemp: 7500, shadow: 0.5 }),
  p('evening', 'Evening', { ambient: 1.4, key: 1.3, azimuth: 60, elevation: 12, keyTemp: 3200, skyTemp: 8000, back: 0.6, backTemp: 9000, shadow: 0.7 }),
  // Raid boss arena: colours from BP_RaidBossLightingVolume (LightColor #694163, LightAlbedo #B150B3, SkyLightIntensity 0.2 vs the
  // normal volume's 1.4); the Yakushima boss variant swaps LightColor to #4C7953. Intensities are eyeballed.
  p('raid-boss', 'Raid boss (purple)', { ambient: 0.6, key: 1.4, azimuth: -30, elevation: 30, keyColor: '#B150B3', skyColor: '#694163', back: 0.8, backColor: '#B150B3', shadow: 0.8 }, 'Palworld'),
  p('raid-boss-green', 'Raid boss (green)', { ambient: 0.6, key: 1.4, azimuth: -30, elevation: 30, keyColor: '#6FB87A', skyColor: '#4C7953', back: 0.8, backColor: '#6FB87A', shadow: 0.8 }, 'Palworld'),
  // Uncalibrated: the time-of-day curve and the Forbidden Laboratory fight's red light live in level data / plugin code that is not extracted.
  p('night', 'Night (moonlit)', { ambient: 0.45, key: 0.55, azimuth: -40, elevation: 45, keyTemp: 4100, skyTemp: 12000, back: 0.3, backTemp: 9000, shadow: 0.8 }),
  p('red-arena', 'Red arena (approx.)', { ambient: 0.5, key: 1.2, azimuth: -30, elevation: 25, keyColor: '#FF3A1A', skyColor: '#5A1008', back: 1, backColor: '#FF2A10', shadow: 0.8 }),
];

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

export function matchPreset(env: Environment): string | null {
  const keys = (e: Environment) => (Object.keys(e) as (keyof Environment)[]).filter((k) => e[k] !== undefined);
  for (const pr of LIGHTING_PRESETS) {
    const ks = new Set([...keys(pr.env), ...keys(env)]);
    if ([...ks].every((k) => same(pr.env[k], env[k]))) return pr.id;
  }
  return null;
}

/** Colour temperature -> linear RGB (0..1), Tanner Helland's blackbody fit, normalised so 6500 K is white. */
export function kelvinToRGB(k: number): [number, number, number] {
  const raw = (kk: number) => {
    const t = Math.min(40000, Math.max(1000, kk)) / 100;
    const r = t <= 66 ? 255 : 329.698727446 * Math.pow(t - 60, -0.1332047592);
    const g = t <= 66 ? 99.4708025861 * Math.log(t) - 161.1195681661 : 288.1221695283 * Math.pow(t - 60, -0.0755148492);
    const b = t >= 66 ? 255 : t <= 19 ? 0 : 138.5177312231 * Math.log(t - 10) - 305.0447927307;
    // sRGB-encoded fit -> linear
    return [r, g, b].map((c) => { const s = Math.min(255, Math.max(0, c)) / 255; return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4); });
  };
  const c = raw(k), w = raw(6500);
  const out = c.map((v, i) => v / w[i]);
  const m = Math.max(...out);
  return [out[0] / m, out[1] / m, out[2] / m];
}
