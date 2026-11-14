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
    # every image must have a pair even if tracking was lost around it
    posed = {f.image.name for f in frames}
    names = [f.image.name for f in bundle.frames]
    for k, n in enumerate(names):
        if n not in posed:
            for m in names[max(0, k - 5):k + 6]:
                if m != n:
                    pairs.add(tuple(sorted((n, m))))
    return sorted(pairs)


def run_sfm(bundle: Bundle, work: Path, quality: Quality, log=print, progress=lambda f, m="": None,
            use_gpu: bool = True) -> SfmResult:
    sfm_dir = work / "sfm"
    if sfm_dir.exists():
        shutil.rmtree(sfm_dir)
    sfm_dir.mkdir(parents=True)
    db = sfm_dir / "database.db"
    image_dir = bundle.frames[0].image.parent
    names = [f.image.name for f in bundle.frames]
    warnings: list[str] = []

    single = len({(f.width, f.height) for f in bundle.frames}) == 1
    fx = float(np.median([f.intrinsics[0] for f in bundle.frames]))
    fy = float(np.median([f.intrinsics[1] for f in bundle.frames]))
    cx, cy = bundle.frames[0].intrinsics[2:]
    known = single and fx > 0
    pairs_file = None
    if bundle.has_poses:
        pairs_file = sfm_dir / "pairs.txt"
        pairs = _pose_pairs(bundle)
        pairs_file.write_text("\n".join(f"{a} {b}" for a, b in pairs), encoding="utf-8")
        log(f"Matching {len(pairs)} pose-selected pairs (exhaustive would be {len(names) * (len(names) - 1) // 2})")

    def on_gpu() -> None:
        """Features and matching with the CUDA build of COLMAP (same database format as pycolmap)."""
        exe = tools.colmap_bin()
        (sfm_dir / "images.txt").write_text("\n".join(names), encoding="utf-8")
        args = ["feature_extractor", "--database_path", db, "--image_path", image_dir,
                "--image_list_path", sfm_dir / "images.txt", "--ImageReader.single_camera", int(single),
                "--FeatureExtraction.use_gpu", 1, "--FeatureExtraction.max_image_size", quality.sfm_image_size,
                "--SiftExtraction.max_num_features", quality.sfm_features]
        if known:
            args += ["--ImageReader.camera_model", "SIMPLE_RADIAL",
                     "--ImageReader.camera_params", f"{(fx + fy) / 2},{cx},{cy},0"]
        progress(0.02, "Extracting features (GPU)")
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
        reader = pycolmap.ImageReaderOptions()
        if known:
            reader.camera_model = "SIMPLE_RADIAL"
            reader.camera_params = f"{(fx + fy) / 2},{cx},{cy},0"
        extraction = pycolmap.FeatureExtractionOptions()
        extraction.max_image_size = quality.sfm_image_size
        extraction.sift.max_num_features = quality.sfm_features
        progress(0.02, "Extracting features")
        pycolmap.extract_features(db, image_dir, image_names=names,
                                  camera_mode=pycolmap.CameraMode.SINGLE if single else pycolmap.CameraMode.AUTO,
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

    # --- mapping
    progress(0.6, "Solving cameras")
    opts = pycolmap.IncrementalPipelineOptions()
    opts.multiple_models = False
    opts.ba_refine_principal_point = False
    models = pycolmap.incremental_mapping(db, image_dir, sfm_dir / "models", options=opts)
    if not models:
        raise RuntimeError("Photo alignment failed: not enough overlap or texture between the photos. "
                           "Try the Draft (LiDAR) preset, or re-shoot with more overlap.")
    rec = max(models.values(), key=lambda r: r.num_reg_images())
    registered = rec.num_reg_images()
    if registered < 0.6 * len(names):
        warnings.append(f"Only {registered} of {len(names)} photos could be aligned.")
    log(f"Aligned {registered}/{len(names)} photos, reprojection error {rec.compute_mean_reprojection_error():.2f} px")

    # --- put the model in the phone's metric frame
    aligned, pose_rms = False, None
    if bundle.has_poses:
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
    und.max_image_size = quality.photo_size
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
