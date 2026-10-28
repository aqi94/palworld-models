"""English names for the weapon models (raw/palworld_weapons/<folder>/), from the game's items.

Sources: raw/palworld_weapons/items.json (`PalExtract items`: every weapon blueprint, its mesh parts and material overrides, the
DT_ItemDataTable.ItemActorClass names that point at it), raw/tables/DT_ItemDataTable.json + DT_ItemNameText_Common.en.json.

A *visual* is a mesh folder plus the materials a blueprint puts on it. Each visual is one model: the plain mesh keeps the id
`Weapon_<folder>`; a blueprint that recolours it (BP_SkyBow: Compound Bow + MI_PalProp_CompoundBow_Blue) adds
`Weapon_<folder>__<class>` with `materialOverrides` (per slot: a material chain, or None for the mesh's own material).

Names, first rule that applies:
  1. an item uses the visual: the lowest-tier item's English name (`items` lists all of them, for search);
  2. a part attached to another mesh of the blueprint (magazine, bullets, sight): "<owner name> – <part>";
  3. an NPC blueprint's visual: "<player item name> (<who>)", e.g. "Assault Rifle (Grass Boss)", "Katana (NPC)";
  4. a Pal's own weapon (held.json, no item): "<Pal>'s <weapon>", e.g. "Tanzee's Assault Rifle";
  5. the folder name, number kept ("Assault Rifle 004").
Names that still collide get the folder appended.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

PLACEHOLDER_TEXT = "en Text"


@dataclass
class Visual:
    folder: str
    overrides: tuple = ()  # material package paths per slot (None = mesh's own); () = plain mesh
    owner_class: str | None = None  # the blueprint that recolours it (only for overrides)
    items: list = field(default_factory=list)  # (rank, name, class)
    parts_of: list = field(default_factory=list)  # (owner visual key, part label)
    npc: list = field(default_factory=list)  # (class, user)
    chains: list = field(default_factory=list)  # per slot: material chain or None

    @property
    def model_id(self) -> str:
        return f"Weapon_{self.folder}" + (f"__{self.owner_class}" if self.overrides else "")


def words(s: str) -> str:
    """AssaultRifle_004 -> Assault Rifle 004; NPC_Male_Believer01Cane -> NPC Male Believer 01 Cane."""
    s = re.sub(r"(?<=[a-z])(?=[A-Z0-9])|(?<=[0-9])(?=[A-Za-z])", " ", s).replace("_", " ").replace("-", " ")
    return " ".join(w[:1].upper() + w[1:] for w in s.split())


def _rows(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return (d[0] if isinstance(d, list) else d).get("Rows", {})


def item_names(tables_dir: str) -> dict[str, list[tuple]]:
    """ItemActorClass -> [(rank, English name, row)], lowest tier first. Rows without English text are left out."""
    # rows and keys disagree on case (row FlameThrower_2, key ITEM_NAME_Flamethrower)
    text = {k.lower(): (v.get("TextData") or {}).get("LocalizedString") for k, v in _rows(os.path.join(tables_dir, "DT_ItemNameText_Common.en.json")).items()}
    out: dict[str, list[tuple]] = {}
    for row, r in _rows(os.path.join(tables_dir, "DT_ItemDataTable.json")).items():
        cls = r.get("ItemActorClass")
        if not cls or cls == "None":
            continue
        key = r["OverrideName"] if r.get("OverrideName") not in (None, "None") else f"ITEM_NAME_{row}"
        name = text.get(key.lower())
        if not name or name.startswith(PLACEHOLDER_TEXT):
            continue
        # rows not legal in game sort last: a class's dev row ("Axe4") loses to its real one, but a Pal-only item (Beegarde's Spear) keeps its name
        out.setdefault(cls, []).append(((r.get("bLegalInGame") is False, r.get("Rank") or 0, r.get("SortId") or 0, row), name, row))
    for v in out.values():
        v.sort()
    return out


# development blueprints (BP_PalSphere_Debug, BP_Fishing_Rod_Test): not in the game's item list
DEV_RE = re.compile(r"(^|_)(Debug|Test)($|_)", re.I)
# a mesh folder named after another plus one of these is a part of it (magazines the reload animation spawns; no blueprint lists them)
PART_SUFFIX_RE = re.compile(r"^(?P<owner>.+?)_(?P<part>Magazine|Mag|Bullets?|Ammo|Clip|Casing|Cylinder|Core|Rocket|Missile|Sticker|Arrow|Anchor|Full)$")
PART_LABELS = {"Mag": "Magazine", "Bullets": "Bullets", "Ammo": "Ammo", "Casing": "Casing", "Clip": "Clip"}


def _part_label(folder: str, owner_folder: str) -> str:
    """AssaultRifle_008_Bullet under AssaultRifle_008 -> Bullet; Shotgun_007_Mag -> Magazine; Samurai13_Sights_01a -> Samurai 13 Sights 01a."""
    rest = folder[len(owner_folder) + 1:] if folder.lower().startswith(owner_folder.lower() + "_") else folder
    return " ".join(PART_LABELS.get(w, w) for w in words(rest).split())


def _npc_who(cls: str, user: str) -> str:
    """AssaultRifle_NPC_GrassBoss -> Grass Boss; Katana_NPC_Otomo -> NPC follower; Shotgun_NPC -> NPC."""
    m = re.search(r"_NPC_?(.*)$", cls)
    tail = (m.group(1) if m else "").replace("Otomo", "").strip("_")
    if tail:
        return words(tail)
    return "NPC follower" if user == "otomo" else "NPC"


def build(raw: str, pal_names: dict[str, str]) -> dict[str, dict]:
    """model id -> {folder, name, items, users, materialOverrides?, partOf?} for every weapon folder in raw/palworld_weapons.

    `pal_names`: Pal code -> English name (for Pal-only weapons)."""
    root = os.path.join(raw, "palworld_weapons")
    folders = sorted(d for d in os.listdir(root) if os.path.isfile(os.path.join(root, d, "pal.json"))) if os.path.isdir(root) else []
    names = item_names(os.path.join(raw, "tables"))
    entries = []
    if os.path.isfile(os.path.join(root, "items.json")):
        with open(os.path.join(root, "items.json"), encoding="utf-8") as f:
            entries = json.load(f)
    held = []
    if os.path.isfile(os.path.join(root, "held.json")):
        with open(os.path.join(root, "held.json"), encoding="utf-8") as f:
            held = json.load(f)
    present = set(folders)
    visuals: dict[tuple, Visual] = {(f, ()): Visual(f) for f in folders}
    by_class = {e["class"]: e for e in entries}

    def class_items(cls: str) -> list[tuple]:
        return [(rank, name, cls) for rank, name, _ in names.get(cls, [])]

    def visual_of(e: dict, p: dict) -> Visual | None:
        if p.get("item") not in present:
            return None
        ov = tuple(o["path"] if o else None for o in p.get("overrides") or [])
        key = (p["item"], ov if any(ov) else ())
        v = visuals.get(key)
        if v is None:
            v = visuals[key] = Visual(p["item"], key[1], owner_class=e["class"],
                                      chains=[o["chain"] if o else None for o in p.get("overrides") or []])
        return v

    entries = [e for e in entries if not DEV_RE.search(e["class"])]
    by_class = {e["class"]: e for e in entries}
    for e in entries:
        comps = {p["component"] for p in e["parts"]}
        own_items = [it for c in e.get("itemClasses") or [] for it in class_items(c)]
        # the weapon body: a visible component not attached to another mesh or a socket (a fishing float hangs on Socket_Float);
        # everything else (magazines, bullets, sights, hidden laser meshes) is a part of it
        body = [p for p in e["parts"] if visual_of(e, p) and p.get("parent") not in comps]
        body.sort(key=lambda p: (not p.get("visible", True), bool(p.get("socket"))))
        main_p = body[0] if body else None
        main_v = visual_of(e, main_p) if main_p else None
        for p in e["parts"]:
            v = visual_of(e, p)
            if v is None or not p.get("visible", True) and e["user"] in ("npc", "otomo"):
                continue
            if e["user"] in ("npc", "otomo"):  # an NPC blueprint's visible meshes are what that NPC carries (no part-of naming)
                v.npc.append((e["class"], e["user"]))
            elif p is not main_p and main_v is not None and v is not main_v:
                # a recoloured part belongs to its own blueprint's item (Megaboost Gun's magazine), a plain one to the body
                v.parts_of.append((main_v, _part_label(v.folder, main_v.folder), sorted(own_items)[0][1] if v.overrides and own_items else None))
            else:
                v.items += own_items

    # NPC blueprints: the player item they derive from (parents with an item, or the class without _NPC...)
    def player_name(e: dict) -> str | None:
        for c in [re.sub(r"_NPC.*$", "", e["class"]), *(e.get("parents") or [])]:
            if names.get(c):
                return names[c][0][1]
            pe = by_class.get(c)
            if pe and pe.get("itemClasses") and names.get(pe["itemClasses"][0]):
                return names[pe["itemClasses"][0]][0][1]
        return None

    player_folders = {p.get("item") for e in entries if e["user"] not in ("npc", "otomo") for p in e["parts"]}
    pal_of: dict[str, list[str]] = {}
    for h in held:
        for p in h.get("parts") or [{"item": h["weapon"]}]:
            if not p.get("mounted"):
                pal_of.setdefault(p["item"], []).append(h["pal"])

    resolved: dict[tuple, str] = {}

    def name_of(key: tuple, v: Visual, depth: int = 0) -> str:
        if key in resolved:
            return resolved[key]
        n = None
        m = PART_SUFFIX_RE.match(v.folder)
        if v.items:
            n = sorted(v.items)[0][1]
        elif v.parts_of and depth < 4:
            owner, label, own = v.parts_of[0]
            okey = next(k for k, x in visuals.items() if x is owner)
            n = f"{own or name_of(okey, owner, depth + 1)} – {label}"
        elif m and (m.group("owner"), ()) in visuals and depth < 4:
            okey = (m.group("owner"), ())
            n = f"{name_of(okey, visuals[okey], depth + 1)} – {PART_LABELS.get(m.group('part'), m.group('part'))}"
        elif v.npc:
            cls, user = v.npc[0]
            plain = visuals.get((v.folder, ()))
            if plain is not None and plain.items:  # a player weapon's mesh (recoloured or not): its item name
                base = sorted(plain.items)[0][1]
            elif v.folder in player_folders:
                base = player_name(by_class[cls]) or words(v.folder)
            else:  # a mesh only NPCs carry (SK_NPC_Male_Believer01Cane): its own name, without the character number
                base = re.sub(r"\s*\d+\s*", " ", words(re.sub(r"^NPC_(Male|Female)_", "", v.folder))).strip()
            n = f"{base} ({_npc_who(cls, user)})"
        elif v.folder in pal_of:
            pal = pal_names.get(pal_of[v.folder][0], pal_of[v.folder][0])
            weapon = words(re.sub(r"_\d+$", "", v.folder))
            n = f"{pal}'s {weapon}"
        elif v.overrides:
            n = words(v.owner_class or v.folder)
        else:
            n = words(v.folder)
        resolved[key] = n
        return n

    out: dict[str, dict] = {}
    for key, v in visuals.items():
        rec = {"folder": v.folder, "name": name_of(key, v)}
        items = sorted({(r, n, c) for r, n, c in v.items})
        if items:
            uniq = {}  # rarity tiers repeat the name: one entry per name, lowest tier's class
            for _, n, c in items:
                uniq.setdefault(n, c)
            rec["items"] = [{"name": n, "class": c} for n, c in uniq.items()]
        users = {"npc": sorted({c for c, _ in v.npc})}
        if v.folder in pal_of and not v.overrides:
            users["pals"] = sorted(set(pal_of[v.folder]))
        users = {k: x for k, x in users.items() if x}
        if users:
            rec["users"] = users
        if v.overrides:
            rec["materialOverrides"] = v.chains
        if v.parts_of:
            rec["partOf"] = v.parts_of[0][0].model_id
        out[v.model_id] = rec
    # still identical: tell them apart by the mesh (rarely needed once items name them)
    seen: dict[str, list[str]] = {}
    for mid, rec in out.items():
        seen.setdefault(rec["name"], []).append(mid)
    for n, ids in seen.items():
        if len(ids) > 1:
            for mid in ids:
                out[mid]["name"] = f"{n} ({words(out[mid]['folder'])}{', ' + mid.split('__', 1)[1] if '__' in mid else ''})"
    return out
