"""Read and validate capture bundles (docs/capture-format.md)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SUPPORTED_VERSIONS = {1}

# ARKit camera (x right, y up, looks down -z) -> OpenCV camera (x right, y down, looks down +z)
_FLIP_YZ = np.diag([1.0, -1.0, -1.0, 1.0])


class BundleError(ValueError):
    pass


@dataclass
class Frame:
    id: int
    image: Path
    width: int
    height: int
    intrinsics: tuple[float, float, float, float]  # fx, fy, cx, cy in pixels
    pose: np.ndarray | None  # 4x4 camera-to-world, ARKit convention
    depth_file: Path | None = None
    confidence_file: Path | None = None
    depth_size: tuple[int, int] | None = None  # width, height
    depth_pose: np.ndarray | None = None  # pose of the depth map when it is not the photo's
    depth_intrinsics: tuple[float, float, float, float] | None = None  # in depth pixels
    tracking: str = "normal"
    sharpness: float | None = None

    @property
    def K(self) -> np.ndarray:
        fx, fy, cx, cy = self.intrinsics
        return np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]])

    @property
    def world_to_cam_cv(self) -> np.ndarray:
        """4x4 world-to-camera in the OpenCV convention (used for projection)."""
        return np.linalg.inv(self.pose @ _FLIP_YZ)

    @property
    def center(self) -> np.ndarray:
        return self.pose[:3, 3]

    @property
    def depth_world_to_cam_cv(self) -> np.ndarray:
        return np.linalg.inv((self.depth_pose if self.depth_pose is not None else self.pose) @ _FLIP_YZ)

    @property
    def depth_K(self) -> tuple[float, float, float, float]:
        """fx, fy, cx, cy for the depth map."""
        if self.depth_intrinsics is not None:
            return self.depth_intrinsics
        sx, sy = self.depth_size[0] / self.width, self.depth_size[1] / self.height
        fx, fy, cx, cy = self.intrinsics
        return fx * sx, fy * sy, cx * sx, cy * sy

    def load_depth(self, min_confidence: int = 1) -> np.ndarray | None:
        """Depth in metres as float32 (H, W); invalid pixels are 0."""
        if self.depth_file is None:
            return None
        w, h = self.depth_size
        depth = np.fromfile(self.depth_file, dtype="<f4").reshape(h, w).copy()
        depth[~np.isfinite(depth)] = 0
        if self.confidence_file is not None and self.confidence_file.exists():
            conf = np.fromfile(self.confidence_file, dtype=np.uint8).reshape(h, w)
            depth[conf < min_confidence] = 0
        return depth


@dataclass
class CropBox:
    center: np.ndarray
    size: np.ndarray
    yaw: float = 0.0  # rotation about world +Y, radians

    def contains(self, pts: np.ndarray, margin: float = 0.0) -> np.ndarray:
        c, s = np.cos(-self.yaw), np.sin(-self.yaw)
        d = pts - self.center
        x = c * d[:, 0] + s * d[:, 2]
        z = -s * d[:, 0] + c * d[:, 2]
        half = self.size / 2 + margin
        return (np.abs(x) <= half[0]) & (np.abs(d[:, 1]) <= half[1]) & (np.abs(z) <= half[2])

    def extended_down(self, amount: float) -> "CropBox":
        """The same box reaching `amount` metres further down (world -Y).

        On the phone the box sits where the user aims; aiming at the top of an object puts the
        box base on that top. Extending it downwards lets the engine find the real support surface.
        """
        size = self.size.copy()
        size[1] += amount
        centre = self.center.copy()
        centre[1] -= amount / 2
        return CropBox(centre, size, self.yaw)

    def corners(self) -> np.ndarray:
        h = self.size / 2
        signs = np.array([[i, j, k] for i in (-1, 1) for j in (-1, 1) for k in (-1, 1)], float)
        local = signs * h
        c, s = np.cos(self.yaw), np.sin(self.yaw)
        rot = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
        return local @ rot.T + self.center


@dataclass
class Bundle:
    path: Path
    manifest: dict
    frames: list[Frame] = field(default_factory=list)
    crop_box: CropBox | None = None

    @property
    def id(self) -> str:
        return self.manifest["id"]

    @property
    def name(self) -> str:
        return self.manifest.get("name") or self.id

    @property
    def mode(self) -> str:
        return self.manifest.get("mode", "object")

    @property
    def has_poses(self) -> bool:
        posed = sum(f.pose is not None for f in self.frames)
        return posed >= max(3, int(0.8 * len(self.frames)))

    @property
    def has_depth(self) -> bool:
        return sum(f.depth_file is not None for f in self.frames) >= 3

    def summary(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "mode": self.mode,
            "created": self.manifest.get("created"),
            "frames": len(self.frames),
            "has_poses": self.has_poses,
            "has_depth": self.has_depth,
            "has_crop_box": self.crop_box is not None,
            "device": self.manifest.get("device", {}).get("model"),
            "resolution": [self.frames[0].width, self.frames[0].height] if self.frames else None,
        }


def load_bundle(path: str | Path) -> Bundle:
    path = Path(path)
    mf = path / "manifest.json"
    if not mf.exists():
        raise BundleError(f"{path}: manifest.json not found")
    try:
        manifest = json.loads(mf.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BundleError(f"{mf}: invalid JSON ({e})") from e
    if manifest.get("format") != "calco-capture":
        raise BundleError(f"{mf}: not a Calco capture (format={manifest.get('format')!r})")
    if manifest.get("version") not in SUPPORTED_VERSIONS:
        raise BundleError(f"{mf}: unsupported version {manifest.get('version')!r}")

    frames = []
    for raw in manifest.get("frames", []):
        image = path / raw["image"]
        if not image.exists():
            raise BundleError(f"frame {raw.get('id')}: missing image {raw['image']}")
        pose = raw.get("pose")
        if pose is not None:
            pose = np.asarray(pose, dtype=np.float64).reshape(4, 4)
            if not np.all(np.isfinite(pose)):
                pose = None
        d = raw.get("depth")
        depth_file = path / d["file"] if d else None
        if depth_file is not None and not depth_file.exists():
            depth_file, d = None, None
        frames.append(
            Frame(
                id=int(raw["id"]),
                image=image,
                width=int(raw["width"]),
                height=int(raw["height"]),
                intrinsics=tuple(float(v) for v in raw["intrinsics"]),
                pose=pose,
                depth_file=depth_file,
                confidence_file=(path / d["confidence"]) if d and d.get("confidence") else None,
                depth_size=(int(d["width"]), int(d["height"])) if d else None,
                depth_pose=np.asarray(d["pose"], dtype=np.float64).reshape(4, 4) if d and d.get("pose") else None,
                depth_intrinsics=tuple(float(v) for v in d["intrinsics"]) if d and d.get("intrinsics") else None,
                tracking=raw.get("tracking", "normal"),
                sharpness=raw.get("sharpness"),
            )
        )
    if len(frames) < 3:
        raise BundleError(f"{path}: a capture needs at least 3 frames, found {len(frames)}")

    cb = manifest.get("crop_box")
    crop = CropBox(np.array(cb["center"], float), np.array(cb["size"], float), float(cb.get("yaw", 0))) if cb else None
    return Bundle(path=path, manifest=manifest, frames=frames, crop_box=crop)
