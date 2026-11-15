"""End-to-end tests on a synthetic capture (no phone, no GPU, no OpenMVS needed)."""

import json

import numpy as np
import pytest

from calco import meshops
from calco.bundle import BundleError, load_bundle
from calco.export import export_model
from calco.geom import robust_umeyama
from calco.mat import LAYOUTS, detect_tags, fit_mat, render_mat
from calco.pipeline import Options, run
from calco.synth import OBJECT_SIZE_M, make_synthetic_bundle

# model frame: x = 80 mm, y = 60 mm (mat y), z = 80 mm (height)
TRUE_MM = np.array([OBJECT_SIZE_M[0], OBJECT_SIZE_M[2], OBJECT_SIZE_M[1]]) * 1000


@pytest.fixture(scope="session")
def bundle_dir(tmp_path_factory):
    return make_synthetic_bundle(tmp_path_factory.mktemp("cap") / "synth.calco", views_per_ring=10,
                                 image_size=(960, 720))


@pytest.fixture(scope="session")
def draft_job(bundle_dir, tmp_path_factory):
    job = tmp_path_factory.mktemp("job")
    report = run(bundle_dir, job, Options(quality="draft"))
    return job, report


def test_bundle_loads(bundle_dir):
    b = load_bundle(bundle_dir)
    assert len(b.frames) == 20 and b.has_poses and b.has_depth and b.crop_box is not None
    d = b.frames[0].load_depth()
    assert d.shape == (192, 256) and 0.15 < d[d > 0].min() < 0.5


def test_bundle_rejects_garbage(tmp_path):
    with pytest.raises(BundleError):
        load_bundle(tmp_path)
    (tmp_path / "manifest.json").write_text(json.dumps({"format": "calco-capture", "version": 99}))
    with pytest.raises(BundleError):
        load_bundle(tmp_path)


def test_umeyama_recovers_similarity():
    rng = np.random.default_rng(0)
    src = rng.normal(size=(50, 3))
    R = np.linalg.qr(rng.normal(size=(3, 3)))[0]
    R *= np.sign(np.linalg.det(R))
    dst = 2.5 * src @ R.T + np.array([1.0, -2.0, 0.5])
    dst[:3] += 5.0  # outliers
    s, R2, t, keep, rms = robust_umeyama(src, dst)
    assert abs(s - 2.5) < 1e-6 and np.allclose(R, R2, atol=1e-6) and rms < 1e-6 and keep.sum() == 47


def test_mat_roundtrip():
    layout = LAYOUTS["A4"]
    tags = detect_tags(render_mat(layout, dpi=150))
    assert len(tags) == layout.grid[0] * layout.grid[1] == 48
    ref = layout.tag_corners_page_mm()
    px = 150 / 25.4
    err = max(np.abs(tags[i] / px - ref[i]).max() for i in tags)
    assert err < 0.2  # mm


def test_mat_scale_from_views(bundle_dir):
    import cv2

    b = load_bundle(bundle_dir)
    obs = [detect_tags(cv2.imread(str(f.image), cv2.IMREAD_GRAYSCALE)) for f in b.frames]
    fit = fit_mat(obs, [f.K @ f.world_to_cam_cv[:3] for f in b.frames])
    assert fit is not None and fit.layout == "A4" and fit.tags_used >= 20
    assert abs(fit.scale - 1.0) < 0.01


def test_draft_pipeline_dimensions(draft_job):
    job, report = draft_job
    assert report["plan"]["method"] == "lidar"
    assert report["scale"]["source"] == "mat"
    dims = np.array(report["model"]["dimensions_mm"])
    assert np.all(np.abs(dims - TRUE_MM) < 8.0), dims  # LiDAR fusion: within 8 mm
    assert (job / "result" / "model.glb").exists()
    status = json.loads((job / "status.json").read_text())
    assert status["state"] == "done" and all(s["state"] == "done" for s in status["stages"])


def test_exports(draft_job):
    import trimesh

    job, report = draft_job
    for fmt in ("glb", "obj", "stl", "3mf", "ply", "usdz"):
        path = export_model(job / "result", job / "exports", "part", fmt)
        assert path.exists() and path.stat().st_size > 1000
    stl = trimesh.load(job / "exports" / "part.stl")
    assert np.allclose(stl.extents, report["model"]["dimensions_mm"], atol=0.05)  # millimetres, Z up
    assert abs(stl.bounds[0][2]) < 0.05  # sits on z = 0
    obj = trimesh.load(job / "exports" / "part.obj", force="mesh")
    assert abs(obj.extents[1] * 1000 - report["model"]["dimensions_mm"][2]) < 0.05  # metres, Y up

    import zipfile

    usdz = zipfile.ZipFile(job / "exports" / "part.usdz").namelist()
    assert usdz[0].endswith(".usdc") and any(n.endswith("texture.jpg") for n in usdz)  # layer first, texture inside

    # DXF sections in millimetres: horizontal slices plus the two middle sections
    dxf = export_model(job / "result", job / "exports", "part", "dxf").read_text().split("\n")
    layers = {dxf[i + 1] for i, v in enumerate(dxf) if v == "8"}
    assert {"X_MID", "Y_MID"} <= layers and sum(name.startswith("Z_") for name in layers) >= 5
    xs = [float(dxf[i + 1]) for i in range(len(dxf) - 1) if dxf[i] in ("10", "11")]
    assert abs(max(xs) - min(xs) - report["model"]["dimensions_mm"][0]) < 5  # widest section ~ the part

    from calco.report_pdf import write_report

    pdf = write_report(job, job / "report.pdf", "Synthetic part")
    assert pdf.read_bytes()[:5] == b"%PDF-" and pdf.stat().st_size > 20_000


def test_mesh_cleanup_ops():
    import open3d as o3d

    a = meshops.Mesh.from_o3d(o3d.geometry.TriangleMesh.create_sphere(1.0, 20))
    b = meshops.Mesh.from_o3d(o3d.geometry.TriangleMesh.create_sphere(0.1, 6).translate([5, 0, 0]))
    both = meshops.Mesh(np.vstack([a.v, b.v]), np.vstack([a.f, b.f + len(a.v)]))
    assert len(meshops.keep_largest(both, 0.2).f) == len(a.f)
    assert meshops.is_watertight(a)
    opened = a.select_vertices(a.v[:, 2] > -0.5)
    assert not meshops.is_watertight(opened) and meshops.boundary_vertices(opened).any()
    assert meshops.is_watertight(meshops.tidy(meshops.fill_holes(opened, 10.0)))


def test_server_upload_and_job_flow(bundle_dir, tmp_path):
    from fastapi.testclient import TestClient

    from calco.library import Library
    from calco.server import create_app

    client = TestClient(create_app(Library(tmp_path / "lib")))
    assert client.get("/api/info").json()["name"] == "Calco"
    cid = load_bundle(bundle_dir).id
    files = sorted(p for p in bundle_dir.rglob("*") if p.is_file())
    for p in files[:6] + [bundle_dir / "manifest.json"]:
        rel = p.relative_to(bundle_dir).as_posix()
        assert client.put(f"/api/captures/{cid}/files/{rel}", content=p.read_bytes()).status_code == 200
    have = client.get(f"/api/captures/{cid}/have").json()
    assert have["files"]["manifest.json"] > 0 and not have["complete"]
    assert client.post(f"/api/captures/{cid}/complete").status_code == 422  # frames still missing
    for p in files:
        rel = p.relative_to(bundle_dir).as_posix()
        if rel not in have["files"]:
            client.put(f"/api/captures/{cid}/files/{rel}", content=p.read_bytes())
    assert client.post(f"/api/captures/{cid}/complete").json()["capture"]["frames"] == 20
    for sneaky in ("../../evil.txt", "%2e%2e/%2e%2e/evil.txt", "..%5C..%5Cevil.txt"):
        assert client.put(f"/api/captures/{cid}/files/{sneaky}", content=b"x").status_code in (400, 404, 405)
    assert not list((tmp_path / "lib").rglob("evil.txt")) and not (tmp_path / "evil.txt").exists()
    caps = client.get("/api/captures").json()
    assert len(caps) == 1 and caps[0]["id"] == cid
    assert client.get(f"/api/captures/{cid}/preview").status_code == 200
    assert client.get("/api/mat.pdf?size=A4").headers["content-type"] == "application/pdf"
    assert client.post("/api/jobs", json={"capture_id": "nope", "options": {}}).status_code == 404


def test_every_mat_layout_is_detected():
    for layout in LAYOUTS.values():
        tags = detect_tags(render_mat(layout, dpi=150))
        assert set(tags) == set(layout.tag_corners_page_mm()), layout.name


def test_bent_mat_still_gives_the_scale():
    """A curled sheet must not bias the scale: it comes from neighbouring tags then."""
    from calco.geom import look_at_arkit

    ref = LAYOUTS["A4"].tag_corners_mat()
    flip = np.diag([1.0, -1, -1, 1])
    K = np.array([[3000.0, 0, 2000], [0, 3000, 1500], [0, 0, 1]])
    radius = 0.4  # metres: the sheet lifts about 3.5 mm at its edges

    def bend(p):
        th = p[:, 0] / radius
        return np.stack([radius * np.sin(th), p[:, 1], radius * (1 - np.cos(th))], 1)

    rng = np.random.default_rng(0)
    projections, observations = [], []
    for k in range(24):
        az, el = 2 * np.pi * k / 24, np.radians(35 if k % 2 else 55)
        eye = 0.45 * np.array([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)])
        P = K @ np.linalg.inv(look_at_arkit(eye, np.zeros(3), up=(0, 0, 1)) @ flip)[:3]
        projections.append(P)
        obs = {}
        for tid, c in ref.items():
            x = np.c_[bend(c), np.ones(4)] @ P.T
            obs[tid] = x[:, :2] / x[:, 2:] + rng.normal(0, 0.15, (4, 2))
        observations.append(obs)
    fit = fit_mat(observations, projections)
    assert fit.method == "neighbours" and fit.rms_mm > 1.0
    assert abs(fit.scale - 1.0) < 0.001


def test_printable_mat_files():
    import io

    import trimesh

    from calco.mat import mat_3d_files

    files = mat_3d_files("A5")
    base = trimesh.load(io.BytesIO(files["base.stl"]), file_type="stl")
    pattern = trimesh.load(io.BytesIO(files["pattern.stl"]), file_type="stl")
    assert np.allclose(base.extents, [148, 210, 2])
    assert pattern.bounds[0][2] < 2.0 < pattern.bounds[1][2] and pattern.extents[0] < 148

    # two-colour 3MF: one object, two parts, filaments 1 and 2 (Bambu Studio / OrcaSlicer)
    import zipfile

    z = zipfile.ZipFile(io.BytesIO(files["scan-mat-A5-two-colour.3mf"]))
    config = z.read("Metadata/model_settings.config").decode()
    assert config.count("<part ") == 2 and 'key="extruder" value="2"' in config
    model = trimesh.load(io.BytesIO(files["scan-mat-A5-two-colour.3mf"]), file_type="3mf", force="mesh")
    assert np.allclose(model.extents[:2], [148, 210])


def test_splat_follows_the_model_frame(tmp_path):
    from scipy.spatial.transform import Rotation

    from calco.splat import transform_splat

    cols = (["x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2"] + [f"f_rest_{i}" for i in range(9)]
            + ["opacity", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"])
    rng = np.random.default_rng(0)
    data = rng.normal(size=(50, len(cols))).astype("<f4")
    q = Rotation.random(50, random_state=1).as_quat()  # x, y, z, w
    data[:, -4:] = q[:, [3, 0, 1, 2]]
    head = "ply\nformat binary_little_endian 1.0\nelement vertex 50\n" + "".join(f"property float {c}\n" for c in cols)
    src = tmp_path / "in.ply"
    src.write_bytes((head + "end_header\n").encode() + data.tobytes())
    R = Rotation.from_euler("xyz", [0.3, -1.1, 2.0]).as_matrix()
    T = np.eye(4)
    T[:3, :3] = 2.0 * R
    T[:3, 3] = [1, 2, 3]
    assert transform_splat(src, tmp_path / "out.ply", T) == 50
    raw = (tmp_path / "out.ply").read_bytes()
    out = np.frombuffer(raw[raw.index(b"end_header\n") + 11:], "<f4").reshape(50, -1)
    assert np.allclose(out[:, :3], data[:, :3] @ (2 * R).T + [1, 2, 3], atol=1e-4)
    assert np.allclose(out[:, 19:22], data[:, 19:22] + np.log(2), atol=1e-5)
    # each splat's axes are rotated with the model
    before = Rotation.from_quat(data[:, -4:][:, [1, 2, 3, 0]]).as_matrix()
    after = Rotation.from_quat(out[:, -4:][:, [1, 2, 3, 0]]).as_matrix()
    assert np.allclose(after, R @ before, atol=1e-4)

    # cropping to the region of interest keeps the file consistent
    from calco.bundle import CropBox

    kept = transform_splat(src, tmp_path / "crop.ply", T, CropBox(np.zeros(3), np.full(3, 1.0)))
    raw = (tmp_path / "crop.ply").read_bytes()
    assert 0 < kept < 50 and f"element vertex {kept}\n".encode() in raw
    assert len(raw) - raw.index(b"end_header\n") - 11 == kept * len(cols) * 4


def test_mixed_phone_and_closeup_photos(tmp_path):
    """Some photos with phone poses, some close-ups without (other lens, unknown intrinsics):
    every photo must be placed and the model must still be in the phone's metric frame."""
    import json as _json

    from calco.presets import QUALITY
    from calco.sfm import run_sfm

    cap = make_synthetic_bundle(tmp_path / "mixed.calco", views_per_ring=10, image_size=(800, 600), seed=3)
    m = _json.loads((cap / "manifest.json").read_text())
    for f in m["frames"][::4]:  # a quarter of the photos become "close-ups" without pose
        f["pose"], f["depth"], f["tracking"] = None, None, "none"
        f["intrinsics"] = [0, 0, f["width"] / 2, f["height"] / 2]
    (cap / "manifest.json").write_text(_json.dumps(m))
    b = load_bundle(cap)
    r = run_sfm(b, tmp_path / "job", QUALITY["draft"], log=lambda *_: None, use_gpu=False)
    assert r.registered == len(b.frames)
    assert r.aligned and r.pose_rms_mm < 10


def test_two_sided_scan_is_combined(tmp_path):
    """Flip on the phone: photos of side 0, then of the part turned over (side 1). The engine
    reconstructs each side and fuses them into one closed part of the right size."""
    import json as _json
    import shutil

    a = make_synthetic_bundle(tmp_path / "a.calco", views_per_ring=8, image_size=(640, 480), seed=11)
    b = make_synthetic_bundle(tmp_path / "b.calco", views_per_ring=8, image_size=(640, 480), seed=12, turned_over=True)
    ma, mb = _json.loads((a / "manifest.json").read_text()), _json.loads((b / "manifest.json").read_text())
    offset = len(ma["frames"])
    for f in mb["frames"]:  # move side 1's files into the first bundle under new names
        new = f["id"] + offset
        for key, sub in (("image", None), ("depth", "file"), ("depth", "confidence")):
            old = f[key] if sub is None else f[key][sub]
            target = old.replace(f"{f['id']:06d}", f"{new:06d}")
            shutil.copy(b / old, a / target)
            if sub is None:
                f[key] = target
            else:
                f[key][sub] = target
        f["id"], f["side"] = new, 1
    ma["frames"] += mb["frames"]
    ma["sides"] = [{"crop_box": ma["crop_box"]}, {"crop_box": mb["crop_box"]}]
    (a / "manifest.json").write_text(_json.dumps(ma))

    bundle = load_bundle(a)
    assert bundle.sides == [0, 1] and len(bundle.only_side(1).frames) == 16
    report = run(a, tmp_path / "job", Options(quality="draft", texture=False))
    dims = sorted(report["model"]["dimensions_mm"])
    assert report["combined"]["overlap_pct"] > 40
    assert all(abs(d - t) < 10 for d, t in zip(dims, sorted(TRUE_MM))), dims  # LiDAR: within 1 cm
    assert (tmp_path / "job" / "result" / "side2.ply").exists()


def test_import_photos_and_video(bundle_dir, tmp_path):
    import cv2
    from fastapi.testclient import TestClient

    from calco.library import Library
    from calco.server import create_app

    frames = sorted((bundle_dir / "frames").glob("*.jpg"))
    video = tmp_path / "turn.mp4"
    first = cv2.imread(str(frames[0]))
    vw = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 10, (first.shape[1], first.shape[0]))
    for f in frames:
        for _ in range(5):  # 5 video frames per photo: the importer keeps about 2 per second
            vw.write(cv2.imread(str(f)))
    vw.release()

    lib = Library(tmp_path / "lib")
    client = TestClient(create_app(lib))
    iid = "0f8e3a1c-1111-2222-3333-444455556666"
    for p in frames[:5] + [video]:
        assert client.put(f"/api/import/{iid}/files/{p.name}", content=p.read_bytes()).status_code == 200
    assert client.put(f"/api/import/{iid}/files/notes.txt", content=b"x").status_code == 415
    r = client.post(f"/api/import/{iid}/complete", json={"name": "Imported part"})
    assert r.status_code == 200, r.text
    cap = r.json()["capture"]
    assert cap["name"] == "Imported part" and not cap["has_poses"] and not cap["has_depth"]
    b = load_bundle(lib.capture_path(cap["id"]))
    assert b.mode == "import" and len(b.frames) == 5 + 20  # 100 video frames at 10 fps: 2 per second kept
    assert not (lib.incoming / f"import-{iid}").exists()


def test_scale_markers():
    from calco.mat import MARKER_MM, detect_tags, fit_markers, render_markers

    page = render_markers(0, dpi=150)
    assert set(detect_tags(page)) == set(range(400, 406))

    # three markers flat on the floor, a reconstruction 1/0.7 too big: the fit must give 0.7
    rng = np.random.default_rng(3)
    half = MARKER_MM / 2000.0
    square = np.array([[-half, 0, -half], [half, 0, -half], [half, 0, half], [-half, 0, half]])
    markers = {400 + k: square + [0.4 * k - 0.4, 0, 0.3 * (k % 2)] for k in range(3)}
    K = np.array([[1000.0, 0, 640], [0, 1000.0, 480], [0, 0, 1]])
    obs, projections = [], []
    for k in range(8):
        a = 2 * np.pi * k / 8
        eye = np.array([1.2 * np.cos(a), 1.0, 1.2 * np.sin(a)])
        f = -eye / np.linalg.norm(eye)
        r = np.cross(f, [0, 1.0, 0]); r /= np.linalg.norm(r)
        d = np.cross(f, r)
        R = np.stack([r, d, f])  # OpenCV camera: x right, y down, z forward
        P = K @ np.hstack([R, (-R @ eye)[:, None]])
        seen = {}
        for tid, pts in markers.items():
            uv = (P @ np.c_[pts, np.ones(4)].T).T
            seen[tid] = uv[:, :2] / uv[:, 2:] + rng.normal(0, 0.2, (4, 2))
        obs.append(seen)
        S = np.diag([1 / 0.7] * 3 + [1.0])  # same cameras in a frame 1/0.7 larger
        projections.append(P @ np.linalg.inv(S))
    fit = fit_markers(obs, projections)
    assert fit.markers == 3 and abs(fit.scale - 0.7) < 0.7 * 0.003


def test_dense_photos_cropped_to_the_part(tmp_path):
    """crop_to_box keeps the model consistent and cuts the photos down to the part on the table."""
    import cv2
    import pycolmap
    from scipy.spatial.transform import Rotation

    from calco.bundle import CropBox
    from calco.mvs import crop_to_box

    rng = np.random.default_rng(0)
    table = np.c_[rng.uniform(-0.5, 0.5, 3000), np.zeros(3000), rng.uniform(-0.5, 0.5, 3000)]
    part = np.c_[rng.uniform(-0.05, 0.05, 1500), rng.uniform(0.0, 0.06, 1500), rng.uniform(-0.04, 0.04, 1500)]
    pts = np.vstack([table, part])
    W, H, f = 1600, 1200, 1300.0
    (tmp_path / "sparse").mkdir()
    (tmp_path / "images").mkdir()
    cams, imgs, tracks = [], [], {i: [] for i in range(len(pts))}
    for k in range(6):
        a = 2 * np.pi * k / 6
        eye = np.array([0.45 * np.cos(a), 0.35, 0.45 * np.sin(a)])
        fwd = -eye / np.linalg.norm(eye)
        right = np.cross(fwd, [0, 1.0, 0]); right /= np.linalg.norm(right)
        R = np.stack([right, np.cross(fwd, right), fwd])
        t = -R @ eye
        cam = pts @ R.T + t
        uv = cam[:, :2] / cam[:, 2:] * f + [W / 2, H / 2]
        ok = (cam[:, 2] > 0) & (uv[:, 0] >= 0) & (uv[:, 0] < W) & (uv[:, 1] >= 0) & (uv[:, 1] < H)
        obs = []
        for i in np.flatnonzero(ok):
            tracks[i].append((k + 1, len(obs)))
            obs.append(f"{uv[i, 0]} {uv[i, 1]} {i + 1}")
        q = Rotation.from_matrix(R).as_quat()
        cams.append(f"{k + 1} PINHOLE {W} {H} {f} {f} {W / 2} {H / 2}")
        imgs.append(f"{k + 1} {q[3]} {q[0]} {q[1]} {q[2]} {t[0]} {t[1]} {t[2]} {k + 1} {k:02d}.jpg\n" + " ".join(obs))
        cv2.imwrite(str(tmp_path / "images" / f"{k:02d}.jpg"), np.full((H, W, 3), 128, np.uint8))
    (tmp_path / "sparse" / "cameras.txt").write_text("\n".join(cams) + "\n")
    (tmp_path / "sparse" / "images.txt").write_text("\n".join(imgs) + "\n")
    (tmp_path / "sparse" / "points3D.txt").write_text("\n".join(
        f"{i + 1} {p[0]} {p[1]} {p[2]} 128 128 128 0.5 " + " ".join(f"{a} {b}" for a, b in tracks[i])
        for i, p in enumerate(pts) if len(tracks[i]) >= 2) + "\n")

    box = CropBox(np.array([0, 0.3, 0.0]), np.full(3, 0.9))  # placed big and high, as people do on the phone
    assert crop_to_box(tmp_path, box, log=lambda m: None) == 6
    rec = pycolmap.Reconstruction(tmp_path / "sparse")
    errs = []
    for p in list(rec.points3D.values())[:500]:
        for e in p.track.elements:
            img = rec.images[e.image_id]
            uv = rec.cameras[img.camera_id].img_from_cam(img.cam_from_world() * p.xyz)
            errs.append(np.linalg.norm(uv - img.points2D[e.point2D_idx].xy))
    assert np.max(errs) < 1e-3  # the shifted cameras still see every point where it was observed
    for img in rec.images.values():
        cam = rec.cameras[img.camera_id]
        assert cam.width * cam.height < 0.5 * W * H  # the table was cut away
        assert cv2.imread(str(tmp_path / "images" / img.name)).shape[:2] == (cam.height, cam.width)
        # every part point is still inside the cropped photo
        for p in part[::50]:
            u, v = cam.img_from_cam(img.cam_from_world() * p)
            assert 0 <= u < cam.width and 0 <= v < cam.height
