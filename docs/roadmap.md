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
| Wi-Fi upload (phone pushes) | **Verified** by tests (resume, validation, path traversal). On the dev PC it was blocked by Windows Firewall (block rule for Python + Public network): detected and explained in the UI, fixed by `tools/allow-wifi.ps1` that the owner runs |
| Cable transfer (computer pulls via usbmuxd) | **Verified** by tests against a fake phone (pull, resume, no re-download of deleted scans). usbmuxd answers on the dev PC. **Never tried with the real phone** |
| Desktop UI in the browser | **Verified** by hand: library, process, tabs, viewer, two-point measure, calibration, export |
| Desktop Tauri shell | Written, **never compiled** (no Rust on the dev machine) |
| iPhone app | **Compiles** in CI (unsigned IPA artifact). The owner ran 0.2 and 0.3 on the phone (capture, pairing and transfer work). 0.4 (automatic close-up lens inside the normal scan, box on the support plane) **not run yet** |
| Pairing (network code + password) | **Verified** by tests (401 without/with a wrong password, code round trip). Not tried with the phone |
| Real capture #1 (pliers + glue stick on a glossy patterned tablecloth, 198 photos, no mat) | See "First real capture" below |

## First real capture (2026-11-14)

198 photos, 4032 × 3024, LiDAR, no scan mat. Shiny thin pliers and a glue stick on a vinyl tablecloth with a repeating lavender print, evening light (9–10 ms exposure).

| | Before | After |
|---|---|---|
| Draft | crashed (a frame with no depth inside the crop box) | 42 s; only the glue stick: the pliers are too thin for LiDAR |
| Camera solving | incremental mapping 6.5 min; a block of 16 photos placed 30 cm off (repeating print), phone vs photos 81.6 mm RMS | phone poses + triangulation + bundle adjustment 63 s, 2.2 mm RMS |
| Blurred photos | all used (sharpness varied 70×) | 11 dropped automatically |
| Depth maps | all 198 photos, 17 min | 60 well-spread photos, ~4.5 min |
| Texture | always, 4.5 min | optional (`texture: false` for printing) |
| Isolation | table left in (fitted plane ~1.7 mm below the real surface) | support surface re-found from the point density, cut sized to its noise: table removed, pliers and glue stick kept |
| Medium total | 28 min | 8 min with texture, 6 min without (verified end to end) |

## Real capture #2 (charger on a box on a carpet: 4 LiDAR photos + 63 close-ups)

Failed with 'mesh.ply: no triangles': all 67 photos were forced into one camera model although the close-ups came from the ultra-wide lens (14 mm equivalent; the close-up camera started at its widest lens instead of 1x). Only 2 photos were placed. Now: one camera per lens from EXIF, solve everything and align to the 4 LiDAR photos: 67/67 placed, 1.8 mm vs phone. The box was then cut away because the crop box base sat on the box lid (where the user aimed); the crop region is now extended downwards and the phone puts the base on the plane below.

Still missing on capture #1: the metal jaws of the pliers (specular metal; needs matte spray) and fine detail on the handles.

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

## Two-sided parts

Flip on the phone (frames carry `side`) or Combine on the computer (two finished results). Each side is
reconstructed and isolated on its own; `merge.py` aligns the second to the first by shape (turned-over and
rotated guesses + FPFH/RANSAC, then point-to-plane ICP) and fuses both with screened Poisson into one closed
mesh. **Verified** on synthetic data: a turned-over part aligned to 0.2 mm (the simulated noise), closed,
dimensions within the noise; a two-sided synthetic capture end to end (LiDAR). **Not yet** on a real part.
The mat cannot be the common reference: it stays put while the part is turned.

## Hardware notes (measured on the RTX 3060 + i3-12100F)

- Real capture, 122 photos, High: 40 min, of which 31 min GPU depth maps at 2000 px (about 6.5 s per photo
  photometric + 9 s geometric). A faster NVIDIA card scales this part almost linearly; the CPU parts
  (camera solving, meshing) are a few minutes.
- A Mac (even an Ultra) cannot run this pipeline on its GPU: COLMAP patch-match and OpenMVS need CUDA. Apple's
  own Object Capture (macOS only, closed) is fast on Apple Silicon but is a different engine.

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
