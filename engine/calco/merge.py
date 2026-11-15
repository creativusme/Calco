"""Combine two scans of the same part (e.g. upright and turned over) into one model.

Each side was reconstructed and isolated on its own support plane, so each is open where the
part touched the table. The second side is aligned to the first by its shape: several turned-over
and rotated starting guesses, each refined with ICP, the best overlap wins; then the surfaces are
fused into one closed mesh (screened Poisson), keeping the scale of the first side.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import open3d as o3d
import trimesh

from .export import load_model

Y_UP = np.array([[1.0, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]])  # Z-up -> GLB Y-up


@dataclass
class Alignment:
    transform: np.ndarray  # 4x4, side B -> side A frame
    fitness: float  # fraction of B's points that found a partner on A
    rmse_mm: float


def _cloud(mesh: trimesh.Trimesh, voxel: float, drop_base: float) -> o3d.geometry.PointCloud:
    """Surface samples with normals, without the flat base cut at the support plane."""
    count = int(min(400_000, max(20_000, mesh.area / (voxel * voxel) * 4)))
    pts, faces = trimesh.sample.sample_surface_even(mesh, count) if count < 200_000 else trimesh.sample.sample_surface(mesh, count)
    normals = mesh.face_normals[faces]
    keep = pts[:, 2] > drop_base  # the flat cap / cut at z = 0 is not real surface
    pc = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(pts[keep]))
    pc.normals = o3d.utility.Vector3dVector(normals[keep])
    return pc


def _guesses(a: o3d.geometry.PointCloud, b: o3d.geometry.PointCloud) -> list[np.ndarray]:
    """Starting poses: B turned over about X or Y (or not), at 12 headings, centres matched."""
    ca, cb = a.get_center(), b.get_center()
    out = []
    flips = [np.eye(3), _rot([1, 0, 0], np.pi), _rot([0, 1, 0], np.pi)]
    for flip in flips:
        for yaw in np.radians(np.arange(0, 360, 30)):
            R = _rot([0, 0, 1], yaw) @ flip
            T = np.eye(4)
            T[:3, :3] = R
            T[:3, 3] = ca - R @ cb
            out.append(T)
    return out


def _rot(axis, angle) -> np.ndarray:
    return o3d.geometry.get_rotation_matrix_from_axis_angle(np.asarray(axis, float) * angle)


def align(a: o3d.geometry.PointCloud, b: o3d.geometry.PointCloud, voxel: float, log=print) -> Alignment:
    a_small, b_small = a.voxel_down_sample(voxel * 2), b.voxel_down_sample(voxel * 2)
    for pc in (a_small, b_small):
        pc.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=voxel * 6, max_nn=30))
    p2p = o3d.pipelines.registration.TransformationEstimationPointToPlane()

    candidates = _guesses(a_small, b_small)
    # global feature matching as one more guess (helps when the turn is not a clean 180 degrees)
    try:
        feat = lambda pc: o3d.pipelines.registration.compute_fpfh_feature(  # noqa: E731
            pc, o3d.geometry.KDTreeSearchParamHybrid(radius=voxel * 10, max_nn=100))
        ransac = o3d.pipelines.registration.registration_ransac_based_on_feature_matching(
            b_small, a_small, feat(b_small), feat(a_small), True, voxel * 3,
            o3d.pipelines.registration.TransformationEstimationPointToPoint(False), 3,
            [o3d.pipelines.registration.CorrespondenceCheckerBasedOnDistance(voxel * 3)],
            o3d.pipelines.registration.RANSACConvergenceCriteria(200_000, 0.999))
        candidates.append(ransac.transformation)
    except RuntimeError:
        pass

    best = None
    for T in candidates:  # coarse ICP from every guess, keep the best overlap
        r = o3d.pipelines.registration.registration_icp(
            b_small, a_small, voxel * 6, T, p2p, o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=40))
        score = r.fitness - 0.5 * r.inlier_rmse / voxel * 0.1
        if best is None or score > best[0]:
            best = (score, r.transformation)
    T = best[1]
    for dist in (voxel * 6, voxel * 3, voxel * 1.5):  # then progressively tighter on the full clouds
        r = o3d.pipelines.registration.registration_icp(
            b, a, dist, T, p2p, o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=60))
        T = r.transformation
    # overlap judged against the scan noise: a LiDAR surface is millimetres thick, a photo one is not
    rmse = r.inlier_rmse if r.fitness > 0 else voxel
    check = o3d.pipelines.registration.evaluate_registration(b, a, max(voxel * 4, 3 * rmse), T)
    log(f"Sides aligned: overlap {check.fitness * 100:.0f}% of side 2, distance {check.inlier_rmse * 1000:.2f} mm RMS")
    return Alignment(T, float(check.fitness), float(check.inlier_rmse * 1000))


def fuse(a: o3d.geometry.PointCloud, b: o3d.geometry.PointCloud, voxel: float, target_faces: int = 600_000,
         log=print) -> trimesh.Trimesh:
    """One closed surface through both point sets (screened Poisson), simplified to target_faces.

    The Poisson grid must match the point spacing: finer and it tears into pieces and holes,
    coarser and it loses detail. Start at the matching depth and step down until it is closed.
    """
    pc = (a + b).voxel_down_sample(voxel)
    size = float(np.max(pc.get_max_bound() - pc.get_min_bound()))
    depth = int(np.clip(round(np.log2(size * 1.1 / voxel)), 6, 11))
    best = None
    for d in range(depth, max(depth - 3, 5), -1):
        mesh, _ = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pc, depth=d, scale=1.1, linear_fit=True)
        labels, counts, _ = mesh.cluster_connected_triangles()  # keep the main piece only
        labels = np.asarray(labels)
        mesh.remove_triangles_by_mask(labels != int(np.argmax(np.asarray(counts))))
        mesh.remove_unreferenced_vertices()
        t = trimesh.Trimesh(np.asarray(mesh.vertices), np.asarray(mesh.triangles), process=True)
        best = (mesh, t)
        if t.is_watertight:
            break
        log(f"Fused surface at depth {d} is not closed, trying a coarser one")
    mesh, t = best
    if len(mesh.triangles) > target_faces:
        mesh = mesh.simplify_quadric_decimation(target_faces)
        t = trimesh.Trimesh(np.asarray(mesh.vertices), np.asarray(mesh.triangles), process=True)
    return t


def combine(result_a: Path, result_b: Path, out_dir: Path, log=print, progress=lambda f, m="": None) -> dict:
    """Align side B to side A, fuse them and write out_dir/result/model.glb plus both aligned sides."""
    t0 = time.time()
    out = Path(out_dir) / "result"
    out.mkdir(parents=True, exist_ok=True)
    ma, mb = load_model(Path(result_a)), load_model(Path(result_b))  # Z-up, metres, base on z = 0
    size = float(max(ma.extents.max(), mb.extents.max()))
    voxel = max(size / 350.0, 0.0002)  # ~0.25 mm on a 9 cm part, ~0.9 mm on a 30 cm one
    cut = 0.004 * size / 0.1 if size < 0.1 else 0.0015
    progress(0.05, "Sampling both sides")
    a, b = _cloud(ma, voxel, cut), _cloud(mb, voxel, cut)
    progress(0.2, "Aligning the two sides")
    al = align(a, b, voxel, log)
    if al.fitness < 0.25:
        raise RuntimeError(f"The two sides overlap too little ({al.fitness * 100:.0f}%) to be aligned. Scan both "
                           "sides so that the edges and sides of the part are visible in both.")
    b.transform(al.transform)
    progress(0.6, "Fusing the surfaces")
    merged = fuse(a, b, voxel, log=log)
    merged.vertices[:, 2] -= merged.vertices[:, 2].min()
    centre = (merged.vertices.min(0) + merged.vertices.max(0)) / 2
    merged.vertices[:, :2] -= centre[:2]
    shift = np.eye(4)
    shift[:2, 3] = -centre[:2]

    glb = merged.copy()
    glb.apply_transform(Y_UP)
    glb.export(out / "model.glb")
    # both sides as they were aligned, for checking or finishing by hand in another program
    ma2, mb2 = ma.copy(), mb.copy()
    mb2.apply_transform(al.transform)
    for m, name in ((ma2, "side1.ply"), (mb2, "side2.ply")):
        m.apply_transform(shift)
        trimesh.Trimesh(m.vertices, m.faces, process=False).export(out / name)
    np.save(out / "side2_to_side1.npy", al.transform)

    dims = (merged.vertices.max(0) - merged.vertices.min(0)) * 1000
    report = {
        "combined": {"overlap_pct": round(al.fitness * 100, 1), "distance_mm": round(al.rmse_mm, 3)},
        "model": {"vertices": int(len(merged.vertices)), "faces": int(len(merged.faces)),
                  "dimensions_mm": [round(float(d), 2) for d in dims], "watertight": bool(merged.is_watertight),
                  "textured": False},
        "seconds": round(time.time() - t0, 1),
    }
    progress(1.0, "Sides combined")
    return report
