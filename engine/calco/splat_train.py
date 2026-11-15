"""Gaussian surfel (2DGS) training on the GPU. Runs in the separate splat environment.

This file is a standalone script: it is executed by the Python of %LOCALAPPDATA%/Calco/tools/splat
(Python 3.10 + PyTorch + gsplat, see tools.py) and must not import the calco package.

Input  (scene.json, written by calco/splat.py): photos with OpenCV cameras, sparse points, a
       normalising transform and the region of interest.
Output (in --out): splat.ply (standard 3D Gaussian splat file, for viewers) and check.jpg.

The method is per-scene optimisation of coloured surfels against the photos (Huang et al.,
"2D Gaussian Splatting", SIGGRAPH 2024). No learned model, no network access.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

from gsplat.rendering import rasterization_2dgs
from gsplat.strategy import DefaultStrategy

C0 = 0.28209479177387814  # SH band 0


def say(msg: str) -> None:
    print(msg, flush=True)


def load_views(scene: dict, max_side: int, device: str):
    views = []
    for v in scene["views"]:
        img = Image.open(v["image"]).convert("RGB")
        s = min(1.0, max_side / max(img.size))
        w, h = round(img.width * s), round(img.height * s)
        if s < 1.0:
            img = img.resize((w, h), Image.LANCZOS)
        K = np.array(v["K"], np.float64)
        K[0] *= w / v["width"]
        K[1] *= h / v["height"]
        views.append({
            "pixels": torch.from_numpy(np.array(img)).to(device),  # uint8 [H, W, 3]
            "K": torch.tensor(K, dtype=torch.float32, device=device),
            "c2w": torch.tensor(np.linalg.inv(np.array(v["w2c"], np.float64)), dtype=torch.float32, device=device),
            "w": w, "h": h,
        })
    return views


def knn_dist(points: torch.Tensor, k: int = 3) -> torch.Tensor:
    out = torch.empty(len(points), device=points.device)
    for i in range(0, len(points), 4096):
        d = torch.cdist(points[i:i + 4096], points)
        out[i:i + 4096] = d.topk(k + 1, largest=False).values[:, 1:].pow(2).mean(1).sqrt()
    return out


def ssim(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    """Mean SSIM of two [1, 3, H, W] images in [0, 1] (11 px Gaussian window)."""
    g = torch.exp(-((torch.arange(11, device=a.device) - 5.0) ** 2) / (2 * 1.5 ** 2))
    g = (g / g.sum())
    w = (g[:, None] @ g[None, :]).expand(3, 1, 11, 11)
    mu_a, mu_b = F.conv2d(a, w, padding=5, groups=3), F.conv2d(b, w, padding=5, groups=3)
    va = F.conv2d(a * a, w, padding=5, groups=3) - mu_a ** 2
    vb = F.conv2d(b * b, w, padding=5, groups=3) - mu_b ** 2
    cov = F.conv2d(a * b, w, padding=5, groups=3) - mu_a * mu_b
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    return (((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (va + vb + c2))).mean()


def render(splats, view, sh_degree, **kw):
    return rasterization_2dgs(
        means=splats["means"], quats=splats["quats"], scales=torch.exp(splats["scales"]),
        opacities=torch.sigmoid(splats["opacities"]), colors=torch.cat([splats["sh0"], splats["shN"]], 1),
        viewmats=torch.linalg.inv(view["c2w"])[None], Ks=view["K"][None], width=view["w"], height=view["h"],
        sh_degree=sh_degree, near_plane=0.01, far_plane=100.0, **kw)


def save_ply(path: Path, splats, unscale: float, offset: np.ndarray) -> None:
    """Standard 3DGS PLY in the scene frame (the surfels' third axis gets a tiny scale)."""
    means = splats["means"].detach().cpu().numpy() / unscale + offset
    scales = splats["scales"].detach().cpu().numpy() - math.log(unscale)
    scales[:, 2] = np.minimum(scales[:, :2].min(1), scales[:, 2]) - 2.0
    quats = F.normalize(splats["quats"].detach(), dim=-1).cpu().numpy()
    opac = splats["opacities"].detach().cpu().numpy()[:, None]
    sh0 = splats["sh0"].detach().cpu().numpy().reshape(len(means), -1)
    shN = splats["shN"].detach().cpu().numpy().transpose(0, 2, 1).reshape(len(means), -1)
    cols = (["x", "y", "z", "nx", "ny", "nz"] + [f"f_dc_{i}" for i in range(3)]
            + [f"f_rest_{i}" for i in range(shN.shape[1])] + ["opacity"]
            + [f"scale_{i}" for i in range(3)] + [f"rot_{i}" for i in range(4)])
    data = np.concatenate([means, np.zeros_like(means), sh0, shN, opac, scales, quats], 1).astype("<f4")
    head = "ply\nformat binary_little_endian 1.0\nelement vertex %d\n" % len(data)
    head += "".join(f"property float {c}\n" for c in cols) + "end_header\n"
    with open(path, "wb") as f:
        f.write(head.encode())
        f.write(data.tobytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--steps", type=int, default=15000)
    ap.add_argument("--max-side", type=int, default=1600)
    ap.add_argument("--sh-degree", type=int, default=3)
    args = ap.parse_args()
    dev = "cuda"
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    scene = json.loads(Path(args.scene).read_text(encoding="utf-8"))

    # normalise: cameras within about a unit sphere around the region of interest
    centres = np.array([np.linalg.inv(np.array(v["w2c"]))[:3, 3] for v in scene["views"]])
    offset = np.array(scene["roi"]["center"], np.float64)
    unscale = 1.0 / max(float(np.median(np.linalg.norm(centres - offset, axis=1))), 1e-6)
    for v in scene["views"]:
        c2w = np.linalg.inv(np.array(v["w2c"], np.float64))
        c2w[:3, 3] = (c2w[:3, 3] - offset) * unscale
        v["w2c"] = np.linalg.inv(c2w).tolist()

    say(f"Loading {len(scene['views'])} photos")
    views = load_views(scene, args.max_side, dev)
    pts = np.array(scene["points"], np.float64)
    xyz = torch.tensor((pts[:, :3] - offset) * unscale, dtype=torch.float32, device=dev)
    rgb = torch.tensor(pts[:, 3:6] / 255.0, dtype=torch.float32, device=dev)
    n = len(xyz)
    K_sh = (args.sh_degree + 1) ** 2
    sh = torch.zeros(n, K_sh, 3, device=dev)
    sh[:, 0] = (rgb - 0.5) / C0
    params = {
        "means": (xyz, 1.6e-4),
        "scales": (torch.log(knn_dist(xyz).clamp_min(1e-5))[:, None].repeat(1, 3), 5e-3),
        "quats": (torch.rand(n, 4, device=dev), 1e-3),
        "opacities": (torch.logit(torch.full((n,), 0.1, device=dev)), 5e-2),
        "sh0": (sh[:, :1].contiguous(), 2.5e-3),
        "shN": (sh[:, 1:].contiguous(), 2.5e-3 / 20),
    }
    splats = torch.nn.ParameterDict({k: torch.nn.Parameter(v) for k, (v, _) in params.items()})
    opts = {k: torch.optim.Adam([{"params": splats[k], "lr": lr, "name": k}], eps=1e-15)
            for k, (_, lr) in params.items()}
    sched = torch.optim.lr_scheduler.ExponentialLR(opts["means"], gamma=0.01 ** (1.0 / args.steps))

    f = args.steps / 30000.0
    strategy = DefaultStrategy(prune_opa=0.05, grow_grad2d=0.0002, grow_scale3d=0.01, prune_scale3d=0.1,
                               refine_start_iter=int(500 * f), refine_stop_iter=int(15000 * f),
                               reset_every=int(3000 * f), refine_every=100, key_for_gradient="gradient_2dgs")
    strategy.check_sanity(splats, opts)
    state = strategy.initialize_state(scene_scale=1.0)

    say(f"Training {args.steps} steps from {n} points")
    t0 = time.time()
    rng = np.random.default_rng(0)
    order = []
    for step in range(args.steps):
        if not order:
            order = list(rng.permutation(len(views)))
        view = views[order.pop()]
        sh_deg = min(step // max(1, int(1000 * f)), args.sh_degree)
        colors, alphas, normals, surf_normals, distort, median, info = render(
            splats, view, sh_deg, render_mode="RGB+ED", distloss=True)
        strategy.step_pre_backward(splats, opts, state, step, info)
        img = colors[..., :3]
        gt = view["pixels"][None].float() / 255.0
        loss = 0.8 * F.l1_loss(img, gt) + 0.2 * (1.0 - ssim(img.permute(0, 3, 1, 2), gt.permute(0, 3, 1, 2)))
        if step > 7000 * f:  # surfels agree with the surface they form
            sn = (surf_normals * alphas.detach())
            loss = loss + 0.05 * (1 - (normals * sn).sum(-1)).mean()
        if step > 3000 * f:  # one surface per ray, not a fog
            loss = loss + 0.01 * distort.mean()
        loss.backward()
        strategy.step_post_backward(splats, opts, state, step, info, packed=False)
        for o in opts.values():
            o.step()
            o.zero_grad(set_to_none=True)
        sched.step()
        if step % 200 == 0 or step == args.steps - 1:
            el = time.time() - t0
            say(f"STEP {step + 1}/{args.steps} loss {loss.item():.4f} splats {len(splats['means'])} {el:.0f}s")

    save_ply(out / "splat.ply", splats, unscale, offset)
    with torch.no_grad():  # a quick look at the result, next to the splat
        colors = render(splats, views[0], args.sh_degree)[0]
        Image.fromarray((colors[0, ..., :3].clamp(0, 1) * 255).byte().cpu().numpy()).save(out / "check.jpg")
    say("DONE")


if __name__ == "__main__":
    main()
