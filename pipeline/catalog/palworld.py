"""Palworld catalog: one model per extracted skeletal mesh folder (raw/palworld/<Code>/pal.json).

Names / Paldeck numbers / elements come from the game's data tables (raw/tables, written by
`PalExtract tables`). Element forms (Kitsunebi_Ice) and skins (PinkCat_Skin001) have their own
meshes, so they are their own models; skins name their base Pal through DT_SkinDataTable.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field

import numpy as np

from ..common.model import Model
from ..importers.palworld import convert as cv

DEFAULT_RAW_DIR = os.environ.get("PALWORLD_RAW", os.path.join(os.path.dirname(__file__), "..", "..", "raw"))

# Meshes whose model is another table row's Pal and that no table links (Bellanoir Libero's mesh is NightLady_Pink).
NAME_ALIASES = {"NightLady_Pink": "NightLady_Dark"}

ELEMENTS = {0: None, 1: "Neutral", 2: "Fire", 3: "Water", 4: "Grass", 5: "Electric", 6: "Ice", 7: "Ground", 8: "Dark", 9: "Dragon"}


# Held weapons sit on the rig's weapon bones (a mesh component named ...L goes to the left hand, everything else to the right).
# The game attaches them in code, so the per-weapon offset (glTF metres / xyzw) is fitted from the Pal's "holding it" animation by
# tools/fit_held_item.py and stored in held_offsets.json, keyed "<item>@<bone>" (or just "<item>" for a single weapon).
HELD_OFFSETS = json.load(open(os.path.join(os.path.dirname(__file__), "held_offsets.json"), encoding="utf-8"))
_NO_OFFSET = {"position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0, 1.0]}


def _rotator_quat(pitch: float, yaw: float, roll: float) -> np.ndarray:
    """Unreal FRotator (degrees) -> quaternion xyzw (FRotator::Quaternion)."""
    sp, sy, sr = (math.sin(math.radians(v) / 2) for v in (pitch, yaw, roll))
    cp, cy, cr = (math.cos(math.radians(v) / 2) for v in (pitch, yaw, roll))
    return np.array([cr * sp * sy - sr * cp * cy, -cr * sp * cy - sr * cp * sy, cr * cp * sy - sr * sp * cy, cr * cp * cy + sr * sp * sy])


def _qmul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array([aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz])


def _qrot(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    u = q[:3]
    return v + 2 * np.cross(u, np.cross(u, v) + q[3] * v)


def attach_offset(part: dict) -> dict:
    """Weapon-in-bone-space offset (glTF metres / xyzw, plus `scale` when not 1) of a component attached to a skeleton socket
    (identity when the weapon actor attaches straight to the hand): socket * component (Unreal space)."""
    qs, qc = _rotator_quat(*part.get("socketRot", [0, 0, 0])), _rotator_quat(*part["rot"])
    pos = np.asarray(part.get("socketLoc", [0, 0, 0]), float) + _qrot(qs, np.asarray(part["loc"], float))
    out = {"position": [round(float(x), 5) for x in cv.pos(pos)], "rotation": [round(float(x), 6) for x in cv.quat(_qmul(qs, qc))]}
    sc = part.get("scale", [1, 1, 1])
    if not np.allclose(sc, 1, atol=1e-3):
        out["scale"] = [round(float(sc[0]), 4), round(float(sc[2]), 4), round(float(sc[1]), 4)]  # Unreal XYZ -> glTF X, Z(up), Y
    return out


def clip_pairs(pairs: list[dict], pal_code: str) -> list[dict]:
    """rides.json `pairs` (asset names, `PalExtract bossride`) -> manifest clip names: which Pal clips play, in order, while the boss plays
    `boss` (the boss's ride clips are authored against them). `rate` = montage play rate; `loop` segments fill the time the others leave."""
    from ..importers.palworld.importer import clip_name

    out = []
    for p in pairs:
        # from / to: the part of the clip a montage segment plays (seconds); absent = the whole clip
        seq = [{"clip": clip_name(s["anim"], pal_code), **({"rate": round(float(s["rate"]), 4)} if abs(s.get("rate", 1) - 1) > 1e-6 else {}),
                **({"loop": True} if s.get("loop") else {}),
                **({"from": s["from"]} if s.get("from", 0) > 1e-3 else {}),
                **({"to": s["to"]} if s.get("to", -1) >= 0 else {})} for s in p["pal"]]
        out.append({"boss": p["boss"][3:] if p["boss"].startswith("AS_") else p["boss"], "pal": seq})
    return out


def absolute_seat_rotation(rot: list[float]) -> list[float]:
    """A component with bAbsoluteRotation holds a world rotation in the Pal actor's frame, where the mesh component sits at yaw -90
    (the mesh faces +Y, the actor +X). Returns it in the Pal model's frame (glTF xyzw)."""
    q = _qmul(_rotator_quat(0, 90, 0), _rotator_quat(*rot))
    return [round(float(x), 6) for x in cv.quat(q)]


@dataclass
class ModelRef:
    game: str
    kind: str
    id: str
    folder: str
    display_name: str
    species: str = ""
    form: str = ""
    anim_folder: str = ""
    extras: dict = field(default_factory=dict)


def _item_label(item: str) -> str:
    """AssaultRifle_004 -> Assault Rifle; QueenBee_spear -> Queen Bee Spear."""
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", re.sub(r"_\d+$", "", item)).replace("_", " ")
    return " ".join(w[:1].upper() + w[1:] for w in words.split())


def _rows(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return (d[0] if isinstance(d, list) else d).get("Rows", {})


def _enum(v) -> int:
    if isinstance(v, str) and "::" in v:
        try:
            return int(v.split("::")[1])
        except ValueError:
            return 0
    return int(v or 0)


class PalworldCatalog:
    game = "palworld"

    def __init__(self, raw_dir: str | None = None):
        self.raw = os.path.abspath(raw_dir or DEFAULT_RAW_DIR)
        t = os.path.join(self.raw, "tables")
        self.params = _rows(os.path.join(t, "DT_PalMonsterParameter.json"))
        self.names = {k: v["TextData"].get("LocalizedString") or v["TextData"].get("SourceString") for k, v in _rows(os.path.join(t, "DT_PalNameText_Common.en.json")).items()}
        self.skins = _rows(os.path.join(t, "DT_SkinDataTable.json"))
        self._names_ci = {k.lower(): v for k, v in self.names.items()}
        self._refs: list[ModelRef] | None = None

    # -- names ---------------------------------------------------------------------------
    def _name(self, code: str) -> str | None:
        # mesh folders and table keys disagree on case (WindChimes / Windchimes) and bosses carry a prefix (RAID_YakushimaBoss002)
        for key in (f"PAL_NAME_{code}", *(f"PAL_NAME_{p}_{code}" for p in ("RAID", "BOSS", "GYM"))):
            n = self._names_ci.get(key.lower())
            if n and not n.startswith("en Text"):
                return n
        return None

    def describe(self, code: str) -> dict:
        skin = None
        base = code
        m = re.match(r"^(.*?)_(Skin\w*)$", code)
        if code in self.skins:
            base = self.skins[code].get("TargetPalName") or base
            skin = code[len(base) + 1 :] if code.startswith(base + "_") else code
        elif m:
            base, skin = m.group(1), m.group(2)
        form_label = None
        if code in NAME_ALIASES:
            base = NAME_ALIASES[code]
        elif not skin and not self._name(base):
            fm = re.match(r"^(.*?)_(Form\d+)$", code)  # a second form with no table row of its own (WorldTreeDragon_Form2)
            if fm and self._name(fm.group(1)):
                base, form_label = fm.group(1), f"Form {fm.group(2)[4:]}"
        p = self.params.get(base) or {}
        name = self._name(base) or base
        if form_label:
            name = f"{name} ({form_label})"
        if skin:
            num = re.sub(r"\D", "", skin)
            label = "Dark skin" if "Dark" in skin else f"Skin {int(num)}" if num else skin
            name = f"{name} ({label})"
        dex = p.get("ZukanIndex")
        dex = dex if isinstance(dex, int) and dex > 0 else None
        elements = [e for e in (ELEMENTS.get(_enum(p.get("ElementType1"))), ELEMENTS.get(_enum(p.get("ElementType2")))) if e]
        return {
            "name": name,
            "base": base,
            "skin": skin,
            "dex": dex,
            "dexSuffix": p.get("ZukanIndexSuffix") or "" if dex else "",
            "elements": elements,
            "rarity": p.get("Rarity"),
            "codeName": code,
        }

    # -- refs ----------------------------------------------------------------------------
    def all(self) -> list[ModelRef]:
        if self._refs is None:
            root = os.path.join(self.raw, "palworld")
            codes = sorted(d for d in os.listdir(root) if os.path.isfile(os.path.join(root, d, "pal.json"))) if os.path.isdir(root) else []
            refs = []
            for code in codes:
                d = self.describe(code)
                form = d["skin"] or (code[len(d["base"]) + 1 :] if code.startswith(d["base"] + "_") else code.split("_", 1)[-1] if code != d["base"] else "")
                refs.append(ModelRef(game="palworld", kind="pal", id=code, folder=os.path.join(root, code), display_name=d["name"],
                                     species=d["base"], form=form,
                                     extras={k: d[k] for k in ("dex", "dexSuffix", "elements", "rarity", "codeName", "skin") if d.get(k) not in (None, "", [])}))
            refs += self._human_refs()
            self._link_boss_rides(refs)
            refs += self._weapon_refs(refs)
            self._refs = refs
        return self._refs

    @staticmethod
    def _held_item(part: dict) -> dict:
        """One attached weapon: bone + local offset. Priority: fitted offset (held_offsets.json), the attach transform stored in the
        blueprints (socket + the weapon's component transform), identity."""
        bone = part["bone"] if "socket" in part else "weapon_l" if part["part"].endswith("L") else "weapon_r"
        offset = HELD_OFFSETS.get(f"{part['item']}@{bone}") or HELD_OFFSETS.get(part["item"])
        if offset is None and "loc" in part:
            offset = attach_offset(part)
        # a weapon that is not hidden by default (the bees' spears) is always in hand; the others only while aiming / firing
        return {"item": f"Weapon_{part['item']}", "bone": bone, **(offset or _NO_OFFSET), **({"always": True} if part.get("visible") else {})}

    def _weapon_refs(self, pal_refs: list[ModelRef]) -> list[ModelRef]:
        """Held items (raw/palworld_weapons, written by `PalExtract weapon`): one object model per weapon, and a `heldItem`
        link on every Pal that carries one (the viewer attaches it to the Pal's weapon bone)."""
        root = os.path.join(self.raw, "palworld_weapons")
        held_path = os.path.join(root, "held.json")
        if not os.path.isdir(root):
            return []
        held = []
        if os.path.isfile(held_path):
            with open(held_path, encoding="utf-8") as f:
                held = json.load(f)
        by_pal: dict[str, list[dict]] = {}
        for h in held:
            for part in h.get("parts") or [{"part": "", "item": h["weapon"]}]:
                if part.get("mounted"):  # missile pods / prism towers around the Pal: attached in code, not to a hand
                    continue
                by_pal.setdefault(h["pal"], []).append(self._held_item(part))
        for r in pal_refs:
            if r.id in by_pal:
                r.extras["heldItem"] = by_pal[r.id][0]
                r.extras["heldItems"] = by_pal[r.id]
        names = sorted({p["item"] for h in held for p in (h.get("parts") or [{"item": h["weapon"]}]) if not p.get("mounted")}
                       | {d for d in os.listdir(root) if os.path.isfile(os.path.join(root, d, "pal.json"))})  # + `weapons-all` meshes
        return [ModelRef(game="palworld", kind="object", id=f"Weapon_{n}", folder=os.path.join(root, n), display_name=_item_label(n), species="Weapon", form=n)
                for n in names if os.path.isfile(os.path.join(root, n, "pal.json"))]

    @staticmethod
    def _human_label(hid: str) -> str:
        """Boss_DesertBoss001 -> Desert Boss 001 (Boss); NPC_Male_Trader01_v02 -> Trader 01 (Male, v02); Player_Female -> Player (Female)."""
        parts = hid.split("_")
        if parts[0] == "Boss":
            return re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", "_".join(parts[1:])).replace("_", " ") + " (Boss)"
        gender = next((p for p in parts if p in ("Male", "Female")), None)
        rest = [p for p in parts[1:] if p != gender]
        ver = [p for p in rest if re.fullmatch(r"v\d+", p)]
        words = " ".join(re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", p) for p in rest if p not in ver) or parts[0]
        extra = ", ".join([x for x in (gender, *ver) if x])
        return f"{words} ({extra})" if extra else words

    def _human_refs(self) -> list[ModelRef]:
        """Characters from raw/palworld_humans (`PalExtract human`): NPCs, bosses and the default player build."""
        root = os.path.join(self.raw, "palworld_humans")
        if not os.path.isdir(root):
            return []
        refs = []
        for hid in sorted(os.listdir(root)):
            if not os.path.isfile(os.path.join(root, hid, "pal.json")):
                continue
            group = "Boss" if hid.startswith("Boss_") else "Player" if hid.startswith("Player_") else "NPC"
            refs.append(ModelRef(game="palworld", kind="human", id=hid, folder=os.path.join(root, hid), display_name=self._human_label(hid),
                                 species=group, form=hid.split("_", 1)[-1], extras={"codeName": hid, "group": group}))
        return refs

    def _link_boss_rides(self, refs: list[ModelRef]) -> None:
        """Tower bosses fight riding a Pal (raw/palworld_humans/rides.json, `PalExtract bossride`): the Pal's extras get `bossRide`
        (the boss model, the bone it sits on and the offset in that bone's space, plus the Pal's in-game `palScale`), and the boss gets
        `mount` (the Pal)."""
        path = os.path.join(self.raw, "palworld_humans", "rides.json")
        if not os.path.isfile(path):
            return
        with open(path, encoding="utf-8") as f:
            rides = json.load(f)
        by_id = {r.id: r for r in refs}
        for r in rides:
            boss, pal = by_id.get(r["boss"]), by_id.get(r["pal"])
            if boss is None or pal is None or "bossRide" in pal.extras:  # skins / hard variants repeat the first row
                continue
            ps = (r.get("palScale") or [1, 1, 1])[0]
            ride = {"bone": r["bone"], **attach_offset({**r, "scale": r["scale"]}), "palScale": round(float(ps), 4)}
            if r.get("absRot"):  # bAbsoluteRotation: the rotation is the actor's frame, not the bone's (only the location follows the bone)
                ride["rotation"] = absolute_seat_rotation(r["rot"])
                ride["rotationSpace"] = "model"
            pairs = clip_pairs(r.get("pairs") or [], pal.id)
            if pairs:
                ride["pairs"] = pairs
            pal.extras["bossRide"] = {"boss": boss.id, **ride}
            boss.extras["mount"] = {"pal": pal.id, **ride}  # same seat, so a boss page can place the Pal without fetching the Pal manifest
            # what the boss holds while riding (children of the marker in the GYM blueprint, on the boss's own sockets: Lily's spear)
            held = [self._held_item(p) for p in r.get("held") or []]
            if held:
                boss.extras["heldItem"], boss.extras["heldItems"] = held[0], held

    def pals(self) -> list[ModelRef]:
        return [r for r in self.all() if r.kind == "pal"]

    def by_kind(self, kind: str | None) -> list[ModelRef]:
        return self.all() if kind is None else [r for r in self.all() if r.kind == kind]

    def find(self, mid: str) -> ModelRef | None:
        for r in self.all():
            if r.id.lower() == mid.lower():
                return r
        return None

    def import_ref(self, ref: ModelRef, with_anim: bool = True) -> Model:
        from ..importers.palworld.importer import import_pal, import_weapon

        if ref.kind == "object":
            return import_weapon(self.raw, os.path.basename(ref.folder), model_id=ref.id, display_name=ref.display_name)
        if ref.kind == "human":
            return import_pal(self.raw, ref.id, model_id=ref.id, display_name=ref.display_name, with_anim=with_anim, sub="palworld_humans", kind="human")
        return import_pal(self.raw, ref.id, model_id=ref.id, display_name=ref.display_name, with_anim=with_anim)
