"""Dense reconstruction and texturing.

Geometry comes from COLMAP's patch-match stereo on the GPU when the CUDA build is installed,
otherwise from OpenMVS on the CPU. Texturing always uses OpenMVS.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import tools
from .presets import Quality
from .tools import openmvs_bin, run_tool

GPU_DENSE = True  # COLMAP CUDA depth maps; set False to always use OpenMVS on the CPU


def _mvs(name: str, args: list, cwd: Path, log) -> None:
    exe = openmvs_bin(name)
    if exe is None:
        raise RuntimeError(f"OpenMVS tool {name} is not installed. Run: calco tools install")
    run_tool(exe, [*args, "-w", cwd, "-v", "1"], cwd, log)


def _scene(dense_dir: Path, mvs: Path, log) -> None:
    """Cameras and photos in OpenMVS format (needed for refinement and texturing)."""
    _mvs("InterfaceCOLMAP", ["-i", dense_dir, "-o", mvs / "scene.mvs", "--image-folder", dense_dir / "images"], mvs, log)


def _part_box(rec, box, log):
    """The part inside the crop box: sparse points above the support plane, robust extent.

    Boxes placed on the phone are often much bigger than the part; the support plane (the
    densest horizontal plane among the points) tells the part from the table it stands on.
    """
    from .geom import fit_plane_ransac

    xyz = np.array([p.xyz for p in rec.points3D.values()])
    xyz = xyz[box.contains(xyz)]
    if len(xyz) < 500:
        return box
    plane = fit_plane_ransac(xyz, 0.004, prefer_normal=np.array([0, 1.0, 0]), max_angle_deg=20)
    if plane is None:
        return box
    n, d, _ = plane
    if n[1] < 0:
        n, d = -n, -d
    part = xyz[xyz @ n + d > 0.006]
    if len(part) < 200:
        return box
    c, s = np.cos(box.yaw), np.sin(box.yaw)
    rot = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])  # box axes in the world
    from scipy.spatial import cKDTree

    # the full extent of the points that have neighbours (edges of a part are sparse, so no percentiles)
    k = cKDTree(part).query(part, k=min(9, len(part)))[0][:, -1]
    part = part[k < 3 * np.median(k)]
    local = (part - box.center) @ rot
    lo, hi = local.min(0) - 0.02, local.max(0) + 0.02
    lo[1] = min(lo[1], float(((-d * n) - box.center) @ rot[:, 1]))  # down to the support
    size = np.minimum(hi - lo, box.size)
    centre = box.center + rot @ ((lo + hi) / 2)
    tight = box.__class__(centre, size, box.yaw)
    if np.prod(size) < 0.8 * np.prod(box.size):
        log("Part inside the crop box: " + " x ".join(f"{v * 1000:.0f}" for v in size) + " mm")
    return tight


def crop_to_box(dense_dir: Path, box, log, margin: float = 0.15, min_gain: float = 0.8) -> int:
    """Crop every undistorted photo to where the region of interest projects, in place.

    Patch-match time grows with the pixels and its size limit applies to the whole photo, so
    the background costs time and steals resolution from the part. After cropping, the same
    pixel budget lands on the object. Each photo gets its own pinhole camera (shifted principal
    point); the model is rewritten in COLMAP's text format. Returns the number of photos cropped.
    """
    import cv2
    import pycolmap

    def g(v) -> str:  # plain decimal text (numpy scalars would print as np.float64(...))
        return f"{float(v):.17g}"

    rec = pycolmap.Reconstruction(dense_dir / "sparse")
    box = _part_box(rec, box, log)
    big = box.__class__(box.center, box.size * (1 + 2 * margin), box.yaw)
    corners = np.c_[big.corners(), np.ones(8)]
    cams, imgs, cropped, saved = [], [], 0, 0.0
    for img in sorted(rec.images.values(), key=lambda i: i.image_id):
        cam = rec.cameras[img.camera_id]
        fx, fy, cx, cy = (float(v) for v in cam.params[:4])
        w, h = cam.width, cam.height
        E = img.cam_from_world().matrix()
        z = (corners @ E.T)[:, 2]
        x0, y0, x1, y1 = 0, 0, w, h
        if np.all(z > 1e-3):  # the whole box in front of the camera: crop to its outline
            uv = (corners @ E.T)[:, :2] / z[:, None] * [fx, fy] + [cx, cy]
            x0, y0 = max(0, int(np.floor(uv[:, 0].min()))), max(0, int(np.floor(uv[:, 1].min())))
            x1, y1 = min(w, int(np.ceil(uv[:, 0].max()))), min(h, int(np.ceil(uv[:, 1].max())))
            if x1 - x0 < 64 or y1 - y0 < 64 or (x1 - x0) * (y1 - y0) > min_gain * w * h:
                x0, y0, x1, y1 = 0, 0, w, h  # box not seen, or too little to gain
        if (x0, y0, x1, y1) != (0, 0, w, h):
            path = dense_dir / "images" / img.name
            im = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            cv2.imwrite(str(path), im[y0:y1, x0:x1], [cv2.IMWRITE_JPEG_QUALITY, 95])
            cropped += 1
            saved += 1 - (x1 - x0) * (y1 - y0) / (w * h)
        cams.append(f"{img.image_id} PINHOLE {x1 - x0} {y1 - y0} {g(fx)} {g(fy)} {g(cx - x0)} {g(cy - y0)}")
        q = img.cam_from_world().rotation.quat  # x, y, z, w
        t = img.cam_from_world().translation
        pts = " ".join(f"{g(p.xy[0] - x0)} {g(p.xy[1] - y0)} {p.point3D_id if p.has_point3D() else -1}"
                       for p in img.points2D)
        imgs.append(f"{img.image_id} {g(q[3])} {g(q[0])} {g(q[1])} {g(q[2])} {g(t[0])} {g(t[1])} {g(t[2])} "
                    f"{img.image_id} {img.name}\n{pts}")
    sparse = dense_dir / "sparse"
    points = [f"{pid} {g(p.xyz[0])} {g(p.xyz[1])} {g(p.xyz[2])} {p.color[0]} {p.color[1]} {p.color[2]} {g(p.error)} "
              + " ".join(f"{e.image_id} {e.point2D_idx}" for e in p.track.elements)
              for pid, p in rec.points3D.items()]
    for old in sparse.glob("*.bin"):
        old.unlink()
    (sparse / "cameras.txt").write_text("\n".join(cams) + "\n", encoding="utf-8")
    (sparse / "images.txt").write_text("\n".join(imgs) + "\n", encoding="utf-8")
    (sparse / "points3D.txt").write_text("\n".join(points) + "\n", encoding="utf-8")
    if cropped:
        log(f"Cropped {cropped} photos to the crop box (on average {100 * saved / cropped:.0f}% of the pixels "
            "were background)")
    return cropped


def _limit_views(dense_dir: Path, quality: Quality, score: dict | None, log) -> int:
    """Compute depth maps for a well-spread subset of the photos only (time grows with every photo)."""
    from .select import spread_subset

    names = sorted(p.name for p in (dense_dir / "images").iterdir())
    chosen = spread_subset(names, quality.dense_views, score)
    stereo = dense_dir / "stereo"
    if len(chosen) == len(names):
        sources = {n: "__auto__, 8" for n in chosen}
    else:
        # source photos must come from the subset too: the geometric pass needs their depth maps
        import pycolmap

        rec = pycolmap.Reconstruction(dense_dir / "sparse")
        seen = {img.name: {p.point3D_id for p in img.points2D if p.has_point3D()} for img in rec.images.values()}
        sources = {}
        for n in chosen:
            mine = seen.get(n, set())
            shared = sorted(((len(mine & seen.get(m, set())), m) for m in chosen if m != n), reverse=True)
            best = [m for k, m in shared[:8] if k > 0] or [m for _, m in shared[:8]]
            sources[n] = ", ".join(best)
    (stereo / "patch-match.cfg").write_text("".join(f"{n}\n{sources[n]}\n" for n in chosen), encoding="utf-8")
    (stereo / "fusion.cfg").write_text("".join(f"{n}\n" for n in chosen), encoding="utf-8")
    if len(chosen) < len(names):
        log(f"Depth maps for {len(chosen)} of {len(names)} photos (spread over the capture, sharpest first)")
    return len(chosen)


def _dense_gpu(dense_dir: Path, mvs: Path, quality: Quality, log, progress, score=None, geometric: bool = True) -> Path:
    exe = tools.colmap_bin()
    progress(0.03, "Computing depth maps (GPU)")
    n_views = max(1, _limit_views(dense_dir, quality, score, log))
    seen = [0]

    def views(line: str) -> None:  # two passes over all photos: photometric, then geometric
        if "Processing view" in line:
            seen[0] += 1
            done = min(seen[0] / ((2 if geometric else 1) * n_views), 1.0)
            progress(0.03 + 0.66 * done, f"Computing depth maps (GPU) {round(done * 100)}%")

    # window_step 2 evaluates every other pixel of the matching window: about twice as fast,
    # no visible loss at these resolutions. Source images per view are capped when undistorting.
    run_tool(exe, ["patch_match_stereo", "--workspace_path", dense_dir,
                   "--PatchMatchStereo.max_image_size", quality.dense_size,
                   "--PatchMatchStereo.num_iterations", quality.dense_iterations,
                   "--PatchMatchStereo.window_step", 2,
                   "--PatchMatchStereo.geom_consistency", int(geometric), "--PatchMatchStereo.cache_size", 16,
                   # a photo whose first-pass depth map is missing must not abort the whole run
                   "--PatchMatchStereo.allow_missing_files", 1], dense_dir, log, on_line=views)
    progress(0.7, "Fusing depth maps")
    run_tool(exe, ["stereo_fusion", "--workspace_path", dense_dir, "--output_path", dense_dir / "fused.ply",
                   "--input_type", "geometric" if geometric else "photometric",
                   "--StereoFusion.max_image_size", quality.dense_size], dense_dir, log)
    if not (dense_dir / "fused.ply").exists():
        raise RuntimeError("COLMAP produced no dense points")
    # OpenMVS imports fused.ply with its visibility and meshes it about twice as fast as COLMAP's mesher
    progress(0.8, "Building mesh")
    _scene(dense_dir, mvs, log)
    _mvs("ReconstructMesh", ["-i", "scene.mvs", "-o", "mesh.ply", "--crop-to-roi", 0, "--remove-spurious", 30],
         mvs, log)
    for dmap in mvs.glob("depth*.dmap"):  # COLMAP depth maps converted on import; not needed any more
        dmap.unlink()
    return mvs / "mesh.ply"


def _dense_cpu(dense_dir: Path, mvs: Path, quality: Quality, log, progress) -> Path:
    _scene(dense_dir, mvs, log)
    progress(0.05, "Computing depth maps (CPU)")
    _mvs("DensifyPointCloud", ["-i", "scene.mvs", "-o", "scene_dense.mvs", "--resolution-level", 0,
                               "--max-resolution", quality.dense_size, "--min-resolution", 320,
                               "--iters", quality.dense_iterations], mvs, log)
    progress(0.75, "Building mesh")
    _mvs("ReconstructMesh", ["-i", "scene_dense.mvs", "-p", "scene_dense.ply", "-o", "mesh.ply",
                             "--crop-to-roi", 0, "--remove-spurious", 30], mvs, log)
    return mvs / "mesh.ply"


def dense_mesh(dense_dir: Path, work: Path, quality: Quality, log=print, progress=lambda f, m="": None,
               use_gpu: bool = True, score: dict | None = None, crop=None) -> tuple[Path, str]:
    """Undistorted COLMAP model -> raw mesh. Returns (PLY path, "gpu" or "cpu").

    crop: region of interest (CropBox, reconstruction frame); the photos are cropped to it first.
    """
    if crop is not None:
        crop_to_box(dense_dir, crop, log)
    mvs = work / "mvs"
    mvs.mkdir(parents=True, exist_ok=True)
    for old in mvs.glob("*"):
        if old.is_file():
            old.unlink()

    mesh, computed_on = None, "cpu"
    if GPU_DENSE and use_gpu and tools.colmap_bin() and tools.have_gpu():
        try:
            mesh, computed_on = _dense_gpu(dense_dir, mvs, quality, log, progress, score), "gpu"
        except RuntimeError as e:
            # the CPU path can take hours on a big capture: first try the GPU once more, single pass
            log(f"GPU dense reconstruction failed ({e}); retrying on the GPU without the second pass")
            try:
                mesh, computed_on = _dense_gpu(dense_dir, mvs, quality, log, progress, score, geometric=False), "gpu"
            except RuntimeError as e2:
                log(f"GPU dense reconstruction failed again, using the CPU instead: {e2}")
    if mesh is None:
        mesh = _dense_cpu(dense_dir, mvs, quality, log, progress)

    if quality.refine:
        progress(0.9, "Refining mesh")
        scene = "scene_dense.mvs" if (mvs / "scene_dense.mvs").exists() else "scene.mvs"
        _mvs("RefineMesh", ["-i", scene, "-m", mesh.name, "-o", "mesh_refined.ply", "--max-views", 8,
                            "--scales", 2], mvs, log)
        if (mvs / "mesh_refined.ply").exists():
            mesh = mvs / "mesh_refined.ply"
    progress(1.0, "Mesh built")
    return mesh, computed_on


def texture_mesh(work: Path, mesh_ply: Path, quality: Quality, log=print) -> Path:
    """Texture a (cleaned) mesh from the photos. Returns the OBJ path (with .mtl and texture)."""
    mvs = work / "mvs"
    out = mvs / "textured.obj"
    scene = "scene_dense.mvs" if (mvs / "scene_dense.mvs").exists() else "scene.mvs"
    _mvs("TextureMesh", ["-i", scene, "-m", mesh_ply, "-o", out, "--export-type", "obj",
                         "--max-texture-size", quality.texture_size, "--empty-color", 0x808080,
                         "--close-holes", 0, "--decimate", 1,
                         # OpenMVS 2.4.0 seam leveling paints black blobs inside the patches: keep it off
                         "--global-seam-leveling", 0, "--local-seam-leveling", 0], mvs, log)
    if not out.exists():
        raise RuntimeError("OpenMVS texturing produced no output")
    return out
