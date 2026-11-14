"""A View is one calibrated, undistorted photo: everything the later stages need to project into it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .bundle import Bundle


@dataclass
class View:
    name: str
    image: Path
    K: np.ndarray  # 3x3 pinhole intrinsics
    w2c: np.ndarray  # 4x4 world-to-camera, OpenCV convention
    width: int
    height: int

    @property
    def P(self) -> np.ndarray:
        return self.K @ self.w2c[:3]

    @property
    def center(self) -> np.ndarray:
        return -self.w2c[:3, :3].T @ self.w2c[:3, 3]

    @property
    def forward(self) -> np.ndarray:
        return self.w2c[2, :3]

    @property
    def up(self) -> np.ndarray:
        return -self.w2c[1, :3]


def views_from_bundle(bundle: Bundle) -> list[View]:
    """Views straight from the phone's poses (no refinement). ARKit images are already rectified."""
    return [
        View(f.image.name, f.image, f.K, f.world_to_cam_cv, f.width, f.height)
        for f in bundle.frames
        if f.pose is not None
    ]


def focus_point(views: list[View]) -> np.ndarray:
    """Point closest to all optical axes: what the cameras were looking at."""
    A, b = np.zeros((3, 3)), np.zeros(3)
    for v in views:
        d = v.forward / np.linalg.norm(v.forward)
        m = np.eye(3) - np.outer(d, d)
        A += m
        b += m @ v.center
    return np.linalg.lstsq(A, b, rcond=None)[0]
