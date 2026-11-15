# Calco

Scan on site with an iPhone, rebuild on your own computer. No cloud, no subscription.

Calco is two programs that share one file format:

- **Calco for iPhone** records a *capture*: high-resolution photos, the camera pose of each photo (ARKit), LiDAR depth and a crop box around the object. It guides you while you walk around (automatic shutter, "slow down", coverage ring).
- **Calco for Windows** turns a capture into a scaled, cleaned, textured 3D model: photogrammetry (COLMAP + OpenMVS, on the GPU when you have an NVIDIA card) or fast LiDAR fusion, automatic object isolation, millimetre-true scale from a scan mat you print on paper or in 3D, calibration from a caliper measurement, and export to STL, 3MF, OBJ, GLB and PLY.

Nothing leaves your computer: no cloud, no accounts, no AI service. The only downloads are the open-source tools (OpenMVS, COLMAP) and the UI fonts, once.

Status: version 1.0. See `docs/roadmap.md` for what has been verified on real scans and what has not.

## Install (Windows)

**[Download Calco-setup.exe](https://github.com/creativusme/Calco/releases/download/desktop-latest/Calco-setup.exe)**, run it, then open **Calco** from the Start menu. That is all: the installer contains the app, the engine, Python, COLMAP (GPU) and OpenMVS. No folders from GitHub, no commands.

- An NVIDIA GPU makes photogrammetry several times faster; without one Calco uses the CPU.
- The optional Gaussian-splat engine (about 3 GB) is downloaded by the app only if you ask for it (Process → *Add Gaussian splat*).
- The installer lets the engine receive the phone's uploads on private networks. If your network is set to Public, Calco shows a **Fix It** button (Windows asks for permission once).

Try it without a phone: **Create a demo capture**, then **Process**.

## Develop

Requirements: Python 3.12, Node 20+. `Start Calco.cmd` runs everything from a source checkout.

```bash
cd engine && python -m venv .venv && .venv\Scripts\python -m pip install -e .[dev]
.venv\Scripts\python -m calco serve              # API + UI on http://localhost:7878 (downloads OpenMVS/COLMAP once)
.venv\Scripts\python -m pytest -q                # tests on a synthetic capture
python desktop/scripts/build_runtime.py           # the self-contained runtime the installer ships
cd desktop && npx tauri build                     # the installer (needs Rust); CI does this on every push
```

## iPhone app

Scans reach the computer in two ways: **by cable** (plug the iPhone in with Calco open on both; the computer pulls what is missing through Apple's USB service, nothing to configure) or **over Wi-Fi** (the installer allows it on private networks; on a Public network Calco offers a one-click fix).

**[Download the latest iPhone app (Calco-unsigned.ipa)](https://github.com/creativusme/Calco/releases/download/ios-latest/Calco-unsigned.ipa)**, then sign and install it with Sideloadly or AltStore. Needs an iPhone with LiDAR (12 Pro or later) and iOS 17.

There is no Mac in this project: GitHub Actions builds the unsigned IPA on every change (`.github/workflows/ios.yml`) and publishes it as the latest release, so the link above is always the newest build.

## How a scan becomes a model

1. **Align**: features are matched only between photos the phone says are near each other, cameras are solved by COLMAP, then the result is aligned to the phone's poses so it is in metres.
2. **Scale**: AprilTags on the scan mat are triangulated; the mat defines scale, the support plane and the origin. Without a mat, scale comes from ARKit/LiDAR (about 1%).
3. **Geometry**: OpenMVS dense reconstruction (Medium/High/Ultra) or LiDAR depth fusion (Draft).
4. **Isolate**: crop to the box, cut the support plane, drop loose pieces, close the base flat.
5. **Texture**, then everything is moved to the model frame: Z up, base on z = 0, centred.

Every result has an accuracy report (scale source, mat fit, expected error) and can be calibrated against one caliper measurement.

## Documents

- `docs/capture-format.md`: the capture bundle, the contract between phone and computer
- `docs/collaudo.md`: field test sheet (Italian)
- `docs/roadmap.md`: what works, what is next
- `CLAUDE.md`: architecture decisions and conventions for contributors and AI agents

## License

AGPL-3.0-only, see `LICENSE`. Commercial licensing: contact the author. Contributions require a contributor agreement so the project can be dual-licensed.

Calco uses COLMAP (BSD), OpenMVS (AGPL), Open3D (MIT), OpenCV (Apache 2.0), trimesh (MIT), xatlas (MIT), three.js (MIT).
