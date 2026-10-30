"""Skew masks by edge rule ("edge protect"), computed outside Toolbag.

Toolbag's skew mask blends, per texel of the low mesh, between the averaged cage direction
(smooth cage, no gaps) and the mesh's own normals (exact projection, no skewing). Painting it
by hand is tedious. The edge rule needs no painting: full correction everywhere, averaged
directions only in a band along

* UV seams and open borders (``seam_px``),
* hard edges, where the vertex normals split (``hard_px``),
* soft edges whose faces bend by more than ``bend_deg`` (``bend_px``): rounded parts and soft
  bevels, where the face normals of neighbours differ and the projection would tear.

Widths are in mask pixels here; the server converts from bake pixels. Distances are measured
directly to the edge segments in UV space, so the band is exact to a fraction of a pixel.

Mask convention as in Toolbag: 255 = averaged (no correction), 0 = full correction. Row 0 is
at the top, where v = 1.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

import numpy as np

# Two normals or uvs count as equal below these differences.
_SAME_NORMAL = 0.9998
_SAME_UV = 1e-6


@dataclass
class MeshArrays:
    vertices: np.ndarray  # (n, 3)
    normals: np.ndarray   # (n, 3)
    uvs: np.ndarray       # (n, 2)
    triangles: np.ndarray  # (m, 3) vertex indices

    @classmethod
    def from_plugin(cls, data: dict) -> MeshArrays:
        """From one entry of the plugin action baker.low_mesh_data."""
        return cls(np.asarray(data["vertices"], np.float64).reshape(-1, 3),
                   np.asarray(data["normals"], np.float64).reshape(-1, 3),
                   np.asarray(data["uvs"], np.float64).reshape(-1, 2),
                   np.asarray(data["triangles"], np.int64).reshape(-1, 3))


@dataclass
class EdgeClasses:
    """Triangle sides as (corner_a, corner_b) vertex index pairs, per class. One side can be
    in several classes (a hard edge is usually a UV seam as well)."""
    seam: list = field(default_factory=list)
    hard: list = field(default_factory=list)
    bent: list = field(default_factory=list)


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(n == 0, 1, n)


def classify_edges(mesh: MeshArrays, bend_deg: float) -> EdgeClasses:
    """Sort the triangle sides of a mesh into seams, hard and bent edges. Vertices are split
    per uv and normal, so the sides are matched by position."""
    verts, normals, uvs, tris = mesh.vertices, mesh.normals, mesh.uvs, mesh.triangles
    scale = np.abs(verts).max() or 1.0
    keys = np.round(verts / scale * 1e5).astype(np.int64)
    _, pos_id = np.unique(keys, axis=0, return_inverse=True)
    pos_id = pos_id.ravel()
    p = verts[tris]
    face = _unit(np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]))
    flip = np.einsum("ij,ij->i", face, normals[tris].sum(axis=1)) < 0
    face[flip] *= -1
    sides: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
    for t, tri in enumerate(tris.tolist()):
        for a, b in ((0, 1), (1, 2), (2, 0)):
            ia, ib = tri[a], tri[b]
            key = ((pos_id[ia], pos_id[ib]) if pos_id[ia] < pos_id[ib]
                   else (pos_id[ib], pos_id[ia]))
            sides.setdefault(key, []).append((t, ia, ib))
    out = EdgeClasses()
    cos_bend = np.cos(np.radians(bend_deg))
    for items in sides.values():
        if len(items) == 1:  # open border: a seam in uv space and a hard edge in 3D
            out.seam.append(items[0][1:])
            out.hard.append(items[0][1:])
            continue
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                (t1, a1, b1), (t2, a2, b2) = items[i], items[j]
                if pos_id[a1] != pos_id[a2]:
                    a2, b2 = b2, a2
                pair = [(a1, b1), (a2, b2)]
                if (np.abs(uvs[a1] - uvs[a2]).max() > _SAME_UV
                        or np.abs(uvs[b1] - uvs[b2]).max() > _SAME_UV):
                    out.seam += pair
                soft = (np.dot(normals[a1], normals[a2]) > _SAME_NORMAL
                        and np.dot(normals[b1], normals[b2]) > _SAME_NORMAL)
                if not soft:
                    out.hard += pair
                elif np.dot(face[t1], face[t2]) < cos_bend:
                    out.bent += pair
    return out


def _uv_px(uvs: np.ndarray, size: int) -> np.ndarray:
    return np.stack([uvs[:, 0], 1.0 - uvs[:, 1]], axis=1) * size


def coverage(meshes: list[MeshArrays], size: int) -> np.ndarray:
    """bool [size, size]: pixels whose centre lies inside a uv triangle."""
    cov = np.zeros((size, size), bool)
    for mesh in meshes:
        uv = _uv_px(mesh.uvs, size)[mesh.triangles]
        for a, b, c in uv:
            x0 = max(int(np.floor(min(a[0], b[0], c[0]))), 0)
            y0 = max(int(np.floor(min(a[1], b[1], c[1]))), 0)
            x1 = min(int(np.ceil(max(a[0], b[0], c[0]))), size)
            y1 = min(int(np.ceil(max(a[1], b[1], c[1]))), size)
            if x1 <= x0 or y1 <= y0:
                continue
            den = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(den) < 1e-12:
                continue
            xs, ys = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
            w0 = ((b[1] - c[1]) * (xs - c[0]) + (c[0] - b[0]) * (ys - c[1])) / den
            w1 = ((c[1] - a[1]) * (xs - c[0]) + (a[0] - c[0]) * (ys - c[1])) / den
            cov[y0:y1, x0:x1] |= (w0 >= 0) & (w1 >= 0) & (1 - w0 - w1 >= 0)
    return cov


def _segment_distance(dist: np.ndarray, uv: np.ndarray, pairs: list, reach: float) -> None:
    """Lower dist to the distance (pixels) from each segment, within `reach` of it."""
    size = dist.shape[0]
    for ia, ib in pairs:
        a, b = uv[ia], uv[ib]
        x0 = max(int(np.floor(min(a[0], b[0]) - reach)), 0)
        y0 = max(int(np.floor(min(a[1], b[1]) - reach)), 0)
        x1 = min(int(np.ceil(max(a[0], b[0]) + reach)) + 1, size)
        y1 = min(int(np.ceil(max(a[1], b[1]) + reach)) + 1, size)
        if x1 <= x0 or y1 <= y0:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
        ab = b - a
        ll = float(ab @ ab)
        if ll < 1e-12:
            d = np.hypot(xs - a[0], ys - a[1])
        else:
            t = np.clip(((xs - a[0]) * ab[0] + (ys - a[1]) * ab[1]) / ll, 0, 1)
            d = np.hypot(xs - (a[0] + t * ab[0]), ys - (a[1] + t * ab[1]))
        np.minimum(dist[y0:y1, x0:x1], d, out=dist[y0:y1, x0:x1])


def edge_mask(meshes: list[MeshArrays], size: int = 1024, seam_px: float = 2.0,
              hard_px: float = 2.0, bend_px: float = 2.0, bend_deg: float = 8.0,
              soft_px: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Mask (uint8 [size, size]) and coverage (bool) of one bake group. Widths in mask
    pixels, 0 switches a class off. A pixel is in a band when its centre is closer to the
    edge than width + 0.5; `soft_px` adds a linear falloff behind the band."""
    covered = coverage(meshes, size)
    if not covered.any():
        return np.full((size, size), 255, np.uint8), covered
    classes = [classify_edges(m, bend_deg) for m in meshes]
    averaged = np.zeros((size, size), np.float32)
    for which, width in (("seam", seam_px), ("hard", hard_px), ("bent", bend_px)):
        if width <= 0:
            continue
        reach = width + 0.5 + soft_px + 1
        dist = np.full((size, size), np.inf, np.float32)
        for mesh, edges in zip(meshes, classes, strict=True):
            _segment_distance(dist, _uv_px(mesh.uvs, size), getattr(edges, which), reach)
        if soft_px > 0:
            part = np.clip(1.0 - (dist - width - 0.5) / soft_px, 0, 1)
        else:
            part = (dist <= width + 0.5).astype(np.float32)
        np.maximum(averaged, part, out=averaged)
    mask = np.where(covered, np.round(averaged * 255), 255).astype(np.uint8)
    return mask, covered


def write_png_gray(path: str, img: np.ndarray) -> None:
    """8 bit greyscale PNG with the standard library only."""
    h, w = img.shape
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as handle:
        handle.write(png)
