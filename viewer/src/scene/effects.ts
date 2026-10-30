import * as THREE from 'three';

/**
 * Palworld effect shaders, rebuilt from the material parameters (the cooked graph is not in the pak). The importer bakes
 * one frame into the GLB (emissive + alpha) and ships the parameters as material extras:
 *
 * - `extras.fire` (M_Pal_Fire: Foxparks, Nitemary, Flambelle, Astegon wings... 35 Pals): two panning noise maps -> 3-colour
 *   HDR ramp color3 -> color2 (midPoint) -> color1, a hot root band along V (V = 1 at the root), noise-threshold alpha.
 * - `extras.vfx2` (M_Pal_Manticore_Fire: Azurmane's mane, Blazehowl): two scrolling texture layers warped by a distortion
 *   map; their product blends color1 -> color2 (brightness rgbMin..rgbMax) and gives the alpha, faded across the strip (maskV) and at grazing angles.
 * - `extras.dream` (M_Pal_Dream: Daedream plumage, Smokie manes): the gradient map is the baked look; a panning noise shimmers it.
 *
 * Fire and vfx2 mirror `fire_shade` / `vfx2_shade` / `filmic` in pipeline/importers/palworld/importer.py (keep them in step).
 * Like Unreal's translucency they blend HDR colour x alpha and tone-map afterwards, so a 200-bright colour at 30 % alpha still
 * glows: the fragment writes filmic(hdr * alpha) with premultiplied blending. All run on the shared effect clock (seconds of
 * unpaused playback, like the gas shells; the GIF export sets it per frame and rounds the speeds to loop, setEffectLoop).
 */
interface TexRef { index: number }
export interface FireParams {
  color1: number[]; color2: number[]; color3: number[]; fresnelColor?: number[];
  midPoint: number; rootColor: number; rootAlpha: number; vertexRoot: number; alpha: number; alphaEdge: number;
  fadeAlpha: number; rgbMin: number; rgbMax: number; fresnelPower: number;
  mainTiling: number[]; mainSpeed: number[]; noiseTiling: number[]; noiseSpeed: number[];
  distortion: number; offsetScale: number; timeScale: number;
  mainTex?: TexRef; noiseTex?: TexRef;
}
interface Vfx2Layer { tiling: number[]; offset: number[]; scroll: number[]; distort: number[]; clamp: boolean[]; rotate: number; rgb: number; alpha: number }
export interface Vfx2Params {
  color1: number[]; color2: number[]; layer1: Vfx2Layer; layer2: Vfx2Layer;
  distTiling: number[]; distScroll: number[]; distOffset: number[]; distortion: number;
  rgbMin: number; rgbMax: number; alphaMin: number; alphaMax: number; maskU: number; maskV: number; fresnel: number;
  distort1: boolean; alphaMode: 'range' | 'add' | 'opaque';
  tex1?: TexRef; tex2?: TexRef; texDist?: TexRef;
}
export interface DreamParams { noiseTex?: TexRef; noiseSpeed: number[]; noiseTiling: number; mainScroll: number[] }

const EXPOSURE = 1.0; // importer.FIRE_EXPOSURE
const CM = 0.01; // Unreal centimetres -> glTF metres (M_Pal_Fire's OffsetScale)

type Kind = 'fire' | 'vfx2' | 'dream' | 'rainbow';
interface FxState {
  kind: Kind;
  uniforms: Record<string, { value: unknown }>;
  samplers: string[]; // uniform per texture index returned by installEffect
  ready: boolean;
  /** Scroll-speed uniforms (setEffectLoop rounds them) with their game values. */
  speeds: string[];
  base?: Record<string, number | THREE.Vector2>;
}

const v3 = (c: number[] | undefined) => new THREE.Vector3(c?.[0] ?? 0, c?.[1] ?? 0, c?.[2] ?? 0);
const v2 = (c: number[] | undefined, d = 0) => new THREE.Vector2(c?.[0] ?? d, c?.[1] ?? d);
const f = (x: number) => x.toFixed(4);

// importer.filmic: per-channel ACES curve blended with the matrixed ACES fit (Hill, as three's ACESFilmicToneMapping).
const HUE_SHIFT = 0.25; // importer.FILMIC_HUE_SHIFT
const FILMIC = `vec3 fxFilmic( vec3 c ) {
	const mat3 aIn = mat3( 0.59719, 0.07600, 0.02840, 0.35458, 0.90834, 0.13383, 0.04823, 0.01566, 0.83777 );
	const mat3 aOut = mat3( 1.60475, -0.10208, -0.00327, -0.53108, 1.10813, -0.07276, -0.07367, -0.00605, 1.07602 );
	c = max( c, vec3( 0.0 ) );
	vec3 v = aIn * ( c / 0.6 );
	v = ( v * ( v + 0.0245786 ) - 0.000090537 ) / ( v * ( 0.983729 * v + 0.4329510 ) + 0.238081 );
	vec3 aces = clamp( aOut * v, 0.0, 1.0 );
	vec3 perChannel = clamp( ( c * ( 2.51 * c + 0.03 ) ) / ( c * ( 2.43 * c + 0.59 ) + 0.14 ), 0.0, 1.0 );
	return mix( perChannel, aces, ${HUE_SHIFT.toFixed(2)} );
}
float fxFresnel( vec3 n, float p ) {
	vec3 fxV = isOrthographic ? vec3( 0.0, 0.0, 1.0 ) : normalize( vViewPosition );
	return pow( 1.0 - clamp( abs( dot( n, fxV ) ), 0.0, 1.0 ), max( p, 0.01 ) );
}`;

/** Final write: premultiplied filmic(hdr * exposure * alpha) (see the header). */
const WRITE = `outgoingLight = fxFilmic( hdr * ${EXPOSURE.toFixed(3)} * a );
		diffuseColor.a = a;`;

type Shader = { vertexShader: string; fragmentShader: string; uniforms: Record<string, unknown> };
const BV = '#include <begin_vertex>', OF = '#include <opaque_fragment>';

// M_Pal_Fire, transcribed from its cooked shaders (importer.fire_shade has the derivation): the distorted main-noise sample m
// drives the colour ramp, the alpha and (vertex shader) a push along the normal toward the flame tip.
const FIRE_COMMON = `uniform sampler2D uFxMain;
uniform sampler2D uFxNoise;
uniform float uFxTime;
uniform vec2 uFxMainTiling, uFxMainSpeed, uFxNoiseTiling, uFxNoiseSpeed;
uniform float uFxDistortion, uFxOn;
float fxMain( vec2 uv ) {
	float nz = texture2D( uFxNoise, uv * uFxNoiseTiling + fract( uFxTime * uFxNoiseSpeed ) ).r;
	return texture2D( uFxMain, uv * uFxMainTiling + fract( uFxTime * uFxMainSpeed ) + nz * uFxDistortion * 0.1 ).r;
}
`;

function patchFire(shader: Shader, u: FxState['uniforms']) {
  if (!shader.vertexShader.includes(BV) || !shader.fragmentShader.includes(OF)) { console.warn('fire: chunk not found'); return; }
  shader.vertexShader = FIRE_COMMON + 'uniform float uFxOffset, uFxVertexRoot;\nvarying vec2 vFxUv;\n' + shader.vertexShader.replace(BV, `${BV}
	vFxUv = uv;
	{
		float ws = clamp( ( fxMain( uv ) - 0.2 ) * 1.25, 0.0, 1.0 );
		transformed += normalize( normal ) * ( uFxOn * uFxOffset * ws * ws * ( 3.0 - 2.0 * ws ) * clamp( ( 1.0 - uv.y ) * ( 1.0 - uFxVertexRoot ), 0.0, 1.0 ) );
	}`);
  shader.fragmentShader = FIRE_COMMON + `
uniform vec3 uFxC1, uFxC2, uFxC3, uFxFresnelColor;
uniform float uFxMid, uFxRootColor, uFxRootAlpha, uFxAlpha, uFxAlphaEdge, uFxRgbMin, uFxRgbMax, uFxFresnelPow;
varying vec2 vFxUv;
` + shader.fragmentShader.replace('void main() {', FILMIC + '\nvoid main() {').replace(OF, `
	if ( uFxOn > 0.5 ) {
		float v = vFxUv.y;
		float m = fxMain( vFxUv );
		float tg = clamp( ( v * ( m + uFxRootColor ) - uFxRgbMin ) / ( uFxRgbMax - uFxRgbMin ), 0.0, 1.0 );
		float g = tg * tg * ( 3.0 - 2.0 * tg );
		vec3 hdr = g <= uFxMid ? mix( uFxC3, uFxC2, g / max( uFxMid, 1e-4 ) ) : mix( uFxC2, uFxC1, ( g - uFxMid ) / max( 1.0 - uFxMid, 1e-4 ) );
		vec3 fxV = isOrthographic ? vec3( 0.0, 0.0, 1.0 ) : normalize( vViewPosition );
		hdr += uFxFresnelColor * ( pow( max( 1.0 - max( dot( normal, fxV ), 0.0 ), 1e-4 ), uFxFresnelPow ) * 0.96 + 0.04 );
		float lo = clamp( uFxAlpha, 0.0, 1.0 - uFxAlphaEdge ), hi = clamp( uFxAlpha + uFxAlphaEdge, 0.0, 1.0 );
		float ta = clamp( ( m * v * v * uFxRootAlpha * ( 1.0 + m ) - lo ) / max( hi - lo, 1e-4 ), 0.0, 1.0 );
		float a = ta * ta * ( 3.0 - 2.0 * ta );
		${WRITE}
	}
	${OF}`);
  Object.assign(shader.uniforms, u);
}

// Transcribed from the cooked pixel shader of M_Pal_Manticore_Fire and its instances (importer.vfx2_shade has the derivation).
function vfx2Layer(i: number, L: Vfx2Layer, warp: boolean) {
  const cu = L.clamp?.[0] ? 'clamp( uu, 0.0, 1.0 )' : 'uu', cv = L.clamp?.[1] ? 'clamp( vv, 0.0, 1.0 )' : 'vv';
  const rot = L.rotate ? `
	float ra = ${f(L.rotate * 2 * Math.PI)}, rs = sin( ra ), rc = cos( ra );
	vec2 r = vec2( ${cu}, ${cv} ) - 0.5;
	vec2 st = vec2( r.x * rc - r.y * rs, r.x * rs + r.y * rc ) + 0.5;` : `
	vec2 st = vec2( ${cu}, ${cv} );`;
  return `float fxLayer${i}( vec2 uv, float d ) {
	float uu = ( uv.x - 0.5 ) * ${f(L.tiling[0])} + 0.5 + uFxTime * uFxScroll${i}.x${warp ? ` + d * ${f(L.distort[0])}` : ''} + ${f(L.offset[0])};
	float vv = ( uv.y - 0.5 ) * ${f(L.tiling[1])} + 0.5 + uFxTime * uFxScroll${i}.y${warp ? ` + d * ${f(L.distort[1])}` : ''} + ${f(L.offset[1])};${rot}
	float t = texture2D( uFxTex${i}, st ).r;
	return t * t;
}`;
}

function patchVfx2(shader: Shader, u: FxState['uniforms'], p: Vfx2Params) {
  if (!shader.vertexShader.includes(BV) || !shader.fragmentShader.includes(OF)) { console.warn('vfx2: chunk not found'); return; }
  shader.vertexShader = 'varying vec2 vFxUv;\n' + shader.vertexShader.replace(BV, `${BV}\n\tvFxUv = uv;`);
  const alpha = p.alphaMode === 'range'
    ? `float x = clamp( a1 * ${f(p.layer1.alpha)} * a2 * ${f(p.layer2.alpha)}, 0.0, 1.0 );
		float ta = clamp( ( x - ${f(p.alphaMin)} ) / ${f(p.alphaMax - p.alphaMin || 1e-4)}, 0.0, 1.0 );
		float a = ta * ta * ( 3.0 - 2.0 * ta );`
    : p.alphaMode === 'add' ? `float a = clamp( a1 * ${f(p.layer1.alpha)} + a2 * ${f(p.layer2.alpha)}, 0.0, 1.0 );` : 'float a = 1.0;';
  // the per-material constants are baked into the source (one program per material; few such materials exist)
  shader.fragmentShader = `uniform sampler2D uFxTex1, uFxTex2, uFxTexDist;
uniform float uFxTime, uFxOn;
uniform vec2 uFxScroll1, uFxScroll2, uFxDistScroll;
uniform vec3 uFxC1, uFxC2;
varying vec2 vFxUv;
${vfx2Layer(1, p.layer1, p.distort1 ?? true)}
${vfx2Layer(2, p.layer2, true)}
` + shader.fragmentShader.replace('void main() {', FILMIC + '\nvoid main() {').replace(OF, `
	if ( uFxOn > 0.5 ) {
		vec2 uv = vFxUv;
		vec2 du = ( uv - 0.5 ) * vec2( ${f(p.distTiling[0])}, ${f(p.distTiling[1])} ) + 0.5 + uFxTime * uFxDistScroll
			+ vec2( ${f(p.distOffset[0])}, ${f(p.distOffset[1])} );
		float d = 2.0 * ${f(p.distortion)} * ( texture2D( uFxTexDist, du ).r - 0.5 );
		float a1 = fxLayer1( uv, d ), a2 = fxLayer2( uv, d );
		float tk = clamp( ( a1 * ${f(p.layer1.rgb)} + a2 * ${f(p.layer2.rgb)} - ${f(p.rgbMin)} ) / ${f(p.rgbMax - p.rgbMin || 1e-4)}, 0.0, 1.0 );
		vec3 hdr = mix( uFxC1, uFxC2, tk * tk * ( 3.0 - 2.0 * tk ) );
		${alpha}
		${p.fresnel ? `a *= pow( max( abs( dot( normal, isOrthographic ? vec3( 0.0, 0.0, 1.0 ) : normalize( vViewPosition ) ) ), 1e-4 ), ${f(p.fresnel)} );` : ''}
		${p.maskU > 0 ? `{ float m = clamp( min( uv.x, 1.0 - uv.x ) * ${f(p.maskU)}, 0.0, 1.0 ); a *= m * m; }` : ''}
		${p.maskV > 0 ? `{ float m = clamp( uv.y * ${f(p.maskV)}, 0.0, 1.0 ); a *= m * m; }` : ''}
		${WRITE}
	}
	${OF}`);
  Object.assign(shader.uniforms, u);
}

// FITTED: hue turns per second per unit of `Rainbow Speed` (the shader graph is not in the pak)
const RAINBOW_TURNS = 0.05;

/** `extras.rainbow` (M_PalLit_Rainbow): the albedo's hue rotates over time (rotation about the grey axis in linear RGB). */
function patchRainbow(shader: Shader, u: FxState['uniforms']) {
  const mf = '#include <map_fragment>';
  if (!shader.fragmentShader.includes(mf)) { console.warn('rainbow: chunk not found'); return; }
  shader.fragmentShader = `uniform float uFxTime, uFxOn, uFxRate;
` + shader.fragmentShader.replace(mf, `${mf}
	if ( uFxOn > 0.5 ) {
		float fxA = uFxTime * uFxRate * 6.28318530718, fxC = cos( fxA ), fxS = sin( fxA );
		vec3 fxK = vec3( 0.57735 );
		diffuseColor.rgb = diffuseColor.rgb * fxC + cross( fxK, diffuseColor.rgb ) * fxS + fxK * dot( fxK, diffuseColor.rgb ) * ( 1.0 - fxC );
	}`);
  Object.assign(shader.uniforms, u);
}

function patchDream(shader: Shader, u: FxState['uniforms']) {
  const em = '#include <emissivemap_fragment>';
  if (!shader.fragmentShader.includes(em)) { console.warn('dream: chunk not found'); return; }
  shader.fragmentShader = `uniform sampler2D uFxNoise;
uniform float uFxTime, uFxTiling, uFxOn;
uniform vec2 uFxNoiseSpeed;
` + shader.fragmentShader.replace(em, `${em}
	#ifdef USE_EMISSIVEMAP
	if ( uFxOn > 0.5 ) totalEmissiveRadiance *= mix( 0.8, 1.25, texture2D( uFxNoise, vEmissiveMapUv * uFxTiling + uFxTime * uFxNoiseSpeed ).r );
	#endif`);
  Object.assign(shader.uniforms, u);
}

/** Install the effect patch on a material (once; before the game-shading patch captures onBeforeCompile). Returns the glTF
 *  texture indices to load (pass the loaded textures to setEffectTextures in the same order), or undefined. */
export function installEffect(mat: THREE.MeshStandardMaterial): (number | undefined)[] | undefined {
  const fire = mat.userData?.fire as FireParams | undefined;
  const vfx2 = mat.userData?.vfx2 as Vfx2Params | undefined;
  const dream = mat.userData?.dream as DreamParams | undefined;
  const rainbow = mat.userData?.rainbow as { speed: number } | undefined;
  if ((!fire && !vfx2 && !dream && !rainbow) || mat.userData.__fx) return undefined;
  let st: FxState, tex: (number | undefined)[];
  if (fire) {
    st = { kind: 'fire', ready: false, samplers: ['uFxMain', 'uFxNoise'], speeds: ['uFxMainSpeed', 'uFxNoiseSpeed'], uniforms: {
      uFxMain: { value: null }, uFxNoise: { value: null }, uFxTime: { value: 0 }, uFxOn: { value: 0 },
      uFxMainTiling: { value: v2(fire.mainTiling, 1) }, uFxMainSpeed: { value: v2(fire.mainSpeed).multiplyScalar(fire.timeScale ?? 1) },
      uFxNoiseTiling: { value: v2(fire.noiseTiling, 1) }, uFxNoiseSpeed: { value: v2(fire.noiseSpeed).multiplyScalar(fire.timeScale ?? 1) },
      uFxDistortion: { value: fire.distortion }, uFxOffset: { value: fire.offsetScale * CM },
      uFxC1: { value: v3(fire.color1) }, uFxC2: { value: v3(fire.color2) }, uFxC3: { value: v3(fire.color3) },
      uFxFresnelColor: { value: v3(fire.fresnelColor) },
      uFxMid: { value: fire.midPoint }, uFxRootColor: { value: fire.rootColor }, uFxRootAlpha: { value: fire.rootAlpha },
      uFxVertexRoot: { value: fire.vertexRoot }, uFxAlpha: { value: fire.alpha }, uFxAlphaEdge: { value: fire.alphaEdge },
      uFxRgbMin: { value: fire.rgbMin }, uFxRgbMax: { value: fire.rgbMax },
      uFxFresnelPow: { value: fire.fresnelPower },
    } };
    tex = [fire.mainTex?.index, fire.noiseTex?.index ?? fire.mainTex?.index];
  } else if (vfx2) {
    st = { kind: 'vfx2', ready: false, samplers: ['uFxTex1', 'uFxTex2', 'uFxTexDist'], speeds: ['uFxScroll1', 'uFxScroll2', 'uFxDistScroll'], uniforms: {
      uFxTex1: { value: null }, uFxTex2: { value: null }, uFxTexDist: { value: null }, uFxTime: { value: 0 }, uFxOn: { value: 0 },
      uFxScroll1: { value: v2(vfx2.layer1.scroll) }, uFxScroll2: { value: v2(vfx2.layer2.scroll) }, uFxDistScroll: { value: v2(vfx2.distScroll) },
      uFxC1: { value: v3(vfx2.color1) }, uFxC2: { value: v3(vfx2.color2) },
    } };
    tex = [vfx2.tex1?.index, vfx2.tex2?.index, vfx2.texDist?.index];
  } else if (rainbow) {
    st = { kind: 'rainbow', ready: false, samplers: [], speeds: ['uFxRate'], uniforms: { uFxTime: { value: 0 }, uFxOn: { value: 1 }, uFxRate: { value: rainbow.speed * RAINBOW_TURNS } } };
    tex = [];
  } else {
    st = { kind: 'dream', ready: false, samplers: ['uFxNoise'], speeds: ['uFxNoiseSpeed'], uniforms: {
      uFxNoise: { value: null }, uFxTime: { value: 0 }, uFxOn: { value: 0 },
      uFxTiling: { value: dream!.noiseTiling ?? 1 }, uFxNoiseSpeed: { value: v2(dream!.noiseSpeed) },
    } };
    tex = [dream!.noiseTex?.index];
  }
  mat.userData.__fx = st;
  const prev = mat.onBeforeCompile, prevKey = mat.customProgramCacheKey;
  mat.onBeforeCompile = function (this: THREE.Material, shader, renderer) {
    prev?.call(this, shader, renderer);
    if (st.kind === 'fire') patchFire(shader as never, st.uniforms);
    else if (st.kind === 'vfx2') patchVfx2(shader as never, st.uniforms, vfx2!);
    else if (st.kind === 'rainbow') patchRainbow(shader as never, st.uniforms);
    else patchDream(shader as never, st.uniforms);
  };
  mat.customProgramCacheKey = function (this: THREE.Material) {
    return (prevKey?.call(this) ?? '') + '|fx-' + st.kind + (st.kind === 'vfx2' ? '-' + mat.uuid : '');
  };
  mat.needsUpdate = true;
  return tex;
}

/** Attach the loaded maps (installEffect's order); the effect switches on once all are there (the baked frame shows until then). */
export function setEffectTextures(mat: THREE.Material, textures: (THREE.Texture | null)[]) {
  const st = mat.userData.__fx as FxState | undefined;
  if (!st || textures.length < st.samplers.length || textures.some((t) => !t)) return;
  st.samplers.forEach((name, i) => {
    const t = textures[i]!;
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.colorSpace = THREE.NoColorSpace;
    t.needsUpdate = true;
    st.uniforms[name].value = t;
  });
  if (st.kind !== 'dream' && st.kind !== 'rainbow') {
    // premultiplied: the fragment already holds colour x alpha (tone-mapped after the multiply, as in Unreal)
    mat.blending = THREE.CustomBlending;
    mat.blendSrc = THREE.OneFactor;
    mat.blendDst = THREE.OneMinusSrcAlphaFactor;
    mat.blendSrcAlpha = THREE.OneFactor;
    mat.blendDstAlpha = THREE.OneMinusSrcAlphaFactor;
    mat.blendEquation = THREE.AddEquation;
  }
  st.uniforms.uFxOn.value = 1;
  st.ready = true;
}

export function updateEffect(mat: THREE.Material, t: number) {
  const st = mat.userData.__fx as FxState | undefined;
  if (st) st.uniforms.uFxTime.value = t;
}

/**
 * GIF export: round every scroll speed s to round(s * period) / period so the effect completes whole cycles in `period`
 * seconds and the GIF loops without a jump (a speed that would round to 0 cycles keeps its value). null restores the
 * game values. Rotated vfx2 layers still do not loop exactly (the rotation mixes the two axes).
 */
export function setEffectLoop(mat: THREE.Material, period: number | null) {
  const st = mat.userData.__fx as FxState | undefined;
  if (!st) return;
  if (period === null) {
    if (st.base) for (const [k, v] of Object.entries(st.base)) st.uniforms[k].value = typeof v === 'number' ? v : v.clone();
    st.base = undefined;
    return;
  }
  const q = (s: number) => { const k = Math.round(s * period); return k === 0 ? s : k / period; };
  st.base ??= Object.fromEntries(st.speeds.map((k) => {
    const v = st.uniforms[k].value as number | THREE.Vector2;
    return [k, typeof v === 'number' ? v : v.clone()];
  }));
  for (const k of st.speeds) {
    const b = st.base[k];
    st.uniforms[k].value = typeof b === 'number' ? q(b) : new THREE.Vector2(q(b.x), q(b.y));
  }
}

export function hasEffect(mat: THREE.Material): boolean {
  return !!(mat.userData?.fire || mat.userData?.vfx2 || mat.userData?.dream || mat.userData?.rainbow);
}
