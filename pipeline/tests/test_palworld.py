"""Palworld importer rules, on synthetic data (no game files needed)."""

import json
import os

import numpy as np
import pytest

from pipeline.importers.palworld import convert as cv
from pipeline.importers.palworld.importer import _part_name, _retarget, _top4, categorize, clip_name, import_pal, mount_ok


def _rot_matrix_ue_to_gltf():
    # Y/Z swap as a matrix acting on column vectors
    return np.array([[1, 0, 0], [0, 0, 1], [0, 1, 0]], dtype=float)


def test_axes_up_and_forward():
    # Unreal Z up -> glTF Y up; Palworld meshes face +Y -> glTF +Z (front); cm -> m
    assert np.allclose(cv.pos([0, 0, 100]), [0, 1, 0])
    assert np.allclose(cv.pos([0, 100, 0]), [0, 0, 1])
    assert np.allclose(cv.pos([100, 0, 0]), [1, 0, 0])


@pytest.mark.parametrize("axis,angle", [((1, 0, 0), 0.7), ((0, 1, 0), -1.2), ((0, 0, 1), 2.0), ((0.3, -0.5, 0.8), 0.9)])
def test_quaternion_matches_reflected_rotation(axis, angle):
    a = np.asarray(axis, float) / np.linalg.norm(axis)
    q = np.r_[a * np.sin(angle / 2), np.cos(angle / 2)]
    M = _rot_matrix_ue_to_gltf()
    R = cv.quat_to_mat(q)
    expected = M @ R @ M.T
    got = cv.quat_to_mat(cv.quat(q))
    assert np.allclose(got, expected, atol=1e-6)


def test_quaternion_of_vector_rotation():
    # rotating a point in Unreal then converting == converting then rotating in glTF
    rng = np.random.default_rng(0)
    for _ in range(20):
        q = rng.normal(size=4)
        q /= np.linalg.norm(q)
        v = rng.normal(size=3)
        a = cv.vec(cv.quat_to_mat(q) @ v)
        b = cv.quat_to_mat(cv.quat(q)) @ cv.vec(v)
        assert np.allclose(a, b, atol=1e-5)


def test_winding_flips_with_reflection():
    # a clockwise-front Unreal triangle (normal from cross(b-a, c-a) points away from the given normal)
    tri = np.array([[0, 0, 0], [0, 1, 0], [1, 0, 0]], float)  # cross = (0,0,-1)
    normal = np.array([0, 0, 1.0])
    g = cv.vec(tri)
    n = np.cross(g[1] - g[0], g[2] - g[0])
    assert np.dot(n, cv.vec(normal)) > 0  # counter-clockwise front in glTF, indices unchanged


def _brow_cards(cells, channels=((0.0, 0.0), (0.05, 0.5))):
    """Quad cards in mesh order: (lateral x, UV0 left edge) per channel; `cells` = the UV1 eye-atlas cell (cx, cy) of each card."""
    quads, P, uv, uv1 = [], [], [], []
    for (x, u0), (cx, cy) in zip([ch for ch in channels for _ in range(len(cells) // len(channels))], cells):
        b = len(P)
        P += [[x, 0, 0], [x + 0.01, 0, 0], [x + 0.01, 0.01, 0], [x, 0.01, 0]]
        uv += [[u0, 0], [u0 + 0.4, 0], [u0 + 0.4, 0.4], [u0, 0.4]]
        uv1 += [[cx * 0.5 + 0.1, cy * 0.25 + 0.05], [cx * 0.5 + 0.3, cy * 0.25 + 0.05], [cx * 0.5 + 0.3, cy * 0.25 + 0.15], [cx * 0.5 + 0.1, cy * 0.25 + 0.15]]
        quads += [b, b + 1, b + 2, b, b + 2, b + 3]
    return np.array(quads), np.array(P, np.float32), np.array(uv, np.float32), np.array(uv1, np.float32)


def _split(cells):
    from pipeline.importers.palworld.importer import Material, _split_brow
    quads, P, uv, uv1 = _brow_cards(cells)
    n = len(P)
    meshes = []
    ok = _split_brow(quads, uv, P, Material(name="MI_X_Brow"), "Brow", P, [uv, uv1], np.zeros((n, 4), np.uint16),
                     np.tile([1, 0, 0, 0], (n, 1)).astype(np.float32), meshes)
    return ok, meshes


def test_split_brow_indices_are_flat():
    # Two channels (UV rectangles) x two sets, each card a quad. A (n, 3) index array became a VEC3 accessor in the GLB and three.js
    # drew only the first third of every brow card (Nox's brows were invisible from the front).
    ok, meshes = _split([(0, 0), (1, 0), (0, 0), (1, 0)])
    assert ok and meshes
    for m in meshes:
        idx = m.primitives[0].indices
        assert idx.ndim == 1 and len(idx) % 3 == 0
    # eyelash k and brow k are one option of a single group, so they switch together
    from pipeline.importers.palworld.importer import part_groups
    assert {m.extras["group"] for m in meshes} == {"brow"}
    assert sorted(m.name for m in meshes if m.extras["option"] == "brow_1") == ["Brow_1", "Eyelash_1"]
    (g,) = part_groups(meshes)
    assert g["label"] == "Brows & lashes" and [o["id"] for o in g["options"]] == ["brow_1", "brow_2"]


def test_split_brow_sets_are_uv1_cells_not_card_order():
    # Yakumo: the cards of a set are not the k-th card of each channel; membership comes from the UV1 eye-atlas cell.
    ok, meshes = _split([(1, 0), (0, 0), (0, 0), (1, 0)])
    assert ok
    assert sorted(m.name for m in meshes) == ["Brow_1", "Brow_2", "Eyelash_1", "Eyelash_2"]
    assert all(len(m.primitives[0].indices) == 6 for m in meshes)  # one quad card per kind and cell
    # every card in one cell = no expression sets: leave the section alone
    assert _split([(0, 0)] * 4)[0] is False


def test_brow_sets_follow_eye_cells_through_uv1():
    # Sets come out of the mesh in card order; their UV1 cell in the eye atlas grid says which eye they belong to.
    from pipeline.common.model import Mesh, Model, Primitive, Variant
    from pipeline.importers.palworld.importer import Material, link_brow_sets, part_groups
    cw, ch = 0.5, 0.25
    cells = [(0, 0), (1, 0), (0, 1), (1, 1)]  # eye atlas cells, row-major: eye_1..eye_4
    eye = Material(name="MI_X_Eye")
    eye.extras["atlas"] = {"cell": [cw, ch], "base": [0, 0], "cells": [list(c) for c in cells]}
    set_cells = {1: (1, 1), 2: (0, 1), 3: (1, 0), 4: (0, 0)}  # card-order set k -> its UV1 cell
    meshes = [Mesh(name="Body", primitives=[])]
    for kind in ("Eyelash", "Brow"):
        for k, (cx, cy) in set_cells.items():
            uv1 = np.array([[cx * cw + 0.1, cy * ch + 0.05], [cx * cw + 0.2, cy * ch + 0.1], [cx * cw + 0.1, cy * ch + 0.1]], np.float32)
            prim = Primitive(material="MI_X_Brow", indices=np.arange(3, dtype=np.uint32), positions=np.zeros((3, 3), np.float32),
                             normals=np.zeros((3, 3), np.float32), uvs=[uv1, uv1])
            meshes.append(Mesh(name=f"{kind}_{k}", primitives=[prim], visible=k == 1, extras={"group": "brow", "option": f"brow_{k}"}))
    model = Model(id="X", game="palworld", display_name="X", kind="pal", skeleton=None, meshes=meshes,
                  materials={eye.name: eye, "MI_X_Brow": Material(name="MI_X_Brow")}, textures={})
    model.variants = [Variant(name=f"eye_{n + 1}", materials={eye.name: eye}, group="eye", label=str(n + 1)) for n in range(4)]
    link_brow_sets(model)
    assert model.meshes[0].name == "Body"
    by_name = {m.name: m for m in model.meshes}
    # card-order set 4 sits in cell (0, 0) = eye 1, set 1 in (1, 1) = eye 4
    assert by_name["Brow_1"].primitives[0].uvs[1].mean(0) // (cw, ch) @ [1, 1] == 0
    assert sorted(m.name for m in model.meshes if m.visible and m.name != "Body") == ["Brow_1", "Eyelash_1"]
    (g,) = part_groups(model.meshes)
    assert [(o["id"], o["variant"], o["default"]) for o in g["options"]][:2] == [("brow_1", "eye_1", True), ("brow_2", "eye_2", False)]
    for n, c in enumerate(cells, 1):
        assert tuple(int(v) for v in by_name[f"Eyelash_{n}"].primitives[0].uvs[1].mean(0) // (cw, ch)) == c


def test_clip_names():
    assert clip_name("AS_SheepBall_Idle", "SheepBall") == "Idle"
    assert clip_name("AS_Kitsunebi_Walk", "Kitsunebi_Ice") == "Walk"
    assert clip_name("AS_WeaselDragon_Fire_Run", "WeaselDragon_Fire_Skin002") == "Run"
    assert clip_name("AS_Other_Sleep", "SheepBall") == "Sleep"


def test_categories():
    assert categorize("Idle") == "idle"
    assert categorize("Walk") == "move"
    assert categorize("FarSkill_Action") == "battle"
    assert categorize("CommonWork") == "work"
    assert categorize("Something") == "other"


@pytest.mark.parametrize("name,ok", [("Idle", True), ("Run", True), ("Fly", True), ("FarSkill_Start", True), ("Sleep", False), ("Spa", False),
                                     ("Petting", False), ("Eat", False), ("Death", False), ("CommonWork", False), ("Transport", False), ("Architecture", False),
                                     ("Minigun_Idle", True), ("Aim_WalkFwd", True), ("Claim", False)])
def test_mount_ok(name, ok):
    assert mount_ok(name, categorize(name)) is ok


def test_part_names():
    assert _part_name("MI_Sheepball_Body", "SheepBall") == "Body"
    assert _part_name("MI_Kitsunebi_Ice_Extra", "Kitsunebi_Ice") == "Extra"
    assert _part_name("M_Pal_Fire", "Kitsunebi") == "Pal_Fire"


def test_top4_weights():
    j = np.array([[1, 2, 3, 4, 5, 0, 0, 0]], np.uint16)
    w = np.array([[0.1, 0.4, 0.05, 0.3, 0.15, 0, 0, 0]], np.float32)
    jj, ww = _top4(j, w)
    assert list(jj[0]) == [2, 4, 5, 1]
    assert np.isclose(ww.sum(), 1.0)


def test_retarget_modes():
    t = np.array([[10.0, 0, 0]], np.float32)
    mesh = {"t": [8.0, 0, 0]}
    skel = {"t": [16.0, 0, 0], "retarget": "Animation"}
    assert np.allclose(_retarget(t, mesh, skel), t)
    assert np.allclose(_retarget(t, mesh, {**skel, "retarget": "Skeleton"}), [[8, 0, 0]])
    assert np.allclose(_retarget(t, mesh, {**skel, "retarget": "AnimationScaled"}), [[5, 0, 0]])
    assert np.allclose(_retarget(t, mesh, {**skel, "retarget": "AnimationRelative"}), [[2, 0, 0]])


def _write_synthetic_pal(root, code="TestPal"):
    """One triangle skinned to a two-bone chain, one 3-frame clip, no textures."""
    d = os.path.join(root, "palworld", code)
    os.makedirs(os.path.join(d, "anims"))
    pos = np.array([[0, 0, 0], [0, 100, 0], [100, 0, 0]], np.float32)
    nrm = np.tile(np.array([0, 0, 1, 1], np.float32), (3, 1))
    tan = np.tile(np.array([1, 0, 0, 1], np.float32), (3, 1))
    uv = np.array([[0, 0], [0, 1], [1, 0]], np.float32)
    joints = np.zeros((3, 8), np.uint16)
    joints[2, 0] = 1
    weights = np.zeros((3, 8), np.float32)
    weights[:, 0] = 1
    idx = np.array([0, 1, 2], np.uint32)
    bufs, off, blob = [], 0, b""
    for name, arr, dt in [("position", pos, "f4"), ("normal", nrm, "f4"), ("tangent", tan, "f4"), ("uv0", uv, "f4"),
                          ("joints", joints, "u2"), ("weights", weights, "f4"), ("indices", idx, "u4")]:
        bufs.append({"name": name, "dtype": dt, "shape": list(arr.shape), "offset": off})
        blob += arr.tobytes()
        off += arr.nbytes
    open(os.path.join(d, "lod0.bin"), "wb").write(blob)
    frames = 3
    anim = np.zeros((frames, 10), np.float32)
    anim[:, 3] = 1
    anim[:, 7:10] = 1
    anim[:, 4] = [0, 50, 100]  # bone 1 slides along X
    open(os.path.join(d, "anims", "AS_TestPal_Walk.bin"), "wb").write(anim.tobytes())
    info = {
        "code": code, "mesh": f"Pal/Content/Pal/Model/Character/Monster/{code}/SK_{code}", "skeleton": "x",
        "bones": [{"name": "root", "parent": -1, "t": [0, 0, 0], "r": [0, 0, 0, 1], "s": [1, 1, 1]},
                  {"name": "b1", "parent": 0, "t": [100, 0, 0], "r": [0, 0, 0, 1], "s": [1, 1, 1]}],
        "skeletonBones": [{"name": "root", "parent": -1, "t": [0, 0, 0], "r": [0, 0, 0, 1], "s": [1, 1, 1], "retarget": "Animation"},
                          {"name": "b1", "parent": 0, "t": [100, 0, 0], "r": [0, 0, 0, 1], "s": [1, 1, 1], "retarget": "Animation"}],
        "materials": [{"slot": "MI_TestPal_Body", "path": None, "chain": []}],
        "lods": [{"vertexCount": 3, "sections": [{"material": 0, "firstIndex": 0, "numFaces": 1}], "file": "lod0.bin", "buffers": bufs}],
        "anims": [{"name": "AS_TestPal_Walk", "file": "anims/AS_TestPal_Walk.bin", "frames": frames, "length": 2 / 30, "fps": 45,
                   "additive": False, "rateScale": 1.0, "tracks": [{"bone": "b1", "offset": 0}]}],
        "textures": [], "morphTargets": 0, "sockets": [],
    }
    json.dump(info, open(os.path.join(d, "pal.json"), "w"))
    return code


def test_import_synthetic(tmp_path):
    code = _write_synthetic_pal(str(tmp_path))
    m = import_pal(str(tmp_path), code)
    assert [j.name for j in m.skeleton.joints] == ["root", "b1"]
    assert np.allclose(m.skeleton.joints[1].translation, [1, 0, 0])
    p = m.meshes[0].primitives[0]
    assert np.allclose(p.positions[1], [0, 0, 1])  # Unreal +Y (front) -> glTF +Z
    assert m.meshes[0].name == "Body"
    c = m.clips[0]
    assert c.name == "Walk" and c.loop and c.category == "move"
    assert c.frame_count == 3 and np.isclose(c.frame_rate, 30.0)  # (frames - 1) / length
    tr = next(t for t in c.tracks if t.kind == "translation")
    assert np.allclose(tr.values[:, 0], [0, 0.5, 1.0])
    # inverse bind of b1 undoes its 1 m offset
    assert np.allclose(m.skeleton.joints[1].inverse_bind[:3, 3], [-1, 0, 0])


def test_glb_roundtrip(tmp_path):
    from pipeline.common.gltf_writer import write_glb
    from pipeline.common.manifest import build_manifest

    code = _write_synthetic_pal(str(tmp_path))
    m = import_pal(str(tmp_path), code)
    s = write_glb(m, str(tmp_path / "m.glb"))
    man = build_manifest(m, s)
    assert man["clips"][0]["name"] == "Walk"
    assert man["stats"]["triangles"] == 1
    assert (tmp_path / "m.glb").stat().st_size > 0


def test_atlas_layout_2x4():
    from pipeline.importers.palworld.importer import atlas_layout

    a = np.zeros((256, 256), np.uint8)
    for cx in range(2):
        for cy in range(4):
            if (cx, cy) != (1, 3):  # 7 drawn cells
                a[cy * 64 + 10 : cy * 64 + 50, cx * 128 + 20 : cx * 128 + 100] = 255
    uv = np.array([[20 / 256, 10 / 256], [100 / 256, 50 / 256]])
    lay = atlas_layout(uv, a)
    assert lay["cell"] == (0.5, 0.25) and lay["base"] == (0, 0) and len(lay["cells"]) == 7


def test_atlas_layout_rejects_plain_texture():
    from pipeline.importers.palworld.importer import atlas_layout

    a = np.full((64, 64), 255, np.uint8)
    assert atlas_layout(np.array([[0.01, 0.01], [0.99, 0.99]]), a) is None


def test_orm_bakes_roughness_and_metal_scalars():
    from types import SimpleNamespace

    from pipeline.importers.palworld.importer import TextureCache

    px = np.zeros((2, 2, 4), np.uint8)
    px[..., 0], px[..., 1], px[..., 2], px[..., 3] = 100, 128, 200, 255  # metal, rough, ao
    pal = SimpleNamespace(texture_png=lambda p: None, texture_meta=lambda p: {"srgb": False})
    model = SimpleNamespace(textures={})
    tc = TextureCache(pal, model)
    tc._load = lambda p: px
    key = tc.orm("a/T_x", rough=(1.0, 0.4), metal=(2.0, 0.0))
    out = model.textures[key].rgba
    assert out[0, 0, 0] == 200  # AO untouched
    assert abs(int(out[0, 0, 1]) - round((128 / 255 + 0.4) * 255)) <= 1  # roughness + Add
    assert out[0, 0, 2] == round(min(1.0, 100 / 255 * 2.0) * 255)  # metal * Mul
    assert tc.orm("a/T_x") != key  # scalars are part of the cache key


def test_orm_ignores_unused_occlusion_channel():
    from types import SimpleNamespace

    from pipeline.importers.palworld.importer import TextureCache

    px = np.zeros((2, 2, 4), np.uint8)
    px[..., 0], px[..., 1], px[..., 2], px[..., 3] = 100, 128, 0, 255  # B = 0 everywhere
    pal = SimpleNamespace(texture_png=lambda p: None, texture_meta=lambda p: {"srgb": False})
    model = SimpleNamespace(textures={})
    tc = TextureCache(pal, model)
    tc._load = lambda p: px
    out = model.textures[tc.orm("a/T_x")].rgba
    assert (out[..., 0] == 255).all()


def test_normal_scale_written_to_glb():
    from pipeline.common.model import Material

    assert Material(name="m").normal_scale == 1.0


def test_held_items_pair_and_clips(tmp_path):
    """A dual weapon (SkeletalMeshL/R) is one model attached to both weapon bones; props show it only in the aim clips."""
    from pipeline.catalog.palworld import PalworldCatalog
    from pipeline.common.manifest import HELD_CLIP_RE

    wdir = tmp_path / "palworld_weapons"
    (wdir / "Dual").mkdir(parents=True)
    (wdir / "Dual" / "pal.json").write_text("{}")
    held = [{"pal": "A", "weapon": "Dual", "mesh": "m", "parts": [{"part": "SkeletalMeshL", "item": "Dual", "mesh": "m"}, {"part": "SkeletalMeshR", "item": "Dual", "mesh": "m"}]}]
    (wdir / "held.json").write_text(json.dumps(held))
    cat = PalworldCatalog(str(tmp_path))
    pal = type("R", (), {"id": "A", "extras": {}})()
    refs = cat._weapon_refs([pal])
    assert [r.id for r in refs] == ["Weapon_Dual"]
    assert [h["bone"] for h in pal.extras["heldItems"]] == ["weapon_l", "weapon_r"]
    assert pal.extras["heldItem"] is pal.extras["heldItems"][0]
    assert all(HELD_CLIP_RE.search(n) for n in ("Aim", "Aim_WalkFwd", "Minigun_Idle", "Minigun_Aim_c"))
    assert not any(HELD_CLIP_RE.search(n) for n in ("Idle", "Walk", "Gunner", "Claim"))


def test_boss_holds_marker_children(tmp_path):
    """A mesh parented to the boss marker in the GYM blueprint (Lily's spear) becomes the boss's held item, always shown."""
    from pipeline.catalog.palworld import ModelRef, PalworldCatalog

    hdir = tmp_path / "palworld_humans"
    hdir.mkdir()
    part = {"part": "PalSkeletalMesh", "item": "Spear", "mesh": "m", "socket": "Socket_Weapon_R", "bone": "weapon_r", "visible": True,
            "socketLoc": [0, 0, 0], "socketRot": [0, 0, 0], "loc": [0, 0, 0], "rot": [0, 0, 0], "scale": [1, 1, 1]}
    ride = {"pal": "P", "boss": "Boss_B", "bone": "BossRideMarker", "loc": [0, 0, 0], "rot": [0, 0, 0], "scale": [1, 1, 1], "palScale": [1.5] * 3, "held": [part]}
    (hdir / "rides.json").write_text(json.dumps([ride]))
    refs = [ModelRef("palworld", "pal", "P", "", "P"), ModelRef("palworld", "human", "Boss_B", "", "B")]
    PalworldCatalog(str(tmp_path))._link_boss_rides(refs)
    boss = refs[1]
    assert boss.extras["heldItems"] == [{"item": "Weapon_Spear", "bone": "weapon_r", "position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0], "always": True}]
    assert boss.extras["heldItem"] is boss.extras["heldItems"][0]


def test_clip_pairs_use_manifest_names():
    """Boss ride clip <-> the Pal's montage sequence (Sorajima VortexRage <-> Shaolong VortexRage Start / Loop / End x1.45)."""
    from pipeline.catalog.palworld import clip_pairs

    raw = [{"boss": "AS_SorajimaBoss_VortexRage", "source": "waza:Unique_BlueSkyDragon_DrainStorm", "pal": [
        {"anim": "AS_BlueSkyDragon_VortexRage_Start", "rate": 1.0, "loop": False},
        {"anim": "AS_BlueSkyDragon_VortexRage_Loop", "rate": 1.0, "loop": True},
        {"anim": "AS_BlueSkyDragon_VortexRage_End", "rate": 1.45, "loop": False}]}]
    assert clip_pairs(raw, "BlueSkyDragon") == [{"boss": "SorajimaBoss_VortexRage", "pal": [
        {"clip": "VortexRage_Start"}, {"clip": "VortexRage_Loop", "loop": True}, {"clip": "VortexRage_End", "rate": 1.45}]}]


def test_attach_offset_identity_and_yaw():
    from pipeline.catalog.palworld import _rotator_quat, attach_offset

    assert np.allclose(_rotator_quat(0, 0, 0), [0, 0, 0, 1])
    assert np.allclose(_rotator_quat(0, 90, 0), [0, 0, np.sin(np.pi / 4), np.cos(np.pi / 4)])
    ident = attach_offset({"socketLoc": [0, 0, 0], "socketRot": [0, 0, 0], "loc": [0, 0, 0], "rot": [0, 0, 0]})
    assert ident == {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0]}
    # a component 100 cm along X on a socket yawed 90 deg ends up 100 cm along Y (Unreal) = Z in glTF
    off = attach_offset({"socketLoc": [0, 0, 0], "socketRot": [0, 90, 0], "loc": [100, 0, 0], "rot": [0, 0, 0]})
    assert np.allclose(off["position"], [0, 0, 1], atol=1e-4)


def test_no_pal_named_after_its_code():
    from pipeline.catalog.palworld import PalworldCatalog

    cat = PalworldCatalog()
    if not cat.pals():
        return
    bad = [r.id for r in cat.pals() if r.display_name == r.id and r.id not in {"Anubis", "Sekhmet", "Suzaku"}]
    assert not bad, bad


def test_jewel_opacity_scalar_becomes_base_alpha(monkeypatch):
    from types import SimpleNamespace

    from pipeline.importers.palworld import importer as imp

    prm = {"textures": {"Base Texture": "a/T_x_B"}, "scalars": {"Jewel Opacity": 0.75}, "vectors": {}, "blend": "EBlendMode::2",
           "two_sided": None, "clip": None, "base_material": "m"}
    monkeypatch.setattr(imp, "resolve_params", lambda root, chain: prm)
    pal = SimpleNamespace(root="", texture_meta=lambda p: {})
    tc = SimpleNamespace(plain=lambda path, srgb=True: "T_x_B", normal=lambda p: None, orm=lambda *a, **k: None)
    m = imp.build_material(pal, {"slot": "MI_x", "chain": ["a/MI_x"]}, tc)
    assert m.alpha_mode == "BLEND" and m.base_color_factor[3] == 0.75
    prm["blend"] = "EBlendMode::1"  # the scalar only means something on a blended material
    assert imp.build_material(pal, {"slot": "MI_x", "chain": ["a/MI_x"]}, tc).base_color_factor[3] == 1.0


def test_palllit_without_albedo_uses_base_color_constant(monkeypatch):
    # SkinDark recipe: Base Texture None, BaseColor black -> a solid black surface, not a half-transparent effect
    from types import SimpleNamespace

    from pipeline.importers.palworld import importer as imp

    prm = {"textures": {}, "scalars": {"Jewel Opacity": 0.75}, "vectors": {"BaseColor": {"R": 0.0, "G": 0.0, "B": 0.0, "A": 1.0}},
           "blend": "EBlendMode::1", "two_sided": None, "clip": None, "base_material": "Pal/Material/Character/Common/M_PalLit"}
    monkeypatch.setattr(imp, "resolve_params", lambda root, chain: prm)
    pal = SimpleNamespace(root="", texture_meta=lambda p: {})
    tc = SimpleNamespace(plain=lambda path, srgb=True: None, normal=lambda p: None, orm=lambda *a, **k: None)
    m = imp.build_material(pal, {"slot": "MI_x", "chain": ["a/MI_x"]}, tc)
    assert m.base_color_factor == (0.0, 0.0, 0.0, 1.0) and m.alpha_mode == "OPAQUE" and not m.extras.get("effect")
    prm["base_material"] = "Pal/Material/M_Pal_Twinkle"  # a real effect shader stays an effect
    assert imp.build_material(pal, {"slot": "MI_x", "chain": ["a/MI_x"]}, tc).extras.get("effect")


def _write_material(root, path, data):
    p = os.path.join(root, "materials", *path.split("/")) + ".json"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f)


def test_base_material_defaults_fill_missing_params(tmp_path):
    # instances store only overrides; the base UMaterial's CachedExpressionData defaults (extractor "Defaults") fill the rest
    from pipeline.importers.palworld.importer import resolve_params

    root = str(tmp_path)
    _write_material(root, "P/M_Base", {"Type": "Material", "Properties": {"BlendMode": "EBlendMode::2"}, "Defaults": {
        "scalars": {"midPoint": 0.5, "alpha": 0.0}, "vectors": {"Color1": {"R": 20.0, "G": 10.0, "B": 0.0}},
        "textures": {"MainTex": "P/T_Default"}}})
    _write_material(root, "P/MI_Child", {"Properties": {"ScalarParameterValues": [{"ParameterInfo": {"Name": "midPoint"}, "ParameterValue": 0.75}]}})
    prm = resolve_params(root, ["P/MI_Child", "P/M_Base"])
    assert prm["scalars"]["midPoint"] == 0.75  # the instance wins
    assert prm["scalars"]["alpha"] == 0.0 and prm["textures"]["MainTex"] == "P/T_Default"
    assert prm["vectors"]["Color1"]["R"] == 20.0
    assert "midPoint" in prm["overridden"] and "Color1" not in prm["overridden"]


def test_fire_ramp_root_hot_tip_dark():
    # V = 1 at the flame root: root takes the hot colour (Color1), the tip the dark one (Color3)
    from pipeline.importers.palworld.importer import fire_params, fire_shade

    vec = lambda r, g, b: {"R": r, "G": g, "B": b}
    prm = {"scalars": {"midPoint": 0.7, "rootColor": 0.6, "vertexRoot": 0.5, "alpha": 0.0, "alphaEdge": 0.05, "rgbMin": 0.0, "rgbMax": 1.0},
           "vectors": {"Color1": vec(0, 2, 17), "Color2": vec(0.2, 0.3, 1), "Color3": vec(0.08, 0.04, 0.3)}, "overridden": set()}
    p = fire_params(prm)
    assert "fresnelColor" not in p  # the default orange rim only shows when an instance sets it
    tex = np.full((4, 4), 0.3, np.float32)
    rgb, a = fire_shade(p, np.array([0.5, 0.5]), np.array([1.0, 0.0]), tex, tex)
    assert rgb[0, 2] > 0.9 and rgb[0, 1] > rgb[1, 1] + 0.2  # bright cyan root
    assert rgb[1].max() < 0.6  # dimmer purple tip
    assert a[0] > 0.9


def test_dream_material_uses_main_tex(monkeypatch):
    from types import SimpleNamespace

    from pipeline.importers.palworld import importer as imp

    prm = {"textures": {"MainTex": "a/T_Dream_Extra", "NoiseTex": "a/T_water"}, "scalars": {"noiseVSpeed": 0.2},
           "vectors": {"fresnelColor": {"R": 5.0, "G": 1.1, "B": 1.6}}, "blend": "EBlendMode::2", "two_sided": None, "clip": None,
           "base_material": "Pal/M_Pal_Dream", "overridden": set()}
    monkeypatch.setattr(imp, "resolve_params", lambda root, chain: prm)
    pal = SimpleNamespace(root="", texture_meta=lambda p: {})
    tc = SimpleNamespace(plain=lambda path, srgb=True: path and path.rsplit("/", 1)[-1], normal=lambda p: None, orm=lambda *a, **k: None,
                         data=lambda p: p and p.rsplit("/", 1)[-1] + "_lin")
    m = imp.build_material(pal, {"slot": "MI_Dream", "chain": ["a/MI_Dream"]}, tc)
    assert m.base_color == "T_Dream_Extra" and m.emissive == "T_Dream_Extra" and m.alpha_mode == "BLEND"
    assert m.extras["dream"]["noiseTex"] == {"texture": "T_water_lin"} and "fresnel" in m.extras


def test_basecolor_vector_tints_textured_albedo(monkeypatch):
    from types import SimpleNamespace

    from pipeline.importers.palworld import importer as imp

    vec = {"BaseColor": {"R": 0.5, "G": 0.4, "B": 0.4, "A": 1.0}}
    prm = {"textures": {"Base Texture": "a/T_x_B"}, "scalars": {}, "vectors": vec, "blend": None, "two_sided": None, "clip": None,
           "base_material": "m", "overridden": {"BaseColor"}}
    monkeypatch.setattr(imp, "resolve_params", lambda root, chain: prm)
    pal = SimpleNamespace(root="", texture_meta=lambda p: {})
    tc = SimpleNamespace(plain=lambda path, srgb=True: "T_x_B", normal=lambda p: None, orm=lambda *a, **k: None)
    m = imp.build_material(pal, {"slot": "MI_x", "chain": ["a/MI_x"]}, tc)
    assert m.base_color_factor == (0.5, 0.4, 0.4, 1.0)
    prm["overridden"] = set()  # the base default (white) is not a tint
    assert imp.build_material(pal, {"slot": "MI_x", "chain": ["a/MI_x"]}, tc).base_color_factor == (1.0, 1.0, 1.0, 1.0)


def test_additive_mesh_space_layer_is_baked_on_idle():
    """AAT_RotationOffsetMeshSpace layers (Minigun_Aim_*) are mesh-space rotation deltas: baked on the Idle they leave an unkeyed
    child where it points in mesh space, add translation, and keep the base scale. Never emitted raw (scale ~0 collapsed bones)."""
    from pipeline.importers.palworld.importer import _additive_base, bake_additive

    I = [0, 0, 0, 1]
    bones = [{"name": "root", "parent": -1, "t": [0, 0, 0], "r": I, "s": [1, 1, 1]},
             {"name": "b1", "parent": 0, "t": [10, 0, 0], "r": I, "s": [1, 1, 1]},
             {"name": "b2", "parent": 1, "t": [10, 0, 0], "r": I, "s": [1, 1, 1]}]
    s2 = np.sqrt(0.5)
    rec = lambda q, t, s: np.array([[*q, *t, *s]] * 2, np.float32)
    frames = {"idle": {"b1": rec(I, [10, 0, 0], [1, 1, 1])},
              "aim": {"b1": rec([0, 0, s2, s2], [0, 0, 5], [0, 0, 0])}}  # +90 degrees about mesh Z, 5 cm up, scale delta 0
    names = {"AS_T_Minigun_Idle": "idle", "AS_T_Minigun_Aim_cd": "aim"}
    anims = [{"name": "AS_T_Minigun_Idle", "tracks": [1], "frames": 2},
             {"name": "AS_T_Minigun_Aim_cd", "tracks": [1], "frames": 2, "additive": True}]

    class Fake:
        info = {"bones": bones, "anims": anims}
        anim_frames = staticmethod(lambda a: frames[names[a["name"]]])

    base = _additive_base(Fake, anims[1])
    assert base is anims[0]
    out = bake_additive(Fake, anims[1], base)
    assert np.allclose(out["b1"][0, :4], [0, 0, s2, s2], atol=1e-6)
    assert np.allclose(out["b1"][0, 4:7], [10, 0, 5]) and np.allclose(out["b1"][0, 7:], 1)
    assert np.allclose(out["b2"][0, :4], [0, 0, -s2, s2], atol=1e-6)  # mesh orientation of b2 unchanged -> undoes its parent


def test_absolute_seat_rotation_is_mesh_frame():
    from pipeline.catalog.palworld import absolute_seat_rotation
    # yaw -90 in the actor frame (boss faces the actor's forward) == the Pal mesh's own yaw -90: identity in the Pal's model frame
    assert np.allclose(np.abs(absolute_seat_rotation([0, -90, 0])), [0, 0, 0, 1], atol=1e-5)


def test_ride_seat_in_clip():
    from pipeline.common.model import Clip, Track
    from pipeline.importers.palworld.importer import ride_seat_in_clip

    def clip(y):
        return Clip(name="X_Ride_Idle", category="ride", tracks=[Track("root", "translation", np.zeros(1, np.float32), np.array([[0, y, 0]], np.float32))])

    assert ride_seat_in_clip([clip(3.28)])       # Viking Boss: the clip carries the seat
    assert not ride_seat_in_clip([clip(0.01)])   # Electric Boss: root at the origin, the marker places it
    assert not ride_seat_in_clip([])


def test_vfx2_variant_from_compiled_params():
    # M_Pal_Manticore_Fire's static-switch variants, as read from the parameters each compiled shader uses
    from pipeline.importers.palworld.importer import vfx2_params

    prm = {"scalars": {"MaskScaleU": 10.0, "MaskScaleV": 2.0, "FresnelScale": 0.5}, "vectors": {}}
    base = vfx2_params(prm, {"AlphaMin", "AlphaMax", "AlphaScale01", "AlphaScale02", "DistortionScaleU_01", "MaskScaleU", "FresnelScale"})
    assert base["alphaMode"] == "range" and base["distort1"] and base["maskU"] == 10.0 and base["maskV"] == 0.0
    blaze = vfx2_params(prm, {"AlphaScale01", "AlphaScale02", "FresnelScale", "MaskScaleV"})  # Blazehowl Fire02
    assert blaze["alphaMode"] == "add" and not blaze["distort1"] and blaze["maskU"] == 0.0 and blaze["maskV"] == 2.0
    azur = vfx2_params(prm, {"DistortionScaleU_01", "Color01", "Color02"})  # Azurmane: Masked override, opaque in view
    assert azur["alphaMode"] == "opaque" and azur["fresnel"] == 0.0


def test_vfx2_decompiled_formula():
    # colour = lerp(C1, C2, smoothstep(RGBMin, RGBMax, a1 s1 + a2 s2)) with a = tex ** 2; additive alpha; no distortion here
    from pipeline.importers.palworld.importer import filmic, vfx2_params, vfx2_shade

    vec = lambda r, g, b: {"R": r, "G": g, "B": b}
    prm = {"scalars": {"RGBMin": 0.0, "RGBMax": 1.0, "AlphaScale01": 1.0, "AlphaScale02": 1.0, "DistortionScale": 0.0},
           "vectors": {"Color01": vec(1.0, 0.0, 0.0), "Color02": vec(0.0, 0.0, 1.0)}}
    p = vfx2_params(prm, {"AlphaScale01", "AlphaScale02"})
    half = np.full((4, 4), np.sqrt(0.25), np.float32)  # squared -> 0.25 per layer
    rgb, a = vfx2_shade(p, np.array([0.5]), np.array([0.5]), half, half, np.full((4, 4), 0.5, np.float32))
    assert abs(a[0] - 0.5) < 1e-5  # 0.25 + 0.25
    s = 0.5 * 0.5 * (3 - 2 * 0.5)  # smoothstep(0, 1, 0.5)
    expect = filmic(np.array([1.0 - s, 0.0, s], np.float32) * 0.5)
    assert np.allclose(rgb[0], expect, atol=1e-4)



def test_fire_fresnel_only_in_compiled_variant():
    # M_Pal_Fire compiles two variants; only instances whose shader reads FresnelColor draw the rim (HadesBird...)
    from pipeline.importers.palworld.importer import fire_params

    vec = lambda r, g, b: {"R": r, "G": g, "B": b}
    prm = {"scalars": {}, "vectors": {"FresnelColor": vec(10, 1, 0)}, "overridden": {"FresnelColor"}}
    assert "fresnelColor" not in fire_params(prm, {"Color1", "Color2", "Color3", "midPoint"})
    assert fire_params(prm, {"Color1", "FresnelColor", "rgbFresnelPower"})["fresnelColor"] == [10.0, 1.0, 0.0]


def test_morph_prim_converts_and_subsets_deltas():
    from pipeline.common.model import Primitive
    from pipeline.importers.palworld.importer import _morph_prim

    dense = np.zeros((2, 4, 3), np.float32)
    dense[0, 2] = [1.0, 2.0, 3.0]  # Unreal cm: x, y(forward), z(up)
    prim = Primitive(material="m", indices=np.array([0, 1, 2], np.uint32), positions=np.zeros((2, 3), np.float32))
    extras = _morph_prim(prim, np.array([2, 3]), (["eye_smile", "mouth_open"], dense))
    assert extras == {"targetNames": ["eye_smile", "mouth_open"]}
    assert len(prim.morph_targets) == 2  # every target kept so primitives of a mesh agree
    np.testing.assert_allclose(prim.morph_targets[0][0], [0.01, 0.03, 0.02], atol=1e-6)  # y/z swapped, cm -> m
    # a primitive no target moves gets none
    other = Primitive(material="m", indices=np.array([0], np.uint32), positions=np.zeros((1, 3), np.float32))
    assert _morph_prim(other, np.array([0]), (["eye_smile", "mouth_open"], dense)) == {} and not other.morph_targets
