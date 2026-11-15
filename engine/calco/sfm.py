"""Structure from motion with COLMAP. Phone poses are used to choose image pairs and to put the
result in the metric ARKit frame; the camera geometry itself is re-estimated from the photos."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pycolmap

from . import tools
from .bundle import Bundle
from .presets import Quality
from .views import View


@dataclass
class SfmResult:
    views: list[View]
    dense_dir: Path  # undistorted images + pinhole model, ready for OpenMVS
    registered: int
    total: int
    reprojection_px: float
    pose_rms_mm: float | None  # agreement between refined camera centres and the phone's
    aligned: bool  # True when the model is in the metric ARKit frame
    sparse_ply: Path | None = None
    computed_on: str = "cpu"
    warnings: list[str] = field(default_factory=list)


def _pose_pairs(bundle: Bundle, neighbours: int = 16) -> list[tuple[str, str]]:
    """Image pairs worth matching, chosen from the phone poses."""
    frames = [f for f in bundle.frames if f.pose is not None]
    centers = np.array([f.center for f in frames])
    fwd = np.array([-f.pose[:3, 2] for f in frames])
    pairs = set()
    for i in range(len(frames)):
        dist = np.linalg.norm(centers - centers[i], axis=1)
        dist[(fwd @ fwd[i]) < np.cos(np.radians(75))] = np.inf
        dist[i] = np.inf
        for j in np.argsort(dist)[:neighbours]:
            if np.isfinite(dist[j]):
                pairs.add(tuple(sorted((frames[i].image.name, frames[j].image.name))))
    # photos without a pose (detail/macro shots, or tracking lost) are matched with every photo
    posed = {f.image.name for f in frames}
    names = [f.image.name for f in bundle.frames]
    for n in names:
        if n not in posed:
            for m in names:
                if m != n:
                    pairs.add(tuple(sorted((n, m))))
    return sorted(pairs)


def _exif_focal(path: Path) -> float | None:
    """Focal length in mm from the photo's EXIF data (tells the phone's lenses apart)."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            value = im.getexif().get_ifd(0x8769).get(37386)  # Exif IFD, FocalLength
        return round(float(value), 2) if value else None
    except (OSError, ValueError, TypeError, ZeroDivisionError):
        return None


def _open_db(path: Path):
    return pycolmap.Database.open(str(path)) if hasattr(pycolmap.Database, "open") else pycolmap.Database(str(path))


def _known_pose_model(bundle: Bundle, db: Path, image_dir: Path, out: Path, log) -> "pycolmap.Reconstruction | None":
    """Triangulate with the phone's poses, then refine everything with bundle adjustment.

    Much faster than solving the cameras from scratch (no registration loop) and it cannot
    build a wrong 'fork' of the model on repetitive textures, which incremental mapping does.
    Returns None when the result does not look right, so the caller can fall back.
    """
    from scipy.spatial.transform import Rotation

    by = {f.image.name: f for f in bundle.frames if f.pose is not None}
    conn = _open_db(db)
    cams, imgs = conn.read_all_cameras(), conn.read_all_images()
    conn.close()
    txt = out / "known"
    txt.mkdir(parents=True, exist_ok=True)
    with open(txt / "cameras.txt", "w") as fh:
        for c in cams:
            fh.write(f"{c.camera_id} {c.model.name} {c.width} {c.height} {' '.join(repr(float(p)) for p in c.params)}\n")
    with open(txt / "images.txt", "w") as fh:
        for im in imgs:
            f = by.get(im.name)
            if f is None:
                continue  # photos without a phone pose are registered afterwards
            w2c = f.world_to_cam_cv
            x, y, z, w = Rotation.from_matrix(w2c[:3, :3]).as_quat()
            t = w2c[:3, 3]
            fh.write(f"{im.image_id} {w} {x} {y} {z} {t[0]} {t[1]} {t[2]} {im.camera_id} {im.name}\n\n")
    (txt / "points3D.txt").write_text("")

    rec = pycolmap.triangulate_points(pycolmap.Reconstruction(txt), db, image_dir, out / "triangulated")
    if len(rec.points3D) < 500:
        log(f"Triangulating with the phone poses gave only {len(rec.points3D)} points")
        return None
    ba = pycolmap.BundleAdjustmentOptions()
    ba.refine_principal_point = False
    pycolmap.bundle_adjustment(rec, ba)
    drift = _drift_mm(rec, by)
    if drift[0] > 10 or drift[1] > 30:
        # With few points the free adjustment can slide whole parts of the model away (seen on a real
        # capture: 25 cm). The phone poses are better than that: keep them and refine only the rest.
        log(f"Free adjustment drifted from the phone poses (median {drift[0]:.0f} mm, p90 {drift[1]:.0f} mm): "
            "keeping the phone poses fixed")
        rec = pycolmap.triangulate_points(pycolmap.Reconstruction(txt), db, image_dir, out / "triangulated")
        ba.refine_rig_from_world = False
        pycolmap.bundle_adjustment(rec, ba)
    log(f"Phone-pose model: {rec.num_reg_images()} photos, {len(rec.points3D)} points, "
        f"reprojection {rec.compute_mean_reprojection_error():.2f} px")
    return rec


def _drift_mm(rec: "pycolmap.Reconstruction", phone: dict) -> tuple[float, float]:
    """How far the solved cameras are from the phone's, after the best similarity (median, 90th pct)."""
    from .geom import robust_umeyama

    names = [im.name for im in rec.images.values() if im.has_pose and im.name in phone]
    if len(names) < 4:
        return 0.0, 0.0
    by_name = {im.name: im for im in rec.images.values()}
    src = np.array([by_name[n].projection_center() for n in names])
    dst = np.array([phone[n].center for n in names])
    s, R, t, _, _ = robust_umeyama(src, dst)
    res = np.linalg.norm(dst - (s * src @ R.T + t), axis=1) * 1000
    return float(np.median(res)), float(np.percentile(res, 90))


def run_sfm(bundle: Bundle, work: Path, quality: Quality, log=print, progress=lambda f, m="": None,
            use_gpu: bool = True, photo_size: int | None = None) -> SfmResult:
    sfm_dir = work / "sfm"
    if sfm_dir.exists():
        shutil.rmtree(sfm_dir)
    sfm_dir.mkdir(parents=True)
    db = sfm_dir / "database.db"
    image_dir = bundle.frames[0].image.parent
    names = [f.image.name for f in bundle.frames]
    warnings: list[str] = []

    # Photos with a phone pose share the phone's calibrated main camera. Close-up photos have
    # no pose and may come from another lens (the phone switches lenses by itself): one camera
    # model per lens, read from the photo's EXIF focal length, refined by bundle adjustment.
    posed = [f for f in bundle.frames if f.pose is not None]
    free = [f for f in bundle.frames if f.pose is None]
    groups: list[tuple[list[str], str, str | None]] = []  # (image names, camera model, params)
    if posed:
        fx = float(np.median([f.intrinsics[0] for f in posed]))
        fy = float(np.median([f.intrinsics[1] for f in posed]))
        cx, cy = posed[0].intrinsics[2:]
        sizes = {(f.width, f.height) for f in posed}
        known = len(sizes) == 1 and fx > 0
        groups.append(([f.image.name for f in posed], "SIMPLE_RADIAL", f"{(fx + fy) / 2},{cx},{cy},0" if known else None))
    lenses: dict[tuple, list[str]] = {}
    for f in free:
        lenses.setdefault((_exif_focal(f.image), f.width, f.height), []).append(f.image.name)
    for (focal, _, _), group in sorted(lenses.items(), key=lambda kv: -len(kv[1])):
        groups.append((group, "RADIAL", None))  # wide lenses need two distortion terms
        log(f"{len(group)} close-up photos from the {focal or '?'} mm lens")
    use_poses = len(posed) >= 3
    pairs_file = None
    if use_poses:
        pairs_file = sfm_dir / "pairs.txt"
        pairs = _pose_pairs(bundle)
        pairs_file.write_text("\n".join(f"{a} {b}" for a, b in pairs), encoding="utf-8")
        log(f"Matching {len(pairs)} selected pairs (exhaustive would be {len(names) * (len(names) - 1) // 2})")

    def on_gpu() -> None:
        """Features and matching with the CUDA build of COLMAP (same database format as pycolmap)."""
        exe = tools.colmap_bin()
        progress(0.02, "Extracting features (GPU)")
        for i, (group, model, params) in enumerate(groups):
            listing = sfm_dir / f"images_{i}.txt"
            listing.write_text("\n".join(group), encoding="utf-8")
            args = ["feature_extractor", "--database_path", db, "--image_path", image_dir,
                    "--image_list_path", listing, "--ImageReader.single_camera", 1,
                    "--ImageReader.camera_model", model,
                    "--FeatureExtraction.use_gpu", 1, "--FeatureExtraction.max_image_size", quality.sfm_image_size,
                    "--SiftExtraction.max_num_features", quality.sfm_features]
            if params:
                args += ["--ImageReader.camera_params", params]
            tools.run_tool(exe, args, sfm_dir, log)
        progress(0.3, "Matching features (GPU)")
        if pairs_file:
            tools.run_tool(exe, ["matches_importer", "--database_path", db, "--match_list_path", pairs_file,
                                 "--match_type", "pairs", "--FeatureMatching.use_gpu", 1], sfm_dir, log)
        elif len(names) <= 250:
            tools.run_tool(exe, ["exhaustive_matcher", "--database_path", db, "--FeatureMatching.use_gpu", 1],
                           sfm_dir, log)
        else:
            tools.run_tool(exe, ["sequential_matcher", "--database_path", db, "--FeatureMatching.use_gpu", 1,
                                 "--SequentialMatching.overlap", 20], sfm_dir, log)

    def on_cpu() -> None:
        extraction = pycolmap.FeatureExtractionOptions()
        extraction.max_image_size = quality.sfm_image_size
        extraction.sift.max_num_features = quality.sfm_features
        progress(0.02, "Extracting features")
        for group, model, params in groups:
            reader = pycolmap.ImageReaderOptions()
            reader.camera_model = model
            if params:
                reader.camera_params = params
            pycolmap.extract_features(db, image_dir, image_names=group, camera_mode=pycolmap.CameraMode.SINGLE,
                                      reader_options=reader, extraction_options=extraction)
        progress(0.35, "Matching features")
        if pairs_file:
            pairing = pycolmap.ImportedPairingOptions()
            pairing.match_list_path = str(pairs_file)
            pycolmap.match_image_pairs(db, pairing_options=pairing)
        elif len(names) <= 150:
            pycolmap.match_exhaustive(db)
        else:
            pairing = pycolmap.SequentialPairingOptions()
            pairing.overlap = 20
            pycolmap.match_sequential(db, pairing_options=pairing)

    computed_on = "cpu"
    if use_gpu and tools.colmap_bin() and tools.have_gpu():
        try:
            on_gpu()
            computed_on = "gpu"
        except RuntimeError as e:
            log(f"GPU feature matching failed, using the CPU instead: {e}")
            db.unlink(missing_ok=True)
    if computed_on == "cpu":
        on_cpu()

    # --- cameras: start from the phone's poses when there are enough, else solve from the photos
    progress(0.6, "Solving cameras")
    opts = pycolmap.IncrementalPipelineOptions()
    opts.multiple_models = False
    opts.ba_refine_principal_point = False
    rec = None
    if len(posed) >= 8:
        try:
            rec = _known_pose_model(bundle, db, image_dir, sfm_dir, log)
        except Exception as e:  # any problem: the classic path below still works
            log(f"Phone-pose triangulation failed ({e}); solving the cameras from the photos instead")
            rec = None
        if rec is not None and free:
            # close-up photos have no phone pose: register them against the fixed model
            progress(0.75, f"Placing {len(free)} close-up photos")
            seed = sfm_dir / "seed"
            seed.mkdir(exist_ok=True)
            rec.write(seed)
            opts.fix_existing_frames = True
            more = pycolmap.incremental_mapping(db, image_dir, sfm_dir / "models", options=opts, input_path=seed)
            opts.fix_existing_frames = False
            base = rec
            if more:
                rec = max(more.values(), key=lambda r: r.num_reg_images())
            # Placing the close-ups must not move the phone-pose cameras. When it does (seen on a real
            # capture: up to 1.3 m), the close-ups were placed wrongly and would wreck the geometry.
            before = {im.name: im.projection_center() for im in base.images.values() if im.has_pose}
            moved = np.array([np.linalg.norm(im.projection_center() - before[im.name]) * 1000
                              for im in rec.images.values() if im.has_pose and im.name in before] or [0.0])
            if len(moved) < 0.8 * len(before) or np.median(moved) > 5 or np.percentile(moved, 90) > 20:
                log(f"Placing the close-ups moved the phone-pose cameras (median {np.median(moved):.1f} mm, "
                    f"max {moved.max():.0f} mm): keeping the phone-pose model without them")
                warnings.append(f"The {len(free)} close-up photos could not be placed reliably and were left out. "
                                "Take more normal photos around the part so the close-ups have something to attach to.")
                rec = base
            placed = sum(1 for im in rec.images.values() if im.has_pose and im.name in {f.image.name for f in free})
            log(f"Placed {placed} of {len(free)} close-up photos on the phone-pose model")
            if placed < 0.5 * len(free) and len(free) > len(posed) and rec is not base:
                log("Most close-ups did not fit the phone-pose model: solving all photos together instead")
                rec = None
    if rec is None:
        models = pycolmap.incremental_mapping(db, image_dir, sfm_dir / "models", options=opts)
        if not models:
            raise RuntimeError("Photo alignment failed: not enough overlap or texture between the photos. "
                               "Re-shoot with more overlap (each part of the object in at least 3 photos).")
        rec = max(models.values(), key=lambda r: r.num_reg_images())
    registered = rec.num_reg_images()
    if registered < max(3, 0.3 * len(names)):
        raise RuntimeError(
            f"Only {registered} of {len(names)} photos could be placed, too few to build the object. "
            "Usually the photos overlap too little, are blurred, or the object has no texture (plain, shiny "
            "or transparent). Take more photos with small steps between them.")
    if registered < 0.6 * len(names):
        warnings.append(f"Only {registered} of {len(names)} photos could be aligned.")
    log(f"Aligned {registered}/{len(names)} photos, reprojection error {rec.compute_mean_reprojection_error():.2f} px")

    # --- put the model in the phone's metric frame
    aligned, pose_rms = False, None
    if use_poses:
        by_name = {f.image.name: f for f in bundle.frames if f.pose is not None}
        tgt_names = list(by_name)
        tgt = np.array([by_name[n].center for n in tgt_names])
        ransac = pycolmap.RANSACOptions()
        ransac.max_error = 0.05
        sim = pycolmap.align_reconstruction_to_locations(rec, tgt_names, tgt, 3, ransac)
        if sim is None:
            warnings.append("Could not align the model to the phone poses; scale comes from the mat only.")
        else:
            rec.transform(sim)
            aligned = True
            res = [np.linalg.norm(img.projection_center() - by_name[img.name].center)
                   for img in rec.images.values() if img.has_pose and img.name in by_name]
            pose_rms = float(np.sqrt(np.mean(np.square(res))) * 1000.0)
            if pose_rms < 0.05:  # the phone poses were kept fixed: there is nothing to compare
                log("Phone poses kept fixed")
                pose_rms = None
            else:
                log(f"Phone poses vs photogrammetry: {pose_rms:.1f} mm RMS")

    model_dir = sfm_dir / "aligned"
    model_dir.mkdir()
    rec.write(model_dir)
    sparse_ply = sfm_dir / "sparse.ply"
    rec.export_PLY(sparse_ply)

    # --- undistort for dense matching and texturing
    progress(0.9, "Undistorting photos")
    dense_dir = work / "dense"
    if dense_dir.exists():
        shutil.rmtree(dense_dir)
    und = pycolmap.UndistortCameraOptions()
    und.max_image_size = photo_size or quality.photo_size
    # 8 source photos per depth map: enough overlap for a walk-around capture, about 2x faster than 20
    pycolmap.undistort_images(dense_dir, model_dir, image_dir, output_type="COLMAP", undistort_options=und,
                              num_patch_match_src_images=8)

    urec = pycolmap.Reconstruction(dense_dir / "sparse")
    views = []
    for img in sorted(urec.images.values(), key=lambda im: im.name):
        if not img.has_pose:
            continue
        cam = urec.cameras[img.camera_id]
        w2c = np.eye(4)
        w2c[:3] = img.cam_from_world().matrix()
        views.append(View(img.name, dense_dir / "images" / img.name, cam.calibration_matrix(), w2c,
                          cam.width, cam.height))
    progress(1.0, "Cameras solved")
    return SfmResult(views, dense_dir, registered, len(names), float(rec.compute_mean_reprojection_error()),
                     pose_rms, aligned, sparse_ply, computed_on, warnings)
