import AVFoundation
import CoreMotion
import SwiftUI

/// Close-up camera for small parts and details. Uses the phone's virtual multi-lens camera, which
/// switches by itself to the ultra-wide lens (the one that focuses at 2 cm) when the subject is close.
/// No ARKit here: these photos have no pose; the engine places them from the photos themselves
/// and takes the scale from the scan mat or from the LiDAR part of the same capture.
final class MacroCamera: NSObject, ObservableObject, AVCapturePhotoCaptureDelegate {
    @Published private(set) var running = false
    @Published private(set) var lens = ""
    @Published var auto = true
    @Published private(set) var steady = true
    @Published private(set) var error: String?

    let session = AVCaptureSession()
    var onPhoto: ((Data, Int, Int) -> Void)?

    private let output = AVCapturePhotoOutput()
    private let queue = DispatchQueue(label: "calco.macro")
    private let motion = CMMotionManager()
    private var lastAttitude: CMAttitude?
    private var lastShot = Date.distantPast
    private var busy = false
    private var lensObservation: NSKeyValueObservation?

    func start() {
        queue.async { [self] in
            if session.inputs.isEmpty { configure() }
            session.startRunning()
            let ok = session.isRunning
            DispatchQueue.main.async {
                self.running = ok
                if ok { self.startMotion() }
            }
        }
    }

    func stop() {
        motion.stopDeviceMotionUpdates()
        queue.async { self.session.stopRunning() }
        running = false
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
            if device.isFocusModeSupported(.continuousAutoFocus) { device.focusMode = .continuousAutoFocus }
            if device.isSmoothAutoFocusSupported { device.isSmoothAutoFocusEnabled = false }
            device.unlockForConfiguration()
        }
        lensObservation = device.observe(\.activePrimaryConstituent, options: [.initial, .new]) { [weak self] d, _ in
            let name: String
            switch d.activePrimaryConstituent?.deviceType {
            case .some(.builtInUltraWideCamera): name = "Macro lens"
            case .some(.builtInTelephotoCamera): name = "Tele lens"
            default: name = "Main lens"
            }
            DispatchQueue.main.async { self?.lens = name }
        }
    }

    /// Automatic shutter: a photo each time the phone has turned about 6 degrees and is held still
    /// (rotation below 0.35 rad/s), so close-ups are not blurred.
    private func startMotion() {
        guard motion.isDeviceMotionAvailable else { return }
        motion.deviceMotionUpdateInterval = 1.0 / 30
        motion.startDeviceMotionUpdates(to: .main) { [weak self] data, _ in
            guard let self, let data else { return }
            let r = data.rotationRate
            let spin = sqrt(r.x * r.x + r.y * r.y + r.z * r.z)
            self.steady = spin < 0.35
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

/// Full-screen close-up camera. Used for the Macro scan mode and for "Details" inside a LiDAR scan.
struct MacroCaptureScreen: View {
    @ObservedObject var capture: CaptureSession
    let title: String
    let onDone: () -> Void
    @StateObject private var camera = MacroCamera()
    @State private var added = 0

    var body: some View {
        ZStack {
            CameraPreview(session: camera.session).ignoresSafeArea()
            VStack(spacing: 12) {
                HStack {
                    Text(title).font(.headline)
                    Spacer()
                    if !camera.lens.isEmpty {
                        Text(camera.lens).font(.caption.weight(.semibold))
                            .padding(.horizontal, 10).padding(.vertical, 5)
                            .background(.ultraThinMaterial, in: Capsule())
                            .transition(.opacity)
                    }
                    Label("\(added)", systemImage: "camera.macro").monospacedDigit()
                        .font(.subheadline.weight(.semibold))
                        .padding(.horizontal, 12).frame(height: 34)
                        .background(.ultraThinMaterial, in: Capsule())
                        .contentTransition(.numericText())
                }
                .foregroundStyle(.white)
                if let error = camera.error {
                    Text(error).padding(8).background(.red.opacity(0.85), in: Capsule()).foregroundStyle(.white)
                }
                Spacer()
                Text(camera.steady ? "Hold 3–15 cm from the part and turn around it slowly" : "Hold still")
                    .font(.subheadline)
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(.ultraThinMaterial, in: Capsule())
                    .foregroundStyle(.white)
                    .animation(.easeInOut(duration: 0.2), value: camera.steady)
                HStack {
                    Toggle(isOn: $camera.auto) { Text("Auto").font(.subheadline.weight(.semibold)) }
                        .toggleStyle(.button).tint(.calcoAccent)
                    Spacer()
                    Button { camera.shoot() } label: {
                        ZStack {
                            Circle().strokeBorder(.white, lineWidth: 4).frame(width: 78, height: 78)
                            Circle().fill(.white).frame(width: 64, height: 64)
                        }
                    }
                    .accessibilityLabel("Take photo")
                    Spacer()
                    Button(action: onDone) {
                        Text("Done").font(.headline).frame(width: 74, height: 52)
                            .background(.ultraThinMaterial, in: Capsule())
                    }
                    .foregroundStyle(.white)
                }
            }
            .padding()
        }
        .onAppear {
            camera.onPhoto = { data, w, h in
                capture.appendPhoto(jpeg: data, width: w, height: h)
                withAnimation(.snappy) { added += 1 }
            }
            camera.start()
        }
        .onDisappear { camera.stop() }
    }
}
