"""The reconstruction pipeline: capture bundle -> scaled, cleaned, textured model + report.

Everything is computed in the reconstruction frame (ARKit world when phone poses exist). The
*model frame* is applied at the very end: millimetre-true scale, +Z up, support plane at z = 0,
object centred on the origin.
"""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np
import trimesh

from . import __version__, meshops, tools
from .bundle import Bundle, load_bundle
from .geom import fit_plane_ransac, sim3_matrix, transform_points
from .mat import MatFit, detect_tags, fit_mat
from .meshops import Mesh
from .presets import QUALITY, Quality
from .views import View, focus_point, views_from_bundle

Z_UP_TO_Y_UP = np.array([[1.0, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]])  # (x, y, z) -> (x, z, -y)


@dataclass
class Options:
    quality: str = "medium"
    method: str = "auto"  # auto | lidar | photo
    isolate: bool = True  # crop to the object, remove the support plane and loose pieces
    close_bottom: bool = True  # flat, closed base where the object touched the support
    texture: bool = True  # colour texture; off for 3D printing: faster, smaller photos
    drop_blurry: bool = True  # leave out blurred / out-of-focus photos
    print_scale: float = 1.0  # measured length of the mat's 100 mm bar / 100
    texture_size: int | None = None
    gpu: bool = True  # use the graphics card when the CUDA engine is installed

    @staticmethod
    def from_dict(d: dict | None) -> "Options":
        d = d or {}
        return Options(**{k: v for k, v in d.items() if k in Options.__dataclass_fields__})


STAGES = [
    ("ingest", "Read capture"),
    ("select", "Pick sharp photos"),
    ("align", "Align photos"),
    ("scale", "Scale & orientation"),
    ("geometry", "Build geometry"),
    ("cleanup", "Isolate object"),
    ("texture", "Texture"),
    ("finalize", "Write result"),
]


def plan(bundle: Bundle, options: Options) -> dict:
    """Decide what to run for this capture. This is the 'automatic' part; options override it."""
    quality = QUALITY[options.quality]
    method = options.method
    notes = []
    if method == "auto":
        method = quality.method
    if method == "lidar" and not (bundle.has_depth and bundle.has_poses):
        method = "photo"
        posed = sum(f.pose is not None for f in bundle.frames)
        notes.append("Most photos are close-ups without LiDAR: using photogrammetry." if bundle.has_depth and posed
                     else "No LiDAR data in this capture: using photogrammetry.")
    if method == "photo" and not tools.have_openmvs():
        if bundle.has_depth and bundle.has_poses:
            method = "lidar"
            notes.append("OpenMVS is not installed: falling back to LiDAR fusion. Run 'calco tools install'.")
        else:
            raise RuntimeError("Photogrammetry needs OpenMVS. Run: calco tools install")
    isolate = options.isolate and bundle.mode != "space"
    return {"method": method, "isolate": isolate, "quality": quality.name, "notes": notes}


class Job:
    """Progress, log and status file for one pipeline run."""

    def __init__(self, job_dir: Path, stage_names: list[str]):
        self.dir = Path(job_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.dir / "log.txt"
        self.status = {
            "state": "running", "progress": 0.0, "message": "Starting", "error": None,
            "started": time.time(), "finished": None,
            "stages": [{"name": n, "label": l, "state": "pending", "seconds": None}
                       for n, l in STAGES if n in stage_names],
        }
        self._stage = None
        self._t0 = None
        self._last_write = 0.0
        self.save(force=True)

    def log(self, msg: str) -> None:
        with self.log_file.open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

    def save(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_write < 0.3:
            return
        self._last_write = now
        tmp = self.dir / "status.json.tmp"
        tmp.write_text(json.dumps(self.status), encoding="utf-8")
        # Windows refuses to replace a file another process is reading at that instant (the UI
        # polls it): retry briefly, and never let a status update stop the reconstruction
        for attempt in range(20):
            try:
                tmp.replace(self.dir / "status.json")
                return
            except PermissionError:
                time.sleep(0.05 * (attempt + 1))
        self._last_write = 0.0  # try again on the next update

    def _entry(self, name):
        return next(s for s in self.status["stages"] if s["name"] == name)

    def start(self, name: str) -> None:
        self._stage, self._t0 = name, time.time()
        self._entry(name)["state"] = "running"
        self.status["message"] = self._entry(name)["label"]
        self.log(f"== {name}")
        self.save(force=True)

    def progress(self, fraction: float, message: str = "") -> None:
        names = [s["name"] for s in self.status["stages"]]
        i = names.index(self._stage)
        self.status["progress"] = (i + min(max(fraction, 0.0), 1.0)) / len(names)
        if message:
            self.status["message"] = message
        self.save()

    def done(self) -> None:
        e = self._entry(self._stage)
        e["state"], e["seconds"] = "done", round(time.time() - self._t0, 1)
        self.progress(1.0)
        self.save(force=True)

    def finish(self, state: str, error: str | None = None) -> None:
        self.status.update(state=state, error=error, finished=time.time())
        if state == "done":
            self.status["progress"], self.status["message"] = 1.0, "Done"
        for s in self.status["stages"]:
            if s["state"] == "running":
                s["state"] = "failed" if state == "failed" else "pending"
        self.save(force=True)


@dataclass
class ModelFrame:
    M: np.ndarray  # 4x4 similarity, reconstruction frame -> model frame
    scale_source: str  # mat | lidar | none
    has_plane: bool
    mat: MatFit | None = None
    lidar_vs_mat_pct: float | None = None
    notes: list[str] = field(default_factory=list)


def _detect_mat(views: list[View], print_scale: float, job: Job) -> MatFit | None:
    from concurrent.futures import ThreadPoolExecutor

    from .select import spread_subset

    # 40 photos spread over the capture are plenty, and half resolution keeps sub-pixel corners
    by_name = {v.name: v for v in views}
    views = [by_name[n] for n in spread_subset([v.name for v in views], 40)]

    def tags(v: View) -> dict:
        gray = cv2.imread(str(v.image), cv2.IMREAD_REDUCED_GRAYSCALE_2 if max(v.width, v.height) > 2400 else cv2.IMREAD_GRAYSCALE)
        if gray is None:
            return {}
        k = v.width / gray.shape[1]
        return {tid: (c + 0.5) * k - 0.5 for tid, c in detect_tags(gray).items()}

    with ThreadPoolExecutor(max_workers=4) as pool:
        obs = list(pool.map(tags, views))
    job.progress(0.9, "Looking for the scan mat")
    fit = fit_mat(obs, [v.P for v in views], print_scale)
    if fit:
        job.log(f"Scan mat {fit.layout}: {fit.tags_used} tags, fit {fit.rms_mm:.2f} mm RMS, "
                f"scale {fit.scale:.5f} ({fit.method})")
    else:
        job.log("No scan mat found")
    return fit


def _frame_from_plane(mesh: Mesh, up: np.ndarray, threshold: float, metric: bool) -> ModelFrame:
    """Model frame from the support plane found in the mesh (no mat)."""
    h = mesh.v @ up
    low = mesh.v[h <= np.percentile(h, 40)]
    if len(low) > 60000:
        low = low[np.random.default_rng(0).choice(len(low), 60000, replace=False)]
    plane = fit_plane_ransac(low, threshold, prefer_normal=up, max_angle_deg=20 if metric else 35)
    src = "lidar" if metric else "none"
    if plane is not None and plane[2].mean() > 0.15:
        n, d, _ = plane
        z, origin, has_plane = n, -d * n, True
    else:
        z, origin, has_plane = up, mesh.v[np.argmin(h)], False
    x = np.array([1.0, 0, 0]) - z[0] * z
    if np.linalg.norm(x) < 1e-3:
        x = np.array([0.0, 0, 1]) - z[2] * z
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    R = np.stack([x, y, z])
    return ModelFrame(sim3_matrix(1.0, R, -R @ origin), src, has_plane)


def _isolate(mesh: Mesh, frame: ModelFrame, crop_box, views: list[View], cut: float,
             close_bottom: bool, job: Job) -> Mesh:
    # 1. region of interest, in the reconstruction frame
    if crop_box is not None:
        inside = crop_box.contains(mesh.v, margin=0.02 * float(crop_box.size.max()))
    else:
        c = focus_point(views)
        r = 0.75 * float(np.median([np.linalg.norm(v.center - c) for v in views]))
        inside = np.linalg.norm(mesh.v - c, axis=1) < r
    mesh = mesh.select_vertices(inside)
    if len(mesh.f) == 0:
        raise RuntimeError("Nothing left inside the crop region. Check the crop box around the object.")

    # 2. cut away the support plane (z = 0 in the model frame)
    if frame.has_plane:
        z = transform_points(frame.M, mesh.v)[:, 2]
        if frame.scale_source == "none":  # arbitrary units: cut relative to the object height
            cut = 0.01 * float(np.percentile(z, 99))
        mesh = mesh.select_vertices(z > cut)
        if len(mesh.f) == 0:
            raise RuntimeError("The whole mesh lies on the support plane.")
    if frame.has_plane:
        # a piece must rise 1.5 mm above the cut: table crumbs do not, thin parts (washers, pliers) do
        mesh = meshops.drop_flat_pieces(mesh, transform_points(frame.M, mesh.v)[:, 2], min_height=cut + 0.0015)
    mesh = meshops.keep_largest(mesh, min_ratio=0.02)

    # 3. flat closed base: snap the open rim down to the plane, then cap it
    if frame.has_plane and close_bottom:
        vm = transform_points(frame.M, mesh.v)
        rim = meshops.boundary_vertices(mesh) & (vm[:, 2] < 4 * cut)
        vm[rim, 2] = 0.0
        mesh.v = transform_points(np.linalg.inv(frame.M), vm)
        size = float(np.linalg.norm(mesh.v.max(0) - mesh.v.min(0)))
        try:
            mesh = meshops.fill_holes(mesh, max_hole_size=size)
        except Exception as e:  # hole filling is best effort
            job.log(f"Hole filling skipped: {e}")
    return meshops.tidy(mesh)


def _settle_on_ground(mesh: Mesh, frame: ModelFrame, cut: float, job: Job) -> float:
    """Put z = 0 exactly on the reconstructed support surface and size the cut to its noise.

    The fitted plane can sit a millimetre or two off the real surface (reflections on glossy
    tablecloths put points below it, paper is not perfectly flat). The surface is the densest
    layer of points near the plane: move z = 0 to that peak, then cut 3 sigma above it.
    """
    z = transform_points(frame.M, mesh.v)[:, 2]
    near = z[np.abs(z) < 0.006]
    if len(near) < 2000:
        return cut
    hist, edges = np.histogram(near, bins=np.arange(-0.006, 0.00601, 0.0002))
    peak = float(edges[np.argmax(hist)] + 0.0001)
    layer = near[np.abs(near - peak) < 0.002]
    sigma = float(np.median(np.abs(layer - np.median(layer))) * 1.4826)
    frame.M[2, 3] -= peak
    new_cut = float(np.clip(3.0 * sigma + 0.0004, cut, 0.004))
    job.log(f"Support surface: moved {peak * 1000:+.2f} mm, noise {sigma * 1000:.2f} mm, cutting {new_cut * 1000:.2f} mm above it")
    return new_cut


def frame_scale(frame: ModelFrame) -> float:
    """Model-frame units per reconstruction-frame unit."""
    return float(np.cbrt(abs(np.linalg.det(frame.M[:3, :3]))))


def run(bundle_path: str | Path, job_dir: str | Path, options: Options | None = None) -> dict:
    """Run the full pipeline. Returns the report (also written to job_dir/report.json)."""
    options = options or Options()
    job_dir = Path(job_dir).resolve()  # external tools run with their own working directory
    bundle = load_bundle(Path(bundle_path).resolve())
    p = plan(bundle, options)
    quality: Quality = QUALITY[p["quality"]]
    photo = p["method"] == "photo"
    skip = set()
    if not photo:
        skip |= {"align", "select"}
    if not options.drop_blurry:
        skip.add("select")
    if not p["isolate"]:
        skip.add("cleanup")
    if not options.texture:
        skip.add("texture")
    names = [n for n, _ in STAGES if n not in skip]
    job = Job(Path(job_dir), names)
    work = job.dir / "work"
    work.mkdir(exist_ok=True)
    out = job.dir / "result"
    out.mkdir(exist_ok=True)
    report: dict = {"engine": __version__, "capture": bundle.summary(), "plan": p, "options": asdict(options),
                    "warnings": list(p["notes"])}
    try:
        job.start("ingest")
        job.log(f"Capture {bundle.name}: {len(bundle.frames)} frames, mode {bundle.mode}, plan {p}")
        job.done()

        # ---- photos worth using
        scores = None
        if "select" in names:
            from .select import drop_blurry

            job.start("select")
            bundle, dropped, scores = drop_blurry(bundle, job.log, job.progress)
            report["photos"] = {"dropped_blurry": dropped, "used": len(bundle.frames)}
            job.done()

        # ---- cameras
        sfm = None
        if photo:
            from .sfm import run_sfm

            job.start("align")
            sfm = run_sfm(bundle, work, quality, job.log, job.progress, options.gpu,
                          photo_size=None if options.texture else quality.dense_size)
            report["compute"] = {"features": sfm.computed_on, "dense": "cpu"}
            views = sfm.views
            report["warnings"] += sfm.warnings
            report["alignment"] = {"registered": sfm.registered, "total": sfm.total,
                                   "reprojection_px": round(sfm.reprojection_px, 3),
                                   "phone_pose_rms_mm": sfm.pose_rms_mm and round(sfm.pose_rms_mm, 2)}
            if sfm.sparse_ply:
                (out / "sparse.ply").write_bytes(sfm.sparse_ply.read_bytes())
            job.done()
        else:
            views = views_from_bundle(bundle)
        metric = sfm.aligned if sfm is not None else bundle.has_poses

        # ---- scale reference
        job.start("scale")
        mat = _detect_mat(views, options.print_scale, job)
        job.done()

        # ---- geometry
        job.start("geometry")
        if photo:
            from .mvs import dense_mesh

            mesh_path, dense_on = dense_mesh(sfm.dense_dir, work, quality, job.log, job.progress, options.gpu, scores)
            report["compute"]["dense"] = dense_on
            try:
                mesh = Mesh.read(mesh_path)
            except RuntimeError:
                mesh = Mesh(np.zeros((0, 3)), np.zeros((0, 3), int))
            if len(mesh.f) < 200:
                raise RuntimeError("The dense reconstruction found almost no surface. The photos overlap too little "
                                   "or the object has no texture (plain, shiny or transparent): re-shoot with more "
                                   "photos and small steps, or use matte spray.")
        else:
            from .tsdf import fuse_depth

            mesh = meshops.smooth(fuse_depth(bundle, job.progress), 4)  # LiDAR noise; before the base is flattened
        job.log(f"Raw mesh: {len(mesh.v)} vertices, {len(mesh.f)} faces")
        job.done()

        # ---- model frame
        # the box base may sit on the object's top (where the user aimed): look below it too
        crop = bundle.crop_box.extended_down(float(bundle.crop_box.size.max())) if bundle.crop_box is not None else None
        cut = 0.0012 if photo else 0.006  # metres above the support plane that are discarded
        if mat is not None:
            frame = ModelFrame(mat.transform, "mat", True, mat)
            if photo and mat.rms_mm > 1.0:
                report["warnings"].append(
                    f"The scan mat is not flat (about {mat.rms_mm:.1f} mm out of plane). Scale was taken from "
                    "neighbouring tags, which bending barely affects, but a rigid mat is more accurate.")
            if metric:
                frame.lidar_vs_mat_pct = round((mat.scale - 1.0) * 100, 3)
        else:
            up = np.array([0.0, 1.0, 0.0]) if metric else np.mean([v.up for v in views], axis=0)
            up = up / np.linalg.norm(up)
            roi = mesh
            if p["isolate"] and crop is not None and metric:
                roi = mesh.select_vertices(crop.contains(mesh.v, margin=0.05))
            frame = _frame_from_plane(roi if len(roi.f) else mesh, up, 2.5 * cut, metric)
            if not metric:
                report["warnings"].append("No scale reference (no LiDAR poses, no scan mat): the model is not to "
                                          "scale. Use Calibrate with a known measurement.")

        if frame.has_plane and frame.scale_source != "none":
            cut = _settle_on_ground(mesh, frame, cut, job)

        # ---- isolate object
        if p["isolate"]:
            job.start("cleanup")
            mesh = _isolate(mesh, frame, crop if metric else None, views, cut,
                            options.close_bottom, job)
            job.progress(0.6, "Simplifying")
            mesh = meshops.decimate(mesh, quality.target_faces)
            job.done()
        else:
            mesh = meshops.decimate(meshops.tidy(mesh), quality.target_faces)

        # ---- texture
        tex_size = options.texture_size or quality.texture_size
        if not options.texture:
            tm = trimesh.Trimesh(mesh.v, mesh.f, process=False)  # geometry only, e.g. for 3D printing
        elif photo:
            job.start("texture")
            from .mvs import texture_mesh

            cleaned = work / "mvs" / "cleaned.ply"
            mesh.write(cleaned)
            job.progress(0.1, "Texturing from photos")
            tm = trimesh.load(texture_mesh(work, cleaned, quality, job.log), force="mesh", process=False)
            # photo textures already contain the lighting: plain, non-metallic material
            image = getattr(tm.visual.material, "image", None) or getattr(tm.visual.material, "baseColorTexture", None)
            tm.visual.material = trimesh.visual.material.PBRMaterial(
                baseColorTexture=image, metallicFactor=0.0, roughnessFactor=1.0)
            job.done()
        else:
            job.start("texture")
            from .texture import bake_texture

            from .select import spread_subset

            by_name = {v.name: v for v in views}  # 40 well-spread photos colour a LiDAR mesh fine
            tex_views = [by_name[n] for n in spread_subset(list(by_name), 40)]
            tm = bake_texture(mesh, tex_views, min(tex_size, 4096), job.progress)
            job.done()

        # ---- finalize: move into the model frame, centred on the object
        job.start("finalize")
        vm = transform_points(frame.M, np.asarray(tm.vertices))
        centre = (vm.min(0) + vm.max(0)) / 2
        shift = np.array([centre[0], centre[1], 0.0 if frame.has_plane else vm[:, 2].min()])
        vm -= shift
        if frame.has_plane and p["isolate"]:
            vm[:, 2] = np.maximum(vm[:, 2], 0.0)  # nothing below the surface the object stood on
        tm.vertices = vm
        final_M = frame.M.copy()
        final_M[:3, 3] -= shift
        np.save(out / "model_from_capture.npy", final_M)

        glb = tm.copy()
        glb.apply_transform(Z_UP_TO_Y_UP)
        glb.export(out / "model.glb")

        geo = Mesh(vm, np.asarray(tm.faces))
        dims = (vm.max(0) - vm.min(0)) * 1000.0
        report["model"] = {
            "vertices": int(len(vm)), "faces": int(len(tm.faces)),
            "dimensions_mm": [round(float(d), 2) for d in dims],
            "watertight": meshops.is_watertight(meshops.tidy(geo)),
            "textured": options.texture,
        }
        report["scale"] = _scale_report(frame, dims, views, photo, quality)
        report["seconds"] = round(time.time() - job.status["started"], 1)
        (job.dir / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
        job.done()
        job.finish("done")
        return report
    except Exception as e:  # report the failure to the UI; the traceback goes to the log
        job.log(traceback.format_exc())
        job.finish("failed", str(e))
        raise


def _scale_report(frame: ModelFrame, dims_mm: np.ndarray, views: list[View], photo: bool, quality: Quality) -> dict:
    """Where the scale comes from and an honest estimate of the dimensional error."""
    largest = float(dims_mm.max())
    if frame.scale_source == "mat":
        if frame.mat.method == "layout":
            mat_span = {"A5": 180.0, "A4": 250.0, "A3": 360.0}[frame.mat.layout]
            scale_pct = 100.0 * frame.mat.rms_mm / mat_span + 0.05  # + printer/paper tolerance
        else:
            scale_pct = 0.2  # bent sheet: scale from neighbouring tags
    elif frame.scale_source == "lidar":
        scale_pct = 1.0
    else:
        scale_pct = None
    if photo and views:
        v = views[0]
        d = float(np.median([np.linalg.norm(x.center) for x in views])) if frame.scale_source == "none" else \
            float(np.median([np.linalg.norm(x.center - focus_point(views)) for x in views]))
        px = d * frame_scale(frame) / v.K[0, 0] * max(1.0, max(v.width, v.height) / quality.dense_size) * 1000.0
        noise_mm = px  # about one dense-matching pixel
    else:
        noise_mm = 5.0
    return {
        "source": frame.scale_source,
        "mat": frame.mat and {"layout": frame.mat.layout, "tags": frame.mat.tags_used,
                              "fit_rms_mm": round(frame.mat.rms_mm, 3), "method": frame.mat.method},
        "lidar_vs_mat_pct": frame.lidar_vs_mat_pct,
        "scale_error_pct": scale_pct and round(scale_pct, 3),
        "surface_noise_mm": round(noise_mm, 2),
        "expected_error_mm": None if scale_pct is None else round(largest * scale_pct / 100.0 + noise_mm, 2),
    }
