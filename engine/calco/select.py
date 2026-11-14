"""Pick the photos worth using: drop blurred or out-of-focus shots, thin out for dense matching."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import cv2
import numpy as np

from .bundle import Bundle

WINDOW = 13  # photos either side used as the local reference
KEEP_RATIO = 0.5  # a photo sharper than half of its neighbours' median is kept
MAX_DROP = 0.25  # never throw away more than a quarter of the capture


def sharpness(path) -> float:
    """Variance of the Laplacian on a quarter-size image: high = sharp, low = blurred."""
    gray = cv2.imread(str(path), cv2.IMREAD_REDUCED_GRAYSCALE_4)
    if gray is None:
        return 0.0
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def drop_blurry(bundle: Bundle, log=print, progress=lambda f, m="": None) -> tuple[Bundle, list[int], dict[str, float]]:
    """Return (bundle without blurred photos, dropped frame ids, sharpness by image name).

    Sharpness depends on the scene (a plain wall is never 'sharp'), so each photo is compared
    with the photos taken just before and after it, not with a fixed threshold.
    """
    frames = bundle.frames
    with ThreadPoolExecutor(max_workers=4) as pool:
        scores = []
        for i, s in enumerate(pool.map(sharpness, [f.image for f in frames])):
            scores.append(s)
            if i % 10 == 0:
                progress(i / len(frames), f"Checking photo sharpness {i}/{len(frames)}")
    s = np.array(scores)
    local = np.array([np.median(s[max(0, i - WINDOW // 2):i + WINDOW // 2 + 1]) for i in range(len(s))])
    ratio = s / np.maximum(local, 1e-9)
    candidates = np.flatnonzero(ratio < KEEP_RATIO)
    candidates = candidates[np.argsort(ratio[candidates])][: int(MAX_DROP * len(frames))]
    if len(frames) - len(candidates) < 8:
        candidates = candidates[:0]
    drop = set(candidates.tolist())
    kept = [f for i, f in enumerate(frames) if i not in drop]
    dropped = [frames[i].id for i in sorted(drop)]
    if dropped:
        log(f"Dropped {len(dropped)} blurred photos: {dropped}")
    return replace(bundle, frames=kept), dropped, {f.image.name: float(v) for f, v in zip(frames, s)}


def spread_subset(names: list[str], count: int, score: dict[str, float] | None = None) -> list[str]:
    """`count` photos spread evenly over the capture order, the sharpest of each stretch."""
    if count <= 0 or len(names) <= count:
        return list(names)
    edges = np.linspace(0, len(names), count + 1).astype(int)
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        chunk = names[a:max(b, a + 1)]
        out.append(max(chunk, key=lambda n: (score or {}).get(n, 0.0)))
    return out
