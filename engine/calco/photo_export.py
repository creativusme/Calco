"""The photos of a capture with their camera positions, for other programs.

One zip with the original photos (EXIF kept) and the cameras in the formats other tools read:

- colmap/       COLMAP text model (cameras.txt, images.txt, points3D.txt): Gaussian-splat trainers,
                Nerfstudio, Postshot, Metashape and RealityScan importers, COLMAP itself.
- transforms.json  Nerfstudio / instant-ngp convention (OpenGL camera-to-world).
- xmp/          one RealityScan / RealityCapture sidecar per photo (position + rotation priors).
- cameras.csv   label, x, y, z, yaw, pitch, roll: Metashape "Import Reference" and spreadsheets.

When the capture has been processed, the cameras are the refined ones (bundle adjusted, scaled by
the mat) in the frame of the exported model: Z up, metres, origin on the support under the part,
so a mesh exported from Calco lines up with the photos. Otherwise they are the phone's ARKit
poses (Y up, metres, about 1 cm accurate).
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np

from .bundle import Bundle

# OpenCV camera (x right, y down, z forward) <-> OpenGL / ARKit camera (x right, y up, z backward)
_FLIP = np.diag([1.0, -1.0, -1.0, 1.0])


def _cameras_from_job(bundle: Bundle, job_dir: Path) -> tuple[list[dict], str] | None:
    """Refined cameras (OpenCV world-to-camera) in the model frame, or None."""
    model = job_dir / "work" / "sfm" / "aligned"
    M_path = job_dir / "result" / "model_from_capture.npy"
    if not model.exists() or not M_path.exists():
        return None
    import pycolmap

    rec = pycolmap.Reconstruction(model)
    M = np.load(M_path)
    s = float(np.cbrt(np.linalg.det(M[:3, :3])))
    R_m, t_m = M[:3, :3] / s, M[:3, 3]
    cams = []
    for img in sorted(rec.images.values(), key=lambda i: i.name):
        if not img.has_pose:
            continue
        cam = rec.cameras[img.camera_id]
        E = np.eye(4)
        E[:3] = img.cam_from_world().matrix()
        # world_model = s R x + t  ->  camera = E (world_model - t)/s ... keep metric: rotate, then scale translation
        R = E[:3, :3] @ R_m.T
        C = s * (R_m @ (-E[:3, :3].T @ E[:3, 3])) + t_m  # camera centre in the model frame
        w2c = np.eye(4)
        w2c[:3, :3] = R
        w2c[:3, 3] = -R @ C
        p = [float(v) for v in cam.params]
        model_name = cam.model.name
        if model_name == "SIMPLE_RADIAL":
            fx = fy = p[0]
            cx, cy, k1, k2 = p[1], p[2], p[3], 0.0
        elif model_name == "RADIAL":
            fx = fy = p[0]
            cx, cy, k1, k2 = p[1], p[2], p[3], p[4]
        else:  # PINHOLE / OPENCV and friends
            fx, fy, cx, cy = p[:4]
            k1 = p[4] if len(p) > 4 else 0.0
            k2 = p[5] if len(p) > 5 else 0.0
        cams.append({"name": img.name, "w2c": w2c, "fx": fx, "fy": fy, "cx": cx, "cy": cy, "k1": k1, "k2": k2,
                     "width": cam.width, "height": cam.height})
    return cams, "model"


def _cameras_from_phone(bundle: Bundle) -> tuple[list[dict], str]:
    cams = []
    for f in bundle.frames:
        if f.pose is None:
            continue
        fx, fy, cx, cy = f.intrinsics
        cams.append({"name": f.image.name, "w2c": f.world_to_cam_cv, "fx": fx, "fy": fy, "cx": cx, "cy": cy,
                     "k1": 0.0, "k2": 0.0, "width": f.width, "height": f.height})
    return cams, "arkit"


def _quat_wxyz(R: np.ndarray) -> np.ndarray:
    from scipy.spatial.transform import Rotation

    x, y, z, w = Rotation.from_matrix(R).as_quat()
    return np.array([w, x, y, z])


def _xmp(c: dict) -> str:
    """RealityScan / RealityCapture camera prior (w2c rotation in the OpenCV convention)."""
    R, w2c = c["w2c"][:3, :3], c["w2c"]
    C = -R.T @ w2c[:3, 3]
    side = max(c["width"], c["height"])
    f35 = c["fx"] * 36.0 / side
    ppu, ppv = (c["cx"] - c["width"] / 2) / side, (c["cy"] - c["height"] / 2) / side
    rot = " ".join(f"{v:.12f}" for v in R.reshape(-1))
    pos = " ".join(f"{v:.9f}" for v in C)
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description xmlns:xcr="http://www.capturingreality.com/ns/xcr/1.1#"\n'
        '   xcr:Version="3" xcr:PosePrior="initial" xcr:Coordinates="absolute"\n'
        f'   xcr:DistortionModel="brown3" xcr:FocalLength35mm="{f35:.6f}" xcr:Skew="0"\n'
        f'   xcr:AspectRatio="{c["fy"] / c["fx"]:.8f}" xcr:PrincipalPointU="{ppu:.8f}" xcr:PrincipalPointV="{ppv:.8f}"\n'
        '   xcr:CalibrationPrior="initial" xcr:CalibrationGroup="-1" xcr:DistortionGroup="-1" xcr:InTexturing="1"\n'
        '   xcr:InMeshing="1">\n'
        f'   <xcr:Rotation>{rot}</xcr:Rotation>\n'
        f'   <xcr:Position>{pos}</xcr:Position>\n'
        f'   <xcr:DistortionCoeficients>{c["k1"]:.8f} {c["k2"]:.8f} 0 0 0 0</xcr:DistortionCoeficients>\n'
        '  </rdf:Description>\n'
        ' </rdf:RDF>\n'
        '</x:xmpmeta>\n')


def write_photo_package(bundle: Bundle, out_zip: Path, job_dir: Path | None = None) -> dict:
    """Write the zip. Returns {"photos": n, "cameras": n, "frame": "model" | "arkit"}."""
    from scipy.spatial.transform import Rotation

    found = _cameras_from_job(bundle, job_dir) if job_dir is not None else None
    cams, frame = found if found else _cameras_from_phone(bundle)
    by_name = {c["name"]: c for c in cams}

    colmap_cams, colmap_imgs, frames_json, csv = [], [], [], ["label,x,y,z,yaw,pitch,roll"]
    for i, c in enumerate(cams, start=1):
        colmap_cams.append(f"{i} OPENCV {c['width']} {c['height']} {c['fx']:.6f} {c['fy']:.6f} {c['cx']:.6f} "
                           f"{c['cy']:.6f} {c['k1']:.8f} {c['k2']:.8f} 0 0")
        q = _quat_wxyz(c["w2c"][:3, :3])
        t = c["w2c"][:3, 3]
        colmap_imgs.append(f"{i} {' '.join(f'{v:.12f}' for v in q)} {' '.join(f'{v:.9f}' for v in t)} {i} "
                           f"images/{c['name']}\n")
        c2w_gl = np.linalg.inv(c["w2c"]) @ _FLIP
        frames_json.append({"file_path": f"images/{c['name']}", "transform_matrix": c2w_gl.tolist(),
                            "fl_x": c["fx"], "fl_y": c["fy"], "cx": c["cx"], "cy": c["cy"],
                            "w": c["width"], "h": c["height"], "k1": c["k1"], "k2": c["k2"]})
        C = c2w_gl[:3, 3]
        yaw, pitch, roll = Rotation.from_matrix(c2w_gl[:3, :3]).as_euler("ZXY", degrees=True)
        csv.append(f"{c['name']},{C[0]:.6f},{C[1]:.6f},{C[2]:.6f},{yaw:.4f},{pitch:.4f},{roll:.4f}")

    readme = (
        "Calco photo export\n\n"
        + ("Cameras: refined by Calco (bundle adjusted, scaled), in the frame of the exported model:\n"
           "Z up, metres, origin on the support under the part. Export the mesh as OBJ/STL from the same\n"
           "result and it lines up with these cameras (OBJ/GLB are Y up: rotate -90 deg about X).\n"
           if frame == "model" else
           "Cameras: the iPhone's ARKit poses (Y up, metres, about 1 cm accurate; not refined).\n"
           "Process the capture in Calco first to get refined, scaled cameras.\n")
        + "\nPhotos without a camera (close-ups that were not placed) are included but not listed.\n\n"
        "colmap/sparse/0  COLMAP text model, image paths relative to this folder (images/...)\n"
        "transforms.json  Nerfstudio / instant-ngp (OpenGL camera-to-world)\n"
        "xmp/             RealityScan / RealityCapture: copy the .xmp files next to the photos\n"
        "cameras.csv      label, x, y, z, yaw, pitch, roll (camera looks along its -Z, OpenGL)\n")

    out_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_STORED) as z:  # JPEGs do not compress
        for f in bundle.frames:
            z.write(f.image, f"images/{f.image.name}")
            if f.image.name in by_name:
                z.writestr(f"xmp/{f.image.stem}.xmp", _xmp(by_name[f.image.name]))
        z.writestr("colmap/sparse/0/cameras.txt", "\n".join(colmap_cams) + "\n")
        z.writestr("colmap/sparse/0/images.txt", "".join(colmap_imgs))
        z.writestr("colmap/sparse/0/points3D.txt", "")
        z.writestr("transforms.json", json.dumps({"camera_model": "OPENCV", "frames": frames_json}, indent=1))
        z.writestr("cameras.csv", "\n".join(csv) + "\n")
        z.writestr("README.txt", readme)
    return {"photos": len(bundle.frames), "cameras": len(cams), "frame": frame}
