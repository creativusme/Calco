"""One-page PDF report of a result: views of the model, dimensions and the accuracy report.

Rendered on the CPU (ray casting) and laid out with Pillow, so it needs nothing beyond the engine.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import open3d as o3d
from PIL import Image, ImageDraw, ImageFont

from .export import load_model

A4 = (2480, 3508)  # 300 dpi
INK, MUTED, LINE, ACCENT = (20, 22, 26), (110, 114, 122), (210, 212, 216), (61, 74, 0)


def _font(size: int, bold: bool = False):
    for name in (("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _views(vertices: np.ndarray, faces: np.ndarray, size=(1100, 820)) -> list[Image.Image]:
    """Clay renders (Z up): front-left, front-right, top, front."""
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(vertices.astype(np.float32), faces.astype(np.uint32))
    c = (vertices.min(0) + vertices.max(0)) / 2
    r = float(np.linalg.norm(vertices.max(0) - vertices.min(0)))
    light = np.array([0.4, -0.5, 0.8]) / np.linalg.norm([0.4, -0.5, 0.8])
    out = []
    for az, el in ((-60, 30), (-130, 30), (-90, 89), (-90, 5)):
        a, e = np.radians(az), np.radians(el)
        eye = c + (2.4 if el > 80 else 1.7) * r * np.array([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)])
        up = [0, 1, 0] if el > 80 else [0, 0, 1]
        rays = o3d.t.geometry.RaycastingScene.create_rays_pinhole(
            35.0, o3d.core.Tensor(c, dtype=o3d.core.float32), o3d.core.Tensor(eye, dtype=o3d.core.float32), up,
            size[0], size[1])
        ans = scene.cast_rays(rays)
        hit = np.isfinite(ans["t_hit"].numpy())
        n = ans["primitive_normals"].numpy()
        d = rays.numpy()[..., 3:]
        d /= np.linalg.norm(d, axis=-1, keepdims=True)
        n = np.where(((n * d).sum(-1) > 0)[..., None], -n, n)  # face the camera
        shade = 0.25 + 0.55 * np.clip(n @ light, 0, 1) + 0.2 * np.abs((n * d).sum(-1))
        img = np.where(hit, np.clip(shade, 0, 1) * 225, 255).astype(np.uint8)
        out.append(Image.fromarray(img).convert("RGB"))
    return out


def write_report(job_dir: Path, out: Path, title: str, user_scale: float = 1.0) -> Path:
    report = json.loads((job_dir / "report.json").read_text(encoding="utf-8"))
    mesh = load_model(job_dir / "result")
    page = Image.new("RGB", A4, "white")
    g = ImageDraw.Draw(page)
    m = 160  # margins
    g.text((m, m), title, font=_font(84, True), fill=INK)
    when = time.strftime("%d %b %Y %H:%M", time.localtime())
    cap = report.get("capture", {})
    g.text((m, m + 115), f"Calco scan report  ·  {when}  ·  {cap.get('frames', '?')} photos  ·  "
                         f"{cap.get('device') or ''}", font=_font(36), fill=MUTED)
    g.line((m, m + 185, A4[0] - m, m + 185), fill=LINE, width=3)

    views = _views(np.asarray(mesh.vertices), np.asarray(mesh.faces))
    w = (A4[0] - 2 * m - 40) // 2
    for k, img in enumerate(views):
        img = img.resize((w, int(w * img.height / img.width)))
        x, y = m + (k % 2) * (w + 40), m + 230 + (k // 2) * (img.height + 40)
        page.paste(img, (x, y))
        g.rectangle((x, y, x + img.width, y + img.height), outline=LINE, width=2)
        g.text((x + 18, y + 12), ("View 1", "View 2", "Top", "Front")[k], font=_font(30), fill=MUTED)
    y = m + 230 + 2 * (int(w * views[0].height / views[0].width) + 40) + 20

    dims = np.array(report["model"]["dimensions_mm"]) * user_scale
    g.text((m, y), "Dimensions", font=_font(44, True), fill=INK)
    g.text((m, y + 70), f"X {dims[0]:.2f} mm    Y {dims[1]:.2f} mm    Z {dims[2]:.2f} mm",
           font=_font(56, True), fill=ACCENT)
    y += 180
    sc = report.get("scale", {})
    src = {"mat": "Scan mat", "markers": "Scale markers", "lidar": "LiDAR", "none": "No reference"}.get(sc.get("source"), "?")
    if abs(user_scale - 1) > 1e-9:
        src = "Caliper calibration"
    rows = [("Scale from", src)]
    if sc.get("expected_error_mm") is not None and abs(user_scale - 1) < 1e-9:
        rows.append(("Expected error", f"± {sc['expected_error_mm']} mm"))
    if sc.get("mat"):
        rows.append(("Mat fit", f"{sc['mat']['tags']} tags, {sc['mat']['fit_rms_mm']} mm"))
    if report.get("alignment"):
        a = report["alignment"]
        rows.append(("Photos aligned", f"{a['registered']} / {a['total']}, {a['reprojection_px']} px"))
    rows.append(("Mesh", f"{report['model']['faces']:,} faces, {'closed' if report['model']['watertight'] else 'open'}"))
    rows.append(("Quality", f"{report['plan']['quality']}, {report['plan']['method']}, {report.get('seconds', 0) / 60:.0f} min"))
    for label, value in rows:
        g.text((m, y), label, font=_font(38), fill=MUTED)
        g.text((m + 520, y), str(value), font=_font(38), fill=INK)
        y += 62
    for warning in report.get("warnings", []):
        y += 20
        words, line = warning.split(), ""
        for word in words:
            if g.textlength(line + " " + word, font=_font(34)) > A4[0] - 2 * m:
                g.text((m, y), line.strip(), font=_font(34), fill=INK)
                y, line = y + 48, ""
            line += " " + word
        g.text((m, y), line.strip(), font=_font(34), fill=INK)
        y += 48
    g.text((m, A4[1] - m), "Made with Calco (open source, AGPL-3.0). Measurements are only as good as the scale "
                           "reference: check one with a caliper.", font=_font(30), fill=MUTED)
    page.save(out, "PDF", resolution=300.0)
    return out
