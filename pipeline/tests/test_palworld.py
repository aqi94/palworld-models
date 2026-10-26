"""Palworld importer rules, on synthetic data (no game files needed)."""

import json
import os

import numpy as np
import pytest

from pipeline.importers.palworld import convert as cv
from pipeline.importers.palworld.importer import _part_name, _retarget, _top4, categorize, clip_name, import_pal


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


def test_attach_offset_identity_and_yaw():
    from pipeline.catalog.palworld import _rotator_quat, attach_offset

    assert np.allclose(_rotator_quat(0, 0, 0), [0, 0, 0, 1])
    assert np.allclose(_rotator_quat(0, 90, 0), [0, 0, np.sin(np.pi / 4), np.cos(np.pi / 4)])
    ident = attach_offset({"socketLoc": [0, 0, 0], "socketRot": [0, 0, 0], "loc": [0, 0, 0], "rot": [0, 0, 0]})
    assert ident == {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0]}
    # a component 100 cm along X on a socket yawed 90 deg ends up 100 cm along Y (Unreal) = Z in glTF
    off = attach_offset({"socketLoc": [0, 0, 0], "socketRot": [0, 90, 0], "loc": [100, 0, 0], "rot": [0, 0, 0]})
    assert np.allclose(off["position"], [0, 0, 1], atol=1e-4)
