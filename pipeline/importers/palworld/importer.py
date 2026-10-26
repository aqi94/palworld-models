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

import os
import re

import numpy as np
from PIL import Image

from ...common.model import Clip, Joint, Material, Mesh, Model, Primitive, Skeleton, TextureImage, Track
from . import convert as cv
from .raw import RawPal, load_material, load_pal

# --- clip naming / categories -------------------------------------------------------------

CATEGORY_RULES: list[tuple[str, str]] = [
    ("idle", r"^(Idle|Rest|Sleep|Petting|Eat|Spa|Encount|Happy|Joy|Sad|Dizzy|Stretch|Yawn)"),
    ("move", r"^(Walk|Run|Jump|Swim|Fly|Glide|Fall|Landing|Sprint|Dash|Korogaru|Ride|Climb|Turn)"),
    ("battle", r"(Attack|Skill|Damage|Hit|Shield|Dead|Death|Stun|Down|GetUp|Blow|Guard|Charge|Beam|Breath|Bite|Tackle|Roar|Shot|Fire|Gunner|Shock)"),
    ("work", r"(Work|Architecture|Carry|Carrying|Transport|Mining|Deforest|Craft|Cool|Watering|Seeding|Kindling|Generate|Farm|Medicine|Handcraft|Collection|Oil)"),
]
LOOP_RE = re.compile(r"(Loop|^Idle|^Walk|^Run|^Swim|^Fly|^Glide|^Sprint|Carrying$|^Transport$|^CommonWork$|^Spa$|^Rest\d*$)", re.I)


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
    out = {"textures": {}, "scalars": {}, "vectors": {}, "blend": None, "two_sided": None, "clip": None, "base_material": chain[-1] if chain else None}
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
    return out


def _first(d: dict, names) -> str | None:
    for n in names:
        if n in d:
            return d[n]
    return None


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    c = c.astype(np.float32) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


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
    m.base_color = texcache.plain(base, srgb=True)
    if base:
        meta = pal.texture_meta(base)
        w = (WRAP.get(meta.get("addressX", "TA_Wrap"), "repeat"), WRAP.get(meta.get("addressY", "TA_Wrap"), "repeat"))
        m.wrap = {k: w for k in ("base_color", "normal", "metallic_roughness", "occlusion", "emissive")}
    m.normal = texcache.normal(_first(tx, NORMAL_PARAMS))
    sc = prm["scalars"]
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
            m.emissive_factor = (1.0, 1.0, 1.0)
    m.alpha_mode = BLEND.get(prm["blend"] or "", "OPAQUE")
    if m.alpha_mode == "MASK":
        m.alpha_cutoff = prm["clip"] if prm["clip"] is not None else 0.3333
    m.double_sided = bool(prm["two_sided"])
    if m.base_color is None:
        # effect materials (flames, jewels) have no albedo map: tint from their main colour, half transparent
        col = _first(prm["vectors"], ("BaseColor", "Color2", "Color1", "Color01", "Color02", "Emissive Color", "FresnelColor"))
        if col:
            rgb = np.array([col.get("R", 1.0), col.get("G", 1.0), col.get("B", 1.0)], dtype=float)
            rgb = rgb / max(1.0, float(rgb.max()))
            m.base_color_factor = (float(rgb[0]), float(rgb[1]), float(rgb[2]), 0.5)
        m.alpha_mode = "BLEND"
        m.extras["effect"] = True
    m.extras["unreal"] = {"material": chain[0] if chain else None, "base": prm["base_material"],
                          "scalars": {k: v for k, v in prm["scalars"].items() if isinstance(v, (int, float))}}
    return m


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
        prim = Primitive(material=mat.name, indices=remap.astype(np.uint32), positions=P[verts], normals=N[verts],
                         uvs=[u[verts] for u in uvs[:2]], joints=J[verts], weights=W[verts])
        meshes.append(Mesh(name=name, primitives=[prim], extras={"effect": True} if mat.extras.get("effect") else {}))
    return meshes


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


def build_clips(pal: RawPal) -> list[Clip]:
    bones = {b["name"]: b for b in pal.info["bones"]}
    skel = {b["name"]: b for b in (pal.info.get("skeletonBones") or [])}
    clips: list[Clip] = []
    seen: dict[str, int] = {}
    for i, a in enumerate(pal.info["anims"]):
        if "error" in a or not a.get("tracks"):
            continue
        if a.get("additive"):
            continue  # additive layers (aim offsets) are deltas, not standalone poses
        frames = int(a["frames"])
        length = float(a.get("length") or 0.0)
        fps = (frames - 1) / length if frames > 1 and length > 0 else 30.0
        rate = float(a.get("rateScale") or 1.0) or 1.0
        times = (np.arange(frames, dtype=np.float32) / fps / abs(rate)).astype(np.float32)
        data = pal.anim_frames(a)
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
        name = clip_name(a["name"], pal.code)
        if name in seen:
            seen[name] += 1
            name = f"{name} [{seen[name]}]"
        else:
            seen[name] = 0
        clips.append(Clip(name=name, tracks=tracks, frame_rate=round(fps * abs(rate), 3), frame_count=frames,
                          loop=bool(LOOP_RE.search(name)), index=i, category=categorize(name), source=a["name"]))
    return clips


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


def atlas_layout(uv: np.ndarray, alpha: np.ndarray) -> dict | None:
    """Eye/mouth atlases: the mesh UVs sit inside one cell of a power-of-two grid and other cells hold alternate
    expressions drawn at the same spot. Every grid whose cell holds the UV box is scored by its "aligned" cells (content
    mostly inside the UV box moved to that cell); the best grid wins (ties: larger cells).
    Returns {cell: (w, h), base: (cx, cy), cells: [(cx, cy), ...]} or None."""
    lo, hi = uv.min(0), uv.max(0)
    if lo.min() < 0 or hi.max() > 1:
        return None
    h, w = alpha.shape
    mask = alpha > 16
    margin = 0.02
    best = None
    for cw in CELL_SIZES:
        for ch in CELL_SIZES:
            # UV padding may overshoot a cell edge by a few texels: test the fit on a slightly shrunk box
            bx, by = int((lo[0] + FIT_TOL) // cw), int((lo[1] + FIT_TOL) // ch)
            if int(min(hi[0] - FIT_TOL, 0.9999) // cw) != bx or int(min(hi[1] - FIT_TOL, 0.9999) // ch) != by:
                continue  # the UVs straddle a cell border at this size
            good = []
            for cy in range(round(1 / ch)):
                for cx in range(round(1 / cw)):
                    x0, y0 = int(cx * cw * w), int(cy * ch * h)
                    cell = mask[y0 : y0 + int(ch * h), x0 : x0 + int(cw * w)]
                    n = int(cell.sum())
                    if n < 0.002 * cell.size:
                        continue
                    ox, oy = (cx - bx) * cw, (cy - by) * ch
                    u0, u1 = int((lo[0] + ox - margin) * w) - x0, int((hi[0] + ox + margin) * w) - x0
                    v0, v1 = int((lo[1] + oy - margin) * h) - y0, int((hi[1] + oy + margin) * h) - y0
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
        if not mat.base_color or mat.extras.get("effect"):
            continue
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


def build_weapon_material(pal: RawPal, slot: dict, texcache: TextureCache) -> Material:
    """Weapon materials come in three shapes: a PalLit instance (launcher, hammer: same model as the Pals), an instance of a
    plain master material with Albedo / Normal / RMA parameters (shotgun), or a bare UMaterial whose maps are found by name
    among the mesh folder's textures (rifles: *_BaseColor|_B, *_Normal|_N, *_AO_Metallic_Roughness (amr) | *_orm)."""
    chain = slot.get("chain") or []
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


def import_pal(raw_root: str, code: str, model_id: str | None = None, display_name: str | None = None, with_anim: bool = True) -> Model:
    pal = load_pal(raw_root, code)
    model = Model(id=model_id or code, game="palworld", display_name=display_name or code, kind="pal", skeleton=None,
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
    if with_anim:
        model.clips = build_clips(pal)
        idle = [c.name for c in model.clips if c.name.lower() == "idle"] or [c.name for c in model.clips if c.category == "idle"]
        model.default_clip = idle[0] if idle else (model.clips[0].name if model.clips else None)
    model.source_paths = [pal.info["mesh"]]
    model.extras["skeleton"] = pal.info.get("skeleton")
    return model
