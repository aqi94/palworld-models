"""Unreal -> glTF space conversion.

Unreal is left-handed, Z up, centimetres, and Palworld meshes face +Y in mesh space. glTF is
right-handed, Y up, metres, models facing +Z. Swapping Y and Z does all of it at once: up becomes +Y,
the +Y facing becomes +Z, and the swap is a reflection, which is exactly the handedness change.

For a reflection M (det -1) a rotation quaternion (v, w) becomes (-M v, w).
"""

from __future__ import annotations

import numpy as np

CM = 0.01  # Unreal units are centimetres


def vec(v) -> np.ndarray:
    """Positions / translations / normals: (..., 3) Unreal -> glTF (no scaling)."""
    v = np.asarray(v, dtype=np.float32)
    return v[..., [0, 2, 1]]


def pos(v) -> np.ndarray:
    return vec(v) * CM


def quat(q) -> np.ndarray:
    """(..., 4) x,y,z,w Unreal -> glTF."""
    q = np.asarray(q, dtype=np.float32)
    out = np.empty_like(q)
    out[..., 0] = -q[..., 0]
    out[..., 1] = -q[..., 2]
    out[..., 2] = -q[..., 1]
    out[..., 3] = q[..., 3]
    return out


def scale(s) -> np.ndarray:
    s = np.asarray(s, dtype=np.float32)
    return s[..., [0, 2, 1]]


def quat_to_mat(q) -> np.ndarray:
    x, y, z, w = (float(c) for c in q)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def trs_matrix(t, r, s) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3] = quat_to_mat(r) * np.asarray(s, dtype=float)[None, :]
    m[:3, 3] = t
    return m
