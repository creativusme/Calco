"""LiDAR depth fusion: phone depth maps + ARKit poses -> coloured mesh. Fast, metric, ±1 cm."""

from __future__ import annotations

import cv2
import numpy as np
import open3d as o3d

from .bundle import Bundle
from .meshops import Mesh


def fuse_depth(bundle: Bundle, progress=lambda f, m="": None) -> Mesh:
    frames = [f for f in bundle.frames if f.pose is not None and f.depth_file is not None]
    if len(frames) < 3:
        raise RuntimeError("LiDAR fusion needs at least 3 frames with depth and pose")

    crop = bundle.crop_box
    if crop is not None:
        voxel = float(np.clip(crop.size.max() / 300.0, 0.003, 0.012))
        max_depth = 4.0
    else:
        voxel = 0.02 if bundle.mode == "space" else 0.006
        max_depth = 5.0 if bundle.mode == "space" else 2.5
    trunc_mult = max(4.0, 0.015 / voxel)
    dev = o3d.core.Device("CPU:0")
    grid = o3d.t.geometry.VoxelBlockGrid(
        attr_names=("tsdf", "weight", "color"),
        attr_dtypes=(o3d.core.float32, o3d.core.float32, o3d.core.float32),
        attr_channels=((1), (1), (3)), voxel_size=voxel, block_resolution=8, block_count=60000, device=dev)

    for i, f in enumerate(frames):
        depth = f.load_depth(min_confidence=1)
        dw, dh = f.depth_size
        fx, fy, cx, cy = f.depth_K
        w2c = f.depth_world_to_cam_cv
        depth[depth > max_depth] = 0

        if crop is not None:  # drop everything that back-projects outside the region of interest
            v, u = np.mgrid[0:dh, 0:dw]
            z = depth.reshape(-1)
            cam = np.stack([(u.reshape(-1) - cx) / fx * z, (v.reshape(-1) - cy) / fy * z, z], 1)
            c2w = np.linalg.inv(w2c)
            world = cam @ c2w[:3, :3].T + c2w[:3, 3]
            inside = crop.contains(world, margin=0.15 * float(crop.size.max()))
            depth = np.where(inside.reshape(dh, dw), depth, 0).astype(np.float32)

        color = cv2.cvtColor(cv2.imread(str(f.image)), cv2.COLOR_BGR2RGB)
        color = cv2.resize(color, (dw, dh), interpolation=cv2.INTER_AREA)
        K = o3d.core.Tensor(np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]]))
        E = o3d.core.Tensor(w2c)
        d_img = o3d.t.geometry.Image(np.ascontiguousarray(depth))
        c_img = o3d.t.geometry.Image(np.ascontiguousarray(color.astype(np.float32) / 255.0))
        blocks = grid.compute_unique_block_coordinates(d_img, K, E, 1.0, max_depth, trunc_mult)
        if blocks.shape[0] == 0:
            continue
        grid.integrate(blocks, d_img, c_img, K, E, 1.0, max_depth, trunc_mult)
        progress((i + 1) / len(frames), f"Fusing depth {i + 1}/{len(frames)}")

    mesh = grid.extract_triangle_mesh(weight_threshold=1.5).to_legacy()
    if len(mesh.triangles) == 0:
        raise RuntimeError("LiDAR fusion produced an empty mesh (is the crop box around the object?)")
    return Mesh.from_o3d(mesh)
