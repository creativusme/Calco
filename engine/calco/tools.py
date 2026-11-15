"""External reconstruction tools (OpenMVS). Downloaded on demand, never bundled in the repo."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

OPENMVS = {
    "version": "2.4.0",
    "url": "https://github.com/cdcseacave/openMVS/releases/download/v2.4.0/OpenMVS_Windows_x64.zip",
    "sha256": "0c31660c15c9ebc4c106873cf67564d9570d404aef7a6403451da1b6178b2167",
}

# COLMAP's CUDA build bundles the CUDA runtime, so only an NVIDIA driver is needed.
COLMAP = {
    "version": "4.2.1",  # must match the pycolmap version: both read and write the same database
    "url": "https://github.com/colmap/colmap/releases/download/4.2.1/colmap-x64-windows-cuda.zip",
    "sha256": "e9c5cbd84c2ea986d2e970a2473fc2d2e6b34a2cdcf5d3df2765c319a63af881",
}


def tools_dir() -> Path:
    base = os.environ.get("CALCO_TOOLS") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Calco" / "tools"
    return Path(base)


def openmvs_bin(name: str) -> Path | None:
    """Path to an OpenMVS executable (e.g. 'DensifyPointCloud') or None if not installed."""
    exe = name + (".exe" if os.name == "nt" else "")
    found = shutil.which(exe)
    if found:
        return Path(found)
    root = tools_dir() / "openmvs"
    if root.exists():
        for p in root.rglob(exe):
            return p
    return None


def have_openmvs() -> bool:
    return all(openmvs_bin(n) for n in ("InterfaceCOLMAP", "DensifyPointCloud", "ReconstructMesh", "TextureMesh"))


# What the automatic setup is doing right now, for the UI: {"component", "done", "total"} or None.
PROGRESS: dict | None = None


def _download(url: str, target: Path, component: str, log) -> None:
    """Download with progress reported in PROGRESS; resumes nothing, retries once."""
    global PROGRESS

    def hook(blocks: int, size: int, total: int) -> None:
        global PROGRESS
        PROGRESS = {"component": component, "done": blocks * size, "total": max(total, 0)}

    for attempt in range(2):
        try:
            PROGRESS = {"component": component, "done": 0, "total": 0}
            urllib.request.urlretrieve(url, target, reporthook=hook)
            return
        except OSError as e:
            log(f"Download of {component} failed ({e}){', retrying' if attempt == 0 else ''}")
            if attempt:
                raise
        finally:
            PROGRESS = None


def install_openmvs(log=print) -> Path:
    root = tools_dir() / "openmvs"
    if have_openmvs():
        log(f"OpenMVS already installed in {root}")
        return root
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "openmvs.zip"
    log(f"Downloading OpenMVS {OPENMVS['version']} from {OPENMVS['url']}")
    _download(OPENMVS["url"], archive, "OpenMVS", log)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if OPENMVS["sha256"] and digest != OPENMVS["sha256"]:
        archive.unlink()
        raise RuntimeError(f"OpenMVS download hash mismatch: {digest}")
    log(f"sha256 {digest}")
    with zipfile.ZipFile(archive) as z:
        z.extractall(root)
    archive.unlink()
    if not have_openmvs():
        raise RuntimeError(f"OpenMVS executables not found after extracting to {root}")
    return root


def colmap_bin() -> Path | None:
    """Path to the CUDA build of COLMAP, or None if it is not installed."""
    root = tools_dir() / "colmap"
    if root.exists():
        for p in root.rglob("colmap.exe"):
            return p
    return None


def have_gpu() -> bool:
    try:
        return subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def install_colmap(log=print) -> Path:
    root = tools_dir() / "colmap"
    if colmap_bin():
        log(f"COLMAP (CUDA) already installed in {root}")
        return root
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "colmap.zip"
    log(f"Downloading COLMAP {COLMAP['version']} CUDA build (about 415 MB) from {COLMAP['url']}")
    _download(COLMAP["url"], archive, "GPU engine (COLMAP)", log)
    h = hashlib.sha256()
    with archive.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    if COLMAP["sha256"] and h.hexdigest() != COLMAP["sha256"]:
        archive.unlink()
        raise RuntimeError(f"COLMAP download hash mismatch: {h.hexdigest()}")
    log(f"sha256 {h.hexdigest()}")
    with zipfile.ZipFile(archive) as z:
        z.extractall(root)
    archive.unlink()
    if not colmap_bin():
        raise RuntimeError(f"colmap.exe not found after extracting to {root}")
    return root


def missing() -> list[str]:
    """Components this computer should have but does not yet."""
    out = [] if have_openmvs() else ["openmvs"]
    if not colmap_bin() and have_gpu():
        out.append("colmap")
    return out


def install_all(log=print) -> None:
    """Everything Calco needs on this computer: OpenMVS always, the CUDA engine when there is an NVIDIA GPU."""
    for name in missing():
        (install_openmvs if name == "openmvs" else install_colmap)(log=log)


_GLOG = re.compile(r"^[IWEF]\d{8} [\d:.]+\s+\d+ \S+\] ")  # COLMAP log prefix
_NOISE = ("Sweep ", "Initialization:", "Iteration ", "Total: ")


def run_tool(exe: Path, args: list[str], cwd: Path, log=print, on_line=None) -> None:
    """Run an external tool, streaming its output to the log. Raises on failure.

    on_line(line) is called for every output line, e.g. to turn it into progress.
    """
    cmd = [str(exe), *[str(a) for a in args]]
    log("$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            errors="replace")
    tail = []
    for line in proc.stdout:
        line = _GLOG.sub("", line.rstrip())
        if on_line and line:
            on_line(line)
        if line and not line.lstrip().startswith(_NOISE):
            tail.append(line)
            tail = tail[-30:]
            log("  " + line)
    code = proc.wait()
    # OpenMVS writes its real output to <tool>-<timestamp>.log in the working directory
    logs = sorted(cwd.glob(f"{exe.stem}-*.log"), key=lambda q: q.stat().st_mtime)
    if logs:
        lines = [ln.split("] ", 1)[-1] for ln in logs[-1].read_text(errors="replace").splitlines()]
        lines = [ln for ln in lines if ln.strip() and "MEMORYINFO" not in ln and not ln.startswith(("\t", "}"))]
        for ln in lines[-25:]:
            log("  " + ln)
        tail = lines[-8:] or tail
        for old in logs:
            old.unlink()
    if code != 0:
        raise RuntimeError(f"{exe.name} failed (exit {code}):\n" + "\n".join(tail[-8:]))


def status() -> dict:
    info = {"openmvs": have_openmvs(), "colmap_cuda": colmap_bin() is not None, "tools_dir": str(tools_dir()),
            "gpu": None}
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        info["gpu"] = out.splitlines()[0] if out else None
    except (OSError, subprocess.SubprocessError):
        pass
    return info
