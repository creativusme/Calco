"""Quality presets (how hard the engine works) and export profiles (what the result is for)."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Quality:
    name: str
    label: str
    hint: str
    method: str  # "lidar" = depth fusion only, "photo" = photogrammetry
    sfm_image_size: int = 1600  # longest side used for feature extraction
    sfm_features: int = 6000
    mvs_resolution_level: int = 2  # images are halved this many times for dense matching
    mvs_max_resolution: int = 2560
    refine: bool = False
    target_faces: int = 300_000
    texture_size: int = 4096


QUALITY = {
    q.name: q
    for q in (
        Quality("draft", "Draft", "LiDAR only, about a minute. Good to check coverage, ±1 cm.", "lidar",
                target_faces=150_000, texture_size=2048),
        Quality("medium", "Medium", "Photogrammetry at reduced resolution. A few minutes.", "photo",
                sfm_image_size=1600, mvs_resolution_level=2, target_faces=400_000, texture_size=4096),
        Quality("high", "High", "Photogrammetry at half resolution. The everyday choice for parts.", "photo",
                sfm_image_size=2400, sfm_features=8000, mvs_resolution_level=1, mvs_max_resolution=3200,
                target_faces=1_000_000, texture_size=8192),
        Quality("ultra", "Ultra", "Full resolution plus mesh refinement. Can take hours.", "photo",
                sfm_image_size=3200, sfm_features=10000, mvs_resolution_level=0, mvs_max_resolution=4096,
                refine=True, target_faces=2_500_000, texture_size=8192),
    )
}


@dataclass(frozen=True)
class ExportProfile:
    name: str
    label: str
    hint: str
    formats: tuple[str, ...]
    units: str = "m"
    textured: bool = True


PROFILES = {
    p.name: p
    for p in (
        ExportProfile("print", "3D print", "Closed mesh in millimetres, no texture.", ("stl", "3mf"), "mm", False),
        ExportProfile("blender", "Blender / render", "Textured mesh in metres.", ("glb", "obj"), "m", True),
        ExportProfile("cad", "CAD (Fusion)", "Clean mesh in millimetres for reverse engineering.", ("obj", "stl"), "mm", False),
        ExportProfile("web", "Web / AR", "Single-file GLB.", ("glb",), "m", True),
        ExportProfile("points", "Point cloud", "Coloured vertices as PLY.", ("ply",), "m", False),
    )
}

EXPORT_FORMATS = ("glb", "obj", "stl", "3mf", "ply")
UNIT_SCALE = {"m": 1.0, "cm": 100.0, "mm": 1000.0}


def describe() -> dict:
    return {
        "quality": [asdict(q) for q in QUALITY.values()],
        "profiles": [asdict(p) for p in PROFILES.values()],
        "formats": list(EXPORT_FORMATS),
        "units": list(UNIT_SCALE),
    }
