import ARKit
import CoreImage
import SceneKit
import UIKit

/// Owns the ARKit session during a scan: decides when to take photos, writes the capture
/// bundle (docs/capture-format.md) and publishes the live guidance shown in the HUD.
final class CaptureSession: NSObject, ObservableObject, ARSessionDelegate {
    // MARK: Published state

    @Published var frameCount = 0
    @Published var isRecording = false
    @Published var hint = "Aim at the object and tap Place box"
    @Published var warning: String?
    @Published var coverage = [Bool](repeating: false, count: CaptureSession.sectors * 2)
    @Published var cropBox: CropBox?
    @Published var objectSize: Float
    @Published var hasLiDAR = false

    static let sectors = 24

    let session = ARSession()
    let mode: ScanMode
    let captureID: String
    weak var sceneView: ARSCNView?

    // MARK: Private state

    private let bundleURL: URL
    private let ioQueue = DispatchQueue(label: "calco.capture.io", qos: .userInitiated)
    private let ciContext = CIContext()
    private var records: [FrameRecord] = []  // ioQueue only
    private var shotPositions: [simd_float3] = []  // main thread only
    private var nextID = 1
    private var inFlight = false
    private var lastShot: simd_float4x4?
    private var lastPose: (transform: simd_float4x4, time: TimeInterval)?
    private var speed: Float = 0
    private var focus: simd_float3?
    private var boxNode: SCNNode?
    private let created = Date()

    init(mode: ScanMode) {
        self.mode = mode
        self.objectSize = mode.defaultSize
        let id = UUID().uuidString.lowercased()
        self.captureID = id
        self.bundleURL = CaptureStore.bundleURL(id: id)
        super.init()
        session.delegate = self
    }

    // MARK: Session lifecycle

    func start() {
        let fm = FileManager.default
        try? fm.createDirectory(at: bundleURL.appendingPathComponent("frames"), withIntermediateDirectories: true)
        try? fm.createDirectory(at: bundleURL.appendingPathComponent("depth"), withIntermediateDirectories: true)

        let config = ARWorldTrackingConfiguration()
        config.planeDetection = [.horizontal]
        config.isAutoFocusEnabled = true
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) {
            config.frameSemantics.insert(.sceneDepth)
            hasLiDAR = true
        }
        if ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh) {
            config.sceneReconstruction = .mesh
        }
        if let hires = ARWorldTrackingConfiguration.recommendedVideoFormatForHighResolutionFrameCapturing {
            config.videoFormat = hires
        }
        session.run(config, options: [.resetTracking, .removeExistingAnchors])
        UIApplication.shared.isIdleTimerDisabled = true
    }

    func stop() {
        session.pause()
        UIApplication.shared.isIdleTimerDisabled = false
    }

    func toggleRecording() {
        isRecording.toggle()
        if isRecording { lastShot = nil }
    }

    /// Finish the scan. Returns false (and deletes the bundle) when there are too few photos.
    func finish(completion: @escaping (Bool) -> Void) {
        isRecording = false
        ioQueue.async { [self] in
            let ok = records.count >= 3
            if ok {
                writeManifest()
                writePreview()
            } else {
                try? FileManager.default.removeItem(at: bundleURL)
            }
            DispatchQueue.main.async { completion(ok) }
        }
    }

    func discard() {
        isRecording = false
        ioQueue.async { [bundleURL] in try? FileManager.default.removeItem(at: bundleURL) }
    }

    // MARK: Crop box

    /// Put the crop box on whatever is under the centre of the screen.
    func placeBox() {
        guard let view = sceneView else { return }
        let centre = CGPoint(x: view.bounds.midX, y: view.bounds.midY)
        var hit: simd_float3?
        if let query = view.raycastQuery(from: centre, allowing: .estimatedPlane, alignment: .any),
           let result = session.raycast(query).first {
            hit = simd_make_float3(result.worldTransform.columns.3)
        } else if let frame = session.currentFrame, let p = Self.centreDepthPoint(frame) {
            hit = p
        }
        guard let base = hit else {
            warning = "Could not find a surface here. Move a little and try again."
            return
        }
        warning = nil
        focus = base
        updateBox(base: base)
        if !isRecording { hint = "Adjust the size, then tap Start" }
    }

    func resizeBox() {
        guard let box = cropBox else { return }
        let base = simd_float3(box.center[0], box.center[1] - box.size[1] / 2, box.center[2])
        updateBox(base: base)
    }

    private func updateBox(base: simd_float3) {
        let s = objectSize
        cropBox = CropBox(center: [base.x, base.y + s / 2, base.z], size: [s, s, s], yaw: 0)
        coverage = [Bool](repeating: false, count: Self.sectors * 2)
        for p in shotPositions { markCoverage(position: p) }

        boxNode?.removeFromParentNode()
        let geometry = SCNBox(width: CGFloat(s), height: CGFloat(s), length: CGFloat(s), chamferRadius: 0)
        let material = SCNMaterial()
        material.diffuse.contents = UIColor(red: 0.92, green: 0.64, blue: 0.36, alpha: 1)
        material.fillMode = .lines
        material.lightingModel = .constant
        material.isDoubleSided = true
        geometry.materials = [material]
        let node = SCNNode(geometry: geometry)
        node.simdPosition = simd_float3(base.x, base.y + s / 2, base.z)
        sceneView?.scene.rootNode.addChildNode(node)
        boxNode = node
    }

    // MARK: ARSessionDelegate

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        let t = frame.camera.transform
        if let last = lastPose, frame.timestamp > last.time {
            let d = simd_distance(simd_make_float3(t.columns.3), simd_make_float3(last.transform.columns.3))
            let v = d / Float(frame.timestamp - last.time)
            speed = 0.8 * speed + 0.2 * v
        }
        lastPose = (t, frame.timestamp)

        let tracking = Self.trackingProblem(frame.camera.trackingState)
        updateGuidance(frame: frame, tracking: tracking)

        guard isRecording, !inFlight, tracking == nil, speed < 0.12 else { return }
        if let last = lastShot {
            let step = mode.shutterStep
            let moved = simd_distance(simd_make_float3(t.columns.3), simd_make_float3(last.columns.3))
            let cosAngle = simd_dot(simd_make_float3(t.columns.2), simd_make_float3(last.columns.2))
            let angle = acos(max(-1, min(1, cosAngle))) * 180 / .pi
            guard moved > step.move || angle > step.angle else { return }
        }
        takePhoto(videoFrame: frame)
    }

    func takeManualPhoto() {
        guard !inFlight, let frame = session.currentFrame else { return }
        takePhoto(videoFrame: frame)
    }

    private func takePhoto(videoFrame: ARFrame) {
        inFlight = true
        lastShot = videoFrame.camera.transform
        // depth comes with the video frame; the photo is a separate high-resolution frame
        let depth = Self.extractDepth(videoFrame)
        session.captureHighResolutionFrame { [weak self] photo, _ in
            guard let self else { return }
            let source = photo ?? videoFrame
            self.save(image: source.capturedImage, camera: source.camera, timestamp: source.timestamp, depth: depth)
        }
    }

    // MARK: Saving

    private struct DepthPayload {
        var depth: Data
        var confidence: Data?
        var width: Int
        var height: Int
        var pose: [Float]
        var intrinsics: [Float]
    }

    private static func extractDepth(_ frame: ARFrame) -> DepthPayload? {
        guard let scene = frame.sceneDepth else { return nil }
        let map = scene.depthMap
        let w = CVPixelBufferGetWidth(map), h = CVPixelBufferGetHeight(map)
        let res = frame.camera.imageResolution
        let k = frame.camera.intrinsics
        let sx = Float(w) / Float(res.width), sy = Float(h) / Float(res.height)
        return DepthPayload(
            depth: bytes(of: map, bytesPerPixel: 4),
            confidence: scene.confidenceMap.map { bytes(of: $0, bytesPerPixel: 1) },
            width: w, height: h,
            pose: rowMajor(frame.camera.transform),
            intrinsics: [k[0][0] * sx, k[1][1] * sy, k[2][0] * sx, k[2][1] * sy])
    }

    private func save(image: CVPixelBuffer, camera: ARCamera, timestamp: TimeInterval, depth: DepthPayload?) {
        let k = camera.intrinsics
        let res = camera.imageResolution
        let pose = Self.rowMajor(camera.transform)
        let exposure = camera.exposureDuration
        let position = simd_make_float3(camera.transform.columns.3)

        ioQueue.async { [self] in
            let id = nextID
            nextID += 1
            let name = String(format: "%06d", id)
            var record = FrameRecord(
                id: id, image: "frames/\(name).jpg", width: Int(res.width), height: Int(res.height),
                timestamp: timestamp, intrinsics: [k[0][0], k[1][1], k[2][0], k[2][1]], pose: pose,
                tracking: "normal", depth: nil, exposure: Exposure(duration: exposure))
            do {
                let jpeg = bundleURL.appendingPathComponent(record.image)
                let options: [CIImageRepresentationOption: Any] =
                    [CIImageRepresentationOption(rawValue: kCGImageDestinationLossyCompressionQuality as String): 0.9]
                try ciContext.writeJPEGRepresentation(
                    of: CIImage(cvPixelBuffer: image), to: jpeg,
                    colorSpace: CGColorSpace(name: CGColorSpace.sRGB)!, options: options)
                if let d = depth {
                    let file = "depth/\(name).f32"
                    try d.depth.write(to: bundleURL.appendingPathComponent(file))
                    var conf: String?
                    if let c = d.confidence {
                        conf = "depth/\(name).u8"
                        try c.write(to: bundleURL.appendingPathComponent(conf!))
                    }
                    record.depth = DepthRecord(file: file, confidence: conf, width: d.width, height: d.height,
                                               pose: d.pose, intrinsics: d.intrinsics)
                }
                records.append(record)
                if records.count % 10 == 0 { writeManifest() }  // survive a crash mid-scan
                let count = records.count
                DispatchQueue.main.async {
                    self.frameCount = count
                    self.shotPositions.append(position)
                    self.markCoverage(position: position)
                    self.addMarker(at: position)
                    self.inFlight = false
                }
            } catch {
                DispatchQueue.main.async {
                    self.warning = "Could not save the photo: \(error.localizedDescription)"
                    self.inFlight = false
                }
            }
        }
    }

    /// Called on ioQueue.
    private func writeManifest() {
        var uts = utsname()
        uname(&uts)
        let model = withUnsafeBytes(of: &uts.machine) { raw in
            String(decoding: raw.prefix(while: { $0 != 0 }), as: UTF8.self)
        }
        let nameFormat = DateFormatter()
        nameFormat.dateFormat = "d MMM HH:mm"
        let manifest = Manifest(
            id: captureID,
            name: "\(mode.title) \(nameFormat.string(from: created))",
            created: ISO8601DateFormatter().string(from: created),
            mode: mode.rawValue,
            device: DeviceInfo(model: model, os: UIDevice.current.systemVersion,
                               app: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0"),
            crop_box: DispatchQueue.main.sync { cropBox },
            frames: records)
        if let data = try? JSONEncoder().encode(manifest) {
            try? data.write(to: bundleURL.appendingPathComponent("manifest.json"), options: .atomic)
        }
    }

    /// Called on ioQueue.
    private func writePreview() {
        guard let first = records.first,
              let image = CIImage(contentsOf: bundleURL.appendingPathComponent(first.image)) else { return }
        let scale = 480 / image.extent.width
        let small = image.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        try? ciContext.writeJPEGRepresentation(
            of: small, to: bundleURL.appendingPathComponent("preview.jpg"),
            colorSpace: CGColorSpace(name: CGColorSpace.sRGB)!, options: [:])
    }

    // MARK: Guidance

    private func markCoverage(position: simd_float3) {
        guard let centre = cropBox.map({ simd_float3($0.center[0], $0.center[1], $0.center[2]) }) ?? focus else { return }
        let v = position - centre
        let length = simd_length(v)
        guard length > 0.01 else { return }
        let azimuth = atan2(v.z, v.x) + .pi
        let sector = min(Self.sectors - 1, Int(azimuth / (2 * .pi) * Float(Self.sectors)))
        let elevation = asin(max(-1, min(1, v.y / length))) * 180 / .pi
        coverage[(elevation > 35 ? Self.sectors : 0) + sector] = true
    }

    private func addMarker(at position: simd_float3) {
        guard let root = sceneView?.scene.rootNode else { return }
        let sphere = SCNSphere(radius: 0.004)
        sphere.firstMaterial?.diffuse.contents = UIColor.white
        sphere.firstMaterial?.lightingModel = .constant
        let node = SCNNode(geometry: sphere)
        node.simdPosition = position
        root.addChildNode(node)
    }

    private func updateGuidance(frame: ARFrame, tracking: String?) {
        var newWarning: String?
        if let tracking {
            newWarning = tracking
        } else if isRecording, speed > 0.12 {
            newWarning = "Slow down"
        } else if let light = frame.lightEstimate, light.ambientIntensity < 200 {
            newWarning = "Too dark: add light for sharp photos"
        }
        if newWarning != warning, !(warning?.hasPrefix("Could not") ?? false) || newWarning != nil {
            warning = newWarning
        }

        guard isRecording else { return }
        if focus == nil, frameCount > 0, let p = Self.centreDepthPoint(frame) { focus = p }
        let low = coverage[0..<Self.sectors].filter { $0 }.count
        let high = coverage[Self.sectors...].filter { $0 }.count
        let newHint: String
        if mode == .space {
            newHint = "Walk slowly and sweep every surface"
        } else if low < Self.sectors * 3 / 4 {
            newHint = "Walk slowly all the way around"
        } else if high < Self.sectors / 2 {
            newHint = "Now go around again from higher up"
        } else {
            newHint = "Good coverage. Add close-ups of details, then tap Done"
        }
        if newHint != hint { hint = newHint }
    }

    var coveragePercent: Int {
        Int((Float(coverage.filter { $0 }.count) / Float(coverage.count) * 100).rounded())
    }

    // MARK: Helpers

    private static func trackingProblem(_ state: ARCamera.TrackingState) -> String? {
        switch state {
        case .normal: return nil
        case .notAvailable: return "Tracking unavailable"
        case .limited(let reason):
            switch reason {
            case .excessiveMotion: return "Moving too fast"
            case .insufficientFeatures: return "Not enough detail in view: step back"
            case .initializing: return "Starting up: move the phone slowly"
            case .relocalizing: return "Lost position: point back at the object"
            @unknown default: return "Tracking limited"
            }
        }
    }

    private static func rowMajor(_ m: simd_float4x4) -> [Float] {
        var out: [Float] = []
        out.reserveCapacity(16)
        for row in 0..<4 { for col in 0..<4 { out.append(m[col][row]) } }
        return out
    }

    private static func bytes(of buffer: CVPixelBuffer, bytesPerPixel: Int) -> Data {
        CVPixelBufferLockBaseAddress(buffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        let w = CVPixelBufferGetWidth(buffer), h = CVPixelBufferGetHeight(buffer)
        let stride = CVPixelBufferGetBytesPerRow(buffer)
        guard let base = CVPixelBufferGetBaseAddress(buffer) else { return Data() }
        var data = Data(capacity: w * h * bytesPerPixel)
        for row in 0..<h {
            data.append(base.advanced(by: row * stride).assumingMemoryBound(to: UInt8.self), count: w * bytesPerPixel)
        }
        return data
    }

    /// World position of what is at the centre of the image, from LiDAR depth.
    private static func centreDepthPoint(_ frame: ARFrame) -> simd_float3? {
        guard let map = frame.sceneDepth?.depthMap else { return nil }
        CVPixelBufferLockBaseAddress(map, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(map, .readOnly) }
        let w = CVPixelBufferGetWidth(map), h = CVPixelBufferGetHeight(map)
        guard let base = CVPixelBufferGetBaseAddress(map) else { return nil }
        let row = base.advanced(by: (h / 2) * CVPixelBufferGetBytesPerRow(map)).assumingMemoryBound(to: Float32.self)
        let z = row[w / 2]
        guard z.isFinite, z > 0.05, z < 5 else { return nil }
        // the centre pixel lies on the optical axis: z metres in front of the camera (-Z)
        let t = frame.camera.transform
        return simd_make_float3(t.columns.3) - simd_make_float3(t.columns.2) * z
    }
}
