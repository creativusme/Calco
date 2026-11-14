# Roadmap

Keep this file honest: "verified" means it ran and the result was checked, not that the code exists.

## State on 2026-11-14

| Part | State |
|---|---|
| Capture format v1 | Specified; engine reads it; synthetic generator writes it |
| Engine: LiDAR fusion (Draft) | **Verified** on synthetic data: 22 s, dimensions within ~5 mm |
| Engine: photogrammetry, GPU path (default) | **Verified** on synthetic data, Medium: 4 min, 80.25 × 60.12 × 80.20 mm for an 80 × 60 × 80 part, expected error ±0.57 mm |
| Engine: photogrammetry, CPU fallback (OpenMVS) | **Verified** on synthetic data, Medium with `"gpu": false`: 7.5 min, 80.34 × 60.32 × 79.87 mm |
| Engine: High preset | **Verified** on synthetic data from the UI: 8 min on GPU, 80.18 × 60.18 × 80.09 mm, expected error ±0.46 mm |
| Engine: Ultra preset | Same path as High plus OpenMVS mesh refinement; **not run yet** |
| Scan mat A5 / A4 / A3 | **Verified** on synthetic data (0.15 mm fit). Not yet tested with a real print |
| Scan mat that is not flat | **Verified** by simulation: 3 mm curl gives 0.02% scale error, a rolled sheet (20 cm radius) 0.14% |
| 3D-printable mat (STL) | Files generated and dimensions checked; **never printed** |
| Object isolation, flat closed base | **Verified** on synthetic data |
| Export GLB, OBJ, STL, 3MF, PLY | **Verified** by tests (units, up axis) |
| Server + upload protocol | **Verified** by tests (resume, validation, path traversal) |
| Desktop UI in the browser | **Verified** by hand: library, process, tabs, viewer, two-point measure, calibration, export |
| Desktop Tauri shell | Written, **never compiled** (no Rust on the dev machine) |
| iPhone app | **Compiles** in CI (unsigned IPA artifact). **Never run on a phone** |
| Real captures from a phone | **None yet**. Everything above is from the synthetic scene |

## GPU

`calco tools install-gpu` downloads the official COLMAP 4.2.1 CUDA build (415 MB, bundles the CUDA runtime).
When it is present and an NVIDIA GPU is found, the engine uses it for:

- feature extraction and matching: 36 photos in about 4 s on an RTX 3060 (about 70 s on the i3 CPU);
  the whole "Align photos" stage went from 73 s to 21 s;
- depth maps (COLMAP patch-match stereo, 8 source photos per view, window step 2): 36 photos at 1000 px
  in 2 min 40 s. Fusion runs in COLMAP (7 s); meshing is done by OpenMVS from the fused points (about 40 s,
  half the time of COLMAP's own mesher).

Camera solving (bundle adjustment), mesh clean-up and texturing stay on the CPU. If a GPU step fails, the
engine logs it and repeats that step on the CPU. `"gpu": false` in the job options forces the CPU.
`GPU_DENSE` in `engine/calco/mvs.py` switches the GPU depth maps off globally.

Tried and dropped: the OpenMVS CUDA build (v2.4.0) fails on this machine with "CUDA error: named symbol not
found" before computing anything.

Not tuned yet: depth maps take about 2–2.5 s per photo per pass, slower than an RTX 3060 should manage.
Worth profiling (image loading, cache size, fewer geometric-consistency iterations) once real captures exist.

## Why not a home-made photogrammetry engine

COLMAP and OpenMVS are the reference open implementations and are what the commercial-grade open pipelines
build on; a new engine would take years to reach them. What Calco adds is around them: phone poses to pick
image pairs and fix the scale, the scan mat, automatic isolation, GPU routing, presets. The realistic ways to
raise quality from here are listed under "Next", and they need real captures to be judged.

## Known limitations

- OpenMVS 2.4.0 seam leveling produces black blobs, so it is off: photos with different exposure can leave visible seams in the texture.
- Auto-exposure and autofocus are left on in the phone; one shared camera model is assumed.
- The server has no authentication: anyone on the same network can upload or delete captures.
- No on-phone 3D preview: a scan can only be judged on the computer.
- UI fonts are downloaded from Fontshare at build time (their licence does not allow committing them); offline builds fall back to system fonts.

## Next, in order

1. **First real capture**: install the IPA, run `docs/collaudo.md`, fix what the first on-device run reveals.
2. **Quality on real data**: depth-map masks from the crop box (skip the background), drop blurry frames, per-frame intrinsics, lock exposure/white balance in the app, compare COLMAP and OpenMVS dense results on the same capture, try the learned features in COLMAP 4 (ALIKED) on low-texture parts.
3. **Macro mode** for parts under 3 cm (ultra-wide camera, no ARKit, scale from the mat).
4. **Merge two scans** of the same part (upright + flipped) using the mat or ICP.
5. **Loose scale markers** for parts too big for a mat; **AI masks** (SAM2/BiRefNet, running locally) for objects that cannot sit on a plane.
6. **Phone preview** (Object Capture on device) and notes/photos attached to a capture.
7. **Import** photo folders and video; **Gaussian splatting** (gsplat) as a second output.
8. **CAD helpers**: section slices to DXF, plane/cylinder fitting, USDZ/FBX export, PDF site report.
9. Tauri installer, engine bundled without a separate Python install.
