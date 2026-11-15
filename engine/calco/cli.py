"""Command line: calco serve | process | export | synth | mat | tools."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="calco", description="Calco reconstruction engine")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the local API server (phone uploads + desktop UI)")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=7878)
    s.add_argument("--library", type=Path, default=None)

    p = sub.add_parser("process", help="reconstruct a capture bundle")
    p.add_argument("bundle", type=Path)
    p.add_argument("--job-dir", type=Path, default=None)
    p.add_argument("--preset", default=None, help="draft | medium | high | ultra")
    p.add_argument("--options", default=None, help="JSON object with pipeline options")

    e = sub.add_parser("export", help="export a finished job")
    e.add_argument("job_dir", type=Path)
    e.add_argument("--format", action="append", required=True)
    e.add_argument("--units", default=None)
    e.add_argument("--name", default="model")
    e.add_argument("--no-texture", action="store_true")

    c = sub.add_parser("combine", help="align and fuse two finished results (both sides of a part)")
    c.add_argument("job_a", type=Path)
    c.add_argument("job_b", type=Path)
    c.add_argument("--job-dir", type=Path, required=True)

    y = sub.add_parser("synth", help="write a synthetic capture bundle (for testing)")
    y.add_argument("out", type=Path)
    y.add_argument("--views", type=int, default=18, help="views per ring")

    m = sub.add_parser("mat", help="write the printable scan mat PDF")
    m.add_argument("out", type=Path)
    m.add_argument("--size", default="A4", choices=["A5", "A4", "A3"])
    m.add_argument("--print3d", action="store_true", help="write a zip of STL files for a 3D-printed mat")

    t = sub.add_parser("tools", help="external tools: OpenMVS, and COLMAP with CUDA for the GPU")
    t.add_argument("action", choices=["install", "install-gpu", "status"],
                   help="install = everything this computer needs (OpenMVS, and the GPU engine with an NVIDIA card)")

    a = ap.parse_args(argv)

    if a.cmd == "serve":
        from .server import serve

        serve(a.host, a.port, a.library)
    elif a.cmd == "process":
        from .pipeline import Options, run

        opts = json.loads(a.options) if a.options else {}
        if a.preset:
            opts["quality"] = a.preset
        job_dir = a.job_dir or a.bundle.with_suffix(".job")
        try:
            report = run(a.bundle, job_dir, Options.from_dict(opts))
        except Exception as ex:
            print(f"FAILED: {ex}", file=sys.stderr)
            return 1
        print(json.dumps({k: report.get(k) for k in ("plan", "model", "scale", "seconds", "warnings")}, indent=1))
        print(f"Result: {job_dir / 'result' / 'model.glb'}")
    elif a.cmd == "export":
        from .export import export_model

        for fmt in a.format:
            print(export_model(a.job_dir / "result", a.job_dir / "exports", a.name, fmt, a.units, not a.no_texture))
    elif a.cmd == "combine":
        from .pipeline import run_combine

        try:
            report = run_combine(a.job_a, a.job_b, a.job_dir)
        except Exception as ex:
            print(f"FAILED: {ex}", file=sys.stderr)
            return 1
        print(json.dumps({k: report.get(k) for k in ("combined", "model", "seconds")}, indent=1))
    elif a.cmd == "synth":
        from .synth import make_synthetic_bundle

        print(make_synthetic_bundle(a.out, views_per_ring=a.views))
    elif a.cmd == "mat":
        from .mat import write_mat_3d, write_mat_pdf

        print(write_mat_3d(a.out, a.size) if a.print3d else write_mat_pdf(a.out, a.size))
    elif a.cmd == "tools":
        from . import tools

        if a.action == "install":
            tools.install_all()
        elif a.action == "install-gpu":
            tools.install_colmap()
        print(json.dumps(tools.status(), indent=1))
    return 0
