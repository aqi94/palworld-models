import * as THREE from 'three';

/** Material extras `fresnel`: the game's Fresnel emissive rim (PalLit `Fresnel Emissive Color / Exponent / Fraction`, effect shaders' `fresnelColor`). */
export interface FresnelParams { color: [number, number, number]; exponent: number; fraction: number }

/** Add the rim to a material's emissive light (once; chained like the eye catchlight, before the game-shading patch captures onBeforeCompile). */
export function installFresnel(mat: THREE.Material): boolean {
  const p = mat.userData?.fresnel as FresnelParams | undefined;
  if (!p) return false;
  if (mat.userData.__fresnel) return true;
  mat.userData.__fresnel = true;
  const prev = mat.onBeforeCompile, prevKey = mat.customProgramCacheKey;
  mat.onBeforeCompile = function (this: THREE.Material, shader, renderer) {
    prev?.call(this, shader, renderer);
    shader.uniforms.uFrColor = { value: new THREE.Vector3(...p.color).multiplyScalar(p.fraction) };
    shader.uniforms.uFrExp = { value: p.exponent };
    shader.fragmentShader = shader.fragmentShader
      .replace('void main() {', 'uniform vec3 uFrColor;\nuniform float uFrExp;\nvoid main() {')
      .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>
	{
		vec3 frV = isOrthographic ? vec3( 0.0, 0.0, 1.0 ) : normalize( vViewPosition );
		totalEmissiveRadiance += uFrColor * pow( 1.0 - saturate( abs( dot( normal, frV ) ) ), uFrExp );
	}`);
  };
  mat.customProgramCacheKey = () => (prevKey ? prevKey.call(mat) : '') + '|fresnel';
  mat.needsUpdate = true;
  return true;
}
