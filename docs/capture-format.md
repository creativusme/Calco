# Calco capture bundle — format v1

A capture bundle is a directory named `<id>.calco` written by the phone and read by the engine. It holds everything recorded on site; nothing in it is processed or lossy beyond JPEG compression of the photos.

```
<id>.calco/
  manifest.json
  frames/000001.jpg        photo, sensor orientation, EXIF orientation = 1
  depth/000001.f32         LiDAR depth, float32 little-endian, metres, row-major
  depth/000001.u8          LiDAR confidence, uint8 per pixel: 0 low, 1 medium, 2 high
  notes/…                  optional attachments (photos, audio)
  preview.jpg              optional thumbnail
```

## manifest.json

```jsonc
{
  "format": "calco-capture",
  "version": 1,
  "id": "3f6c…",                    // UUID, also the directory name
  "name": "Tractor cap",
  "created": "2026-11-14T16:20:00Z",
  "mode": "object",                 // object | space | person | macro | import
  "device": { "model": "iPhone16,1", "os": "18.1", "app": "0.1.0" },

  // Region of interest chosen on site (world frame). null = whole scene.
  "crop_box": { "center": [0, 0.1, -0.5], "size": [0.4, 0.3, 0.4], "yaw": 0.0 },

  "frames": [
    {
      "id": 1,
      "image": "frames/000001.jpg",
      "width": 4032, "height": 3024,
      "timestamp": 1234.567,                    // seconds, monotonic
      "intrinsics": [fx, fy, cx, cy],           // pixels, for the image as stored
      "pose": [m00, m01, …, m33],               // 16 numbers, row-major, camera-to-world; null if untracked
      "tracking": "normal",                     // normal | limited | none
      "depth": { "file": "depth/000001.f32", "confidence": "depth/000001.u8",
                 "width": 256, "height": 192,
                 "pose": [ … ], "intrinsics": [fx, fy, cx, cy] },  // null when no LiDAR frame
      "exposure": { "duration": 0.008, "iso": 80 },
      "sharpness": 0.83                         // optional, 0..1, phone-side estimate
      "side": 1                                 // optional: 0 as placed, 1 after the first Flip, ...
    }
  ],

  // optional, only when the part was turned over during the scan: the crop box of each side
  "sides": [ { "crop_box": { … } }, { "crop_box": { … } } ],

  "notes": [ { "text": "serial plate on the left", "file": null, "position": [x, y, z] } ],
  "location": { "lat": 45.07, "lon": 7.68, "heading": 120.0 }   // optional
}
```

## Conventions

- **World frame**: ARKit world. Metres, right-handed, **+Y up**, gravity aligned.
- **Pose**: camera-to-world. Camera axes are ARKit's: +X right, +Y up, **camera looks along −Z** (for the image in sensor/landscape orientation).
- **Intrinsics** refer to the stored image: origin top-left, x right, y down. The depth map covers the same field of view; scale intrinsics by `depth.width / width`.
- `depth.pose` and `depth.intrinsics` (in depth pixels) are optional. The phone writes them because the depth map comes from the video frame, a few milliseconds away from the high-resolution photo. When absent, the photo's pose and scaled intrinsics apply.
- Depth pixels with value `0`, `NaN` or confidence `0` are invalid.
- `mode: macro` and `mode: import` frames have `pose: null` and `depth: null`; the engine then runs full structure-from-motion and takes scale from the scan mat or a manual measurement.

## Sides (Flip)

A part can be scanned upright and then turned over within one capture. Every frame carries `side`; `sides[k].crop_box` is the box placed for side k (the top-level `crop_box` stays the first side's). The engine reconstructs each side on its own and fuses them, aligning them by shape: the support surface moves with the part, so it cannot be the reference. Frames without `side` are side 0, so older captures read unchanged.

## Transfer protocol

The engine advertises `_calco._tcp` on the local network (default port 7878). Upload is per file and resumable:

1. `GET  /api/captures/{id}/have` → `{ "files": { "frames/000001.jpg": 2481923, … } }` (sizes already received)
2. `PUT  /api/captures/{id}/files/{path}` with the raw file body, for each missing or size-mismatched file. `manifest.json` goes last.
3. `POST /api/captures/{id}/complete` → the engine validates the bundle and adds it to the library.

## Versioning

`version` is an integer. Additive optional fields do not bump it. The engine must keep reading every version it has ever accepted.
