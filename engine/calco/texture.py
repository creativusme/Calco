"""Built-in texture baker: UV-unwrap a mesh and blend the photos into one texture.

Used for LiDAR meshes and as a fallback when OpenMVS is not installed. Every texel is
coloured by the photos that see it most frontally, with an occlusion test per photo.
"""

from __future__ import annotations

import cv2
import numpy as np
import open3d as o3d
import trimesh
import xatlas

from .meshops import Mesh
from .views import View


def _sample_bilinear(img: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    u0 = np.clip(np.floor(u).astype(np.int32), 0, img.shape[1] - 2)
    v0 = np.clip(np.floor(v).astype(np.int32), 0, img.shape[0] - 2)
    fu = (u - u0)[:, None].astype(np.float32)
    fv = (v - v0)[:, None].astype(np.float32)
    a, b = img[v0, u0], img[v0, u0 + 1]
    c, d = img[v0 + 1, u0], img[v0 + 1, u0 + 1]
    return (a * (1 - fu) + b * fu) * (1 - fv) + (c * (1 - fu) + d * fu) * fv


def bake_texture(mesh: Mesh, views: list[View], size: int = 4096,
                 progress=lambda f, m="": None) -> trimesh.Trimesh:
    progress(0.0, "Unwrapping UVs")
    vmap, faces, uvs = xatlas.parametrize(mesh.v.astype(np.float32), mesh.f.astype(np.uint32))
    verts = mesh.v[vmap]
    faces = faces.astype(np.int64)
    normals = mesh.vertex_normals()[vmap]

    # which face covers each texel: ray-cast the UV layout from above
    uv_scene = o3d.t.geometry.RaycastingScene()
    uv3 = np.concatenate([uvs, np.zeros((len(uvs), 1), np.float32)], 1).astype(np.float32)
    uv_scene.add_triangles(o3d.core.Tensor(uv3), o3d.core.Tensor(faces.astype(np.uint32)))
    ty, tx = np.mgrid[0:size, 0:size]
    tu = (tx.reshape(-1) + 0.5) / size
    tv = (ty.reshape(-1) + 0.5) / size
    rays = np.stack([tu, tv, np.ones_like(tu), np.zeros_like(tu), np.zeros_like(tu), -np.ones_like(tu)], 1)
    hit = uv_scene.cast_rays(o3d.core.Tensor(rays.astype(np.float32)))
    prim = hit["primitive_ids"].numpy()
    covered = np.flatnonzero(np.isfinite(hit["t_hit"].numpy()))
    bary = hit["primitive_uvs"].numpy()[covered]
    tri = faces[prim[covered]]
    w0 = (1 - bary[:, 0] - bary[:, 1])[:, None]
    w1, w2 = bary[:, 0:1], bary[:, 1:2]
    pos = (verts[tri[:, 0]] * w0 + verts[tri[:, 1]] * w1 + verts[tri[:, 2]] * w2).astype(np.float32)
    nrm = normals[tri[:, 0]] * w0 + normals[tri[:, 1]] * w1 + normals[tri[:, 2]] * w2
    nrm = (nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)).astype(np.float32)

    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.core.Tensor(verts.astype(np.float32)), o3d.core.Tensor(faces.astype(np.uint32)))
    eps = 1e-3 * float(np.linalg.norm(verts.max(0) - verts.min(0)))

    acc = np.zeros((len(pos), 3), np.float32)
    wsum = np.zeros(len(pos), np.float32)
    for i, view in enumerate(views):
        R, t = view.w2c[:3, :3].astype(np.float32), view.w2c[:3, 3].astype(np.float32)
        cam = pos @ R.T + t
        z = cam[:, 2]
        ok = z > 1e-6
        u = np.where(ok, view.K[0, 0] * cam[:, 0] / np.where(ok, z, 1) + view.K[0, 2], -1)
        v = np.where(ok, view.K[1, 1] * cam[:, 1] / np.where(ok, z, 1) + view.K[1, 2], -1)
        border = 4
        ok &= (u >= border) & (u < view.width - 1 - border) & (v >= border) & (v < view.height - 1 - border)
        to_cam = view.center.astype(np.float32) - pos
        dist = np.linalg.norm(to_cam, axis=1)
        cosang = np.einsum("ij,ij->i", nrm, to_cam) / np.maximum(dist, 1e-9)
        ok &= cosang > 0.25
        idx = np.flatnonzero(ok)
        if len(idx) == 0:
            continue
        origin = pos[idx] + nrm[idx] * eps
        d = view.center.astype(np.float32) - origin
        length = np.linalg.norm(d, axis=1, keepdims=True)
        occ_rays = np.concatenate([origin, d / length], 1).astype(np.float32)
        occluded = scene.test_occlusions(o3d.core.Tensor(occ_rays), tnear=0.0, tfar=float(length.max())).numpy()
        idx = idx[~occluded]
        if len(idx) == 0:
            continue
        img = cv2.cvtColor(cv2.imread(str(view.image)), cv2.COLOR_BGR2RGB).astype(np.float32)
        weight = cosang[idx] ** 4
        acc[idx] += _sample_bilinear(img, u[idx], v[idx]) * weight[:, None]
        wsum[idx] += weight
        progress(0.1 + 0.85 * (i + 1) / len(views), f"Projecting photo {i + 1}/{len(views)}")

    tex = np.zeros((size * size, 3), np.float32)
    valid = np.zeros(size * size, np.float32)
    seen = wsum > 0
    tex[covered[seen]] = acc[seen] / wsum[seen, None]
    valid[covered[seen]] = 1
    tex, valid = tex.reshape(size, size, 3), valid.reshape(size, size)

    # grow colours into unseen texels and chart gutters so there are no dark seams
    for _ in range(12):
        if valid.all():
            break
        k = (5, 5)
        blur_c = cv2.blur(tex * valid[..., None], k)
        blur_w = cv2.blur(valid, k)
        grow = (valid == 0) & (blur_w > 1e-6)
        tex[grow] = blur_c[grow] / blur_w[grow][:, None]
        valid[grow] = 1
    tex[valid == 0] = 128

    image = np.clip(tex, 0, 255).astype(np.uint8)[::-1]  # image row 0 is v = 1
    from PIL import Image

    material = trimesh.visual.material.PBRMaterial(baseColorTexture=Image.fromarray(image),
                                                   metallicFactor=0.0, roughnessFactor=1.0)
    visual = trimesh.visual.TextureVisuals(uv=uvs, material=material)
    progress(1.0, "Texture baked")
    return trimesh.Trimesh(vertices=verts, faces=faces, visual=visual, process=False)
