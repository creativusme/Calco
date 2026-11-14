"""Small geometry helpers shared by the pipeline stages."""

from __future__ import annotations

import numpy as np


def umeyama(src: np.ndarray, dst: np.ndarray, with_scale: bool = True) -> tuple[float, np.ndarray, np.ndarray]:
    """Least-squares similarity (s, R, t) with dst ≈ s * R @ src + t."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    cov = xd.T @ xs / len(src)
    U, S, Vt = np.linalg.svd(cov)
    D = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        D[2, 2] = -1
    R = U @ D @ Vt
    s = float(np.trace(np.diag(S) @ D) / xs.var(0).sum()) if with_scale else 1.0
    t = mu_d - s * R @ mu_s
    return s, R, t


def robust_umeyama(src: np.ndarray, dst: np.ndarray, with_scale: bool = True, iters: int = 3):
    """Umeyama with outlier rejection. Returns (s, R, t, inlier_mask, rms)."""
    keep = np.ones(len(src), bool)
    for _ in range(iters):
        s, R, t = umeyama(src[keep], dst[keep], with_scale)
        res = np.linalg.norm(dst - (s * src @ R.T + t), axis=1)
        thr = max(3.0 * np.median(res[keep]), 1e-9)
        new_keep = res <= thr
        if new_keep.sum() < 4 or np.array_equal(new_keep, keep):
            break
        keep = new_keep
    res = np.linalg.norm(dst[keep] - (s * src[keep] @ R.T + t), axis=1)
    return s, R, t, keep, float(np.sqrt((res**2).mean()))


def sim3_matrix(s: float, R: np.ndarray, t: np.ndarray) -> np.ndarray:
    M = np.eye(4)
    M[:3, :3] = s * R
    M[:3, 3] = t
    return M


def transform_points(M: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return pts @ M[:3, :3].T + M[:3, 3]


def triangulate_dlt(projections: list[np.ndarray], pixels: np.ndarray) -> np.ndarray:
    """Multi-view linear triangulation. projections: 3x4 matrices, pixels: (N, 2)."""
    A = []
    for P, (u, v) in zip(projections, pixels):
        A.append(u * P[2] - P[0])
        A.append(v * P[2] - P[1])
    _, _, Vt = np.linalg.svd(np.asarray(A))
    X = Vt[-1]
    return X[:3] / X[3]


def look_at_arkit(eye: np.ndarray, target: np.ndarray, up=(0.0, 1.0, 0.0)) -> np.ndarray:
    """Camera-to-world pose in ARKit convention (camera looks along its -Z)."""
    z = eye - target
    z = z / np.linalg.norm(z)
    x = np.cross(np.asarray(up, float), z)
    x = x / np.linalg.norm(x)
    y = np.cross(z, x)
    M = np.eye(4)
    M[:3, 0], M[:3, 1], M[:3, 2], M[:3, 3] = x, y, z, eye
    return M


def fit_plane_ransac(pts: np.ndarray, threshold: float, prefer_normal: np.ndarray | None = None,
                     max_angle_deg: float = 25.0, iters: int = 400, seed: int = 0):
    """RANSAC plane n·p + d = 0. Returns (n, d, inlier_mask) or None."""
    rng = np.random.default_rng(seed)
    best, best_count = None, 0
    cos_lim = np.cos(np.radians(max_angle_deg))
    n_pts = len(pts)
    if n_pts < 3:
        return None
    for _ in range(iters):
        a, b, c = pts[rng.choice(n_pts, 3, replace=False)]
        n = np.cross(b - a, c - a)
        norm = np.linalg.norm(n)
        if norm < 1e-12:
            continue
        n = n / norm
        if prefer_normal is not None:
            dot = float(n @ prefer_normal)
            if abs(dot) < cos_lim:
                continue
            if dot < 0:
                n = -n
        d = -float(n @ a)
        count = int((np.abs(pts @ n + d) < threshold).sum())
        if count > best_count:
            best, best_count = (n, d), count
    if best is None:
        return None
    n, d = best
    inl = np.abs(pts @ n + d) < threshold
    # least-squares refit on inliers
    c = pts[inl].mean(0)
    _, _, Vt = np.linalg.svd(pts[inl] - c, full_matrices=False)
    n2 = Vt[-1]
    if n2 @ n < 0:
        n2 = -n2
    d2 = -float(n2 @ c)
    return n2, d2, np.abs(pts @ n2 + d2) < threshold
