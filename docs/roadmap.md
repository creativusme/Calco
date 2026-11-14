# Roadmap

Keep this file honest: "verified" means it ran and the result was checked, not that the code exists.

## State on 2026-11-14

| Part | State |
|---|---|
| Capture format v1 | Specified; engine reads it; synthetic generator writes it |
| Engine: LiDAR fusion (Draft) | **Verified** on synthetic data: 22 s, dimensions within ~5 mm |
| Engine: photogrammetry (Medium) | **Verified** on synthetic data: 36/36 photos, 0.39 px, dimensions within 0.7 mm of an 80 mm part |
| Engine: High / Ultra presets | Same code path as Medium with other settings; **not run yet** |
| Scan mat (A4/A3 AprilTag) | **Verified** on synthetic data (0.15 mm fit). Not yet tested with a real print |
| Object isolation, flat closed base | **Verified** on synthetic data |
| Export GLB, OBJ, STL, 3MF, PLY | **Verified** by tests (units, up axis) |
| Server + upload protocol | **Verified** by tests (resume, validation, path traversal) |
| Desktop UI in the browser | **Verified** by hand: library, process, progress, viewer, accuracy, export |
| Desktop Tauri shell | Written, **never compiled** (no Rust on the dev machine) |
| iPhone app | Written, **never compiled or run**. First CI build will show the compile errors |
| Real captures from a phone | **None yet**. Everything above is from the synthetic scene |

## Known limitations

- OpenMVS 2.4.0 seam leveling produces black blobs, so it is off: photos with different exposure can leave visible seams in the texture.
- pycolmap from PyPI is CPU only: feature extraction and matching do not use the GPU. OpenMVS is the CPU build too. Fine for ~100 photos, slow for 500.
- Auto-exposure and autofocus are left on in the phone; one shared camera model is assumed.
- The server has no authentication: anyone on the same network can upload or delete captures.
- No on-phone 3D preview: a scan can only be judged on the computer.

## Next, in order

1. **First real capture**: fix whatever the iOS CI build and the first on-device run reveal; compare a measured part against `docs/collaudo.md` section F.
2. **Speed and quality on real data**: OpenMVS depth-map masks from the crop box (skip the background), drop blurry frames, per-frame intrinsics, lock exposure/white balance in the app, CUDA builds of COLMAP/OpenMVS.
3. **Macro mode** for parts under 3 cm (ultra-wide camera, no ARKit, scale from the mat).
4. **Merge two scans** of the same part (upright + flipped) using the mat or ICP.
5. **AI masks** (SAM2/BiRefNet) for objects that cannot sit on a plane.
6. **Phone preview** (Object Capture on device) and notes/photos attached to a capture.
7. **Import** photo folders and video; **Gaussian splatting** (gsplat) as a second output.
8. **CAD helpers**: section slices to DXF, plane/cylinder fitting, USDZ/FBX export, PDF site report.
9. Tauri installer, engine bundled without a separate Python install.
