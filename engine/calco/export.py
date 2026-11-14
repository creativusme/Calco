"""Export a finished model to the formats other programs want."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

from .presets import EXPORT_FORMATS, UNIT_SCALE

Y_UP_TO_Z_UP = np.array([[1.0, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]])
# formats whose users expect +Y up (DCC tools); the others are written +Z up (CAD, slicers)
Y_UP_FORMATS = {"glb", "obj"}
DEFAULT_UNITS = {"glb": "m", "obj": "m", "ply": "m", "stl": "mm", "3mf": "mm"}


def load_model(result_dir: Path) -> trimesh.Trimesh:
    """The canonical result (model.glb, Y-up, metres) as a Z-up mesh in metres."""
    mesh = trimesh.load(result_dir / "model.glb", force="mesh", process=False)
    mesh.apply_transform(Y_UP_TO_Z_UP)
    return mesh


def export_model(result_dir: Path, out_dir: Path, name: str, fmt: str, units: str | None = None,
                 textured: bool = True, user_scale: float = 1.0) -> Path:
    """Write <name>.<fmt> (plus side files) into out_dir. user_scale is the caliper calibration."""
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"Unsupported format {fmt!r}. Supported: {', '.join(EXPORT_FORMATS)}")
    units = units or DEFAULT_UNITS[fmt]
    if fmt == "glb":
        units = "m"  # glTF is metres by specification
    mesh = load_model(result_dir)
    mesh.apply_scale(UNIT_SCALE[units] * user_scale)
    if fmt in Y_UP_FORMATS:
        mesh.apply_transform(np.linalg.inv(Y_UP_TO_Z_UP))

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.{fmt}"
    if fmt == "ply":
        colored = trimesh.Trimesh(mesh.vertices, mesh.faces, vertex_colors=mesh.visual.to_color().vertex_colors,
                                  process=False)
        colored.export(path)
    elif fmt in ("stl", "3mf") or not textured:
        solid = trimesh.Trimesh(mesh.vertices, mesh.faces, process=False)
        solid.merge_vertices()  # UV seams split vertices; slicers and CAD want one closed shell
        solid.export(path)
    else:
        mesh.export(path)
    return path
