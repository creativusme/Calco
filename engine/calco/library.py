"""On-disk library: captures received from the phone and the jobs run on them.

<library>/captures/<id>.calco/      capture bundles
<library>/jobs/<job id>/            job.json, status.json, log.txt, report.json, result/, exports/
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .bundle import BundleError, load_bundle


def _kill_tree(proc: subprocess.Popen) -> None:
    """Stop a job and everything it started (COLMAP, OpenMVS): killing only Python leaves them running."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        proc.kill()


def default_library() -> Path:
    return Path(os.environ.get("CALCO_LIBRARY") or Path.home() / "Calco")


class Library:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or default_library())
        self.captures = self.root / "captures"
        self.jobs = self.root / "jobs"
        self.incoming = self.root / "incoming"
        for d in (self.captures, self.jobs, self.incoming):
            d.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._running: str | None = None
        self._recover()
        threading.Thread(target=self._worker, daemon=True).start()

    # ---- captures
    def capture_path(self, cid: str) -> Path:
        if not cid or any(ch in cid for ch in "/\\:") or cid.startswith("."):
            raise ValueError("invalid capture id")
        return self.captures / f"{cid}.calco"

    def list_captures(self) -> list[dict]:
        out = []
        for p in sorted(self.captures.glob("*.calco"), key=lambda q: q.stat().st_mtime, reverse=True):
            try:
                s = load_bundle(p).summary()
            except (BundleError, OSError, KeyError, ValueError) as e:
                s = {"id": p.stem, "name": p.stem, "error": str(e)}
            s["size_mb"] = round(sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e6, 1)
            s["jobs"] = [j for j in self.list_jobs() if j["capture_id"] == s["id"]]
            out.append(s)
        return out

    def delete_capture(self, cid: str) -> None:
        for j in self.list_jobs():
            if j["capture_id"] == cid:
                self.delete_job(j["id"])
        shutil.rmtree(self.capture_path(cid), ignore_errors=True)

    def complete_incoming(self, cid: str) -> dict:
        """Validate a fully received bundle in incoming/ and move it into the library."""
        src, dst = self.incoming / self.capture_path(cid).name, self.capture_path(cid)
        if not src.exists():
            raise FileNotFoundError(f"nothing received for capture {cid}")
        bundle = load_bundle(src)  # raises BundleError when files are missing
        for part in src.rglob("*.part"):
            part.unlink()
        if dst.exists():
            shutil.rmtree(dst)
        shutil.move(str(src), str(dst))
        return bundle.summary()

    def import_capture(self, src: Path) -> str:
        """Copy a bundle directory into the library. Returns the capture id."""
        b = load_bundle(src)
        dst = self.capture_path(b.id)
        if dst.resolve() != Path(src).resolve():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        return b.id

    # ---- jobs
    def job_dir(self, jid: str) -> Path:
        if not jid or any(ch in jid for ch in "/\\:.") :
            raise ValueError("invalid job id")
        return self.jobs / jid

    def _read(self, path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def get_job(self, jid: str) -> dict | None:
        d = self.job_dir(jid)
        job = self._read(d / "job.json")
        if job is None:
            return None
        status = self._read(d / "status.json") or {}
        job.update({k: status.get(k) for k in ("progress", "message", "error", "stages", "started", "finished",
                                               "eta_seconds")})
        if job["state"] == "running" and status.get("state") in ("done", "failed"):
            job["state"] = status["state"]
        job["report"] = self._read(d / "report.json") if job["state"] == "done" else None
        job["has_model"] = (d / "result" / "model.glb").exists()
        return job

    def list_jobs(self) -> list[dict]:
        jobs = [self.get_job(p.name) for p in self.jobs.iterdir() if (p / "job.json").exists()]
        return sorted([j for j in jobs if j], key=lambda j: j["created"], reverse=True)

    def _write_job(self, job: dict) -> None:
        keep = {k: job[k] for k in ("id", "capture_id", "capture_name", "options", "state", "created", "user_scale")}
        d = self.job_dir(job["id"])
        d.mkdir(parents=True, exist_ok=True)
        (d / "job.json").write_text(json.dumps(keep), encoding="utf-8")

    def create_job(self, cid: str, options: dict) -> dict:
        bundle = load_bundle(self.capture_path(cid))
        job = {"id": time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6], "capture_id": cid,
               "capture_name": bundle.name, "options": options, "state": "queued", "created": time.time(),
               "user_scale": 1.0}
        self._write_job(job)
        return self.get_job(job["id"])

    def set_state(self, jid: str, state: str) -> None:
        job = self._read(self.job_dir(jid) / "job.json")
        if job:
            job["state"] = state
            self._write_job(job)

    def set_user_scale(self, jid: str, scale: float) -> None:
        job = self._read(self.job_dir(jid) / "job.json")
        if job is None:
            raise KeyError(jid)
        job["user_scale"] = float(scale)
        self._write_job(job)

    def cancel_job(self, jid: str) -> None:
        with self._lock:
            if self._running == jid and self._proc and self._proc.poll() is None:
                _kill_tree(self._proc)
            self.set_state(jid, "cancelled")

    def delete_job(self, jid: str) -> None:
        self.cancel_job(jid)
        shutil.rmtree(self.job_dir(jid), ignore_errors=True)

    def _recover(self) -> None:
        """Jobs that were running when the server stopped cannot be resumed: mark them failed."""
        for p in self.jobs.iterdir():
            job = self._read(p / "job.json")
            if job and job["state"] == "running":
                status = self._read(p / "status.json") or {}
                job["state"] = status.get("state") if status.get("state") in ("done", "failed") else "failed"
                self._write_job(job)

    def _worker(self) -> None:
        """Run queued jobs one at a time, each in its own process so a crash cannot take the server down."""
        while True:
            queued = [j for j in self.list_jobs() if j["state"] == "queued"]
            if not queued:
                time.sleep(0.7)
                continue
            job = queued[-1]  # oldest first
            d = self.job_dir(job["id"])
            with self._lock:
                self.set_state(job["id"], "running")
                self._running = job["id"]
                pair = job["options"].get("combine")
                if pair:  # two finished results of the same part, aligned and fused
                    cmd = [sys.executable, "-m", "calco", "combine", str(self.job_dir(pair[0])), str(self.job_dir(pair[1])),
                           "--job-dir", str(d)]
                else:
                    cmd = [sys.executable, "-m", "calco", "process", str(self.capture_path(job["capture_id"])),
                           "--job-dir", str(d), "--options", json.dumps(job["options"])]
                with (d / "process.log").open("w", encoding="utf-8") as fh:
                    self._proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT)
            code = self._proc.wait()
            with self._lock:
                self._running = None
                current = self._read(d / "job.json")
                if current and current["state"] == "running":
                    status = self._read(d / "status.json") or {}
                    ok = code == 0 and status.get("state") == "done"
                    if not ok and status.get("state") != "failed":
                        status.update(state="failed", error=status.get("error") or f"Engine process exited ({code})")
                        (d / "status.json").write_text(json.dumps(status), encoding="utf-8")
                    self.set_state(job["id"], "done" if ok else "failed")
