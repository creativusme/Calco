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
    photo_size: int = 2560  # longest side of the undistorted photos (texture detail)
    dense_size: int = 1000  # longest side used for dense matching (geometry detail)
    dense_iterations: int = 3
    dense_views: int = 0  # photos that get a depth map (0 = all); the rest still help texturing
    refine: bool = False
    splat_steps: int = 7000  # Gaussian splat training (optional, GPU)
    splat_size: int = 1600  # longest side of the photos the splat is trained on
    target_faces: int = 300_000
    texture_size: int = 4096


QUALITY = {
    q.name: q
    for q in (
        Quality("draft", "Draft", "LiDAR only, about a minute. Good to check coverage, ±1 cm.", "lidar",
                target_faces=150_000, texture_size=2048),
        Quality("medium", "Medium", "Photogrammetry at 1000 px. A few minutes.", "photo",
                sfm_image_size=1600, photo_size=2560, dense_size=1000, dense_iterations=3, dense_views=60,
                splat_steps=7000, splat_size=1600, target_faces=400_000, texture_size=4096),
        Quality("high", "High", "Photogrammetry at 2000 px. The everyday choice for parts.", "photo",
                sfm_image_size=2400, sfm_features=8000, photo_size=4096, dense_size=2000, dense_iterations=5, dense_views=120,
                splat_steps=15000, splat_size=2000, target_faces=1_000_000, texture_size=8192),
        Quality("ultra", "Ultra", "Photogrammetry at 3200 px plus mesh refinement. Can take hours.", "photo",
                sfm_image_size=3200, sfm_features=10000, photo_size=4096, dense_size=3200, dense_iterations=5,
                splat_steps=30000, splat_size=2400, refine=True, target_faces=2_500_000, texture_size=8192),
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
