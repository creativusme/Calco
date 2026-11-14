"""Printable scan mat (AprilTag 36h11 grid): generation, detection and metric frame estimation.

Mat frame: origin at the page centre, +X right, +Y towards the top of the page, +Z out of the
paper. Units are metres. The mat gives scale, the support plane and a stable origin.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .geom import robust_umeyama, sim3_matrix, triangulate_dlt

_DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)


@dataclass(frozen=True)
class MatLayout:
    name: str
    page_mm: tuple[float, float]  # width, height (portrait)
    tag_mm: float
    gap_mm: float
    first_id: int
    margin_mm: float = 12.0
    footer_mm: float = 14.0

    @property
    def grid(self) -> tuple[int, int]:
        w, h = self.page_mm
        pitch = self.tag_mm + self.gap_mm
        cols = int((w - 2 * self.margin_mm + self.gap_mm) // pitch)
        rows = int((h - 2 * self.margin_mm - self.footer_mm + self.gap_mm) // pitch)
        return cols, rows

    def tag_corners_page_mm(self) -> dict[int, np.ndarray]:
        """Tag id -> (4, 2) corners in page mm (origin top-left, y down), order TL, TR, BR, BL."""
        w, h = self.page_mm
        cols, rows = self.grid
        pitch = self.tag_mm + self.gap_mm
        grid_w = cols * self.tag_mm + (cols - 1) * self.gap_mm
        grid_h = rows * self.tag_mm + (rows - 1) * self.gap_mm
        x0 = (w - grid_w) / 2
        y0 = self.margin_mm + (h - 2 * self.margin_mm - self.footer_mm - grid_h) / 2
        out = {}
        for r in range(rows):
            for c in range(cols):
                x, y, t = x0 + c * pitch, y0 + r * pitch, self.tag_mm
                out[self.first_id + r * cols + c] = np.array([[x, y], [x + t, y], [x + t, y + t], [x, y + t]])
        return out

    def tag_corners_mat(self) -> dict[int, np.ndarray]:
        """Tag id -> (4, 3) corners in the mat frame, metres."""
        w, h = self.page_mm
        out = {}
        for tid, c in self.tag_corners_page_mm().items():
            xyz = np.zeros((4, 3))
            xyz[:, 0] = (c[:, 0] - w / 2) / 1000.0
            xyz[:, 1] = (h / 2 - c[:, 1]) / 1000.0
            out[tid] = xyz
        return out


LAYOUTS = {
    "A4": MatLayout("A4", (210.0, 297.0), tag_mm=24.0, gap_mm=8.0, first_id=0),
    "A3": MatLayout("A3", (297.0, 420.0), tag_mm=32.0, gap_mm=10.0, first_id=100),
}


def layout_for_ids(ids) -> MatLayout | None:
    """Pick the layout that explains most of the detected ids."""
    best, best_n = None, 0
    ids = set(int(i) for i in ids)
    for lay in LAYOUTS.values():
        n = len(ids & set(lay.tag_corners_page_mm()))
        if n > best_n:
            best, best_n = lay, n
    return best if best_n >= 2 else None


def render_mat(layout: MatLayout, dpi: int = 600) -> np.ndarray:
    """Grayscale image of the mat at the given dpi."""
    px = dpi / 25.4
    w, h = layout.page_mm
    img = np.full((round(h * px), round(w * px)), 255, np.uint8)
    for tid, c in layout.tag_corners_page_mm().items():
        side = round(layout.tag_mm * px)
        tag = cv2.aruco.generateImageMarker(_DICT, tid, side)
        x, y = round(c[0, 0] * px), round(c[0, 1] * px)
        img[y:y + side, x:x + side] = tag

    # footer: 100 mm calibration bar + label
    fy = (h - layout.margin_mm - 5.0) * px
    bx0 = layout.margin_mm * px
    bx1 = bx0 + 100.0 * px
    lw = max(1, round(0.3 * px))
    cv2.line(img, (round(bx0), round(fy)), (round(bx1), round(fy)), 0, lw)
    for bx in (bx0, bx1):
        cv2.line(img, (round(bx), round(fy - 2 * px)), (round(bx), round(fy + 2 * px)), 0, lw)
    text = f"Calco scan mat {layout.name}  |  bar = 100.0 mm  |  print at 100% (no fit-to-page)"
    cv2.putText(img, text, (round(bx1 + 4 * px), round(fy + 1.2 * px)), cv2.FONT_HERSHEY_SIMPLEX,
                0.085 * px, 0, max(1, round(0.12 * px)), cv2.LINE_AA)
    return img


def write_mat_pdf(path: str | Path, size: str = "A4", dpi: int = 600) -> Path:
    from PIL import Image

    path = Path(path)
    Image.fromarray(render_mat(LAYOUTS[size], dpi)).save(path, "PDF", resolution=float(dpi))
    return path


def detect_tags(gray: np.ndarray) -> dict[int, np.ndarray]:
    """Tag id -> (4, 2) pixel corners (TL, TR, BR, BL)."""
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    params.cornerRefinementWinSize = 4
    corners, ids, _ = cv2.aruco.ArucoDetector(_DICT, params).detectMarkers(gray)
    if ids is None:
        return {}
    return {int(i): c.reshape(4, 2).astype(np.float64) for i, c in zip(ids.ravel(), corners)}


@dataclass
class MatFit:
    layout: str
    transform: np.ndarray  # 4x4 similarity: reconstruction frame -> mat frame (metres)
    scale: float
    rms_mm: float
    tags_used: int
    points_used: int


def fit_mat(observations: list[dict[int, np.ndarray]], projections: list[np.ndarray],
            print_scale: float = 1.0) -> MatFit | None:
    """Estimate the similarity from the reconstruction frame to the mat frame.

    observations[i] are the tags seen in image i, projections[i] its 3x4 matrix (pinhole,
    undistorted pixels). print_scale corrects a printer that does not print at exactly 100%
    (measured length of the 100 mm bar / 100).
    """
    all_ids = [tid for obs in observations for tid in obs]
    layout = layout_for_ids(all_ids)
    if layout is None:
        return None
    ref = layout.tag_corners_mat()

    src, dst, tags = [], [], set()
    for tid, corners_mat in ref.items():
        views = [i for i, obs in enumerate(observations) if tid in obs]
        if len(views) < 3:
            continue
        for k in range(4):
            px = np.array([observations[i][tid][k] for i in views])
            X = triangulate_dlt([projections[i] for i in views], px)
            if np.all(np.isfinite(X)):
                src.append(X)
                dst.append(corners_mat[k] * print_scale)
                tags.add(tid)
    if len(tags) < 2:
        return None
    src, dst = np.asarray(src), np.asarray(dst)
    s, R, t, keep, rms = robust_umeyama(src, dst)
    return MatFit(layout.name, sim3_matrix(s, R, t), s, rms * 1000.0, len(tags), int(keep.sum()))
