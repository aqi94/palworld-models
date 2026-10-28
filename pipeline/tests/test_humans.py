"""Human NPCs: shared animation packs, clip rules, labels (synthetic data, no game files needed)."""

import json
import os

import numpy as np

from pipeline.catalog.palworld import pack_label
from pipeline.cli import categorize_model
from pipeline.common.manifest import build_manifest
from pipeline.importers.palworld.importer import human_categorize, human_loop, import_animpack, pack_default_clip
from pipeline.common.model import Clip


def test_human_clip_rules_read_the_action_after_the_owner():
    assert human_categorize("Player_Female_Idle_AssaultRifle") == "idle"
    assert human_categorize("Player_Female_Jog_Handgun") == "move"
    assert human_categorize("Player_Female_Reload_AssaultRifle") == "battle"
    assert human_categorize("Player_Female_WalkFwd_Aim_Bowgun") == "battle"  # aiming while walking is combat
    assert human_categorize("Player_Female_HorseRide_Idle_AssaultRifle") == "ride"
    assert human_categorize("NPC_Female_Talking") == "emote"
    assert human_categorize("NPC_Fat_Attack_HeavyWeapon") == "battle"
    assert human_loop("Player_Female_Idle_None") and human_loop("Player_Female_Crouch_Walk_Handgun") and human_loop("Player_Female_JogFwd_Aim_Bowgun")
    assert not human_loop("Player_Female_Sprint_Start") and not human_loop("Player_Female_Reload_AssaultRifle") and not human_loop("Player_Female_JumpEnd_None")


def test_pack_default_is_the_plain_idle():
    clips = [Clip(name=n, tracks=[], frame_rate=30, frame_count=2, loop=human_loop(n), category=human_categorize(n))
             for n in ("Player_Female_AirJump_None", "Player_Female_Crouch_Idle_AssaultRifle", "Player_Female_Idle_AssaultRifle", "Player_Female_HorseRide_Idle_AssaultRifle")]
    assert pack_default_clip(clips) == "Player_Female_Idle_AssaultRifle"


def test_pack_labels():
    assert pack_label("Player_Female_ShooterAnime_AssaultRifle") == "Assault Rifle"
    assert pack_label("Player_Female_ShooterAnime_None") == "Unarmed"
    assert pack_label("NPC_Fat_ShooterAnime_HeavyWeapon") == "Heavy Weapon (Fat)"
    assert pack_label("Actions_NPC_StandardHumanDataSet") == "Actions (Standard)"
    assert pack_label("NPC_Extra") == "NPC extras"


def test_human_subcategories():
    assert categorize_model("human", "NPC_Male_Soldier01", {"group": "NPC"}) == ("humans", "npc")
    assert categorize_model("human", "Boss_SnowBoss001", {"group": "Boss"}) == ("humans", "boss")


def _write_pack(root: str) -> None:
    """A two-bone skeleton and one clip that animates only the child: the pack must not invent a root track."""
    d = os.path.join(root, "palworld_animpacks", "Set")
    os.makedirs(os.path.join(d, "anims"))
    bones = [{"name": "root", "parent": -1, "t": [0, 0, 0], "r": [0, 0, 0, 1], "s": [1, 1, 1], "retarget": "Animation"},
             {"name": "hand", "parent": 0, "t": [0, 0, 100], "r": [0, 0, 0, 1], "s": [1, 1, 1], "retarget": "Animation"}]
    frames = np.array([[0, 0, 0, 1, 0, 0, 100, 1, 1, 1], [0, 0, 0.7071, 0.7071, 0, 0, 120, 1, 1, 1]], np.float32)
    frames.tofile(os.path.join(d, "anims", "AS_Player_Female_Idle_None.bin"))
    info = {"code": "Set", "mesh": None, "skeleton": "SK", "bones": bones, "skeletonBones": bones, "materials": [], "lods": [], "textures": [], "sockets": [],
            "anims": [{"name": "AS_Player_Female_Idle_None", "file": "anims/AS_Player_Female_Idle_None.bin", "frames": 2, "length": 1 / 30,
                       "additive": False, "tracks": [{"bone": "hand", "offset": 0}]}]}
    with open(os.path.join(d, "pal.json"), "w") as f:
        json.dump(info, f)


def test_animpack_keeps_only_authored_tracks(tmp_path):
    _write_pack(str(tmp_path))
    m = import_animpack(str(tmp_path), "Set", model_id="Pack_Set")
    assert m.kind == "animpack" and not m.meshes
    [c] = m.clips
    assert c.name == "Player_Female_Idle_None" and c.loop and m.default_clip == c.name
    assert {t.joint for t in c.tracks} == {"hand"}  # bones the clip leaves alone keep the target mesh's own bind pose
    assert np.allclose(c.tracks[1].values[1], [0, 1.2, 0])  # Unreal Z cm -> glTF Y m, played as authored (retarget mode Animation)


def test_pack_weapons_give_the_hand_prop(tmp_path):
    _write_pack(str(tmp_path))
    m = import_animpack(str(tmp_path), "Set")
    m.kind = "human"
    m.extras["animPacks"] = [{"id": "Rifle", "heldItems": [{"item": "Weapon_AssaultRifle_001", "bone": "hand", "position": [0, 0, 0], "rotation": [0, 0, 0, 1]}]}]
    man = build_manifest(m)
    assert [p["bone"] for p in man["props"]] == ["hand"]


def test_pack_honours_retarget_modes(tmp_path):
    """SK_PalHuman_Skeleton: almost every bone is Skeleton mode (the mesh's own offset), root/pelvis AnimationScaled. A pack drops the
    translation of Skeleton bones (each mesh keeps its own) and records the source of scaled ones for the viewer."""
    _write_pack(str(tmp_path))
    pj = os.path.join(str(tmp_path), "palworld_animpacks", "Set", "pal.json")
    info = json.load(open(pj))
    info["skeletonBones"][1]["retarget"] = "Skeleton"
    open(pj, "w").write(json.dumps(info))
    m = import_animpack(str(tmp_path), "Set")
    assert {(t.joint, t.kind) for t in m.clips[0].tracks} == {("hand", "rotation")}
    info["skeletonBones"][1]["retarget"] = "AnimationScaled"
    info["retargetSources"] = {"SK_Player_Female": {"hand": [0, 0, 50]}}
    info["anims"][0]["retargetSource"] = "SK_Player_Female"
    open(pj, "w").write(json.dumps(info))
    m = import_animpack(str(tmp_path), "Set")
    assert ("hand", "translation") in {(t.joint, t.kind) for t in m.clips[0].tracks}
    assert m.extras["retarget"] == {"hand": {"mode": "AnimationScaled", "sources": {"Player_Female_Idle_None": [0.0, 0.5, 0.0]}}}
