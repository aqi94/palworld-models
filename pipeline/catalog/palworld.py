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
        self._refs: list[ModelRef] | None = None

    # -- names ---------------------------------------------------------------------------
    def _name(self, code: str) -> str | None:
        n = self.names.get(f"PAL_NAME_{code}")
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
        p = self.params.get(base) or {}
        name = self._name(base) or base
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
                form = d["skin"] or (code[len(d["base"]) + 1 :] if code != d["base"] else "")
                refs.append(ModelRef(game="palworld", kind="pal", id=code, folder=os.path.join(root, code), display_name=d["name"],
                                     species=d["base"], form=form,
                                     extras={k: d[k] for k in ("dex", "dexSuffix", "elements", "rarity", "codeName", "skin") if d.get(k) not in (None, "", [])}))
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
        if not os.path.isfile(held_path):
            return []
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
        names = sorted({p["item"] for h in held for p in (h.get("parts") or [{"item": h["weapon"]}]) if not p.get("mounted")})
        return [ModelRef(game="palworld", kind="object", id=f"Weapon_{n}", folder=os.path.join(root, n), display_name=n, species="Weapon", form=n)
                for n in names if os.path.isfile(os.path.join(root, n, "pal.json"))]

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
        return import_pal(self.raw, ref.id, model_id=ref.id, display_name=ref.display_name, with_anim=with_anim)
