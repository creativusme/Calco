import AVFoundation
import CoreMotion
import SwiftUI

/// Close-up camera used automatically during a scan when the phone gets too close for the main
/// lens. It is the phone's virtual multi-lens camera set to "1x", so, exactly like the Camera app,
/// it switches by itself to the ultra-wide lens (which focuses at 2 cm) when the subject is close.
/// ARKit cannot run at the same time (it owns the main camera): these photos carry no pose; the
/// engine places them from the photos themselves.
final class MacroCamera: NSObject, ObservableObject, AVCapturePhotoCaptureDelegate {
    enum Lens: Equatable { case unknown, main, macro, tele }

    @Published private(set) var running = false
    @Published private(set) var lens: Lens = .unknown
    @Published private(set) var steady = true
    @Published private(set) var error: String?
    /// Automatic shutter on/off (follows the scan's record button).
    var auto = false

    let session = AVCaptureSession()
    var onPhoto: ((Data, Int, Int) -> Void)?

    private let output = AVCapturePhotoOutput()
    private let queue = DispatchQueue(label: "calco.macro")
    private let motion = CMMotionManager()
    private var lastAttitude: CMAttitude?
    private var lastShot = Date.distantPast
    private var busy = false
    private var lensObservation: NSKeyValueObservation?

    /// Starts the camera. ARKit releases the camera asynchronously, so retry a few times.
    func start(attempt: Int = 0) {
        queue.async { [self] in
            if session.inputs.isEmpty { configure() }
            if !session.isRunning { session.startRunning() }
            let ok = session.isRunning
            DispatchQueue.main.async {
                self.running = ok
                if ok {
                    self.startMotion()
                } else if attempt < 8 {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { self.start(attempt: attempt + 1) }
                } else {
                    self.error = "Close-up camera not available"
                }
            }
        }
    }

    /// Stops the camera and calls back once it has really let go of it (so ARKit can take it back).
    func stop(completion: (() -> Void)? = nil) {
        motion.stopDeviceMotionUpdates()
        running = false
        queue.async {
            self.session.stopRunning()
            DispatchQueue.main.async { completion?() }
        }
    }

    private func configure() {
        session.beginConfiguration()
        defer { session.commitConfiguration() }
        session.sessionPreset = .photo
        let types: [AVCaptureDevice.DeviceType] = [.builtInTripleCamera, .builtInDualWideCamera, .builtInWideAngleCamera]
        guard let device = AVCaptureDevice.DiscoverySession(deviceTypes: types, mediaType: .video, position: .back).devices.first,
              let input = try? AVCaptureDeviceInput(device: device),
              session.canAddInput(input), session.canAddOutput(output) else {
            DispatchQueue.main.async { self.error = "Camera not available" }
            return
        }
        session.addInput(input)
        session.addOutput(output)
        output.maxPhotoQualityPrioritization = .quality
        if let best = device.activeFormat.supportedMaxPhotoDimensions.max(by: { $0.width * $0.height < $1.width * $1.height }) {
            output.maxPhotoDimensions = best
        }
        // photos in sensor (landscape) orientation with EXIF orientation 1, like the ARKit photos
        if let connection = output.connection(with: .video), connection.isVideoRotationAngleSupported(0) {
            connection.videoRotationAngle = 0
        }
        if (try? device.lockForConfiguration()) != nil {
            // A virtual camera starts at its widest lens. Zoom to "1x" (the main lens): from there it
            // drops to the ultra-wide only when the subject is too close to focus, i.e. automatic macro.
            if let oneX = device.virtualDeviceSwitchOverVideoZoomFactors.first {
                device.videoZoomFactor = CGFloat(truncating: oneX)
            }
            if device.isFocusModeSupported(.continuousAutoFocus) { device.focusMode = .continuousAutoFocus }
            device.unlockForConfiguration()
        }
        lensObservation = device.observe(\.activePrimaryConstituent, options: [.initial, .new]) { [weak self] d, _ in
            let lens: Lens
            switch d.activePrimaryConstituent?.deviceType {
            case .some(.builtInUltraWideCamera): lens = .macro
            case .some(.builtInTelephotoCamera): lens = .tele
            case .some(.builtInWideAngleCamera): lens = .main
            default: lens = d.isVirtualDevice ? .unknown : .main
            }
            DispatchQueue.main.async { self?.lens = lens }
        }
    }

    /// Automatic shutter: a photo each time the phone has turned about 6 degrees and is held still
    /// (rotation below 0.35 rad/s), so close-ups are not blurred.
    private func startMotion() {
        guard motion.isDeviceMotionAvailable, !motion.isDeviceMotionActive else { return }
        motion.deviceMotionUpdateInterval = 1.0 / 30
        motion.startDeviceMotionUpdates(to: .main) { [weak self] data, _ in
            guard let self, let data else { return }
            let r = data.rotationRate
            let spin = sqrt(r.x * r.x + r.y * r.y + r.z * r.z)
            if (spin < 0.35) != self.steady { self.steady = spin < 0.35 }
            guard self.auto, self.running, self.steady, Date().timeIntervalSince(self.lastShot) > 0.6 else { return }
            if let last = self.lastAttitude, let now = data.attitude.copy() as? CMAttitude {
                now.multiply(byInverseOf: last)
                let angle = 2 * acos(min(1, abs(now.quaternion.w))) * 180 / .pi
                guard angle > 6 else { return }
            }
            self.shoot()
        }
    }

    func shoot() {
        guard !busy, running else { return }
        busy = true
        lastShot = Date()
        lastAttitude = motion.deviceMotion?.attitude.copy() as? CMAttitude
        let settings: AVCapturePhotoSettings
        if output.availablePhotoCodecTypes.contains(.jpeg) {
            settings = AVCapturePhotoSettings(format: [AVVideoCodecKey: AVVideoCodecType.jpeg])
        } else {
            settings = AVCapturePhotoSettings()
        }
        settings.maxPhotoDimensions = output.maxPhotoDimensions
        settings.photoQualityPrioritization = .balanced
        queue.async { self.output.capturePhoto(with: settings, delegate: self) }
    }

    func photoOutput(_ output: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto, error: Error?) {
        let dims = photo.resolvedSettings.photoDimensions
        let data = error == nil ? photo.fileDataRepresentation() : nil
        DispatchQueue.main.async {
            self.busy = false
            if let data { self.onPhoto?(data, Int(dims.width), Int(dims.height)) }
        }
    }
}

struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> PreviewView {
        let view = PreviewView()
        view.previewLayer.session = session
        view.previewLayer.videoGravity = .resizeAspectFill
        return view
    }

    func updateUIView(_ uiView: PreviewView, context: Context) {}

    final class PreviewView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    }
}

/// Shown over the scan while the phone is too close for LiDAR tracking. Takes close-up photos into
/// the same capture and hands back to LiDAR by itself when the phone moves away again.
struct CloseUpLayer: View {
    @ObservedObject var capture: CaptureSession
    @StateObject private var camera = MacroCamera()
    @State private var mainSince: Date?
    @State private var leaving = false
    private let tick = Timer.publish(every: 0.2, on: .main, in: .common).autoconnect()

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            CameraPreview(session: camera.session).ignoresSafeArea()
                .opacity(camera.running ? 1 : 0)
                .animation(.easeOut(duration: 0.25), value: camera.running)
            VStack {
                Spacer().frame(height: 64)
                HStack(spacing: 8) {
                    Image(systemName: "camera.macro")
                    Text(camera.lens == .macro ? "Close-up lens" : "Close-up")
                    Text("· LiDAR paused").foregroundStyle(.white.opacity(0.7))
                }
                .font(.subheadline.weight(.semibold))
                .padding(.horizontal, 14).padding(.vertical, 8)
                .background(Color.calcoAccent.opacity(0.9), in: Capsule())
                .foregroundStyle(.black)
                if let error = camera.error {
                    Text(error).padding(8).background(.red.opacity(0.85), in: Capsule()).foregroundStyle(.white)
                }
                Spacer()
                Text(camera.steady ? "Turn slowly around the detail. Move back to return to LiDAR." : "Hold still")
                    .font(.subheadline)
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(.ultraThinMaterial, in: Capsule())
                    .foregroundStyle(.white)
                    .animation(.easeInOut(duration: 0.2), value: camera.steady)
                Spacer().frame(height: 120)
            }
            .padding()
        }
        .onAppear {
            camera.auto = capture.isRecording
            camera.onPhoto = { data, w, h in
                if capture.isRecording || !camera.auto { capture.appendPhoto(jpeg: data, width: w, height: h) }
            }
            camera.start()
        }
        .onDisappear { camera.stop() }
        .onChange(of: capture.isRecording) { _, recording in camera.auto = recording }
        .onChange(of: capture.manualShot) { _, _ in camera.shoot() }
        .onReceive(tick) { now in
            // back to LiDAR once the phone has been far enough for the main lens for a moment
            guard !leaving, camera.running, now.timeIntervalSince(capture.closeUpSince) > 1.5 else { return }
            if camera.lens == .main {
                if mainSince == nil { mainSince = now }
                if let since = mainSince, now.timeIntervalSince(since) > 1.2 { leave() }
            } else {
                mainSince = nil
            }
        }
        .onChange(of: capture.leaveCloseUpRequest) { _, _ in leave() }
    }

    private func leave() {
        guard !leaving else { return }
        leaving = true
        camera.stop { capture.leaveCloseUp() }
    }
}
