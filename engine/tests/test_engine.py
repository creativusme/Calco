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
    for fmt in ("glb", "obj", "stl", "3mf", "ply"):
        path = export_model(job / "result", job / "exports", "part", fmt)
        assert path.exists() and path.stat().st_size > 1000
    stl = trimesh.load(job / "exports" / "part.stl")
    assert np.allclose(stl.extents, report["model"]["dimensions_mm"], atol=0.05)  # millimetres, Z up
    assert abs(stl.bounds[0][2]) < 0.05  # sits on z = 0
    obj = trimesh.load(job / "exports" / "part.obj", force="mesh")
    assert abs(obj.extents[1] * 1000 - report["model"]["dimensions_mm"][2]) < 0.05  # metres, Y up


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
