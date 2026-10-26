"""Fit a held weapon's offset on a Pal's weapon bone from an animation where the Pal holds it (Unreal space), then print it as
glTF metres/xyzw for pipeline/catalog/held_offsets.json.

The game attaches weapons in code, so the grip is recovered geometrically: the weapon's long axis (+X in weapon space) is laid
along the line between the Pal's two weapon bones, and the weapon's two grip bones are put on those bones.

  python tools/fit_held_item.py ElecPanda Minigun AS_ElecPanda_Minigun_Idle box_handle_back box_handle_front
"""
import json
import sys

import numpy as np

sys.path.insert(0, ".")
from pipeline.importers.palworld import convert as cv  # noqa: E402
from pipeline.importers.palworld.raw import load_pal  # noqa: E402


def qm(q):
    x, y, z, w = np.asarray(q, float) / np.linalg.norm(q)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def worlds(pal, anim=None):
    fr = pal.anim_frames(anim) if anim else {}
    W = []
    for b in pal.info["bones"]:
        n = b["name"]
        t, q = (np.array(fr[n][0][4:7]), np.array(fr[n][0][:4])) if n in fr else (np.array(b["t"]), np.array(b["r"]))
        M = np.eye(4); M[:3, :3] = qm(q); M[:3, 3] = t
        W.append(W[b["parent"]] @ M if b["parent"] >= 0 else M)
    return {b["name"]: W[i] for i, b in enumerate(pal.info["bones"])}


def quat(m):
    t = np.trace(m)
    if t > 0:
        s = np.sqrt(t + 1) * 2; return np.array([(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, s / 4])
    i = int(np.argmax(np.diag(m))); j, k = (i + 1) % 3, (i + 2) % 3
    s = np.sqrt(1 + m[i, i] - m[j, j] - m[k, k]) * 2
    q = np.zeros(4); q[i] = s / 4; q[3] = (m[k, j] - m[j, k]) / s; q[j] = (m[j, i] + m[i, j]) / s; q[k] = (m[k, i] + m[i, k]) / s
    return q


def main(code, weapon, anim_name, grip_a, grip_b, root="raw"):
    pal = load_pal(root, code)
    anim = next(a for a in pal.info["anims"] if a["name"] == anim_name)
    P = worlds(pal, anim)
    G = worlds(load_pal(root, weapon, "palworld_weapons"))
    # gun axis +X -> the line weapon_r -> weapon_l ... in the order the grips are named (a at weapon_r, b at weapon_l)
    pr, pl = P["weapon_r"][:3, 3], P["weapon_l"][:3, 3]
    ga, gb = G[grip_a][:3, 3], G[grip_b][:3, 3]
    fwd = pl - pr; fwd[2] = 0; fwd /= np.linalg.norm(fwd)
    up = np.array([0, 0, 1.0])
    side = np.cross(up, fwd)  # left-handed UE: x=forward, y=right, z=up
    # weapon axes in Pal space: x->fwd, z->up, y = z cross x
    Rg = np.stack([fwd, np.cross(up, fwd), up], axis=1)
    ghx = (ga + gb) / 2
    T = (pr + pl) / 2 - Rg @ ghx
    Wg = np.eye(4); Wg[:3, :3] = Rg; Wg[:3, 3] = T
    O = np.linalg.inv(P["weapon_r"]) @ Wg
    t, q = O[:3, 3], quat(O[:3, :3])
    out = {"position": [round(float(x), 5) for x in cv.pos(t)], "rotation": [round(float(x), 6) for x in cv.quat(q)]}
    print(json.dumps({weapon: out}))
    return out


if __name__ == "__main__":
    main(*sys.argv[1:6])
