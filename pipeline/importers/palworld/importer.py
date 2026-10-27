"""Palworld importer: extractor output (see extractor/PalExtract/Pals.cs) -> game-agnostic Model.

Rules (apply to every Pal, no per-model fixes):
  * Space: Unreal (left-handed, Z up, cm, mesh faces +Y) -> glTF by swapping Y/Z and scaling 0.01
    (convert.py). The swap is a reflection, which also turns Unreal's clockwise front faces into glTF's
    counter-clockwise ones, so indices are kept as they are.
  * Skeleton = the skeletal mesh's reference skeleton. Vertex bone indices already point into it.
  * Clips: every AnimSequence of the Pal's skeleton. Keys are per-frame local transforms in Unreal space;
    translation retargeting follows the USkeleton bone tree mode (Animation / Skeleton / AnimationScaled /
    AnimationRelative), same as the engine's FAnimationRuntime::RetargetBoneTransform.
  * Materials: the PalLit family. Parameters resolve child-first along the instance chain.
      Base Texture -> base colour, Normal Map -> normal (DirectX green flipped for glTF),
      MetallicRoughnessOcclusionSpecularTexture (R metal, G rough, B occlusion) -> glTF ORM.
  * Vertex colours are shader masks in Unreal, never exported as COLOR_0.
"""

from __future__ import annotations

import json
import os
import re

import numpy as np
from PIL import Image

from ...common.model import Clip, Joint, Material, Mesh, Model, Primitive, Skeleton, TextureImage, Track
from . import convert as cv
from .raw import RawPal, load_material, load_pal
from .shaders import used_params

# --- clip naming / categories -------------------------------------------------------------

CATEGORY_RULES: list[tuple[str, str]] = [
    ("work", r"Architecture"),  # first: the battle rule's "Hit" would match arcHItecture
    ("idle", r"^(Idle|Rest|Sleep|Petting|Eat|Spa|Encount|Happy|Joy|Sad|Dizzy|Stretch|Yawn)"),
    ("move", r"^(Walk|Run|Jump|Swim|Fly|Glide|Fall|Landing|Sprint|Dash|Korogaru|Ride|Climb|Turn)"),
    ("battle", r"(^Aim|Minigun|Attack|Skill|Damage|Hit|Shield|Dead|Death|Stun|Down|GetUp|Blow|Guard|Charge|Beam|Breath|Bite|Tackle|Roar|Shot|Fire|Gunner|Shock)"),  # aiming / firing a held weapon is combat
    ("work", r"(Work|Architecture|Carry|Carrying|Transport|Mining|Deforest|Craft|Cool|Watering|Seeding|Kindling|Generate|Farm|Medicine|Handcraft|Collection|Oil)"),
]
DEMO_CLIP_RE = re.compile(r"Demo", re.I)  # cutscene sequences (Demo/BossBattle<X>Start/Animation/AS_<X>StartDemo_...)
LOOP_RE = re.compile(r"(Loop|^Idle|^Walk|^Run|^Swim|^Fly|^Glide|^Sprint|Carrying$|^Transport$|^CommonWork$|^Spa$|^Rest\d*$)", re.I)


# A Pal under a rider (tower bosses) only locomotes and fights; the rest of the idle set (sleep, spa, petting...), work and one-offs are
# animations for a Pal on its own, so the boss page's mount picker hides them.
MOUNT_EXCLUDE_RE = re.compile(r"(Sleep|Spa|Eat|Petting|Rest|Encount|Happy|Joy|Sad|Dizzy|Stretch|Yawn|Dead|Death|Down|GetUp|Capture|Summon|Hug|Carry|Transport)", re.I)


def mount_ok(name: str, category: str) -> bool:
    return category in ("idle", "move", "battle") and not MOUNT_EXCLUDE_RE.search(name)


def clip_name(asset: str, code: str) -> str:
    """AS_Kitsunebi_Idle (for Kitsunebi_Ice too) -> Idle. Strips the longest code prefix."""
    name = asset[3:] if asset.startswith("AS_") else asset
    parts = code.split("_")
    for n in range(len(parts), 0, -1):
        prefix = "_".join(parts[:n]) + "_"
        if name.lower().startswith(prefix.lower()):
            return name[len(prefix):] or name
    # anims named after another Pal (shared skeletons): drop the first token
    return name.split("_", 1)[1] if "_" in name else name


def categorize(name: str) -> str:
    for cat, rx in CATEGORY_RULES:
        if re.search(rx, name, re.I):
            return cat
    return "other"


# --- materials ----------------------------------------------------------------------------

BASE_PARAMS = ("Base Texture", "Base Color", "BaseColor")
NORMAL_PARAMS = ("Normal Map", "Normal")
MROS_PARAMS = ("MetallicRoughnessOcclusionSpecularTexture",)
EMISSIVE_PARAMS = ("Emissive Texture",)
BLEND = {"EBlendMode::0": "OPAQUE", "EBlendMode::1": "MASK", "EBlendMode::2": "BLEND", "EBlendMode::3": "BLEND",
         "EBlendMode::4": "BLEND", "BLEND_Opaque": "OPAQUE", "BLEND_Masked": "MASK", "BLEND_Translucent": "BLEND",
         "BLEND_Additive": "BLEND", "BLEND_Modulate": "BLEND"}


def resolve_params(root: str, chain: list[str]) -> dict:
    """Child-first merge of texture/scalar/vector params + blend/two-sided overrides along a material chain."""
    out = {"textures": {}, "scalars": {}, "vectors": {}, "blend": None, "two_sided": None, "clip": None, "base_material": chain[-1] if chain else None,
           "chain": list(chain)}
    for path in chain:
        d = load_material(root, path)
        if d is None:
            continue
        p = d.get("Properties") or {}
        for t in p.get("TextureParameterValues") or []:
            pv = t.get("ParameterValue")
            name = t["ParameterInfo"]["Name"]
            if pv and name not in out["textures"]:
                out["textures"][name] = pv["ObjectPath"].rsplit(".", 1)[0]
        for s in p.get("ScalarParameterValues") or []:
            out["scalars"].setdefault(s["ParameterInfo"]["Name"], s["ParameterValue"])
        for v in p.get("VectorParameterValues") or []:
            out["vectors"].setdefault(v["ParameterInfo"]["Name"], v["ParameterValue"])
        bo = p.get("BasePropertyOverrides") or {}
        if out["blend"] is None and bo.get("bOverride_BlendMode", "BlendMode" in bo) and "BlendMode" in bo:
            out["blend"] = bo["BlendMode"]
        if out["two_sided"] is None and "TwoSided" in bo:
            out["two_sided"] = bool(bo["TwoSided"])
        if out["clip"] is None and "OpacityMaskClipValue" in bo:
            out["clip"] = float(bo["OpacityMaskClipValue"])
        # base UMaterial: plain properties
        if d.get("Type") == "Material" or path == chain[-1]:
            if out["blend"] is None and isinstance(p.get("BlendMode"), str):
                out["blend"] = p["BlendMode"]
            if out["two_sided"] is None and "TwoSided" in p:
                out["two_sided"] = bool(p["TwoSided"])
    # names some instance in the chain sets (the rest come from the base material's defaults below)
    out["overridden"] = {k for kind in ("textures", "scalars", "vectors") for k in out[kind]}
    base = load_material(root, chain[-1]) if chain else None
    defaults = (base or {}).get("Defaults") or {}
    # Parameter defaults of the base UMaterial (its CachedExpressionData): instances store only what they override,
    # e.g. M_Pal_Dream's MainTex (Daedream's gradient) and M_Pal_Fire's colour ramp exist only here.
    for name, path in (defaults.get("textures") or {}).items():
        if not is_placeholder_texture(path):
            out["textures"].setdefault(name, path)
    for name, v in (defaults.get("scalars") or {}).items():
        out["scalars"].setdefault(name, v)
    for name, v in (defaults.get("vectors") or {}).items():
        out["vectors"].setdefault(name, v)
    return out


_PLACEHOLDER_RE = re.compile(r"^(Default|T_Black|T_White|T_Default(Mask|Normal)|Black|White|Grey|Gray|FlatNormal)", re.I)


def is_placeholder_texture(path: str) -> bool:
    """Engine / project stand-ins a base material uses when an instance sets no texture (M_PalLit: DefaultBaseTexture,
    DefaultNormalMap, Engine Black): they mean "no map", not a real default. Mirrored in the extractor's MaterialDefaults."""
    return path.startswith("Engine/") or bool(_PLACEHOLDER_RE.match(path.rsplit("/", 1)[-1]))


def _first(d: dict, names) -> str | None:
    for n in names:
        if n in d:
            return d[n]
    return None


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    c = c.astype(np.float32) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


# Fitted, not read from the shader: with 0.88 the slimes' Hue values land on blue / pink / purple / red (1.0 gives teal and magenta).
HUE_TURNS = 0.88


class TextureCache:
    def __init__(self, pal: RawPal, model: Model):
        self.pal = pal
        self.model = model

    def _load(self, path: str) -> np.ndarray | None:
        png = self.pal.texture_png(path)
        if png is None:
            return None
        return np.array(Image.open(png).convert("RGBA"))

    def plain(self, path: str | None, srgb: bool = True) -> str | None:
        if not path:
            return None
        key = path.rsplit("/", 1)[-1]
        if key in self.model.textures:
            return key
        rgba = self._load(path)
        if rgba is None:
            return None
        self.model.textures[key] = TextureImage(key, rgba, srgb=srgb)
        return key

    def eye(self, layers: list[str], size: int = 1024) -> str | None:
        """Human eye (MI_PalLit_HumanEyeBase): the shader stacks White, Cornea (iris), Pupil and Highlight textures (each RGBA over the
        one below, same UVs; the shader's parallax "height" offsets between them are dropped). Flattened into one albedo."""
        layers = [p for p in layers if p]
        if not layers:
            return None
        key = "eye_" + "+".join(p.rsplit("/", 1)[-1] for p in layers)
        if key in self.model.textures:
            return key
        out = None
        for p in layers:
            rgba = self._load(p)
            if rgba is None:
                continue
            im = Image.fromarray(rgba).resize((size, size), Image.LANCZOS)
            if out is None:
                out = Image.new("RGBA", (size, size), (255, 255, 255, 255))
            out.alpha_composite(im)
        if out is None:
            return None
        arr = np.array(out)
        arr[..., 3] = 255
        self.model.textures[key] = TextureImage(key, arr, srgb=True)
        return key

    def data(self, path: str | None) -> str | None:
        """Grey data map for an effect shader (noise): sRGB-flagged sources are decoded to linear so the viewer samples raw."""
        if not path:
            return None
        key = path.rsplit("/", 1)[-1] + "_lin"
        if key in self.model.textures:
            return key
        rgba = self._load(path)
        if rgba is None:
            return None
        if self.pal.texture_meta(path).get("srgb", False):
            rgba = rgba.copy()
            rgba[..., :3] = (_srgb_to_linear(rgba[..., :3]) * 255.0 + 0.5).astype(np.uint8)
        self.model.textures[key] = TextureImage(key, rgba, srgb=False)
        return key

    def normal(self, path: str | None) -> str | None:
        if not path:
            return None
        key = path.rsplit("/", 1)[-1]
        if key in self.model.textures:
            return key
        rgba = self._load(path)
        if rgba is None:
            return None
        rgba = rgba.copy()
        rgba[..., 1] = 255 - rgba[..., 1]  # DirectX (Unreal) -> OpenGL (glTF) green
        rgba[..., 3] = 255
        self.model.textures[key] = TextureImage(key, rgba, srgb=False)
        return key

    def with_opacity(self, path: str, mask_path: str) -> str | None:
        """Base colour with alpha taken from the blue channel of the packed M texture. Brow cards (MI_PalLit_CharacterBrowBase) use
        a DXT1 albedo without alpha: the opacity mask rides in the M texture's occlusion slot, so without it every card is solid."""
        key = path.rsplit("/", 1)[-1] + "_opM"
        if key in self.model.textures:
            return key
        rgba, mask = self._load(path), self._load(mask_path)
        if rgba is None or mask is None:
            return None
        if mask.shape[:2] != rgba.shape[:2]:
            mask = np.array(Image.fromarray(mask).resize((rgba.shape[1], rgba.shape[0]), Image.BILINEAR))
        rgba = rgba.copy()
        rgba[..., 3] = mask[..., 2]
        self.model.textures[key] = TextureImage(key, rgba, srgb=True)
        return key

    def recolor(self, path: str, mask_path: str | None, hue: float, sat: float, val: float) -> str | None:
        """PalLit `Masked BaseColor Hue/Saturation/Brightness`: the albedo's hue is rotated by -Hue * HUE_TURNS turns, saturation and value
        scaled, weighted by the mask's red channel (Terraria slimes and the other recoloured Pals share one green texture)."""
        key = f"{path.rsplit('/', 1)[-1]}_hsv{hue:g}_{sat:g}_{val:g}"
        if key in self.model.textures:
            return key
        rgba = self._load(path)
        if rgba is None:
            return None
        hsv = np.array(Image.fromarray(rgba[..., :3]).convert("HSV")).astype(np.float32) / 255.0
        h = (hsv[..., 0] - hue * HUE_TURNS) % 1.0
        sv = np.clip(hsv[..., 1] * sat, 0.0, 1.0)
        v = np.clip(hsv[..., 2] * val, 0.0, 1.0)
        tinted = np.array(Image.fromarray((np.stack([h, sv, v], -1) * 255.0 + 0.5).astype(np.uint8), "HSV").convert("RGB")).astype(np.float32)
        w = 1.0
        mask = self._load(mask_path) if mask_path else None
        if mask is not None:
            w = np.array(Image.fromarray(mask[..., 0]).resize((rgba.shape[1], rgba.shape[0]), Image.BILINEAR)).astype(np.float32)[..., None] / 255.0
        out = rgba.copy()
        out[..., :3] = np.clip(rgba[..., :3] * (1.0 - w) + tinted * w + 0.5, 0, 255).astype(np.uint8)
        self.model.textures[key] = TextureImage(key, out, srgb=True)
        return key

    def orm(self, path: str | None, rough: tuple[float, float] = (1.0, 0.0), metal: tuple[float, float] = (1.0, 0.0), packing: str = "mro") -> str | None:
        """Unreal R=metallic G=roughness B=occlusion -> glTF R=occlusion G=roughness B=metallic (linear).

        `rough` / `metal` are the material's (Mul, Add) scalars, baked in as clip(v * Mul + Add) since glTF has no offset."""
        if not path:
            return None
        key = path.rsplit("/", 1)[-1] + "_ORM"
        if packing != "mro":
            key += f"_{packing}"
        if rough != (1.0, 0.0) or metal != (1.0, 0.0):
            key += f"_r{rough[0]:g}_{rough[1]:g}_m{metal[0]:g}_{metal[1]:g}"
        if key in self.model.textures:
            return key
        rgba = self._load(path)
        if rgba is None:
            return None
        ch = rgba[..., :3]
        if self.pal.texture_meta(path).get("srgb", False):
            ch = (_srgb_to_linear(ch) * 255.0 + 0.5).astype(np.uint8)
        # "mro": R metallic G roughness B occlusion (PalLit); "amr": R occlusion G metallic B roughness (weapons)
        # (metallic, roughness, occlusion) source channels; "orm" is already glTF's layout, "rma" is R roughness G metallic B AO
        r_i, g_i, ao_i = {"mro": (0, 1, 2), "amr": (1, 2, 0), "orm": (2, 1, 0), "rma": (1, 0, 2)}[packing]
        g = np.clip(ch[..., g_i].astype(np.float32) / 255.0 * rough[0] + rough[1], 0.0, 1.0)
        r = np.clip(ch[..., r_i].astype(np.float32) / 255.0 * metal[0] + metal[1], 0.0, 1.0)
        out = np.empty_like(rgba)
        ao = ch[..., ao_i]
        if ao.max() < 40:
            # unused channel (authored as 0 on ~20 pals): treating it as occlusion would black out all ambient light
            ao = np.full_like(ao, 255)
        out[..., 0] = ao
        out[..., 1] = (g * 255.0 + 0.5).astype(np.uint8)
        out[..., 2] = (r * 255.0 + 0.5).astype(np.uint8)
        out[..., 3] = 255
        self.model.textures[key] = TextureImage(key, out, srgb=False)
        return key


WRAP = {"TA_Wrap": "repeat", "TA_Clamp": "clamp", "TA_Mirror": "mirror"}


def build_material(pal: RawPal, slot: dict, texcache: TextureCache) -> Material:
    chain = slot.get("chain") or []
    prm = resolve_params(pal.root, chain)
    tx = prm["textures"]
    name = slot["slot"]
    m = Material(name=name)
    base = _first(tx, BASE_PARAMS)
    sc = prm["scalars"]
    if not base and ("Eye White Texture" in tx or "Eye Cornea Texture" in tx):
        m.base_color = texcache.eye([tx.get(k) for k in ("Eye White Texture", "Eye Cornea Texture", "Eye Pupil Texture", "Eye Highlight Texture")])
        m.roughness_factor = float(sc.get("Roughness", 0.1))
        m.metallic_factor = 0.0
        m.extras["specular"] = max(0.0, min(1.0, float(sc.get("Specular", 0.3)) / 0.5))
        return m
    tint = [sc.get(f"Masked BaseColor {k}") for k in ("Hue", "Saturation", "Brightness")]
    # the base defaults are the identity (0, 1, 1): recolour only when the values change something (HSV round trips are lossy)
    if base and (float(tint[0] or 0.0) != 0.0 or float(1.0 if tint[1] is None else tint[1]) != 1.0 or float(1.0 if tint[2] is None else tint[2]) != 1.0):
        m.base_color = texcache.recolor(base, _first(tx, ("BaseColor Mask",)), float(tint[0] or 0.0),
                                        float(1.0 if tint[1] is None else tint[1]), float(1.0 if tint[2] is None else tint[2]))
    elif base and prm["blend"] == "EBlendMode::1" and any(c.rsplit("/", 1)[-1].startswith("MI_PalLit_CharacterBrowBase") for c in chain)             and _first(tx, MROS_PARAMS) and pal.texture_meta(base).get("format") == "PF_DXT1":
        m.base_color = texcache.with_opacity(base, _first(tx, MROS_PARAMS)) or texcache.plain(base, srgb=True)
    else:
        m.base_color = texcache.plain(base, srgb=True)
    if base:
        meta = pal.texture_meta(base)
        w = (WRAP.get(meta.get("addressX", "TA_Wrap"), "repeat"), WRAP.get(meta.get("addressY", "TA_Wrap"), "repeat"))
        m.wrap = {k: w for k in ("base_color", "normal", "metallic_roughness", "occlusion", "emissive")}
    m.normal = texcache.normal(_first(tx, NORMAL_PARAMS))
    rough = (float(sc.get("Roughness Mul", 1.0)), float(sc.get("Roughness Add", 0.0)))
    metal = (float(sc.get("Metallic Mul", 1.0)), float(sc.get("Metallic Add", 0.0)))
    orm = texcache.orm(_first(tx, MROS_PARAMS), rough, metal)
    if orm:
        m.metallic_roughness = orm
        m.occlusion = orm
        m.metallic_factor = 1.0
        m.roughness_factor = 1.0
    else:
        # no packed map: the absolute scalars are all there is (glTF defaults otherwise)
        if "Roughness" in sc:
            m.roughness_factor = float(sc["Roughness"])
        if "Metallic" in sc:
            m.metallic_factor = float(sc["Metallic"])
    if "Specular" in sc:
        # Unreal Specular 0.5 == glTF's default 4% F0; most pal materials sit at 0-0.1 (matte fur, skin)
        m.extras["specular"] = max(0.0, min(1.0, float(sc["Specular"]) / 0.5))
    if m.normal and "Normal Map Intensity" in sc:
        m.normal_scale = float(sc["Normal Map Intensity"])
    em = _first(tx, EMISSIVE_PARAMS)
    if em:
        m.emissive = texcache.plain(em, srgb=True)
        if m.emissive:
            # `Emissive Texture Intensity` scales the map (Enchanted Sword: the albedo as the map at 0.005 = no glow); glTF's factor
            # tops out at 1, so intensities above 1 (20-200 on the EM masks) stay at full strength
            ei = float(sc.get("Emissive Texture Intensity", 1.0))
            f = max(0.0, min(1.0, ei))
            m.emissive_factor = (f, f, f)
    m.alpha_mode = BLEND.get(prm["blend"] or "", "OPAQUE")
    if m.alpha_mode == "MASK":
        m.alpha_cutoff = prm["clip"] if prm["clip"] is not None else 0.3333
    m.double_sided = bool(prm["two_sided"])
    if m.alpha_mode == "BLEND" and m.base_color is not None and "Jewel Opacity" in sc:
        # PalLitJewel family: the albedo is opaque DXT1, translucency is the scalar (Jetragon wings 0.75)
        m.base_color_factor = (1.0, 1.0, 1.0, max(0.0, min(1.0, float(sc["Jewel Opacity"]))))
    tint_vec = prm["vectors"].get("BaseColor")
    if base and m.base_color is not None and tint_vec and "BaseColor" in prm.get("overridden", ()):
        # PalLit `BaseColor` multiplies the albedo (the maps are authored bright and grey, DarkAlien's is ~0.2 linear under a 0.23 tint);
        # the no-albedo branch below uses it as the albedo itself
        m.base_color_factor = (float(tint_vec.get("R", 1.0)), float(tint_vec.get("G", 1.0)), float(tint_vec.get("B", 1.0)),
                               m.base_color_factor[3])
    family = (prm["base_material"] or "").rsplit("/", 1)[-1]
    if family in FIRE_MATERIALS:
        _fire_material(m, prm, texcache)
    elif family in VFX2_MATERIALS and _first(tx, ("Texture01",)):
        _vfx2_material(m, prm, texcache)
    elif family in DREAM_MATERIALS and _first(tx, ("MainTex",)):
        _dream_material(m, prm, texcache)
    if m.base_color is None and family == "M_PalLit":
        # plain PalLit with no albedo map (the SkinDark recipe: Base Texture None, BaseColor black, Base Color Intensity 0):
        # the albedo is the constant BaseColor vector, a solid surface, not an effect
        col = prm["vectors"].get("BaseColor") or {}
        m.base_color_factor = (float(col.get("R", 1.0)), float(col.get("G", 1.0)), float(col.get("B", 1.0)), 1.0)
        if m.alpha_mode == "MASK":  # no albedo alpha to cut with
            m.alpha_mode = "OPAQUE"
    elif m.base_color is None:
        # effect materials (flames, jewels) have no albedo map: tint from their main colour, half transparent
        # HDR colours (R 200, G 4.5) clip to a pure red when normalised by the max channel; the Fresnel colour is the one
        # authored as the visible hue (fire: orange), so it wins over Color1/2. Names differ in case between materials.
        vec = {k.lower(): v for k, v in prm["vectors"].items()}
        col = _first(vec, ("basecolor", "fresnelcolor", "fresnel emissive color", "emissive color", "color", "color2", "color1", "color01", "color02", "color3"))
        if col:
            rgb = np.array([col.get("R", 1.0), col.get("G", 1.0), col.get("B", 1.0)], dtype=float)
            rgb = rgb / max(1.0, float(rgb.max()))
            m.base_color_factor = (float(rgb[0]), float(rgb[1]), float(rgb[2]), 0.6)
            m.emissive_factor = (float(rgb[0]), float(rgb[1]), float(rgb[2]))  # flames / glows are self-lit
        m.alpha_mode = "BLEND"
        m.extras["effect"] = True
    if "Rainbow Speed" in prm.get("overridden", ()):
        # M_PalLit_Rainbow (Rainbow Slime): the shader cycles the albedo's hue over time (graph not in the pak); the GLB keeps the plain albedo
        m.extras["rainbow"] = {"speed": float(prm["scalars"]["Rainbow Speed"])}
    fresnel = _fresnel(prm) if not ({"fire", "vfx2"} & m.extras.keys()) else None
    if fresnel:
        m.extras["fresnel"] = fresnel
    if any(c.rsplit("/", 1)[-1].startswith("MI_PalLit_CharacterBrowBase") for c in chain):
        m.extras["brow"] = True  # slot names vary (MI_*_Brow, MI_LeafMomonga_Blow), the parent instance does not
    m.extras["unreal"] = {"material": chain[0] if chain else None, "base": prm["base_material"],
                          "scalars": {k: v for k, v in prm["scalars"].items() if isinstance(v, (int, float))}}
    return m


# --- effect shaders (unlit translucent; the cooked graph is not in the pak, these are rebuilt from the parameters) -------

FIRE_MATERIALS = {"M_Pal_Fire"}
DREAM_MATERIALS = {"M_Pal_Dream"}
FIRE_BAKE_SIZE = 256


def _rgb(v: dict | None, default=(0.0, 0.0, 0.0)) -> list[float]:
    return [float(v.get(c, d)) for c, d in zip("RGB", default)] if v else list(default)


# Unreal's tonemapper sits between a per-channel ACES curve (keeps saturated hues: Nitemary's cyan root, Blazehowl's yellow)
# and the matrixed ACES fit (turns Astegon's (8, 0, 0.15) red coral instead of magenta): blend the two (fitted on pal_samples/).
FILMIC_HUE_SHIFT = 0.25
# ACES fit (Stephen Hill): sRGB -> AP1-ish, RRT+ODT curve, back; same as three.js ACESFilmicToneMapping (exposure / 0.6).
# Unlike a per-channel curve it shifts very bright reds toward orange / white as Unreal's tonemapper does (Astegon's wings).
_ACES_IN = np.array([[0.59719, 0.35458, 0.04823], [0.07600, 0.90834, 0.01566], [0.02840, 0.13383, 0.83777]], np.float32)
_ACES_OUT = np.array([[1.60475, -0.53108, -0.07367], [-0.10208, 1.10813, -0.00605], [-0.00327, -0.07276, 1.07602]], np.float32)


def filmic(x: np.ndarray) -> np.ndarray:
    """HDR linear rgb (..., 3) -> displayable linear. The effect colours are authored for Unreal's filmic tonemapper (fire
    Color1 = (200, 4.5, 0)); the viewer's default tone mapping is linear, so the effect shader applies this itself.
    Mirrored in viewer/src/scene/effects.ts."""
    x = np.maximum(np.asarray(x, dtype=np.float32), 0.0)
    v = (x / 0.6) @ _ACES_IN.T
    v = (v * (v + 0.0245786) - 0.000090537) / (v * (0.983729 * v + 0.4329510) + 0.238081)
    aces = np.clip(v @ _ACES_OUT.T, 0.0, 1.0)
    per_channel = np.clip((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0)
    return per_channel + (aces - per_channel) * FILMIC_HUE_SHIFT


def fire_params(prm: dict, used: set[str] | None = None) -> dict:
    """M_Pal_Fire parameters (instance overrides over the base defaults) in the form the viewer shader reads. The maths is the
    cooked shader's (`fire_shade`). Its two compiled variants differ only in the fresnel rim: instances whose shader reads
    FresnelColor (7 Pals: HadesBird, GhostAnglerfish...) add it; the rest never draw it whatever FresnelColor holds."""
    sc = {k: float(v) for k, v in prm["scalars"].items() if isinstance(v, (int, float))}
    vec = prm["vectors"]
    g = lambda k, d: round(sc.get(k, d), 5)
    p = {
        "color1": _rgb(vec.get("Color1"), (1, 1, 1)), "color2": _rgb(vec.get("Color2"), (0.5, 0.5, 0.5)),
        "color3": _rgb(vec.get("Color3")),
        "midPoint": g("midPoint", 0.5), "rootColor": g("rootColor", 0.5), "rootAlpha": g("rootAlpha", 1.0),
        "vertexRoot": g("vertexRoot", 0.5), "alpha": g("alpha", 0.0), "alphaEdge": g("alphaEdge", 0.0),
        "fadeAlpha": g("fadeAlpha", 0.1), "rgbMin": g("rgbMin", 0.0), "rgbMax": g("rgbMax", 1.0),
        "fresnelPower": g("rgbFresnelPower", 5.0),
        "mainTiling": [g("MainUTilling", 1.0), g("MainVTilling", 1.0)], "mainSpeed": [g("MainUSpeed", 0.0), g("MainVSpeed", 1.0)],
        "noiseTiling": [g("NoiseUTilling", 1.0), g("NoiseVTilling", 1.0)], "noiseSpeed": [g("NoiseUSpeed", 0.0), g("NoiseVSpeed", 0.5)],
        "distortion": g("DistortionScale", 1.0), "offsetScale": g("OffsetScale", 10.0), "timeScale": g("TimeScale", 1.0),
    }
    if ("FresnelColor" in used) if used is not None else ("FresnelColor" in prm.get("overridden", ())):
        p["fresnelColor"] = _rgb(vec.get("FresnelColor"))
    return p


# Exposure for the effect HDR colours before the tone curve. The shaders divide their colour by the eye adaptation, so it shows
# at its authored brightness whatever the exposure: 1.0 (checked against pal_samples/ with the 0.25 hue shift).
FIRE_EXPOSURE = 1.0


def fire_shade(p: dict, u: np.ndarray, v: np.ndarray, main: np.ndarray, noise: np.ndarray, t: float = 0.0, n_dot_v=None):
    """M_Pal_Fire, transcribed from its decompiled base-pass pixel shader (raw/shaders/.../Kitsunebi/M_Pal_Fire/*Pixel_12.asm,
    slots via shaders.decode) -> (premultiplied display rgb, alpha); `main` / `noise` are the linear 2-D textures:

      T = time * TimeScale; nz = NoiseTex(uv * NoiseTilling + frac(T * NoiseSpeed));
      m = MainTex(uv * MainTilling + frac(T * MainSpeed) + nz * DistortionScale * 0.1); v = uv.y (1 at the flame root)
      g = smoothstep(rgbMin, rgbMax, v * (m + rootColor)) (+ fadeAlpha where the flame meets other geometry: needs scene depth)
      colour = g <= midPoint ? lerp(Color3, Color2, g / midPoint) : lerp(Color2, Color1, (g - midPoint) / (1 - midPoint))
               (+ FresnelColor * ((1 - max(N.V, 0)) ** rgbFresnelPower * 0.96 + 0.04) in the fresnel variant), / eye adaptation
      alpha = smoothstep(clamp(alpha, 0, 1 - alphaEdge), saturate(alpha + alphaEdge), m * v * v * rootAlpha * (1 + m))
    The vertex shader also pushes vertices along the normal by OffsetScale cm * smoothstep(0.2, 1, m) * saturate((1 - v) *
    (1 - vertexRoot)) (viewer only). Mirrored in effects.ts."""
    ts = t * p["timeScale"]
    nt, ns = p["noiseTiling"], p["noiseSpeed"]
    nz = _sample(noise, u * nt[0] + (ts * ns[0]) % 1.0, v * nt[1] + (ts * ns[1]) % 1.0)
    mt, ms = p["mainTiling"], p["mainSpeed"]
    shift = nz * p["distortion"] * 0.1
    m = _sample(main, u * mt[0] + (ts * ms[0]) % 1.0 + shift, v * mt[1] + (ts * ms[1]) % 1.0 + shift)
    g = _smoothstep(p["rgbMin"], p["rgbMax"], v * (m + p["rootColor"]))
    mid = p["midPoint"]
    c1, c2, c3 = (np.array(p[k], dtype=np.float32) for k in ("color1", "color2", "color3"))
    lo = (g / max(mid, 1e-4))[..., None]
    hi = ((g - mid) / max(1.0 - mid, 1e-4))[..., None]
    rgb = np.where((g <= mid)[..., None], c3 + (c2 - c3) * lo, c2 + (c1 - c2) * hi)
    if "fresnelColor" in p:
        f = 0.04 if n_dot_v is None else np.maximum(1.0 - np.maximum(n_dot_v, 0.0), 1e-4) ** p["fresnelPower"] * 0.96 + 0.04
        rgb = rgb + np.array(p["fresnelColor"], np.float32) * np.asarray(f, np.float32)[..., None]
    lo_a = min(max(p["alpha"], 0.0), 1.0 - p["alphaEdge"])
    hi_a = min(max(p["alpha"] + p["alphaEdge"], 0.0), 1.0)
    a = _smoothstep(lo_a, hi_a, m * v * v * p["rootAlpha"] * (1.0 + m))
    return filmic(rgb * FIRE_EXPOSURE * a[..., None]), a


def bake_premultiplied(col: np.ndarray, a: np.ndarray) -> np.ndarray:
    """Premultiplied (colour, alpha) -> straight-alpha sRGB RGBA8 for glTF BLEND. A bright HDR effect at low alpha still
    glows in game; straight alpha can only reach that brightness with alpha >= the colour's max channel, so alpha is raised
    to it (the effect hides a little more of what is behind it)."""
    ab = np.maximum(a, col.max(axis=-1))
    rgb = np.where(ab[..., None] > 1e-4, col / np.maximum(ab, 1e-4)[..., None], 0.0)
    srgb = np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(np.clip(rgb, 0.0, 1.0), 1 / 2.4) - 0.055)
    out = np.empty(rgb.shape[:2] + (4,), np.uint8)
    out[..., :3] = np.clip(srgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
    out[..., 3] = np.clip(ab * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return out


def _linear_gray(texcache: "TextureCache", path: str | None) -> np.ndarray | None:
    """Single-channel float texture as the shader sees it (sRGB-flagged textures are decoded to linear)."""
    if not path:
        return None
    rgba = texcache._load(path)
    if rgba is None:
        return None
    r = rgba[..., 0]
    return _srgb_to_linear(r) if texcache.pal.texture_meta(path).get("srgb", False) else r.astype(np.float32) / 255.0


def _fire_material(m: Material, prm: dict, texcache: "TextureCache") -> None:
    """M_Pal_Fire (35 Pals): unlit translucent flame, maths in `fire_shade`. The GLB gets one baked frame (emissive + alpha)
    for viewers without the shader; `extras.fire` carries the parameters and the two maps for the animated version
    (viewer/src/scene/effects.ts)."""
    tx = prm["textures"]
    p = fire_params(prm, compiled_params(texcache.pal.root, prm))
    main_path, noise_path = _first(tx, ("MainTex",)), _first(tx, ("NoiseTex",)) or _first(tx, ("MainTex",))
    main, noise = _linear_gray(texcache, main_path), _linear_gray(texcache, noise_path)
    if main is None:
        return
    if noise is None:
        noise = main
    key = f"{m.name}_fire"
    if key not in texcache.model.textures:
        s = FIRE_BAKE_SIZE
        u, v = np.meshgrid((np.arange(s) + 0.5) / s, (np.arange(s) + 0.5) / s)
        texcache.model.textures[key] = TextureImage(key, bake_premultiplied(*fire_shade(p, u, v, main, noise)), srgb=True)
    m.base_color = key  # alpha only: the colour is all emissive (unlit)
    m.base_color_factor = (0.0, 0.0, 0.0, 1.0)
    m.emissive = key
    m.emissive_factor = (1.0, 1.0, 1.0)
    m.metallic_factor, m.roughness_factor = 0.0, 1.0
    m.alpha_mode = "BLEND"
    m.extras["effect"] = True
    m.extras["specular"] = 0.0
    m.extras["fire"] = dict(p, mainTex={"texture": texcache.data(main_path)}, noiseTex={"texture": texcache.data(noise_path or main_path)})


VFX2_MATERIALS = {"M_Pal_Manticore_Fire"}


def compiled_params(root: str, prm: dict) -> set[str] | None:
    """Parameters read by the compiled shader of this material: the first chain entry with its own dumped shader map (an
    instance with static switches / overrides has one, otherwise its parent's is used)."""
    for path in prm.get("chain") or []:
        used = used_params(root, path)
        if used is not None:
            return used
    return None


def vfx2_params(prm: dict, used: set[str] | None = None) -> dict:
    """M_Pal_Manticore_Fire (Azurmane's mane, Blazehowl's flames) parameters. The maths is the cooked pixel shader's, decompiled
    with `PalExtract shader` (raw/shaders/<material>/*.asm; constant slots decoded by shaders.decode); see `vfx2_shade`.
    Instances compile different static-switch variants; which one shows in the parameters their shader reads (`used`):
      DistortionScaleU_01 read -> layer 1 is distorted (base material, Azurmane; not Blazehowl's two);
      AlphaMin/AlphaMax read -> alpha = smoothstep(AlphaMin, AlphaMax, a1 s1 * a2 s2) (base material);
      AlphaScale0x read without them -> alpha = saturate(a1 s1 + a2 s2) (Blazehowl);
      no AlphaScale read -> opaque: the Masked override (Azurmane) only clips on the dissolve / near-camera dither, ~1 in view;
      MaskScaleU read -> * saturate(min(u, 1 - u) * MaskScaleU) ** 2; MaskScaleV read -> * saturate(v * MaskScaleV) ** 2;
      FresnelScale read -> * |N.V| ** FresnelScale.
    Without a dump (`used` None) the base material's variant is assumed."""
    sc = {k: float(v) for k, v in prm["scalars"].items() if isinstance(v, (int, float))}
    has = (lambda k: k in used) if used is not None else (lambda k: k not in ("MaskScaleV",))
    g = lambda k, d: round(sc.get(k, d), 5)
    layer = lambda i: {"tiling": [g(f"TilingU_0{i}", 1.0), g(f"TilingV_0{i}", 1.0)], "offset": [g(f"OffsetU_0{i}", 0.0), g(f"OffsetV_0{i}", 0.0)],
                       "scroll": [g(f"ScrollingU_0{i}", 0.0), g(f"ScrollingV_0{i}", 0.0)],
                       "distort": [g(f"DistortionScaleU_0{i}", 0.0), g(f"DistortionScaleV_0{i}", 0.0)],
                       "clamp": [g(f"Tex0{i}_U_Clamp", 0.0) > 0.5, g(f"Tex0{i}_V_Clamp", 0.0) > 0.5],
                       "rotate": g(f"T0{i}_UV_Rotate", 0.0), "rgb": g(f"RGBScale0{i}", 1.0), "alpha": g(f"AlphaScale0{i}", 1.0)}
    vec = prm["vectors"]
    return {"color1": _rgb(vec.get("Color01"), (1, 1, 1)), "color2": _rgb(vec.get("Color02"), (1, 1, 1)),
            "layer1": layer(1), "layer2": layer(2),
            "distTiling": [g("DistortionTilingU", 1.0), g("DistortionTilingV", 1.0)],
            "distScroll": [g("DistortionScrollingU", 0.0), g("DistortionScrollingV", 0.0)],
            "distOffset": [g("DistortionOffsetU", 0.0), g("DistortionOffsetV", 0.0)], "distortion": g("DistortionScale", 1.0),
            "rgbMin": g("RGBMin", 0.0), "rgbMax": g("RGBMax", 1.0), "alphaMin": g("AlphaMin", 0.0), "alphaMax": g("AlphaMax", 1.0),
            "distort1": has("DistortionScaleU_01"),
            "alphaMode": "range" if has("AlphaMin") else "add" if has("AlphaScale01") else "opaque",
            "maskU": g("MaskScaleU", 0.0) if has("MaskScaleU") else 0.0, "maskV": g("MaskScaleV", 0.0) if has("MaskScaleV") else 0.0,
            "fresnel": g("FresnelScale", 0.0) if has("FresnelScale") else 0.0}


def _sample(img: np.ndarray, uu, vv, clamp=(False, False)) -> np.ndarray:
    """Nearest sample with repeat (or clamp-to-edge per axis) addressing."""
    h, w = img.shape
    x = np.floor(np.clip(uu, 0.0, 1.0 - 1e-6) * w if clamp[0] else uu * w).astype(np.int64) % w
    y = np.floor(np.clip(vv, 0.0, 1.0 - 1e-6) * h if clamp[1] else vv * h).astype(np.int64) % h
    return img[y, x]


def _smoothstep(lo: float, hi: float, x):
    t = np.clip((x - lo) / (hi - lo if hi != lo else 1e-4), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def vfx2_shade(p: dict, u, v, tex1: np.ndarray, tex2: np.ndarray, dist: np.ndarray, t: float = 0.0, n_dot_v=None):
    """M_Pal_Manticore_Fire, transcribed from its decompiled base-pass pixel shader -> (premultiplied display rgb, alpha):

      tiling is centred: uv' = (uv - 0.5) * Tiling + 0.5 (+ scroll * time); layer 2 (and layer 1 in `distort1` variants) is
      warped by D = 2 * DistortionScale * (TextureDistortion(uv_d) - 0.5) times its DistortionScaleU/V_0i, then offset, optionally
      clamped (Tex0i_U/V_Clamp) and rotated about 0.5 by T0i_UV_Rotate turns; each layer sample is squared (a = tex.r ** 2).
      colour = lerp(Color01, Color02, smoothstep(RGBMin, RGBMax, a1 * RGBScale01 + a2 * RGBScale02)), divided by the eye
      adaptation (so it shows at its authored brightness whatever the exposure).
      alpha by variant (see vfx2_params), times the depth soft edge (SoftEdge) and camera fades (not reproduced). The U mask
      reads raw UV0.x, so geometry outside U 0..1 is hidden.
    Mirrored in effects.ts."""

    def centred(uu, vv, tiling, offset, scroll):
        return ((uu - 0.5) * tiling[0] + 0.5 + t * scroll[0] + offset[0], (vv - 0.5) * tiling[1] + 0.5 + t * scroll[1] + offset[1])

    du, dv = centred(u, v, p["distTiling"], p["distOffset"], p["distScroll"])
    d = 2.0 * p["distortion"] * (_sample(dist, du, dv) - 0.5)

    def layer(L, img, warp):
        uu, vv = centred(u, v, L["tiling"], (0.0, 0.0), L["scroll"])
        uu = uu + d * L["distort"][0] * warp + L["offset"][0]
        vv = vv + d * L["distort"][1] * warp + L["offset"][1]
        if L["clamp"][0]:
            uu = np.clip(uu, 0.0, 1.0)
        if L["clamp"][1]:
            vv = np.clip(vv, 0.0, 1.0)
        if L["rotate"]:
            ang = L["rotate"] * 2.0 * np.pi
            x, y = uu - 0.5, vv - 0.5
            uu, vv = x * np.cos(ang) - y * np.sin(ang) + 0.5, x * np.sin(ang) + y * np.cos(ang) + 0.5
        return _sample(img, uu, vv) ** 2

    a1, a2 = layer(p["layer1"], tex1, 1.0 if p.get("distort1", True) else 0.0), layer(p["layer2"], tex2, 1.0)
    s = _smoothstep(p["rgbMin"], p["rgbMax"], a1 * p["layer1"]["rgb"] + a2 * p["layer2"]["rgb"])[..., None]
    c1, c2 = np.array(p["color1"], np.float32), np.array(p["color2"], np.float32)
    rgb = c1 + (c2 - c1) * s
    mode = p.get("alphaMode", "range")
    if mode == "range":
        a = _smoothstep(p["alphaMin"], p["alphaMax"], np.clip(a1 * p["layer1"]["alpha"] * a2 * p["layer2"]["alpha"], 0.0, 1.0))
    elif mode == "add":
        a = np.clip(a1 * p["layer1"]["alpha"] + a2 * p["layer2"]["alpha"], 0.0, 1.0)
    else:
        a = np.ones_like(a1)
    if n_dot_v is not None and p["fresnel"]:
        a = a * np.abs(n_dot_v) ** p["fresnel"]
    if p["maskU"] > 0:
        a = a * np.clip(np.minimum(u, 1.0 - u) * p["maskU"], 0.0, 1.0) ** 2
    if p.get("maskV", 0) > 0:
        a = a * np.clip(v * p["maskV"], 0.0, 1.0) ** 2
    return filmic(rgb * FIRE_EXPOSURE * a[..., None]), a


def _vfx2_material(m: Material, prm: dict, texcache: "TextureCache") -> None:
    tx = prm["textures"]
    p = vfx2_params(prm, compiled_params(texcache.pal.root, prm))
    paths = [_first(tx, ("Texture01",)), _first(tx, ("Texture02",)) or _first(tx, ("Texture01",)),
             _first(tx, ("TextureDistortion",)) or _first(tx, ("Texture02",)) or _first(tx, ("Texture01",))]
    imgs = [_linear_gray(texcache, q) for q in paths]
    if any(i is None for i in imgs):
        return
    # bake the UV square; with MaskScaleU the shader hides everything outside U 0..1, so U clamps to the transparent edge
    key = f"{m.name}_vfx"
    if key not in texcache.model.textures:
        sz = FIRE_BAKE_SIZE
        u, v = np.meshgrid((np.arange(sz) + 0.5) / sz, (np.arange(sz) + 0.5) / sz)
        texcache.model.textures[key] = TextureImage(key, bake_premultiplied(*vfx2_shade(p, u, v, *imgs)), srgb=True)
    m.base_color = m.emissive = key
    m.base_color_factor = (0.0, 0.0, 0.0, 1.0)
    m.emissive_factor = (1.0, 1.0, 1.0)
    if p["maskU"] > 0:
        m.wrap = {k: ("clamp", "repeat") for k in ("base_color", "emissive")}
    m.metallic_factor, m.roughness_factor = 0.0, 1.0
    m.alpha_mode = "OPAQUE" if p["alphaMode"] == "opaque" else "BLEND"
    m.extras["effect"] = True
    m.extras["specular"] = 0.0
    m.extras["vfx2"] = dict(p, tex1={"texture": texcache.data(paths[0])}, tex2={"texture": texcache.data(paths[1])},
                            texDist={"texture": texcache.data(paths[2])})


def _dream_material(m: Material, prm: dict, texcache: "TextureCache") -> None:
    """M_Pal_Dream (7 Pals: Daedream plumage, Smokie / Smokie Cryst manes, OniGhostGirl, YakushimaBoss001...): unlit translucent; MainTex is the colour gradient,
    with a thin fresnelColor rim (through extras.fresnel) and NoiseTex panned over it as a shimmer."""
    tx, sc = prm["textures"], prm["scalars"]
    key = texcache.plain(_first(tx, ("MainTex",)), srgb=True)
    if not key:
        return
    m.base_color = key
    m.base_color_factor = (0.0, 0.0, 0.0, DREAM_OPACITY)
    m.emissive = key
    ei = _rgb(prm["vectors"].get("Emissive Intensity"), (1, 1, 1))
    m.emissive_factor = tuple(float(min(1.0, c)) for c in ei)
    m.metallic_factor, m.roughness_factor = 0.0, 1.0
    m.alpha_mode = "BLEND"
    m.extras["effect"] = True
    m.extras["specular"] = 0.0
    noise = _first(tx, ("NoiseTex",))
    if noise:
        g = lambda k, d: round(float(sc.get(k, d)), 5)
        m.extras["dream"] = {"noiseTex": {"texture": texcache.data(noise)}, "noiseSpeed": [g("noiseUSpeed", 0.0), g("noiseVSpeed", 0.2)],
                             "noiseTiling": g("noiseUTilling", 1.0), "mainScroll": [g("mainUScroll", 0.0), g("mainVScroll", 0.0)]}


DREAM_OPACITY = 0.75  # no opacity input survives in the cooked material; fitted to the in-game look


def _fresnel(prm: dict) -> dict | None:
    """Fresnel rim (PalLit `Fresnel Emissive Color / Exponent / Fraction`; the effect shaders' `fresnelColor / fresnelPower / FresnelScale`):
    HDR linear colour, kept un-normalised; the viewer adds colour * fraction * (1 - N.V)^exponent to the emissive light."""
    vec = {k.lower(): v for k, v in prm["vectors"].items()}
    sc = {k.lower(): float(v) for k, v in prm["scalars"].items() if isinstance(v, (int, float))}
    col = _first(vec, ("fresnel emissive color", "fresnelcolor"))
    if not col:
        return None
    rgb = [float(col.get(c, 0.0)) for c in "RGB"]
    exponent = sc.get("fresnel exponent", sc.get("fresnelpower", 3.0))
    fraction = sc.get("fresnel fraction", sc.get("fresnelscale", 1.0))
    if fraction <= 0 or max(rgb) <= 0:
        return None
    return {"color": [round(c, 4) for c in rgb], "exponent": round(exponent, 4), "fraction": round(fraction, 4)}


# --- skeleton / mesh ------------------------------------------------------------------------


def build_skeleton(pal: RawPal) -> tuple[Skeleton, list[np.ndarray]]:
    joints: list[Joint] = []
    world: list[np.ndarray] = []
    for b in pal.info["bones"]:
        t = cv.pos(b["t"])
        r = cv.quat(b["r"])
        r = r / np.linalg.norm(r)
        s = cv.scale(b["s"])
        local = cv.trs_matrix(t, r, s)
        w = world[b["parent"]] @ local if b["parent"] >= 0 else local
        world.append(w)
        joints.append(Joint(name=b["name"], parent=b["parent"], translation=tuple(map(float, t)), rotation=tuple(map(float, r)),
                            scale=tuple(map(float, s)), inverse_bind=np.linalg.inv(w), skinning=True, scale_compensate=False))
    return Skeleton(joints=joints, palette=list(range(len(joints)))), world


def _top4(joints: np.ndarray, weights: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(-weights, axis=1)[:, :4]
    j = np.take_along_axis(joints, order, 1).astype(np.uint16)
    w = np.take_along_axis(weights, order, 1).astype(np.float32)
    s = w.sum(1, keepdims=True)
    w = np.where(s > 0, w / np.where(s > 0, s, 1), np.array([1, 0, 0, 0], np.float32))
    j[s[:, 0] <= 0] = 0
    return j, w


def build_meshes(pal: RawPal, materials: list[Material]) -> list[Mesh]:
    buf = pal.lod_buffers(0)
    P = cv.pos(buf["position"])
    N = cv.vec(buf["normal"][:, :3])
    N = N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-8)
    uvs = [buf[k].astype(np.float32) for k in sorted(k for k in buf if re.fullmatch(r"uv\d+", k))]
    J, W = _top4(buf["joints"], buf["weights"])
    idx = buf["indices"]
    morphs = pal.morph_targets(len(P))
    meshes = []
    used: dict[str, int] = {}
    for s in pal.info["lods"][0]["sections"]:
        tri = idx[s["firstIndex"] : s["firstIndex"] + 3 * s["numFaces"]]
        if not len(tri):
            continue
        verts, remap = np.unique(tri, return_inverse=True)
        mat = materials[s["material"]] if s["material"] < len(materials) else materials[0]
        label = _part_name(mat.name, pal.code)
        n = used.get(label, 0)
        used[label] = n + 1
        name = label if n == 0 else f"{label}_{n}"
        if mat.extras.get("brow") and _split_brow(tri, uvs[0] if uvs else None, P, mat, name, N, uvs, J, W, meshes, morphs):
            continue
        prim = Primitive(material=mat.name, indices=remap.astype(np.uint32), positions=P[verts], normals=N[verts],
                         uvs=[u[verts] for u in uvs[:2]], joints=J[verts], weights=W[verts])
        extras = {"effect": True} if mat.extras.get("effect") else {}
        extras.update(_morph_prim(prim, verts, morphs))
        meshes.append(Mesh(name=name, primitives=[prim], extras=extras))
    return meshes


def _morph_prim(prim: Primitive, verts: np.ndarray, morphs: tuple[list[str], np.ndarray]) -> dict:
    """Attach the mesh's morph targets (facial expressions: the humans' eye_close, eye_smile, mouth_open...) to a primitive built from
    the LOD vertices `verts`. Every target is kept, so all primitives of a mesh agree on the target list; primitives no target moves
    get none. Returns the mesh extras (`targetNames`, read by the viewer's expression picker)."""
    names, dense = morphs
    if not names:
        return {}
    d = cv.pos(dense[:, verts])
    if not d.any():
        return {}
    prim.morph_targets = [np.ascontiguousarray(t) for t in d]
    return {"targetNames": list(names)}


BROW_GRID = (0.5, 0.25)  # UV1 cell size of the brow expression sets: the 2 x 4 grid of the eye atlas


def _split_brow(tri, uv0, P, mat, name, N, uvs, J, W, meshes: list, morphs=([], None)) -> bool:
    """Brow cards come as complete brow sets stacked on the face, one per expression. The engine picks the set through UV1: every
    card sits in one cell of the eye atlas grid (BROW_GRID) and the brow mask is shifted by the eye's cell offset. Cards are the
    mesh's connected pieces; a set is the cards of one UV1 cell (not their order in the mesh: that only coincides for some Pals).
    Cards sharing a UV0 rectangle form a channel, channels at the same distance from the face centre one kind (long outer eyelash
    cards, short inner eyebrow cards). `link_brow_sets` renumbers the sets by eye atlas cell afterwards. Exposed as a "brow"
    part group (the viewer shows one set at a time). Returns False when a card straddles two cells or only one set exists."""
    if uv0 is None or len(uvs) < 2:
        return False
    uv1 = uvs[1]
    tri = np.asarray(tri).reshape(-1, 3)
    parent: dict = {}

    def find(k):
        while parent.setdefault(k, k) != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for t in tri:
        for i in t[1:]:
            parent[find(int(i))] = find(int(t[0]))
    cards: dict = {}
    for n, t in enumerate(tri):
        cards.setdefault(find(int(t[0])), []).append(n)
    cw, ch = BROW_GRID
    cell_of, channel_of = [], []
    channels: dict = {}
    for faces in sorted(cards.values(), key=min):
        v = np.unique(tri[faces].ravel())
        lo, hi = uv1[v].min(0), uv1[v].max(0)
        cx, cy = int((lo[0] + hi[0]) / 2 // cw), int((lo[1] + hi[1]) / 2 // ch)
        if int((lo[0] + FIT_TOL) // cw) != cx or int((hi[0] - FIT_TOL) // cw) != cx or int((lo[1] + FIT_TOL) // ch) != cy                 or int((hi[1] - FIT_TOL) // ch) != cy:
            return False
        cell_of.append((cy, cx))
        u = uv0[v]
        channel_of.append(tuple(np.round(np.concatenate([u.min(0), u.max(0)]), 2)))
        channels.setdefault(channel_of[-1], []).append(len(cell_of) - 1)
    ordered = sorted(cards.values(), key=min)
    cells = sorted(set(cell_of))
    if len(cells) < 2:
        return False

    def lateral(ids):
        return float(np.mean([np.abs(P[np.unique(tri[ordered[i]].ravel())][:, 0]).mean() for i in ids]))

    def extent(ids):
        return float(np.mean([np.ptp(P[np.unique(tri[ordered[i]].ravel())], axis=0).max() for i in ids]))

    chans = sorted(channels.values(), key=lateral)
    groups: list[list] = [[chans[0]]]
    for c in chans[1:]:
        if lateral(c) - lateral(groups[-1][-1]) > 0.02:
            groups.append([])
        groups[-1].append(c)
    groups.sort(key=lambda g: -np.mean([extent(c) for c in g]))
    names = ["eyelash", "brow"] if len(groups) == 2 else ["brow"] if len(groups) == 1 else [f"brow{i + 1}" for i in range(len(groups))]
    glabel = "Brows & lashes" if len(groups) > 1 else "Brow"
    for gname, chs in zip(names, groups):
        members = [i for c in chs for i in c]
        # set k of every kind belongs to one expression (eyelash k + brow k): they share one group option and switch together
        for k, cell in enumerate(cells):
            ids = [i for i in members if cell_of[i] == cell]
            if not ids:
                continue
            faces = np.concatenate([ordered[i] for i in ids])
            # flat index list: a (n, 3) inverse was written as a VEC3 accessor and three.js drew only the first third of the triangles
            verts, remap = np.unique(tri[faces].ravel(), return_inverse=True)
            prim = Primitive(material=mat.name, indices=remap.astype(np.uint32), positions=P[verts], normals=N[verts],
                             uvs=[u[verts] for u in uvs[:2]], joints=J[verts], weights=W[verts])
            extras = {"group": "brow", "option": f"brow_{k + 1}", "group_label": glabel}
            extras.update(_morph_prim(prim, verts, morphs))
            meshes.append(Mesh(name=f"{gname.title()}_{k + 1}", primitives=[prim], visible=(k == 0), extras=extras))
    return True


def part_groups(meshes: list[Mesh]) -> list[dict]:
    """Part groups (one option shown at a time) of the meshes tagged extras {group, option}."""
    groups: dict[str, list[str]] = {}
    labels: dict[str, str] = {}
    linked: dict[tuple, str] = {}  # (group, option) -> variant it follows (brow set of an eye cell)
    for m in meshes:
        g, o = m.extras.get("group"), m.extras.get("option")
        if g and o and o not in groups.setdefault(g, []):
            groups[g].append(o)
        if g and m.extras.get("group_label"):
            labels.setdefault(g, m.extras["group_label"])
        if g and o and m.extras.get("variant"):
            linked[(g, o)] = m.extras["variant"]
    return [{"name": g, "label": labels.get(g, g.title()),
             "options": [{"id": o, "label": o.rsplit("_", 1)[-1], "default": i == 0, **({"variant": linked[(g, o)]} if (g, o) in linked else {})}
                         for i, o in enumerate(opts)]}
            for g, opts in groups.items()]


def _part_name(slot: str, code: str) -> str:
    """MI_Sheepball_Body -> Body; MI_Kitsunebi_Ice_Extra -> Extra."""
    s = re.sub(r"^(MI|M)_", "", slot)
    low = s.lower()
    parts = code.split("_")
    for n in range(len(parts), 0, -1):
        p = "_".join(parts[:n]).lower() + "_"
        if low.startswith(p):
            return s[len(p):] or s
    return s


# --- animation ------------------------------------------------------------------------------


def aura_shells(pal: RawPal) -> list[dict]:
    """Glow shells the BP adds at runtime (`pal.json` `auras`: unused instances that set `OutlineWidth`, Enchanted Sword's cyan outline):
    inverted hull pushed `OutlineWidth` px out, unlit `EmissiveColor` x `Emissive Intensity`, pulsed by the Pal's emissive-time curve."""
    out = []
    for a in pal.info.get("auras") or []:
        prm = resolve_params(pal.root, a.get("chain") or [])
        col = _rgb(prm["vectors"].get("EmissiveColor"), (1, 1, 1))
        out.append({"color": col, "intensity": float(prm["scalars"].get("Emissive Intensity", 1.0)),
                    "width": float(prm["scalars"].get("OutlineWidth", 1.0)),
                    "curve": [[k["t"], k["v"]] for k in pal.info.get("auraCurve") or []]})
    return out


def kawaii_physics(pal: RawPal) -> list[dict]:
    """Cloth / hair / belt simulation nodes of a human (raw physics.json, `PalExtract physics`): the game runs the KawaiiPhysics plugin
    on chains of bones that the clips leave at their bind pose. Converted to glTF metres / bone-local space; the viewer simulates them."""
    path = os.path.join(pal.folder, "physics.json")
    if not os.path.isfile(path):
        return []
    from ...catalog.palworld import _qrot, _rotator_quat  # Unreal FRotator maths shared with the held-item offsets

    with open(path, encoding="utf-8") as f:
        nodes = json.load(f)["nodes"]
    names = {b["name"] for b in pal.info["bones"]}
    out = []
    for n in nodes:
        if n.get("root") not in names:  # a layer shared between characters (People01 roots at a bone this skeleton lacks): nothing to simulate
            continue
        st = n.get("settings") or {}
        g = n.get("gravity") or {}
        caps = []
        for c in n.get("capsules") or []:
            axis = _qrot(_rotator_quat(*c["rot"]), np.array([0.0, 0.0, 1.0]))  # capsules lie along the collider's local Z
            half = np.asarray(c["loc"], float), axis * float(c["length"]) * 0.5
            caps.append({"bone": c["bone"], "a": cv.pos(half[0] - half[1]).round(5).tolist(), "b": cv.pos(half[0] + half[1]).round(5).tolist(),
                         "radius": round(float(c["radius"]) * cv.CM, 5)})
        sph = [{"bone": c["bone"], "center": cv.pos(c["loc"]).round(5).tolist(), "radius": round(float(c["radius"]) * cv.CM, 5),
                "inside": "Inner" in str(c.get("limitType", ""))} for c in n.get("spheres") or []]
        out.append({"root": n["root"], "extraRoots": [r for r in n.get("additionalRoots") or [] if r in names], "exclude": n.get("exclude") or [],
                    "damping": st.get("Damping", 0.1), "stiffness": st.get("Stiffness", 0.05), "radius": round(float(st.get("Radius", 3.0)) * cv.CM, 5),
                    "limitAngle": st.get("LimitAngle", 0.0), "gravity": cv.pos([g.get("X", 0), g.get("Y", 0), g.get("Z", 0)]).round(5).tolist(),
                    "fps": n.get("targetFrameRate", 60), "capsules": caps, "spheres": sph})
    return out


def _qmul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Hamilton product a * b of xyzw quaternions (broadcasting): b is applied first, as in the engine's FQuat."""
    ax, ay, az, aw = np.moveaxis(a, -1, 0)
    bx, by, bz, bw = np.moveaxis(b, -1, 0)
    return np.stack([aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz], -1)


def _additive_base(pal: RawPal, a: dict) -> dict | None:
    """The pose clip an additive layer is baked onto: its character's `<prefix>_Idle` (AS_X_Minigun_Aim_cd -> AS_X_Minigun_Idle).
    The game blends these layers over whatever the Pal is doing; the Idle is the standing pose they were authored for."""
    base = re.sub(r"_Aim.*$", "", a["name"]) + "_Idle"
    return next((b for b in pal.info["anims"] if b["name"] == base and not b.get("additive") and b.get("tracks")), None)


def bake_additive(pal: RawPal, a: dict, base: dict) -> dict[str, np.ndarray]:
    """A mesh-space rotation-offset additive layer (AAT_RotationOffsetMeshSpace) applied on frame 0 of `base`, as the engine's
    AccumulateMeshSpaceRotationAdditiveToLocalPose: the stored rotation is a delta in mesh space (not accumulated down the chain),
    so mesh' = delta * mesh(base) and every local rotation is recovered from the new mesh rotations; translation is added and
    the base scale is kept. Bones the layer has no track for take identity deltas. Returns bone -> (frames, 10) like anim_frames."""
    bones = pal.info["bones"]
    add = pal.anim_frames(a)
    bf = pal.anim_frames(base)
    frames = int(a["frames"])
    out: dict[str, np.ndarray] = {}
    ident = np.array([0, 0, 0, 1], np.float32)
    mesh_b: list[np.ndarray] = []   # base rotation in mesh space
    mesh_n: list[np.ndarray] = []   # result, (frames, 4)
    for i, b in enumerate(bones):
        n = b["name"]
        lr = bf[n][0, 0:4] if n in bf else np.asarray(b["r"], np.float32)
        lt = bf[n][0, 4:7] if n in bf else np.asarray(b["t"], np.float32)
        ls = bf[n][0, 7:10] if n in bf else np.asarray(b["s"], np.float32)
        mb = lr if b["parent"] < 0 else _qmul(mesh_b[b["parent"]], lr)
        mesh_b.append(mb)
        d = add.get(n)
        dq = d[:, 0:4] if d is not None else np.broadcast_to(ident, (frames, 4))
        mn = _qmul(dq, np.broadcast_to(mb, (frames, 4)))
        mn = mn / np.maximum(np.linalg.norm(mn, axis=1, keepdims=True), 1e-8)
        mesh_n.append(mn)
        if b["parent"] < 0:
            local = mn
        else:
            pc = mesh_n[b["parent"]] * np.array([-1, -1, -1, 1], np.float32)  # conjugate = inverse of the unit quaternion
            local = _qmul(pc, mn)
        rec = np.zeros((frames, 10), np.float32)
        rec[:, 0:4] = local
        rec[:, 4:7] = lt + (d[:, 4:7] if d is not None else 0.0)
        rec[:, 7:10] = ls  # the layer's scale channel is unreliable (unkeyed bones read 1, keyed ones ~0), and these layers carry no scale
        out[n] = rec
    return out


def build_clips(pal: RawPal) -> list[Clip]:
    bones = {b["name"]: b for b in pal.info["bones"]}
    skel = {b["name"]: b for b in (pal.info.get("skeletonBones") or [])}
    clips: list[Clip] = []
    seen: dict[str, int] = {}
    # humans share one skeleton, so the extractor finds every character's clips; keep the character's own (AS_<Boss>_*)
    human = pal.sub == "palworld_humans"
    owner = pal.code.removeprefix("Boss_").rstrip("0123456789") if pal.sub == "palworld_humans" else None
    for i, a in enumerate(pal.info["anims"]):
        if "error" in a or not a.get("tracks"):
            continue
        if owner and not a["name"].lower().startswith("as_" + owner.lower()):
            continue
        baked = None
        if a.get("additive"):
            # Additive layers (aim offsets) are deltas, not poses: they are baked onto their Idle, and a layer with no known base is skipped.
            base = _additive_base(pal, a) if a.get("additiveType") == "AAT_RotationOffsetMeshSpace" else None
            if base is None:
                continue
            baked = bake_additive(pal, a, base)
        frames = int(a["frames"])
        length = float(a.get("length") or 0.0)
        fps = (frames - 1) / length if frames > 1 and length > 0 else 30.0
        rate = float(a.get("rateScale") or 1.0) or 1.0
        times = (np.arange(frames, dtype=np.float32) / fps / abs(rate)).astype(np.float32)
        data = baked if baked is not None else pal.anim_frames(a)
        tracks = []
        for bone, kv in data.items():
            if bone not in bones:
                continue
            q = cv.quat(kv[:, 0:4])
            q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-8)
            # keep consecutive quaternions in the same hemisphere for clean linear interpolation
            for k in range(1, len(q)):
                if np.dot(q[k], q[k - 1]) < 0:
                    q[k] = -q[k]
            t = _retarget(kv[:, 4:7], bones[bone], skel.get(bone))
            tracks.append(Track(bone, "rotation", times, q.astype(np.float32)))
            tracks.append(Track(bone, "translation", times, cv.pos(t)))
            sc = kv[:, 7:10]
            if np.abs(sc - 1.0).max() > 1e-4:
                tracks.append(Track(bone, "scale", times, cv.scale(sc)))
        # humans share one skeleton, so a clip's owner is part of its identity (ElectricBoss_Ride_Idle_Boss vs ForestBoss_Ride_Idle)
        # cutscene clips (Pal/Content/Pal/Demo: AS_<X>StartDemo_*) carry the character's world travel on `root` (Snow Boss: 47 m): play them in
        # place, in frame. Gameplay clips keep it: a seat-in-clip boss's root IS its seat on the spinning mount (Sorajima VortexRage).
        if human and DEMO_CLIP_RE.search(a["name"]):
            for t in tracks:
                if t.joint == "root" and t.kind == "translation" and len(t.values) > 1 and float(np.ptp(t.values, axis=0).max()) > 1.0:
                    t.values = np.repeat(cv.pos(bones["root"]["t"])[None], len(t.values), axis=0)  # the bind position (the clip starts 47 m away)
        name = a["name"][3:] if human and a["name"].startswith("AS_") else clip_name(a["name"], pal.code)
        if name in seen:
            seen[name] += 1
            name = f"{name} [{seen[name]}]"
        else:
            seen[name] = 0
        category = "ride" if human and "_Ride_" in name else categorize(name)
        clips.append(Clip(name=name, tracks=tracks, frame_rate=round(fps * abs(rate), 3), frame_count=frames,
                          loop=bool(LOOP_RE.search(name)), index=i, category=category, source=a["name"],
                          mount_ok=human or mount_ok(name, category)))
    return clips


SEAT_IN_CLIP_M = 0.5


def ride_seat_in_clip(clips: list[Clip]) -> bool:
    """Some bosses' ride clips are authored in the mount's own space: `root` sits at the seat (Viking Boss 3.3 m up, World Tree 8.5 m), so the
    boss belongs at the mount's model origin and the marker's bone offset must not be added. Others keep `root` at the origin and rely on the marker
    (Electric Boss: 0.01 m). First frame of the ride idle, glTF metres."""
    for c in clips:
        if c.category == "ride" and "_Ride_Idle" in c.name:
            for t in c.tracks:
                if t.joint == "root" and t.kind == "translation" and len(t.values):
                    return float(np.linalg.norm(t.values[0])) > SEAT_IN_CLIP_M
    return False


def _retarget(t: np.ndarray, mesh_bone: dict, skel_bone: dict | None) -> np.ndarray:
    mode = (skel_bone or {}).get("retarget", "Animation")
    if mode == "Skeleton":
        return np.broadcast_to(np.asarray(mesh_bone["t"], np.float32), t.shape)
    if skel_bone is None:
        return t
    src = np.asarray(skel_bone["t"], np.float32)
    dst = np.asarray(mesh_bone["t"], np.float32)
    if mode == "AnimationScaled":
        ls, ld = float(np.linalg.norm(src)), float(np.linalg.norm(dst))
        return t * (ld / ls) if ls > 1e-6 else t
    if mode == "AnimationRelative":
        return t + (dst - src)
    return t


# --- expression atlases ---------------------------------------------------------------------


CELL_SIZES = (0.5, 0.25, 0.125)
FIT_TOL = 0.01
ENGINE_GRIDS = ((0.5, 0.25), (0.5, 0.5))  # eye (2 x 4) and mouth (2 x 2) atlases: the only grids the overshoot fallback may pick
BOX_OVERSHOOT = 0.1  # how far an expression card's UV box may exceed one atlas cell


def atlas_layout(uv: np.ndarray, alpha: np.ndarray) -> dict | None:
    """Eye/mouth atlases: the mesh UVs sit inside one cell of a power-of-two grid and other cells hold alternate
    expressions drawn at the same spot. Every grid whose cell holds the UV box is scored by its "aligned" cells (content
    mostly inside the UV box moved to that cell); the best grid wins (ties: larger cells).
    Returns {cell: (w, h), base: (cx, cy), cells: [(cx, cy), ...]} or None."""
    lo, hi = uv.min(0), uv.max(0)
    # strict: the whole UV box inside one cell; fallback for cards that overshoot their cell (Penking 0.06, Skutlass below 0)
    return _atlas_grids(lo, hi, alpha, strict=True) or _atlas_grids(lo, hi, alpha, strict=False)


def _atlas_grids(lo: np.ndarray, hi: np.ndarray, alpha: np.ndarray, strict: bool) -> dict | None:
    if strict and (lo.min() < 0 or hi.max() > 1):
        return None
    h, w = alpha.shape
    mask = alpha > 16
    margin = 0.02
    best = None
    for cw in CELL_SIZES:
        for ch in CELL_SIZES:
            if not strict and (cw, ch) not in ENGINE_GRIDS:
                continue
            if strict:
                # UV padding may overshoot a cell edge by a few texels: test the fit on a slightly shrunk box
                bx, by = int((lo[0] + FIT_TOL) // cw), int((lo[1] + FIT_TOL) // ch)
                if int(min(hi[0] - FIT_TOL, 0.9999) // cw) != bx or int(min(hi[1] - FIT_TOL, 0.9999) // ch) != by:
                    continue  # the UVs straddle a cell border at this size
                lo_c, hi_c = lo, hi
            else:
                # the base cell holds the box centre, the box is clipped to it, a card much larger than the cell is no atlas card
                bx, by = int(min((lo[0] + hi[0]) / 2, 0.9999) // cw), int(min((lo[1] + hi[1]) / 2, 0.9999) // ch)
                if bx < 0 or by < 0 or hi[0] - lo[0] > cw + BOX_OVERSHOOT or hi[1] - lo[1] > ch + BOX_OVERSHOOT:
                    continue
                lo_c, hi_c = np.maximum(lo, (bx * cw, by * ch)), np.minimum(hi, ((bx + 1) * cw, (by + 1) * ch))
            good = []
            for cy in range(round(1 / ch)):
                for cx in range(round(1 / cw)):
                    x0, y0 = int(cx * cw * w), int(cy * ch * h)
                    cell = mask[y0 : y0 + int(ch * h), x0 : x0 + int(cw * w)]
                    n = int(cell.sum())
                    if n < 0.002 * cell.size:
                        continue
                    ox, oy = (cx - bx) * cw, (cy - by) * ch
                    u0, u1 = int((lo_c[0] + ox - margin) * w) - x0, int((hi_c[0] + ox + margin) * w) - x0
                    v0, v1 = int((lo_c[1] + oy - margin) * h) - y0, int((hi_c[1] + oy + margin) * h) - y0
                    inside = int(cell[max(0, v0) : max(0, v1), max(0, u0) : max(0, u1)].sum())
                    if inside >= 0.8 * n:
                        good.append((cx, cy))
            if (bx, by) in good and len(good) >= 2 and (best is None or len(good) > len(best["cells"])):
                best = {"cell": (cw, ch), "base": (bx, by), "cells": good}
    return best


def build_atlas_variants(model: Model) -> list:
    from ...common.model import Variant

    variants = []
    for mat in list(model.materials.values()):
        if not mat.base_color or mat.extras.get("effect") or mat.extras.get("brow"):
            continue  # brow cards are masks of the eye atlas, not an atlas themselves
        tex = model.textures[mat.base_color].rgba
        if tex[..., 3].min() == 255:
            continue  # no alpha: a plain body texture, never an atlas
        uvs = [p.uvs[0] for m in model.meshes for p in m.primitives if p.material == mat.name and p.uvs]
        if not uvs:
            continue
        lay = atlas_layout(np.concatenate(uvs), tex[..., 3])
        if lay is None:
            continue
        cw, ch = lay["cell"]
        bx, by = lay["base"]
        label = _part_name(mat.name, model.id)
        group = re.sub(r"\W+", "_", label.lower()) or "atlas"
        for n, (cx, cy) in enumerate(lay["cells"]):
            off = ((cx - bx) * cw, (cy - by) * ch)
            over = Material(**{**mat.__dict__, "uv_transform": {**mat.uv_transform, "base_color": (1.0, 1.0, off[0], off[1])}, "extras": dict(mat.extras)})
            variants.append(Variant(name=f"{group}_{n + 1}", materials={mat.name: over}, group=group, label=str(n + 1),
                                    default=(cx, cy) == (bx, by), group_label=label))
        mat.extras["atlas"] = {"cell": [cw, ch], "base": [bx, by], "cells": [list(c) for c in lay["cells"]]}
    return variants


def link_brow_sets(model: Model) -> None:
    """Brow / eyelash sets follow the eye expression. All sets live in one mesh, and their second UV channel puts each set in
    its own cell of the eye atlas grid (Nox: UV1 of set k fills one 0.5 x 0.25 cell). The brow material samples a mask
    (T_Common_FM) through UV1, so shifting UV1 by the eye's cell offset leaves exactly one set in the visible base cell. Mirrored
    here: each set is renumbered to the eye cell its UV1 sits in and tagged with that eye variant (`variant`), so the viewer
    switches both together. Nothing changes unless every set lands in a distinct cell of one atlas."""
    sets: dict[str, list[Mesh]] = {}
    for m in model.meshes:
        if m.extras.get("group") == "brow" and m.extras.get("option"):
            sets.setdefault(m.extras["option"], []).append(m)
    if len(sets) < 2 or any(not p.uvs or len(p.uvs) < 2 for ms in sets.values() for m in ms for p in m.primitives):
        return
    for mat in model.materials.values():
        atlas = mat.extras.get("atlas")
        if not atlas:
            continue
        (cw, ch), cells = atlas["cell"], [tuple(c) for c in atlas["cells"]]
        where: dict[str, tuple] = {}
        for opt, ms in sets.items():
            uv1 = np.concatenate([p.uvs[1] for m in ms for p in m.primitives])
            cx, cy = (int(c) for c in (uv1.mean(0) // (cw, ch)))
            lo, hi = uv1.min(0), uv1.max(0)
            # the whole set must sit inside that cell (FIT_TOL: UV padding)
            if (lo[0] + FIT_TOL) // cw != cx or (hi[0] - FIT_TOL) // cw != cx or (lo[1] + FIT_TOL) // ch != cy or (hi[1] - FIT_TOL) // ch != cy:
                break
            where[opt] = (cx, cy)
        else:
            if len(set(where.values())) != len(where) or not all(c in cells for c in where.values()):
                continue
            group = next(v.group for v in model.variants if mat.name in v.materials)
            base = tuple(atlas["base"])
            for opt, ms in sets.items():
                n = cells.index(where[opt]) + 1
                for m in ms:
                    m.name = re.sub(r"_\d+$", f"_{n}", m.name)
                    m.visible = where[opt] == base
                    m.extras.update(option=f"brow_{n}", variant=f"{group}_{n}")
            # part options are listed in mesh order (first = default): put the brow meshes, in their own slots, in eye-cell order
            slots = [i for i, m in enumerate(model.meshes) if m.extras.get("group") == "brow"]
            ordered = sorted((model.meshes[i] for i in slots), key=lambda m: (int(m.extras["option"].rsplit("_", 1)[1]), m.name))
            for i, m in zip(slots, ordered):
                model.meshes[i] = m
            return


def _skin_part(mi_name: str, code: str, skin: str) -> str:
    """MI_Horus__SkinDark_Body (skin Horus_SkinDark) -> body; matches _part_name of the base slot."""
    token = skin[len(code) + 1 :] if skin.startswith(code + "_") else skin
    s = re.sub(r"^(MI|M)_", "", mi_name)
    s = re.sub(re.escape(token), "", s, flags=re.I)
    return _part_name(re.sub(r"_+", "_", s).strip("_"), code).lower()


def build_skin_variants(pal: RawPal, model: Model, mats: list[Material], texcache: TextureCache) -> list:
    """Material-only skins (<Code>_SkinDark folders without a mesh) -> a "skin" variant group on the base Pal."""
    from ...common.model import Variant

    skins = pal.info.get("materialSkins") or {}
    if not skins:
        return []
    by_part = {_part_name(m.name, pal.code).lower(): m for m in mats}
    out = [Variant(name="skin_default", materials={}, group="skin", label="Default", default=True, group_label="Skin")]
    for skin, entries in sorted(skins.items()):
        over = {}
        for e in entries:
            base = by_part.get(_skin_part(e["name"], pal.code, skin))
            if base is None:
                continue
            m = build_material(pal, {"slot": base.name, "chain": e["chain"]}, texcache)
            over[base.name] = m
        if over:
            token = skin[len(pal.code) + 1 :] if skin.startswith(pal.code + "_") else skin
            label = "Dark" if token.lower() == "skindark" else token.replace("Skin", "Skin ")
            out.append(Variant(name=f"skin_{token.lower()}", materials=over, group="skin", label=label, group_label="Skin"))
    return out if len(out) > 1 else []


# --- held items (weapons) ---------------------------------------------------------------------


def _classify_map(param: str, tex: str) -> tuple[str | None, str | None]:
    """(kind, packing) of a texture bound to a plain master material: kind base|normal|orm, packing for orm (see TextureCache.orm)."""
    for label in (param.lower(), tex.lower()):
        if re.search(r"(^|[ _])(rma)($|[ _])", label):
            return "orm", "rma"
        if re.search(r"(^|[ _])(amr)($|[ _])", label):
            return "orm", "amr"
        if re.search(r"(^|[ _])(a?orm|arm)($|[ _])", label):
            return "orm", "orm"
        if re.search(r"normal|(^|[ _])(nrm|n)$", label):
            return "normal", None
        if re.search(r"dif+use|albedo|base ?colou?r|base texture|(^|[ _])(d|b|bc|bco|alb)$", label):
            return "base", None
    return None, None


def build_weapon_material(pal: RawPal, slot: dict, texcache: TextureCache) -> Material:
    """Weapon materials come in three shapes: a PalLit instance (launcher, hammer: same model as the Pals), an instance of a
    plain master material with Albedo / Normal / RMA parameters (shotgun), or a bare UMaterial whose maps are found by name
    among the mesh folder's textures (rifles: *_BaseColor|_B, *_Normal|_N, *_AO_Metallic_Roughness (amr) | *_orm)."""
    chain = slot.get("chain") or []
    tx: dict[str, str] = {}
    if len(chain) > 1:
        try:
            tx = resolve_params(pal.root, chain)["textures"]
        except (OSError, KeyError):
            tx = {}
        if _first(tx, BASE_PARAMS):  # PalLit family
            return build_material(pal, slot, texcache)
        if tx.get("Albedo"):
            m = Material(name=slot["slot"])
            m.base_color = texcache.plain(tx["Albedo"], srgb=True)
            m.normal = texcache.normal(tx.get("Normal"))
            orm = texcache.orm(tx.get("RMA"), packing="rma")
            if orm:
                m.metallic_roughness = m.occlusion = orm
                m.metallic_factor = m.roughness_factor = 1.0
            return m
    m = Material(name=slot["slot"])
    found: dict[str, tuple[str, str | None]] = {}
    for tp in pal.info.get("folderTextures") or []:
        n = tp.rsplit("/", 1)[-1].lower()
        for kind, suffixes, packing in (("base", ("basecolor", "_b"), None), ("normal", ("normal", "_n"), None),
                                        ("orm", ("ao_metallic_roughness",), "amr"), ("orm", ("_orm",), "orm")):
            if any(n.endswith(s) for s in suffixes):
                found[kind] = (tp, packing)
    # instances of other master materials name their maps freely ("Difuse texture", "ORM map", "Base Normal", T_Pans_ARM, TX_*_RMA):
    # classify each bound texture by parameter name, then by texture name, for the kinds the folder search did not find
    for pname, tp in tx.items():
        kind, packing = _classify_map(pname, tp.rsplit("/", 1)[-1])
        if kind and kind not in found:
            found[kind] = (tp, packing)
    m.base_color = texcache.plain(found.get("base", (None,))[0], srgb=True)
    m.normal = texcache.normal(found.get("normal", (None,))[0])
    if "orm" in found:
        orm = texcache.orm(found["orm"][0], packing=found["orm"][1])
        if orm:
            m.metallic_roughness = orm
            m.occlusion = orm
            m.metallic_factor = m.roughness_factor = 1.0
    return m


def import_weapon(raw_root: str, name: str, model_id: str | None = None, display_name: str | None = None) -> Model:
    pal = load_pal(raw_root, name, "palworld_weapons")
    model = Model(id=model_id or name, game="palworld", display_name=display_name or name, kind="object", skeleton=None,
                  meshes=[], materials={}, textures={})
    model.skeleton, _ = build_skeleton(pal)
    texcache = TextureCache(pal, model)
    mats = []
    for slot in pal.info["materials"]:
        m = build_weapon_material(pal, slot, texcache)
        model.materials[m.name] = m
        mats.append(m)
    model.meshes = build_meshes(pal, mats)
    for t in model.textures.values():  # weapons ship 4K maps for a prop the size of a hand: 2K is plenty
        while max(t.rgba.shape[:2]) > 2048:
            t.rgba = t.rgba[::2, ::2].copy()
    model.source_paths = [pal.info["mesh"]]
    return model


# --- entry ----------------------------------------------------------------------------------


def import_pal(raw_root: str, code: str, model_id: str | None = None, display_name: str | None = None, with_anim: bool = True,
               sub: str = "palworld", kind: str = "pal") -> Model:
    pal = load_pal(raw_root, code, sub)
    model = Model(id=model_id or code, game="palworld", display_name=display_name or code, kind=kind, skeleton=None,
                  meshes=[], materials={}, textures={})
    skeleton, _ = build_skeleton(pal)
    model.skeleton = skeleton
    texcache = TextureCache(pal, model)
    mats = []
    for slot in pal.info["materials"]:
        m = build_material(pal, slot, texcache)
        base = m.name
        k = 1
        while m.name in model.materials:  # two slots can share a material name
            m.name = f"{base}_{k}"
            k += 1
        model.materials[m.name] = m
        mats.append(m)
    model.meshes = build_meshes(pal, mats)
    model.variants = build_atlas_variants(model) + build_skin_variants(pal, model, mats, texcache)
    link_brow_sets(model)  # needs the eye atlas; renumbers the brow sets, so part groups come after it
    if (pg := part_groups(model.meshes)):
        model.extras["part_groups"] = pg
    if (aura := aura_shells(pal)):
        model.extras["auras"] = aura
    if with_anim:
        model.clips = build_clips(pal)
        if kind == "human" and (ph := kawaii_physics(pal)):
            model.extras["physics"] = ph
        if kind == "human" and ride_seat_in_clip(model.clips):
            model.extras["seatInClip"] = True
        idle =[c.name for c in model.clips if c.name.lower() == "idle"] or [c.name for c in model.clips if c.category == "idle"]
        if kind == "human":  # shared skeleton: clips of every character; a standing idle of any (not a ride / emote), the model's own first
            own = (model_id or code).removeprefix("Boss_").rstrip("0123456789")
            idle = sorted((c.name for c in model.clips if re.match(rf"^{own}_.*Idle", c.name)),
                          key=lambda n: ("Ride" in n, "Skin" in n, len(n), n)) or idle
        model.default_clip = idle[0] if idle else (model.clips[0].name if model.clips else None)
    model.source_paths = [pal.info["mesh"]]
    model.extras["skeleton"] = pal.info.get("skeleton")
    return model
