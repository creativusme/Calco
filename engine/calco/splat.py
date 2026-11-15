"""Gaussian splat output: surfels trained on the photos (GPU), for a photoreal view of a result.

Optional and secondary: the mesh (what parts are measured and printed from) always comes from the
depth maps. Training runs in the separate splat environment (tools.splat_python()); this module
prepares its input and moves the result into the model frame.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from . import tools
from .bundle import CropBox
from .presets import Quality


def available() -> bool:
    return tools.splat_python() is not None and tools.have_gpu()


def _scene(dense_dir: Path, crop: CropBox | None) -> dict:
    import pycolmap

    rec = pycolmap.Reconstruction(dense_dir / "sparse")
    views = []
    for img in sorted(rec.images.values(), key=lambda i: i.name):
        cam = rec.cameras[img.camera_id]  # undistorted: PINHOLE
        fx, fy, cx, cy = (float(v) for v in cam.params[:4])
        E = np.eye(4)
        E[:3] = img.cam_from_world().matrix()
        views.append({"image": str(dense_dir / "images" / img.name), "width": cam.width, "height": cam.height,
                      "K": [[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]], "w2c": E.tolist()})
    xyz = np.array([p.xyz for p in rec.points3D.values()])
    rgb = np.array([p.color for p in rec.points3D.values()], float)
    if crop is not None:
        centre = crop.center
    else:
        centre = np.median(xyz, axis=0)
    pts = np.concatenate([xyz, rgb], 1).tolist()
    return {"views": views, "points": pts, "roi": {"center": [float(c) for c in centre]}}


def train(dense_dir: Path, work: Path, quality: Quality, crop: CropBox | None, log=print,
          progress=lambda f, m="": None) -> Path:
    """Train surfels on the undistorted photos. Returns the output folder (splat.ply)."""
    out = work / "splat"
    out.mkdir(parents=True, exist_ok=True)
    scene = _scene(dense_dir, crop)
    (out / "scene.json").write_text(json.dumps(scene), encoding="utf-8")

    def step(line: str) -> None:
        m = re.match(r"STEP (\d+)/(\d+)", line)
        if m:
            f = int(m.group(1)) / int(m.group(2))
            progress(0.02 + 0.96 * f, f"Training splat {round(f * 100)}%")

    progress(0.01, "Training splat")
    script = Path(__file__).with_name("splat_train.py")
    tools.run_tool(tools.splat_python(), ["-I", "-u", script, "--scene", out / "scene.json", "--out", out,
                                           "--steps", quality.splat_steps, "--max-side", quality.splat_size,
                                           "--sh-degree", 1], out, log, on_line=step)
    if not (out / "splat.ply").exists():
        raise RuntimeError("Splat training produced no output")
    return out


def transform_splat(src: Path, dst: Path, T: np.ndarray, keep: CropBox | None = None) -> int:
    """Move a splat PLY (as written by splat_train.py, SH degree <= 1) by a similarity T (4x4).

    Returns the number of splats. Means, scales, rotations and the first SH band follow T.
    """
    from scipy.spatial.transform import Rotation

    raw = src.read_bytes()
    end = raw.index(b"end_header\n") + len(b"end_header\n")
    header = raw[:end].decode()
    cols = [ln.split()[-1] for ln in header.splitlines() if ln.startswith("property")]
    data = np.frombuffer(raw[end:], dtype="<f4").reshape(-1, len(cols)).copy()
    idx = {c: i for i, c in enumerate(cols)}
    if keep is not None:  # the object and some surroundings, not the whole street
        data = data[keep.contains(data[:, [idx["x"], idx["y"], idx["z"]]].astype(float),
                                  margin=0.5 * float(keep.size.max()))]
    s = float(np.cbrt(np.linalg.det(T[:3, :3])))
    R = T[:3, :3] / s
    xyz = [idx["x"], idx["y"], idx["z"]]
    data[:, xyz] = data[:, xyz] @ (s * R).T + T[:3, 3]
    for k in range(3):
        data[:, idx[f"scale_{k}"]] += np.log(s)
    q = data[:, [idx[f"rot_{k}"] for k in range(4)]]  # w, x, y, z
    rot = Rotation.from_matrix(R) * Rotation.from_quat(q[:, [1, 2, 3, 0]])
    data[:, [idx[f"rot_{k}"] for k in range(4)]] = rot.as_quat()[:, [3, 0, 1, 2]]
    rest = [c for c in cols if c.startswith("f_rest_")]
    if len(rest) == 9:  # band 1: per channel a vector in the (-y, z, -x) basis
        P = np.array([[0, -1.0, 0], [0, 0, 1], [-1, 0, 0]])
        M = P @ R @ P.T
        for ch in range(3):
            sel = [idx[f"f_rest_{ch * 3 + j}"] for j in range(3)]
            data[:, sel] = data[:, sel] @ M.T
    elif rest:
        data[:, [idx[c] for c in rest]] = 0.0  # higher bands cannot follow a rotation here: keep plain colour
    header = re.sub(r"element vertex \d+", f"element vertex {len(data)}", header)
    dst.write_bytes(header.encode() + data.astype("<f4").tobytes())
    return len(data)
