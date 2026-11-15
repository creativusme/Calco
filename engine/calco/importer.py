"""Turn photos and videos from anywhere (camera, drone, another app) into a capture bundle.

Imported frames have no pose and no depth (mode "import"): the engine solves the cameras from the
photos and takes the scale from the scan mat, or from a caliper measurement afterwards.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

PHOTO_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
MAX_VIDEO_FRAMES = 300


def _sharpness(gray: np.ndarray) -> float:
    small = cv2.resize(gray, (640, int(640 * gray.shape[0] / gray.shape[1])))
    return float(cv2.Laplacian(small, cv2.CV_64F).var())


def _focal_px(path: Path, width: int, height: int) -> float:
    """Focal length in pixels from EXIF (35 mm equivalent), else a typical phone-camera guess."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            f35 = im.getexif().get_ifd(0x8769).get(41989)  # FocalLengthIn35mmFilm
        if f35:
            return float(f35) / 36.0 * max(width, height)
    except (OSError, ValueError, TypeError):
        pass
    return 0.85 * max(width, height)  # about 28 mm equivalent; refined by the camera solver


def _add_photo(src: Path, dst: Path) -> tuple[int, int]:
    """Copy a photo upright as JPEG (bundles store EXIF orientation 1), keeping its EXIF."""
    from PIL import Image, ImageOps

    with Image.open(src) as im:
        exif = im.getexif()
        upright = exif.get(0x0112, 1) in (None, 1)
        if upright and src.suffix.lower() in (".jpg", ".jpeg"):
            shutil.copyfile(src, dst)
            return im.size
        im = ImageOps.exif_transpose(im).convert("RGB")
        exif[0x0112] = 1
        im.save(dst, quality=95, exif=exif.tobytes())
        return im.size


def _add_video(src: Path, frames_dir: Path, first: int, log) -> list[tuple[str, int, int]]:
    """The sharpest frame of every short window (about 2 per second, at most MAX_VIDEO_FRAMES)."""
    cap = cv2.VideoCapture(str(src))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    window = max(1, int(round(fps / 2)), int(np.ceil(total / MAX_VIDEO_FRAMES)))
    out, best, best_score, i = [], None, -1.0, 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        score = _sharpness(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        if score > best_score:
            best, best_score = frame, score
        i += 1
        if i % window == 0 and best is not None:
            name = f"{first + len(out):06d}.jpg"
            cv2.imwrite(str(frames_dir / name), best, [cv2.IMWRITE_JPEG_QUALITY, 95])
            out.append((name, best.shape[1], best.shape[0]))
            best, best_score = None, -1.0
    cap.release()
    log(f"{src.name}: {len(out)} frames from {i} (sharpest of every {window})")
    return out


def make_bundle(sources: list[Path], library_captures: Path, name: str | None = None, log=print) -> Path:
    """Photos and/or videos -> <library>/captures/<id>.calco. Returns the bundle path."""
    photos = sorted(p for p in sources if p.suffix.lower() in PHOTO_EXT)
    videos = sorted(p for p in sources if p.suffix.lower() in VIDEO_EXT)
    if not photos and not videos:
        raise ValueError("No photos or videos to import (JPEG, PNG, TIFF, MP4, MOV)")
    cid = str(uuid.uuid4())
    out = library_captures / f"{cid}.calco"
    tmp = out.with_name(out.name + ".tmp")
    frames_dir = tmp / "frames"
    frames_dir.mkdir(parents=True)
    try:
        entries = []  # (file name, width, height, focal px)
        for src in photos:
            fname = f"{len(entries) + 1:06d}.jpg"
            w, h = _add_photo(src, frames_dir / fname)
            entries.append((fname, w, h, _focal_px(frames_dir / fname, w, h)))
        for src in videos:
            for fname, w, h in _add_video(src, frames_dir, len(entries) + 1, log):
                entries.append((fname, w, h, 0.85 * max(w, h)))
        if len(entries) < 3:
            raise ValueError("At least 3 photos (or a longer video) are needed")
        frames = [{"id": k + 1, "image": f"frames/{n}", "width": w, "height": h, "timestamp": float(k),
                   "intrinsics": [f, f, w / 2, h / 2], "pose": None, "tracking": "none", "depth": None}
                  for k, (n, w, h, f) in enumerate(entries)]
        first = sources[0].stem if len(sources) == 1 else (sources[0].parent.name or "Import")
        manifest = {
            "format": "calco-capture", "version": 1, "id": cid,
            "name": name or f"{first} (import)",
            "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "import",
            "device": {"model": "import", "os": "-", "app": "calco import"},
            "crop_box": None, "frames": frames, "notes": [],
        }
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        img = cv2.imread(str(frames_dir / entries[0][0]))
        cv2.imwrite(str(tmp / "preview.jpg"), cv2.resize(img, (480, int(480 * img.shape[0] / img.shape[1]))))
        tmp.rename(out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    log(f"Imported {len(entries)} frames as {out.name}")
    return out
