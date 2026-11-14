"""Dense reconstruction and texturing with OpenMVS (external executables, see tools.py)."""

from __future__ import annotations

from pathlib import Path

from .presets import Quality
from .tools import openmvs_bin, run_tool


def _run(name: str, args: list, cwd: Path, log) -> None:
    exe = openmvs_bin(name)
    if exe is None:
        raise RuntimeError(f"OpenMVS tool {name} is not installed. Run: calco tools install")
    run_tool(exe, [*args, "-w", cwd, "-v", "1"], cwd, log)


def dense_mesh(dense_dir: Path, work: Path, quality: Quality, log=print, progress=lambda f, m="": None) -> Path:
    """Undistorted COLMAP model -> dense point cloud -> raw mesh (PLY path)."""
    mvs = work / "mvs"
    mvs.mkdir(parents=True, exist_ok=True)
    for old in mvs.glob("*"):
        if old.is_file():
            old.unlink()

    progress(0.02, "Preparing dense reconstruction")
    _run("InterfaceCOLMAP", ["-i", dense_dir, "-o", mvs / "scene.mvs", "--image-folder", dense_dir / "images"], mvs, log)

    progress(0.05, "Computing depth maps")
    _run("DensifyPointCloud", ["-i", "scene.mvs", "-o", "scene_dense.mvs",
                               "--resolution-level", quality.mvs_resolution_level,
                               "--max-resolution", quality.mvs_max_resolution,
                               "--min-resolution", 480], mvs, log)

    progress(0.7, "Building mesh")
    _run("ReconstructMesh", ["-i", "scene_dense.mvs", "-p", "scene_dense.ply", "-o", "mesh.ply",
                             "--crop-to-roi", 0, "--remove-spurious", 30], mvs, log)
    mesh = mvs / "mesh.ply"

    if quality.refine:
        progress(0.85, "Refining mesh")
        _run("RefineMesh", ["-i", "scene_dense.mvs", "-m", "mesh.ply", "-o", "mesh_refined.ply",
                            "--resolution-level", quality.mvs_resolution_level, "--scales", 2], mvs, log)
        if (mvs / "mesh_refined.ply").exists():
            mesh = mvs / "mesh_refined.ply"
    progress(1.0, "Mesh built")
    return mesh


def texture_mesh(work: Path, mesh_ply: Path, quality: Quality, log=print) -> Path:
    """Texture a (cleaned) mesh from the photos. Returns the OBJ path (with .mtl and texture)."""
    mvs = work / "mvs"
    out = mvs / "textured.obj"
    _run("TextureMesh", ["-i", "scene_dense.mvs", "-m", mesh_ply, "-o", out, "--export-type", "obj",
                         "--max-texture-size", quality.texture_size, "--empty-color", 0x808080,
                         "--close-holes", 0, "--decimate", 1,
                         # OpenMVS 2.4.0 seam leveling paints black blobs inside the patches: keep it off
                         "--global-seam-leveling", 0, "--local-seam-leveling", 0], mvs, log)
    if not out.exists():
        raise RuntimeError("OpenMVS texturing produced no output")
    return out
