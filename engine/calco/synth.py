"""Synthetic capture bundles: a known object on a scan mat, rendered by ray casting.

Used by the tests and for developing the pipeline without a phone. The object is a
80 x 60 mm box, 50 mm tall, with a 40 mm diameter, 30 mm tall cylinder on top.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import cv2
import numpy as np
import open3d as o3d

from .geom import look_at_arkit
from .mat import LAYOUTS, render_mat

OBJECT_SIZE_M = np.array([0.080, 0.080, 0.060])  # x, y (height), z extent of the synthetic object
_FLIP_YZ = np.diag([1.0, -1.0, -1.0, 1.0])


def _object_mesh() -> o3d.geometry.TriangleMesh:
    box = o3d.geometry.TriangleMesh.create_box(0.080, 0.050, 0.060)
    box.translate([-0.040, 0.0, -0.030])
    cyl = o3d.geometry.TriangleMesh.create_cylinder(radius=0.020, height=0.030, resolution=48)
    cyl.rotate(o3d.geometry.get_rotation_matrix_from_xyz([-np.pi / 2, 0, 0]), center=(0, 0, 0))
    cyl.translate([0, 0.065, 0])
    return box + cyl


def _ground_mesh(half: float = 1.5) -> o3d.geometry.TriangleMesh:
    v = np.array([[-half, 0, -half], [half, 0, -half], [half, 0, half], [-half, 0, half]])
    return o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(v), o3d.utility.Vector3iVector([[0, 2, 1], [0, 3, 2]]))


def _noise_color(p: np.ndarray) -> np.ndarray:
    """Blocky multi-scale value noise in [0, 1], (N, 3). Rich in corners for feature matching."""
    out = np.zeros((len(p), 3))
    q = p + 0.01373
    for cell, weight in ((0.004, 0.45), (0.013, 0.35), (0.045, 0.20)):
        i = np.floor(q / cell).astype(np.int64)
        h = (i[:, 0] * 73856093) ^ (i[:, 1] * 19349663) ^ (i[:, 2] * 83492791)
        for ch in range(3):
            hc = (h * (ch * 2 + 3) + ch * 1013904223) & 0xFFFFFF
            out[:, ch] += weight * (hc / float(0xFFFFFF))
    return out


def _render(scene, obj_id, ground_id, mat_img, mat_mm, K, w2c, width, height):
    rays = o3d.t.geometry.RaycastingScene.create_rays_pinhole(
        o3d.core.Tensor(K), o3d.core.Tensor(w2c), width, height)
    ans = scene.cast_rays(rays)
    r = rays.numpy().reshape(-1, 6)
    t = ans["t_hit"].numpy().reshape(-1)
    gid = ans["geometry_ids"].numpy().reshape(-1)
    nrm = ans["primitive_normals"].numpy().reshape(-1, 3)
    hit = np.isfinite(t)
    pts = r[:, :3] + r[:, 3:] * np.where(hit, t, 0)[:, None]

    color = np.full((len(t), 3), 0.75)
    obj = hit & (gid == obj_id)
    color[obj] = 0.15 + 0.8 * _noise_color(pts[obj])
    gnd = hit & (gid == ground_id)
    color[gnd] = 0.35 + 0.4 * _noise_color(pts[gnd])

    # mat texture on the ground: mat x = world x, mat y = -world z
    mw, mh = mat_mm[0] / 1000.0, mat_mm[1] / 1000.0
    on_mat = gnd & (np.abs(pts[:, 0]) < mw / 2) & (np.abs(pts[:, 2]) < mh / 2)
    u = (pts[on_mat, 0] + mw / 2) / mw * (mat_img.shape[1] - 1)
    v = (pts[on_mat, 2] + mh / 2) / mh * (mat_img.shape[0] - 1)
    u0, v0 = np.floor(u).astype(int), np.floor(v).astype(int)
    u1, v1 = np.minimum(u0 + 1, mat_img.shape[1] - 1), np.minimum(v0 + 1, mat_img.shape[0] - 1)
    fu, fv = u - u0, v - v0
    m = mat_img.astype(np.float64)
    sampled = (m[v0, u0] * (1 - fu) + m[v0, u1] * fu) * (1 - fv) + (m[v1, u0] * (1 - fu) + m[v1, u1] * fu) * fv
    color[on_mat] = (sampled.reshape(-1, 1) / 255.0) * 0.92 + 0.04

    light = np.array([0.35, 0.85, 0.4])
    light /= np.linalg.norm(light)
    shade = 0.7 + 0.3 * np.clip(np.abs(nrm @ light), 0, 1)
    color = np.where(hit[:, None], color * shade[:, None], color)

    cam_pts = pts @ w2c[:3, :3].T + w2c[:3, 3]
    depth = np.where(hit, cam_pts[:, 2], 0).astype(np.float32)
    rgb = (np.clip(color, 0, 1) * 255).astype(np.uint8).reshape(height, width, 3)
    return rgb, depth.reshape(height, width)


def make_synthetic_bundle(out_dir: str | Path, views_per_ring: int = 18, image_size=(1280, 960),
                          pose_noise_mm: float = 2.0, pose_noise_deg: float = 0.15,
                          depth_noise_mm: float = 3.0, with_mat: bool = True, seed: int = 1) -> Path:
    """Write a capture bundle to out_dir (should end in .calco) and return its path."""
    out = Path(out_dir)
    (out / "frames").mkdir(parents=True, exist_ok=True)
    (out / "depth").mkdir(exist_ok=True)
    rng = np.random.default_rng(seed)

    scene = o3d.t.geometry.RaycastingScene()
    obj_id = scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(_object_mesh()))
    ground_id = scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(_ground_mesh()))

    layout = LAYOUTS["A4"]
    mat_img = render_mat(layout, dpi=300) if with_mat else np.full((64, 64), 200, np.uint8)
    mat_mm = layout.page_mm if with_mat else (0.0, 0.0)

    W, H = image_size
    f = 0.75 * W
    K = np.array([[f, 0, W / 2], [0, f, H / 2], [0, 0, 1.0]])
    dw, dh = 256, 192
    Kd = K.copy()
    Kd[0] *= dw / W
    Kd[1] *= dh / H
    target = np.array([0.0, 0.035, 0.0])
    ss = 2  # supersampling

    frames = []
    idx = 0
    for elev_deg, radius in ((22.0, 0.36), (48.0, 0.36)):
        for k in range(views_per_ring):
            idx += 1
            az = 2 * np.pi * (k + 0.5 * (elev_deg > 30)) / views_per_ring
            el = np.radians(elev_deg)
            eye = target + radius * np.array([np.cos(el) * np.cos(az), np.sin(el), np.cos(el) * np.sin(az)])
            c2w = look_at_arkit(eye, target)
            w2c = np.linalg.inv(c2w @ _FLIP_YZ)

            Kss = K.copy()
            Kss[:2] *= ss
            rgb, _ = _render(scene, obj_id, ground_id, mat_img, mat_mm, Kss, w2c, W * ss, H * ss)
            rgb = cv2.resize(rgb, (W, H), interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(out / f"frames/{idx:06d}.jpg"), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR),
                        [cv2.IMWRITE_JPEG_QUALITY, 93])

            _, depth = _render(scene, obj_id, ground_id, mat_img, mat_mm, Kd, w2c, dw, dh)
            depth = depth + (depth > 0) * rng.normal(0, depth_noise_mm / 1000.0, depth.shape).astype(np.float32)
            depth.astype("<f4").tofile(out / f"depth/{idx:06d}.f32")
            np.full((dh, dw), 2, np.uint8).tofile(out / f"depth/{idx:06d}.u8")

            # what ARKit would report: the true pose plus a little drift
            noisy = c2w.copy()
            noisy[:3, 3] += rng.normal(0, pose_noise_mm / 1000.0, 3)
            rv = rng.normal(0, np.radians(pose_noise_deg), 3)
            noisy[:3, :3] = cv2.Rodrigues(rv)[0] @ noisy[:3, :3]

            frames.append({
                "id": idx, "image": f"frames/{idx:06d}.jpg", "width": W, "height": H,
                "timestamp": idx * 0.5, "intrinsics": [f, f, W / 2, H / 2],
                "pose": noisy.reshape(-1).tolist(), "tracking": "normal",
                "depth": {"file": f"depth/{idx:06d}.f32", "confidence": f"depth/{idx:06d}.u8",
                          "width": dw, "height": dh},
                "exposure": {"duration": 0.008, "iso": 80}, "sharpness": 1.0,
            })

    manifest = {
        "format": "calco-capture", "version": 1, "id": str(uuid.UUID(int=seed)),
        "name": "Synthetic box + cylinder", "created": "2026-01-01T00:00:00Z", "mode": "object",
        "device": {"model": "synthetic", "os": "-", "app": "synth"},
        "crop_box": {"center": [0.0, 0.045, 0.0], "size": [0.16, 0.12, 0.16], "yaw": 0.0},
        "frames": frames, "notes": [],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    cv2.imwrite(str(out / "preview.jpg"), cv2.resize(cv2.imread(str(out / "frames/000001.jpg")), (480, 360)))
    return out
