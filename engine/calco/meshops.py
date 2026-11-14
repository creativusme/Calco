"""Mesh container and clean-up operations (crop, components, hole filling, decimation)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import open3d as o3d
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components


@dataclass
class Mesh:
    v: np.ndarray  # (N, 3) float64
    f: np.ndarray  # (M, 3) int
    c: np.ndarray | None = None  # (N, 3) vertex colours 0..1

    @staticmethod
    def from_o3d(m: o3d.geometry.TriangleMesh) -> "Mesh":
        c = np.asarray(m.vertex_colors)
        return Mesh(np.asarray(m.vertices).copy(), np.asarray(m.triangles).copy(),
                    c.copy() if len(c) == len(m.vertices) and len(c) else None)

    def to_o3d(self) -> o3d.geometry.TriangleMesh:
        m = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(self.v), o3d.utility.Vector3iVector(self.f))
        if self.c is not None:
            m.vertex_colors = o3d.utility.Vector3dVector(self.c)
        return m

    @staticmethod
    def read(path) -> "Mesh":
        m = o3d.io.read_triangle_mesh(str(path))
        if len(m.triangles) == 0:
            raise RuntimeError(f"{path}: no triangles")
        return Mesh.from_o3d(m)

    def write(self, path) -> None:
        o3d.io.write_triangle_mesh(str(path), self.to_o3d(), write_vertex_normals=False)

    def select_faces(self, face_mask: np.ndarray) -> "Mesh":
        f = self.f[face_mask]
        used = np.zeros(len(self.v), bool)
        used[f] = True
        remap = np.cumsum(used) - 1
        return Mesh(self.v[used], remap[f], self.c[used] if self.c is not None else None)

    def select_vertices(self, vertex_mask: np.ndarray) -> "Mesh":
        return self.select_faces(vertex_mask[self.f].all(1))

    def vertex_normals(self) -> np.ndarray:
        fn = np.cross(self.v[self.f[:, 1]] - self.v[self.f[:, 0]], self.v[self.f[:, 2]] - self.v[self.f[:, 0]])
        n = np.zeros_like(self.v)
        for k in range(3):
            np.add.at(n, self.f[:, k], fn)
        return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-20)


def boundary_vertices(mesh: Mesh) -> np.ndarray:
    """Mask of vertices that lie on an open edge."""
    e = np.sort(np.concatenate([mesh.f[:, [0, 1]], mesh.f[:, [1, 2]], mesh.f[:, [2, 0]]]), axis=1)
    uniq, counts = np.unique(e, axis=0, return_counts=True)
    mask = np.zeros(len(mesh.v), bool)
    mask[uniq[counts == 1].ravel()] = True
    return mask


def is_watertight(mesh: Mesh) -> bool:
    e = np.sort(np.concatenate([mesh.f[:, [0, 1]], mesh.f[:, [1, 2]], mesh.f[:, [2, 0]]]), axis=1)
    _, counts = np.unique(e, axis=0, return_counts=True)
    return bool(len(counts) and np.all(counts == 2))


def keep_largest(mesh: Mesh, min_ratio: float = 0.05) -> Mesh:
    """Keep the largest connected piece and any other piece at least min_ratio of its size."""
    n = len(mesh.v)
    rows = np.concatenate([mesh.f[:, 0], mesh.f[:, 1]])
    cols = np.concatenate([mesh.f[:, 1], mesh.f[:, 2]])
    graph = coo_matrix((np.ones(len(rows), bool), (rows, cols)), shape=(n, n))
    _, labels = connected_components(graph, directed=False)
    face_label = labels[mesh.f[:, 0]]
    sizes = np.bincount(face_label)
    good = np.flatnonzero(sizes >= max(1, min_ratio * sizes.max()))
    return mesh.select_faces(np.isin(face_label, good))


def drop_flat_pieces(mesh: Mesh, height: np.ndarray, min_height: float) -> Mesh:
    """Remove connected pieces that never rise min_height above the support plane (table leftovers)."""
    n = len(mesh.v)
    if n == 0:
        return mesh
    rows = np.concatenate([mesh.f[:, 0], mesh.f[:, 1]])
    cols = np.concatenate([mesh.f[:, 1], mesh.f[:, 2]])
    graph = coo_matrix((np.ones(len(rows), bool), (rows, cols)), shape=(n, n))
    _, labels = connected_components(graph, directed=False)
    top = np.full(labels.max() + 1, -np.inf)
    np.maximum.at(top, labels, height)
    keep = top[labels[mesh.f[:, 0]]] >= min_height
    return mesh.select_faces(keep) if keep.any() else mesh


def tidy(mesh: Mesh) -> Mesh:
    m = mesh.to_o3d()
    m.remove_duplicated_vertices()
    m.remove_degenerate_triangles()
    m.remove_duplicated_triangles()
    m.remove_non_manifold_edges()
    m.remove_unreferenced_vertices()
    return Mesh.from_o3d(m)


def decimate(mesh: Mesh, target_faces: int) -> Mesh:
    if len(mesh.f) <= target_faces:
        return mesh
    return tidy(Mesh.from_o3d(mesh.to_o3d().simplify_quadric_decimation(int(target_faces))))


def fill_holes(mesh: Mesh, max_hole_size: float) -> Mesh:
    """Close holes whose bounding-sphere radius is below max_hole_size (metres)."""
    t = o3d.t.geometry.TriangleMesh.from_legacy(mesh.to_o3d())
    filled = t.fill_holes(hole_size=float(max_hole_size)).to_legacy()
    out = Mesh.from_o3d(filled)
    if mesh.c is not None and out.c is None:
        out.c = mesh.c if len(out.v) == len(mesh.v) else None
    # the filler does not guarantee consistent winding with the neighbours
    m = out.to_o3d()
    m.orient_triangles()
    return Mesh.from_o3d(m) if len(m.triangles) == len(out.f) else out


def smooth(mesh: Mesh, iterations: int = 5) -> Mesh:
    m = mesh.to_o3d().filter_smooth_taubin(number_of_iterations=iterations)
    return Mesh(np.asarray(m.vertices).copy(), mesh.f, mesh.c)
