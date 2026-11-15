"""Export a finished model to the formats other programs want."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

from .presets import EXPORT_FORMATS, UNIT_SCALE

Y_UP_TO_Z_UP = np.array([[1.0, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]])
# formats whose users expect +Y up (DCC tools); the others are written +Z up (CAD, slicers)
Y_UP_FORMATS = {"glb", "obj", "usdz"}
DEFAULT_UNITS = {"glb": "m", "obj": "m", "ply": "m", "stl": "mm", "3mf": "mm", "dxf": "mm", "usdz": "m"}


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
    if fmt in ("glb", "usdz"):
        units = "m"  # glTF is metres by specification; USDZ is written in metres for AR Quick Look
    mesh = load_model(result_dir)
    mesh.apply_scale(UNIT_SCALE[units] * user_scale)
    if fmt in Y_UP_FORMATS:
        mesh.apply_transform(np.linalg.inv(Y_UP_TO_Z_UP))

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.{fmt}"
    if fmt == "usdz":
        write_usdz(mesh, path, textured)
    elif fmt == "dxf":
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


def write_usdz(mesh: trimesh.Trimesh, path: Path, textured: bool = True) -> Path:
    """USDZ for Apple AR Quick Look and Reality tools: Y up, metres, one mesh, optional photo texture."""
    import shutil
    import tempfile

    from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, UsdUtils, Vt

    tmp = Path(tempfile.mkdtemp())
    try:
        stage_path = tmp / "model.usdc"
        stage = Usd.Stage.CreateNew(str(stage_path))
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        root = UsdGeom.Xform.Define(stage, "/Model")
        stage.SetDefaultPrim(root.GetPrim())
        geo = UsdGeom.Mesh.Define(stage, "/Model/Scan")
        geo.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(np.asarray(mesh.vertices, np.float32)))
        geo.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(len(mesh.faces), 3, np.int32)))
        geo.CreateFaceVertexIndicesAttr(Vt.IntArray.FromNumpy(np.asarray(mesh.faces, np.int32).reshape(-1)))
        geo.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(np.asarray(mesh.vertex_normals, np.float32)))
        geo.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
        geo.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
        lo, hi = mesh.bounds
        geo.CreateExtentAttr([Gf.Vec3f(*map(float, lo)), Gf.Vec3f(*map(float, hi))])

        material = UsdShade.Material.Define(stage, "/Model/Material")
        shader = UsdShade.Shader.Define(stage, "/Model/Material/Surface")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(1.0)
        shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
        image = getattr(mesh.visual.material, "baseColorTexture", None) or getattr(mesh.visual.material, "image", None) \
            if textured and hasattr(mesh.visual, "uv") and mesh.visual.uv is not None else None
        if image is not None:
            image.convert("RGB").save(tmp / "texture.jpg", quality=92)
            st = UsdGeom.PrimvarsAPI(geo).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)
            st.Set(Vt.Vec2fArray.FromNumpy(np.asarray(mesh.visual.uv, np.float32)))
            reader = UsdShade.Shader.Define(stage, "/Model/Material/StReader")
            reader.CreateIdAttr("UsdPrimvarReader_float2")
            reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
            tex = UsdShade.Shader.Define(stage, "/Model/Material/Texture")
            tex.CreateIdAttr("UsdUVTexture")
            tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set("texture.jpg")
            tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(reader.ConnectableAPI(), "result")
            tex.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
            shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(tex.ConnectableAPI(), "rgb")
        else:
            shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.64, 0.65, 0.67))
        material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        UsdShade.MaterialBindingAPI.Apply(geo.GetPrim()).Bind(material)
        stage.GetRootLayer().Save()
        if path.exists():
            path.unlink()
        if not UsdUtils.CreateNewARKitUsdzPackage(str(stage_path), str(path)):
            raise RuntimeError("USDZ packaging failed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return path
