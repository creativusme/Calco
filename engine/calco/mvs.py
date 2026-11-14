"""Dense reconstruction and texturing.

Geometry comes from COLMAP's patch-match stereo on the GPU when the CUDA build is installed,
otherwise from OpenMVS on the CPU. Texturing always uses OpenMVS.
"""

from __future__ import annotations

from pathlib import Path

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


def _limit_views(dense_dir: Path, quality: Quality, score: dict | None, log) -> int:
    """Compute depth maps for a well-spread subset of the photos only (time grows with every photo)."""
    from .select import spread_subset

    names = sorted(p.name for p in (dense_dir / "images").iterdir())
    chosen = spread_subset(names, quality.dense_views, score)
    stereo = dense_dir / "stereo"
    (stereo / "patch-match.cfg").write_text("".join(f"{n}\n__auto__, 8\n" for n in chosen), encoding="utf-8")
    (stereo / "fusion.cfg").write_text("".join(f"{n}\n" for n in chosen), encoding="utf-8")
    if len(chosen) < len(names):
        log(f"Depth maps for {len(chosen)} of {len(names)} photos (spread over the capture, sharpest first)")
    return len(chosen)


def _dense_gpu(dense_dir: Path, mvs: Path, quality: Quality, log, progress, score=None) -> Path:
    exe = tools.colmap_bin()
    progress(0.03, "Computing depth maps (GPU)")
    n_views = max(1, _limit_views(dense_dir, quality, score, log))
    seen = [0]

    def views(line: str) -> None:  # two passes over all photos: photometric, then geometric
        if "Processing view" in line:
            seen[0] += 1
            done = min(seen[0] / (2 * n_views), 1.0)
            progress(0.03 + 0.66 * done, f"Computing depth maps (GPU) {round(done * 100)}%")

    # window_step 2 evaluates every other pixel of the matching window: about twice as fast,
    # no visible loss at these resolutions. Source images per view are capped when undistorting.
    run_tool(exe, ["patch_match_stereo", "--workspace_path", dense_dir,
                   "--PatchMatchStereo.max_image_size", quality.dense_size,
                   "--PatchMatchStereo.num_iterations", quality.dense_iterations,
                   "--PatchMatchStereo.window_step", 2,
                   "--PatchMatchStereo.geom_consistency", 1, "--PatchMatchStereo.cache_size", 16], dense_dir, log,
             on_line=views)
    progress(0.7, "Fusing depth maps")
    run_tool(exe, ["stereo_fusion", "--workspace_path", dense_dir, "--output_path", dense_dir / "fused.ply",
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
               use_gpu: bool = True, score: dict | None = None) -> tuple[Path, str]:
    """Undistorted COLMAP model -> raw mesh. Returns (PLY path, "gpu" or "cpu")."""
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
            log(f"GPU dense reconstruction failed, using the CPU instead: {e}")
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
