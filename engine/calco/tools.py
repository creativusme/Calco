"""External reconstruction tools (OpenMVS). Downloaded on demand, never bundled in the repo."""

from __future__ import annotations

import hashlib
import os
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


def install_openmvs(log=print) -> Path:
    root = tools_dir() / "openmvs"
    if have_openmvs():
        log(f"OpenMVS already installed in {root}")
        return root
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "openmvs.zip"
    log(f"Downloading OpenMVS {OPENMVS['version']} from {OPENMVS['url']}")
    urllib.request.urlretrieve(OPENMVS["url"], archive)
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


def run_tool(exe: Path, args: list[str], cwd: Path, log=print) -> None:
    """Run an external tool, streaming its output to the log. Raises on failure."""
    cmd = [str(exe), *[str(a) for a in args]]
    log("$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            errors="replace")
    tail = []
    for line in proc.stdout:
        line = line.rstrip()
        if line:
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
    info = {"openmvs": have_openmvs(), "tools_dir": str(tools_dir()), "gpu": None}
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        info["gpu"] = out.splitlines()[0] if out else None
    except (OSError, subprocess.SubprocessError):
        pass
    return info
