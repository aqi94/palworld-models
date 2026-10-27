"""Reader for the extractor's output (raw/palworld/<Code>/pal.json + binaries, raw/materials, raw/textures)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

DTYPES = {"f4": np.float32, "u1": np.uint8, "u2": np.uint16, "u4": np.uint32}


@dataclass
class RawPal:
    root: str  # the raw/ folder
    code: str
    info: dict
    sub: str = "palworld"  # "palworld" (Pals) or "palworld_weapons" (held items)

    @property
    def folder(self) -> str:
        return os.path.join(self.root, self.sub, self.code)

    def lod_buffers(self, lod: int = 0) -> dict[str, np.ndarray]:
        L = self.info["lods"][lod]
        with open(os.path.join(self.folder, L["file"]), "rb") as f:
            data = f.read()
        out = {}
        for b in L["buffers"]:
            dt = DTYPES[b["dtype"]]
            n = int(np.prod(b["shape"]))
            out[b["name"]] = np.frombuffer(data, dt, count=n, offset=b["offset"]).reshape(b["shape"])
        return out

    def morph_targets(self, vertex_count: int) -> tuple[list[str], np.ndarray]:
        """LOD0 morph targets: names and dense (M, V, 3) float32 position deltas (Unreal space, cm; zero where a vertex is untouched)."""
        ms = self.info.get("morphs") or []
        if not ms:
            return [], np.zeros((0, vertex_count, 3), np.float32)
        with open(os.path.join(self.folder, "morphs.bin"), "rb") as f:
            data = f.read()
        out = np.zeros((len(ms), vertex_count, 3), np.float32)
        for k, m in enumerate(ms):
            n = m["count"]
            idx = np.frombuffer(data, np.uint32, count=n, offset=m["offset"])
            out[k, idx] = np.frombuffer(data, np.float32, count=3 * n, offset=m["offset"] + 4 * n).reshape(n, 3)
        return [m["name"] for m in ms], out

    def anim_frames(self, anim: dict) -> dict[str, np.ndarray]:
        """bone name -> (frames, 10) float32: quat xyzw, translation xyz, scale xyz (Unreal space)."""
        with open(os.path.join(self.folder, anim["file"]), "rb") as f:
            data = f.read()
        n = anim["frames"] * 10
        return {t["bone"]: np.frombuffer(data, np.float32, count=n, offset=t["offset"]).reshape(-1, 10) for t in anim["tracks"]}

    def material(self, path: str) -> dict | None:
        return load_material(self.root, path)

    def texture_png(self, path: str) -> str | None:
        p = os.path.join(self.root, "textures", *path.split("/")) + ".png"
        return p if os.path.isfile(p) else None

    def texture_meta(self, path: str) -> dict:
        p = os.path.join(self.root, "textures", *path.split("/")) + ".json"
        if not os.path.isfile(p):
            return {}
        with open(p, encoding="utf-8") as f:
            return json.load(f)


def load_pal(root: str, code: str, sub: str = "palworld") -> RawPal:
    with open(os.path.join(root, sub, code, "pal.json"), encoding="utf-8") as f:
        return RawPal(root, code, json.load(f), sub)


@lru_cache(maxsize=4096)
def _load_json(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_material(root: str, path: str) -> dict | None:
    p = os.path.join(root, "materials", *path.split("/")) + ".json"
    if not os.path.isfile(p):
        return None
    d = _load_json(p)
    return d[0] if isinstance(d, list) else d
