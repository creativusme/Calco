"""Build the self-contained runtime the Windows installer ships (desktop/src-tauri/runtime).

    python desktop/scripts/build_runtime.py

Layout (mirrors the repository, see engine/calco/paths.py; the app sets CALCO_HOME to it):
    python/              standalone CPython 3.12 with the engine and all its dependencies installed
    desktop/dist/        the UI (run `npm run build` in desktop/ first)
    docs/collaudo.md     field test sheet
    tools/allow-wifi.ps1 Wi-Fi fix
    tools/openmvs, tools/colmap   reconstruction tools, unpacked

Needs internet (Python, packages, tools) and uv (pip install uv). The splat engine (PyTorch, ~3 GB)
is not included: the app downloads it by itself the first time, only on computers with an NVIDIA GPU.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "desktop" / "src-tauri" / "runtime"
PYTHON = "3.12"


def run(*args, env=None) -> None:
    print("$", " ".join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], check=True, env=env)


def uv(*args, env=None) -> None:
    run(sys.executable, "-m", "uv", *args, env=env)


def main() -> None:
    if shutil.which("uv") is None:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "uv"], check=True)
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    tmp = OUT.parent / "runtime-build"
    shutil.rmtree(tmp, ignore_errors=True)

    # 1. a relocatable CPython (python-build-standalone, fetched by uv)
    env = {**os.environ, "UV_PYTHON_INSTALL_DIR": str(tmp / "pythons")}
    uv("python", "install", PYTHON, env=env)
    src = next(p for p in sorted((tmp / "pythons").glob(f"cpython-{PYTHON}.*")) if (p / "python.exe").exists())
    shutil.copytree(src, OUT / "python", symlinks=False)
    py = OUT / "python" / "python.exe"
    for marker in (OUT / "python").rglob("EXTERNALLY-MANAGED"):
        marker.unlink()  # this copy is ours: let packages be installed into it

    # 2. the engine and its dependencies, installed (not linked) into that Python
    uv("pip", "install", "--python", py, "--link-mode", "copy", "--compile-bytecode", REPO / "engine")

    # 3. UI, field test sheet, Wi-Fi fix
    dist = REPO / "desktop" / "dist"
    if not (dist / "index.html").exists():
        raise SystemExit("desktop/dist is missing: run `npm run build` in desktop/ first")
    shutil.copytree(dist, OUT / "desktop" / "dist")
    (OUT / "docs").mkdir()
    shutil.copy(REPO / "docs" / "collaudo.md", OUT / "docs")
    (OUT / "tools").mkdir()
    shutil.copy(REPO / "tools" / "allow-wifi.ps1", OUT / "tools")

    # 4. OpenMVS and the COLMAP CUDA build, unpacked (both are needed even without a GPU check here)
    tool_env = {**os.environ, "CALCO_TOOLS": str(OUT / "tools")}
    run(py, "-c", "from calco import tools; tools.install_openmvs(); tools.install_colmap()", env=tool_env)

    shutil.rmtree(tmp, ignore_errors=True)
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"runtime ready: {OUT} ({size / 1e9:.2f} GB)")


if __name__ == "__main__":
    main()
