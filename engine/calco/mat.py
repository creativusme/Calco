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
    "A5": MatLayout("A5", (148.0, 210.0), tag_mm=20.0, gap_mm=6.0, first_id=200, margin_mm=10.0),
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
    text = f"Calco scan mat {layout.name}  -  bar = 100.0 mm  -  print at 100% (no fit-to-page)"
    cv2.putText(img, text, (round(bx0), round(fy - 3.0 * px)), cv2.FONT_HERSHEY_SIMPLEX,
                0.075 * px, 0, max(1, round(0.11 * px)), cv2.LINE_AA)
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
    rms_mm: float  # how well the rigid, flat layout explains the triangulated tags
    tags_used: int
    points_used: int
    method: str = "layout"  # "layout" = whole sheet, "neighbours" = local distances (bent sheet)


FLAT_RMS_MM = 0.4  # above this the sheet is treated as bent (or the camera poses as noisy)


def fit_mat(observations: list[dict[int, np.ndarray]], projections: list[np.ndarray],
            print_scale: float = 1.0) -> MatFit | None:
    """Estimate the similarity from the reconstruction frame to the mat frame.

    observations[i] are the tags seen in image i, projections[i] its 3x4 matrix (pinhole,
    undistorted pixels). print_scale corrects a print that is not exactly 100% (measured
    length of the 100 mm bar / 100).

    A flat sheet gives the best scale from the whole layout (long baselines). A sheet that
    is curled or wavy would bias that, so then the scale comes from the distances between
    neighbouring tag centres, which bending barely changes.
    """
    all_ids = [tid for obs in observations for tid in obs]
    layout = layout_for_ids(all_ids)
    if layout is None:
        return None
    ref = layout.tag_corners_mat()

    found: dict[int, np.ndarray] = {}
    for tid in ref:
        views = [i for i, obs in enumerate(observations) if tid in obs]
        if len(views) < 3:
            continue
        pts = np.array([triangulate_dlt([projections[i] for i in views],
                                        np.array([observations[i][tid][k] for i in views])) for k in range(4)])
        if np.all(np.isfinite(pts)):
            found[tid] = pts
    if len(found) < 2:
        return None
    tags = sorted(found)
    src = np.concatenate([found[t] for t in tags])
    dst = np.concatenate([ref[t] for t in tags]) * print_scale
    s, R, t, keep, rms = robust_umeyama(src, dst)
    method = "layout"

    if rms * 1000.0 > FLAT_RMS_MM and len(tags) >= 4:
        c_src = np.array([found[t].mean(0) for t in tags])
        c_dst = np.array([ref[t].mean(0) for t in tags]) * print_scale
        reach = 1.5 * (layout.tag_mm + layout.gap_mm) / 1000.0  # direct and diagonal neighbours
        d_dst = np.linalg.norm(c_dst[:, None] - c_dst[None], axis=2)
        d_src = np.linalg.norm(c_src[:, None] - c_src[None], axis=2)
        pair = np.triu((d_dst > 0) & (d_dst <= reach), 1) & (d_src > 0)
        if pair.sum() >= 4:
            s = float(np.median(d_dst[pair] / d_src[pair]))
            _, R, t, keep, _ = robust_umeyama(src * s, dst, with_scale=False)
            method = "neighbours"
    return MatFit(layout.name, sim3_matrix(s, R, t), s, rms * 1000.0, len(tags), int(keep.sum()), method)


def mat_3d_files(size: str = "A5", base_mm: float = 2.0, relief_mm: float = 0.6) -> dict[str, bytes]:
    """A fully 3D-printable mat: a flat plate (white) and the tag pattern raised on it (black).

    Print base.stl and pattern.stl as one object in two colours, or print combined.stl and
    swap filament when the plate is finished (at base_mm).
    """
    import trimesh

    layout = LAYOUTS[size]
    w, h = layout.page_mm
    base = trimesh.creation.box(extents=[w, h, base_mm])
    base.apply_translation([0, 0, base_mm / 2])

    def raised(x0: float, y0: float, x1: float, y1: float):
        """Box over the page-mm rectangle (origin top-left, y down)."""
        b = trimesh.creation.box(extents=[x1 - x0, y1 - y0, relief_mm + 0.05])
        b.apply_translation([(x0 + x1) / 2 - w / 2, h / 2 - (y0 + y1) / 2, base_mm + relief_mm / 2 - 0.025])
        return b

    boxes = []
    cell = layout.tag_mm / 8.0  # AprilTag 36h11: 6x6 data bits + 1 black border on each side
    for tid, c in layout.tag_corners_page_mm().items():
        bits = cv2.aruco.generateImageMarker(_DICT, tid, 8) < 128  # True = black
        x0, y0 = c[0]
        for r in range(8):
            col = 0
            while col < 8:
                if not bits[r, col]:
                    col += 1
                    continue
                end = col
                while end < 8 and bits[r, end]:
                    end += 1
                boxes.append(raised(x0 + col * cell, y0 + r * cell, x0 + end * cell, y0 + (r + 1) * cell))
                col = end
    by = h - layout.margin_mm - 5.0  # 100 mm calibration bar with end ticks
    bx = layout.margin_mm
    boxes += [raised(bx, by - 0.4, bx + 100.0, by + 0.4), raised(bx - 0.4, by - 2.5, bx + 0.4, by + 2.5),
              raised(bx + 99.6, by - 2.5, bx + 100.4, by + 2.5)]
    pattern = trimesh.util.concatenate(boxes)
    readme = (
        f"Calco scan mat {size}, 3D printable\n\n"
        f"Plate {w:.0f} x {h:.0f} x {base_mm} mm, pattern raised {relief_mm} mm.\n\n"
        "Two colours (AMS / MMU / dual extruder): load base.stl and pattern.stl together as one object,\n"
        "base in WHITE, pattern in BLACK.\n"
        f"One extruder: print combined.stl in white and change to black filament at height {base_mm} mm.\n\n"
        "Use MATTE filament: glossy plastic reflects the light and hides the tags.\n"
        "After printing, measure the bar (between the centres of the two end ticks) with a caliper. It should\n"
        "be 100.0 mm; if it is not, enter the measured value as 'Mat calibration bar' when processing.\n"
    )
    return {
        f"scan-mat-{size}-two-colour.3mf": two_colour_3mf(f"Calco scan mat {size}", [
            ("base (white)", base, 1, "#F2F2F2"), ("pattern (black)", pattern, 2, "#1A1A1A")]),
        "base.stl": base.export(file_type="stl"),
        "pattern.stl": pattern.export(file_type="stl"),
        "combined.stl": trimesh.util.concatenate([base, pattern]).export(file_type="stl"),
        "README.txt": readme.encode(),
    }


def two_colour_3mf(name: str, parts: list) -> bytes:
    """One printable object made of several parts, each already assigned to a filament.

    parts: (part name, trimesh mesh in mm, filament number from 1, display colour "#RRGGBB").
    Bambu Studio and OrcaSlicer read the per-part filament from Metadata/model_settings.config;
    other 3MF readers still see one object with coloured parts.
    """
    import io
    import zipfile
    from xml.sax.saxutils import escape

    objs, comps, settings = [], [], []
    mats = "".join(f'<base name="{escape(n)}" displaycolor="{c}FF"/>' for n, _, _, c in parts)
    for i, (pname, mesh, filament, _) in enumerate(parts):
        oid = i + 1
        v = "".join(f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>' for x, y, z in mesh.vertices)
        t = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in mesh.faces)
        objs.append(f'<object id="{oid}" type="model" name="{escape(pname)}" pid="100" pindex="{i}">'
                    f'<mesh><vertices>{v}</vertices><triangles>{t}</triangles></mesh></object>')
        comps.append(f'<component objectid="{oid}"/>')
        settings.append(f'  <part id="{oid}" subtype="normal_part">\n'
                        f'   <metadata key="name" value="{escape(pname)}"/>\n'
                        f'   <metadata key="extruder" value="{filament}"/>\n  </part>\n')
    top = len(parts) + 1
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
             f'<metadata name="Title">{escape(name)}</metadata>\n'
             f'<resources><basematerials id="100">{mats}</basematerials>{"".join(objs)}'
             f'<object id="{top}" type="model" name="{escape(name)}"><components>{"".join(comps)}</components></object>'
             f'</resources>\n<build><item objectid="{top}"/></build>\n</model>\n')
    config = ('<?xml version="1.0" encoding="UTF-8"?>\n<config>\n'
              f' <object id="{top}">\n  <metadata key="name" value="{escape(name)}"/>\n'
              f'  <metadata key="extruder" value="1"/>\n{"".join(settings)} </object>\n</config>\n')
    types = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
             '<Default Extension="config" ContentType="text/xml"/></Types>\n')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>\n')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)
        z.writestr("Metadata/model_settings.config", config)
    return buf.getvalue()


def write_mat_3d(path: str | Path, size: str = "A5") -> Path:
    import zipfile

    path = Path(path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in mat_3d_files(size).items():
            z.writestr(name, data)
    return path


# ---- loose scale markers: single tags of known size for parts too big for a mat
MARKER_MM = 60.0  # black square of each marker, printed at 100%
MARKER_SHEETS = 2  # 6 markers per A4 sheet
MARKER_FIRST_ID = 400  # clear of every mat layout


def marker_ids() -> range:
    return range(MARKER_FIRST_ID, MARKER_FIRST_ID + 6 * MARKER_SHEETS)


def render_markers(sheet: int = 0, dpi: int = 300) -> np.ndarray:
    """A4 page of 6 markers (2 x 3), each with a white border and cut marks."""
    px = dpi / 25.4
    img = np.full((round(297 * px), round(210 * px)), 255, np.uint8)
    side = round(MARKER_MM * px)
    for k in range(6):
        tid = MARKER_FIRST_ID + sheet * 6 + k
        cx, cy = (52.5 + 105.0 * (k % 2)) * px, (50.0 + 86.0 * (k // 2)) * px  # cells end at 264 mm
        x, y = round(cx - side / 2), round(cy - side / 2)
        img[y:y + side, x:x + side] = cv2.aruco.generateImageMarker(_DICT, tid, side)
        cut = round(12 * px)  # cut line 12 mm outside the black square: the white border tags need
        cv2.rectangle(img, (x - cut, y - cut), (x + side + cut, y + side + cut), 170, max(1, round(0.2 * px)))
        cv2.putText(img, f"Calco marker {tid}  -  {MARKER_MM:.1f} mm", (x - cut + round(2 * px), y + side + cut - round(2 * px)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.07 * px, 0, max(1, round(0.1 * px)), cv2.LINE_AA)
    fy, bx0 = 284 * px, 10 * px  # bar and its label below the last row, inside the printable area
    lw = max(1, round(0.3 * px))
    cv2.line(img, (round(bx0), round(fy)), (round(bx0 + 100 * px), round(fy)), 0, lw)
    for bx in (bx0, bx0 + 100 * px):
        cv2.line(img, (round(bx), round(fy - 2 * px)), (round(bx), round(fy + 2 * px)), 0, lw)
    cv2.putText(img, "bar = 100.0 mm  -  print at 100%  -  cut along the grey lines, keep the white border",
                (round(bx0), round(fy - 3 * px)), cv2.FONT_HERSHEY_SIMPLEX, 0.075 * px, 0, max(1, round(0.11 * px)), cv2.LINE_AA)
    return img


def write_markers_pdf(path: str | Path, dpi: int = 300) -> Path:
    from PIL import Image

    path = Path(path)
    pages = [Image.fromarray(render_markers(s, dpi)) for s in range(MARKER_SHEETS)]
    pages[0].save(path, "PDF", resolution=float(dpi), save_all=True, append_images=pages[1:])
    return path


@dataclass
class MarkerFit:
    scale: float  # metres per reconstruction unit
    markers: int
    spread_pct: float  # how much the markers disagree (1 sigma): bent or badly printed markers show here


def fit_markers(observations: list[dict[int, np.ndarray]], projections: list[np.ndarray],
                print_scale: float = 1.0) -> MarkerFit | None:
    """Scale from loose markers: each one's triangulated sides against its printed size."""
    per_marker = []
    for tid in marker_ids():
        views = [i for i, obs in enumerate(observations) if tid in obs]
        if len(views) < 3:
            continue
        pts = np.array([triangulate_dlt([projections[i] for i in views],
                                        np.array([observations[i][tid][k] for i in views])) for k in range(4)])
        if not np.all(np.isfinite(pts)):
            continue
        sides = np.linalg.norm(pts - np.roll(pts, -1, axis=0), axis=1)
        if sides.min() <= 0 or sides.max() / sides.min() > 1.15:  # badly seen or not flat
            continue
        per_marker.append(MARKER_MM * print_scale / 1000.0 / float(np.mean(sides)))
    if not per_marker:
        return None
    s = np.array(per_marker)
    spread = float(np.std(s) / np.mean(s) * 100.0) if len(s) > 1 else 0.5
    return MarkerFit(float(np.median(s)), len(s), spread)
