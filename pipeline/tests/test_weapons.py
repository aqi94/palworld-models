"""Weapon materials (maps named by the base material's ReferencedTextures) and weapon names (from the game's items)."""

import json

from pipeline.catalog.weapon_names import build, words
from pipeline.importers.palworld.importer import _classify_map, weapon_maps


def test_classify_map_suffixes():
    # every packing seen in the weapons' ReferencedTextures; channel orders were read from the cooked pixel shaders
    cases = {
        "T_Weapon_AssaultRifle_008_Magazine_B": ("base", None), "T_Bat_Wood_BC": ("base", None), "Tex_MissileLauncher_Diff": ("base", None),
        "tool_props_text_Tools_01_BaseColor": ("base", None), "T_Weapon_AssaultRifle_008_Magazine_N": ("normal", None),
        "T__Weapon_AssaultRifle_002_Normal": ("normal", None), "T_Weapon_AssaultRifle_008_Magazine_orm": ("orm", "orm"),
        "T_Bat_Wood_ORM": ("orm", "orm"), "T_Revolver_AORM": ("orm", "orm"), "Baseball_Material_001_OcclusionRoughnessMetallic": ("orm", "orm"),
        "Tex_MissileLauncher_Met_Rough_AO": ("orm", "mro"), "T_Weapon_AssaultRifle_002_AO_Metallic_Roughness": ("orm", "amr"),
        "TX_Gun_RMA": ("orm", "rma"), "T_Weapon_Handgun_006_M": ("metal", None), "T_Weapon_Handgun_006_R": ("rough", None),
        "Tex_MissileLauncher_Emissive": ("emissive", None), "T_Weapon_RocketLauncher_006_Sticker_E": ("emissive", None),
        "Tex_MissileLauncher_Spec": (None, None),
    }
    for tex, want in cases.items():
        assert _classify_map("", tex) == want, tex
    assert _classify_map("Difuse texture", "T_Whatever") == ("base", None)  # parameter name first


def test_weapon_maps_prefer_referenced_over_folder():
    # a magazine's material samples its own maps; the mesh folder holds other parts' textures (RocketLauncher_006)
    folder = ["x/T_Weapon_RocketLauncher_006_Body1_B", "x/T_Weapon_RocketLauncher_006_Sticker_B"]
    ref = ["x/T_Weapon_RocketLauncher_006_Body2_B", "x/T_Weapon_RocketLauncher_006_Body2_N", "Engine/Content/DefaultTexture"]
    got = weapon_maps({}, ref, folder)
    assert got["base"][0].endswith("Body2_B") and got["normal"][0].endswith("Body2_N")
    assert weapon_maps({}, [], folder)["base"][0].endswith("Body1_B")  # old extracts: first folder match
    assert weapon_maps({"Albedo": "x/T_A"}, ref, folder)["base"][0] == "x/T_A"  # instance parameters win


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_weapon_names(tmp_path):
    w = tmp_path / "palworld_weapons"
    for f in ("Bow_003", "CompoundBow", "AssaultRifle_008", "AssaultRifle_008_Magazine", "AssaultRifle_008_Bullet", "AssaultRifle_001",
              "AssaultRifle_004", "NPC_Male_Believer01Cane"):
        _write(w / f / "pal.json", {})
    part = lambda comp, item, parent="DefaultSceneRoot", ov=(), visible=True: {
        "component": comp, "item": item, "parent": parent, "socket": None, "visible": visible,
        "overrides": [{"path": o, "chain": [o]} if o else None for o in ov]}
    items = [
        {"class": "WeakerBow", "itemClasses": ["WeakerBow"], "parents": [], "user": "player", "parts": [part("SkeletalMesh", "Bow_003")]},
        {"class": "Bow_Fire", "itemClasses": ["Bow_Fire"], "parents": [], "user": "player", "parts": [part("SkeletalMesh", "Bow_003")]},
        {"class": "CompoundBow", "itemClasses": ["CompoundBow"], "parents": [], "user": "player", "parts": [part("SkeletalMesh", "CompoundBow")]},
        {"class": "SkyBow", "itemClasses": ["SkyBow"], "parents": ["CompoundBow"], "user": "player",
         "parts": [part("SkeletalMesh", "CompoundBow", ov=["m/MI_Blue"])]},
        {"class": "MakeshiftAssaultRifle", "itemClasses": ["MakeshiftAssaultRifle"], "parents": [], "user": "player",
         "parts": [part("Weapon", "AssaultRifle_008"), part("Bullet", "AssaultRifle_008_Bullet", parent="Weapon")]},
        {"class": "NormalRifle", "itemClasses": ["NormalRifle"], "parents": [], "user": "player", "parts": [part("Weapon", "AssaultRifle_001")]},
        {"class": "AssaultRifle_NPC_GrassBoss", "itemClasses": [], "parents": [], "user": "npc",
         "parts": [part("SkeletalMesh", "AssaultRifle_001", ov=["m/M_Pink"])]},
        {"class": "BelieverFatCane_NPC", "itemClasses": [], "parents": [], "user": "npc", "parts": [part("SkeletalMesh", "NPC_Male_Believer01Cane")]},
    ]
    _write(w / "items.json", items)
    _write(w / "held.json", [{"pal": "Monkey", "weapon": "AssaultRifle_004", "parts": [{"item": "AssaultRifle_004"}]}])
    rows = {"WeakerBow": ("WeakerBow", 1), "Bow_Fire": ("Bow_Fire", 2), "CompoundBow": ("CompoundBow", 3), "SkyBow": ("SkyBow", 4),
            "MakeshiftAssaultRifle": ("MakeshiftAssaultRifle", 1), "AssaultRifle_Default1": ("NormalRifle", 3)}
    _write(tmp_path / "tables" / "DT_ItemDataTable.json", [{"Rows": {r: {"ItemActorClass": c, "OverrideName": "None", "Rank": k, "SortId": 0}
                                                                      for r, (c, k) in rows.items()}}])
    text = {"WeakerBow": "Old Bow", "Bow_Fire": "Fire Bow", "CompoundBow": "Compound Bow", "SkyBow": "Mechanical Bow",
            "MakeshiftAssaultRifle": "Makeshift Assault Rifle", "AssaultRifle_Default1": "Assault Rifle"}
    _write(tmp_path / "tables" / "DT_ItemNameText_Common.en.json",
           [{"Rows": {f"ITEM_NAME_{k.upper()}": {"TextData": {"LocalizedString": v}} for k, v in text.items()}}])  # keys differ in case
    got = build(str(tmp_path), {"Monkey": "Tanzee"})
    name = {k: v["name"] for k, v in got.items()}
    assert name["Weapon_Bow_003"] == "Old Bow"  # lowest tier names a shared mesh
    assert [i["name"] for i in got["Weapon_Bow_003"]["items"]] == ["Old Bow", "Fire Bow"]
    assert name["Weapon_CompoundBow"] == "Compound Bow"
    assert name["Weapon_CompoundBow__SkyBow"] == "Mechanical Bow"  # recolour: separate model
    assert got["Weapon_CompoundBow__SkyBow"]["materialOverrides"] == [["m/MI_Blue"]]
    assert name["Weapon_AssaultRifle_008_Bullet"] == "Makeshift Assault Rifle – Bullet"  # component of the blueprint
    assert name["Weapon_AssaultRifle_008_Magazine"] == "Makeshift Assault Rifle – Magazine"  # folder suffix
    assert name["Weapon_AssaultRifle_001__AssaultRifle_NPC_GrassBoss"] == "Assault Rifle (Grass Boss)"
    assert name["Weapon_NPC_Male_Believer01Cane"] == "Believer Cane (NPC)"
    assert name["Weapon_AssaultRifle_004"] == "Tanzee's Assault Rifle"
    assert got["Weapon_AssaultRifle_004"]["users"] == {"pals": ["Monkey"]}
    assert words("AssaultRifle_004") == "Assault Rifle 004"
