"""Export a finished model to the formats other programs want."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

from .presets import EXPORT_FORMATS, UNIT_SCALE

Y_UP_TO_Z_UP = np.array([[1.0, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]])
# formats whose users expect +Y up (DCC tools); the others are written +Z up (CAD, slicers)
Y_UP_FORMATS = {"glb", "obj"}
DEFAULT_UNITS = {"glb": "m", "obj": "m", "ply": "m", "stl": "mm", "3mf": "mm", "dxf": "mm"}


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
    if fmt == "dxf":
        path = out_dir / f"{name}-sections.dxf"
        write_sections_dxf(trimesh.Trimesh(mesh.vertices, mesh.faces, process=False), path, units)
    elif fmt == "ply":
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


def _nice_step(span: float, count: int = 10) -> float:
    raw = span / count
    mag = 10 ** np.floor(np.log10(raw))
    return float(min((m * mag for m in (1, 2, 5, 10)), key=lambda v: abs(v - raw)))


def write_sections_dxf(mesh: trimesh.Trimesh, path: Path, units: str = "mm") -> Path:
    """Cross-sections as 2D lines for CAD sketches (Fusion: Insert > DXF, pick a layer).

    Layers: Z_<height> (horizontal slices at round heights, seen from above), and X_MID / Y_MID
    (vertical sections through the middle, drawn in their own plane: X_MID uses (y, z), Y_MID (x, z)).
    """
    lo, hi = mesh.bounds
    step = _nice_step(hi[2] - lo[2])
    cuts = []  # (layer, origin, normal, which 2 coordinates to draw)
    for z in np.arange(np.ceil(lo[2] / step) * step, hi[2], step):
        if lo[2] < z < hi[2]:
            cuts.append((f"Z_{z:g}", [0, 0, z], [0, 0, 1], (0, 1)))
    mid = (lo + hi) / 2
    cuts += [("X_MID", [mid[0], 0, 0], [1, 0, 0], (1, 2)), ("Y_MID", [0, mid[1], 0], [0, 1, 0], (0, 2))]
    code = {"m": 6, "cm": 5, "mm": 4}[units]
    out = ["0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1009", "9", "$INSUNITS", "70", str(code),
           "0", "ENDSEC",
           "0", "SECTION", "2", "TABLES", "0", "TABLE", "2", "LAYER", "70", str(len(cuts))]
    for k, (layer, *_ ) in enumerate(cuts):
        out += ["0", "LAYER", "2", layer, "70", "0", "62", str(1 + k % 7), "6", "CONTINUOUS"]
    out += ["0", "ENDTAB", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
    for layer, origin, normal, (a, b) in cuts:
        segs = trimesh.intersections.mesh_plane(mesh, np.array(normal, float), np.array(origin, float))
        for p, q in segs:
            out += ["0", "LINE", "8", layer, "10", f"{p[a]:.4f}", "20", f"{p[b]:.4f}", "30", "0",
                    "11", f"{q[a]:.4f}", "21", f"{q[b]:.4f}", "31", "0"]
    out += ["0", "ENDSEC", "0", "EOF"]
    path.write_text("\n".join(out) + "\n", encoding="ascii")
    return path
