"""Local HTTP API. The phone uploads captures here; the desktop UI drives everything through it."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, presets, tools
from .bundle import BundleError, load_bundle
from .export import export_model
from .library import Library

PORT = 7878


class JobRequest(BaseModel):
    capture_id: str
    options: dict = {}


class ExportRequest(BaseModel):
    formats: list[str]
    units: str | None = None
    textured: bool = True


class CalibrateRequest(BaseModel):
    scale: float  # multiply the model by this factor (measured / shown)


def lan_addresses() -> list[str]:
    out = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(("127.", "169.254.")):  # skip loopback and link-local
                out.add(ip)
    except OSError:
        pass
    return sorted(out)


def create_app(library: Library | None = None, ui_dir: Path | None = None) -> FastAPI:
    lib = library or Library()
    app = FastAPI(title="Calco engine", version=__version__)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    busy = {"tools": False, "demo": False}

    def job_or_404(jid: str) -> dict:
        try:
            job = lib.get_job(jid)
        except ValueError:
            job = None
        if job is None:
            raise HTTPException(404, "job not found")
        return job

    def capture_dir(cid: str, incoming: bool = False) -> Path:
        try:
            p = lib.capture_path(cid)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return lib.incoming / p.name if incoming else p

    # ---- info
    @app.get("/api/info")
    def info():
        return {"name": "Calco", "version": __version__, "library": str(lib.root), "port": PORT,
                "addresses": lan_addresses(), "hostname": socket.gethostname(),
                "tools": {**tools.status(), "installing": busy["tools"]}, "presets": presets.describe(),
                "demo_running": busy["demo"]}

    @app.post("/api/tools/install")
    def install_tools(what: str = "openmvs"):
        if busy["tools"]:
            return {"started": False}
        busy["tools"] = True

        def work():
            try:
                (tools.install_colmap if what == "colmap" else tools.install_openmvs)(log=lambda m: None)
            finally:
                busy["tools"] = False

        threading.Thread(target=work, daemon=True).start()
        return {"started": True}

    @app.get("/api/mat.pdf")
    def mat_pdf(size: str = "A4"):
        from .mat import LAYOUTS, write_mat_pdf

        if size not in LAYOUTS:
            raise HTTPException(400, f"size must be one of {list(LAYOUTS)}")
        path = Path(tempfile.gettempdir()) / f"calco-mat-{size}.pdf"
        if not path.exists():
            write_mat_pdf(path, size)
        return FileResponse(path, media_type="application/pdf", filename=f"calco-scan-mat-{size}.pdf")

    @app.get("/api/mat.zip")
    def mat_zip(size: str = "A5"):
        """3D-printable version of the mat (plate + raised pattern)."""
        from .mat import LAYOUTS, write_mat_3d

        if size not in LAYOUTS:
            raise HTTPException(400, f"size must be one of {list(LAYOUTS)}")
        path = Path(tempfile.gettempdir()) / f"calco-mat-{size}-3d.zip"
        if not path.exists():
            write_mat_3d(path, size)
        return FileResponse(path, media_type="application/zip", filename=f"calco-scan-mat-{size}-3d-print.zip")

    @app.post("/api/demo")
    def demo():
        """Create a synthetic capture so the pipeline can be tried without a phone."""
        if busy["demo"]:
            return {"started": False}
        busy["demo"] = True

        def work():
            try:
                from .synth import make_synthetic_bundle

                tmp = Path(tempfile.mkdtemp()) / "demo.calco"
                make_synthetic_bundle(tmp)
                lib.import_capture(tmp)
                shutil.rmtree(tmp.parent, ignore_errors=True)
            finally:
                busy["demo"] = False

        threading.Thread(target=work, daemon=True).start()
        return {"started": True}

    # ---- captures
    @app.get("/api/captures")
    def captures():
        return lib.list_captures()

    @app.delete("/api/captures/{cid}")
    def delete_capture(cid: str):
        capture_dir(cid)
        lib.delete_capture(cid)
        return {"ok": True}

    @app.get("/api/captures/{cid}/preview")
    def preview(cid: str):
        d = capture_dir(cid)
        for cand in (d / "preview.jpg", *sorted((d / "frames").glob("*.jpg"))[:1]):
            if cand.exists():
                return FileResponse(cand, media_type="image/jpeg")
        raise HTTPException(404, "no preview")

    @app.get("/api/captures/{cid}/have")
    def have(cid: str):
        d = capture_dir(cid, incoming=True)
        files = {p.relative_to(d).as_posix(): p.stat().st_size for p in d.rglob("*") if p.is_file()} if d.exists() else {}
        return {"files": files, "complete": capture_dir(cid).exists()}

    @app.put("/api/captures/{cid}/files/{rel:path}")
    async def put_file(cid: str, rel: str, request: Request):
        d = capture_dir(cid, incoming=True)
        target = (d / rel).resolve()
        if d.resolve() not in target.parents:
            raise HTTPException(400, "invalid path")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        size = 0
        with tmp.open("wb") as fh:
            async for chunk in request.stream():
                fh.write(chunk)
                size += len(chunk)
        tmp.replace(target)
        return {"ok": True, "size": size}

    @app.post("/api/captures/{cid}/complete")
    def complete(cid: str):
        src, dst = capture_dir(cid, incoming=True), capture_dir(cid)
        if not src.exists():
            raise HTTPException(404, "nothing uploaded for this capture")
        try:
            bundle = load_bundle(src)
        except BundleError as e:
            raise HTTPException(422, str(e)) from e
        if dst.exists():
            shutil.rmtree(dst)
        shutil.move(str(src), str(dst))
        return {"ok": True, "capture": bundle.summary()}

    # ---- jobs
    @app.get("/api/jobs")
    def jobs():
        return lib.list_jobs()

    @app.post("/api/jobs")
    def create_job(req: JobRequest):
        if not capture_dir(req.capture_id).exists():
            raise HTTPException(404, "capture not found")
        if req.options.get("quality", "medium") not in presets.QUALITY:
            raise HTTPException(400, "unknown quality preset")
        return lib.create_job(req.capture_id, req.options)

    @app.get("/api/jobs/{jid}")
    def job(jid: str):
        return job_or_404(jid)

    @app.post("/api/jobs/{jid}/cancel")
    def cancel(jid: str):
        job_or_404(jid)
        lib.cancel_job(jid)
        return {"ok": True}

    @app.delete("/api/jobs/{jid}")
    def delete_job(jid: str):
        job_or_404(jid)
        lib.delete_job(jid)
        return {"ok": True}

    @app.get("/api/jobs/{jid}/log", response_class=PlainTextResponse)
    def job_log(jid: str):
        job_or_404(jid)
        d = lib.job_dir(jid)
        parts = [p.read_text(encoding="utf-8", errors="replace") for p in (d / "log.txt", d / "process.log") if p.exists()]
        return "\n".join(parts)[-200_000:]

    @app.get("/api/jobs/{jid}/model.glb")
    def model(jid: str):
        job_or_404(jid)
        path = lib.job_dir(jid) / "result" / "model.glb"
        if not path.exists():
            raise HTTPException(404, "no model yet")
        return FileResponse(path, media_type="model/gltf-binary", headers={"Cache-Control": "no-store"})

    @app.post("/api/jobs/{jid}/calibrate")
    def calibrate(jid: str, req: CalibrateRequest):
        job_or_404(jid)
        if not 0.01 < req.scale < 100:
            raise HTTPException(400, "scale out of range")
        lib.set_user_scale(jid, req.scale)
        return lib.get_job(jid)

    @app.post("/api/jobs/{jid}/export")
    def export(jid: str, req: ExportRequest):
        j = job_or_404(jid)
        d = lib.job_dir(jid)
        if not (d / "result" / "model.glb").exists():
            raise HTTPException(409, "job has no model")
        safe = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in j["capture_name"]).strip() or "model"
        out = []
        for fmt in req.formats:
            try:
                p = export_model(d / "result", d / "exports", safe, fmt, req.units, req.textured, j["user_scale"])
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            out.append({"format": fmt, "file": p.name, "size": p.stat().st_size})
        return {"folder": str(d / "exports"), "files": out}

    @app.get("/api/jobs/{jid}/exports/{name}")
    def export_file(jid: str, name: str):
        job_or_404(jid)
        path = (lib.job_dir(jid) / "exports" / name).resolve()
        if path.parent != (lib.job_dir(jid) / "exports").resolve() or not path.exists():
            raise HTTPException(404, "file not found")
        return FileResponse(path, filename=name)

    @app.post("/api/jobs/{jid}/reveal")
    def reveal(jid: str):
        """Open the export folder in the file manager (desktop convenience)."""
        job_or_404(jid)
        folder = lib.job_dir(jid) / "exports"
        folder.mkdir(exist_ok=True)
        if os.name == "nt":
            os.startfile(folder)  # noqa: S606
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])
        return {"ok": True}

    if ui_dir and (ui_dir / "index.html").exists():
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")
    return app


def advertise(port: int):
    """Announce the server on the local network so the phone finds it without typing an address."""
    try:
        from zeroconf import ServiceInfo, Zeroconf

        addrs = [socket.inet_aton(a) for a in lan_addresses()]
        if not addrs:
            return None
        zc = Zeroconf()
        host = socket.gethostname()
        zc.register_service(ServiceInfo("_calco._tcp.local.", f"Calco on {host}._calco._tcp.local.",
                                        addresses=addrs, port=port, properties={"version": __version__},
                                        server=f"{host}.local."))
        return zc
    except Exception as e:  # discovery is a convenience; the address can always be typed in
        print(f"Bonjour advertising unavailable: {e}")
        return None


def serve(host: str = "0.0.0.0", port: int = PORT, library: Path | None = None) -> None:
    import uvicorn

    ui = Path(__file__).resolve().parents[2] / "desktop" / "dist"
    app = create_app(Library(library), ui)
    zc = advertise(port)
    print(f"Calco engine {__version__} on http://localhost:{port}  (phone: {', '.join(lan_addresses()) or 'no LAN address'})")
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        if zc:
            zc.close()
