import ARKit
import SceneKit
import SwiftUI

/// The scanning screen: live camera with LiDAR mesh, crop box, coverage ring and shutter.
struct CaptureView: View {
    @StateObject private var capture: CaptureSession
    @Environment(\.dismiss) private var dismiss
    @State private var confirmDiscard = false
    @State private var finishing = false
    @State private var flash = false
    @State private var showDetails = false
    let onFinished: () -> Void

    init(mode: ScanMode, onFinished: @escaping () -> Void) {
        _capture = StateObject(wrappedValue: CaptureSession(mode: mode))
        self.onFinished = onFinished
    }

    var body: some View {
        ZStack {
            if capture.mode == .macro {
                MacroCaptureScreen(capture: capture, title: "Macro scan") { finish() }
            } else {
                ARContainer(capture: capture).ignoresSafeArea()
                arOverlay
            }
        }
        .overlay {
            // a quick bright frame around the screen each time a photo is taken
            RoundedRectangle(cornerRadius: 44)
                .strokeBorder(Color.calcoAccent, lineWidth: 10)
                .opacity(flash ? 0.9 : 0)
                .ignoresSafeArea()
                .allowsHitTesting(false)
        }
        .onChange(of: capture.shutterCount) { _, _ in
            flash = true
            withAnimation(.easeOut(duration: 0.35)) { flash = false }
        }
        .statusBarHidden()
        .onAppear { capture.start() }
        .onDisappear { capture.stop() }
        .fullScreenCover(isPresented: $showDetails, onDismiss: { capture.resumeAfterDetails() }) {
            MacroCaptureScreen(capture: capture, title: "Detail photos") { showDetails = false }
        }
        .confirmationDialog("Discard this scan?", isPresented: $confirmDiscard, titleVisibility: .visible) {
            Button("Discard \(capture.frameCount) photos", role: .destructive) {
                capture.discard()
                dismiss()
            }
            Button("Keep scanning", role: .cancel) {}
        }
    }

    private func finish() {
        finishing = true
        capture.finish { saved in
            finishing = false
            if saved { onFinished() }
            dismiss()
        }
    }

    private var arOverlay: some View {
            VStack(spacing: 12) {
                topBar
                if let warning = capture.warning {
                    Text(warning)
                        .font(.subheadline.weight(.semibold))
                        .padding(.horizontal, 14).padding(.vertical, 8)
                        .background(.red.opacity(0.85), in: Capsule())
                        .foregroundStyle(.white)
                }
                Spacer()
                Text(capture.hint)
                    .font(.subheadline)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(.ultraThinMaterial, in: Capsule())
                    .animation(.easeInOut(duration: 0.2), value: capture.hint)
                if capture.mode != .space, !capture.isRecording {
                    sizeControl
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }
                bottomBar
            }
            .padding()
            .animation(.snappy(duration: 0.25), value: capture.isRecording)
            .animation(.easeInOut(duration: 0.2), value: capture.warning)
    }

    private var topBar: some View {
        HStack {
            Button {
                if capture.frameCount > 0 { confirmDiscard = true } else { capture.discard(); dismiss() }
            } label: {
                Image(systemName: "xmark").font(.headline).frame(width: 40, height: 40)
                    .background(.ultraThinMaterial, in: Circle())
            }
            .accessibilityLabel("Close")
            Spacer()
            HStack(spacing: 10) {
                Label("\(capture.frameCount)", systemImage: "camera.fill").monospacedDigit()
                if capture.mode != .space {
                    CoverageRing(coverage: capture.coverage).frame(width: 26, height: 26)
                    Text("\(capture.coveragePercent)%").monospacedDigit()
                }
                if !capture.hasLiDAR {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.yellow)
                        .accessibilityLabel("No LiDAR on this device")
                }
            }
            .font(.subheadline.weight(.semibold))
            .padding(.horizontal, 14).frame(height: 40)
            .background(.ultraThinMaterial, in: Capsule())
        }
        .foregroundStyle(.white)
    }

    private var sizeControl: some View {
        HStack(spacing: 12) {
            Button(capture.cropBox == nil ? "Place box" : "Move box") { capture.placeBox() }
                .buttonStyle(.borderedProminent).tint(.calcoAccent).foregroundStyle(.black)
            if capture.cropBox != nil {
                Slider(value: Binding(get: { Double(log10(capture.objectSize)) },
                                      set: { capture.objectSize = Float(pow(10, $0)); capture.resizeBox() }),
                       in: log10(0.03)...log10(3.0))
                Text(Self.sizeLabel(capture.objectSize)).monospacedDigit().frame(width: 62, alignment: .trailing)
            }
        }
        .font(.subheadline)
        .padding(.horizontal, 14).padding(.vertical, 8)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16))
        .foregroundStyle(.white)
    }

    private var bottomBar: some View {
        HStack {
            Button {
                capture.pauseForDetails()
                showDetails = true
            } label: {
                Image(systemName: "camera.macro").font(.title3).frame(width: 52, height: 52)
                    .background(.ultraThinMaterial, in: Circle())
            }
            .accessibilityLabel("Close-up detail photos")
            Spacer()
            Button { capture.toggleRecording() } label: {
                ZStack {
                    Circle().strokeBorder(.white, lineWidth: 4).frame(width: 78, height: 78)
                    RoundedRectangle(cornerRadius: capture.isRecording ? 8 : 32)
                        .fill(.red)
                        .frame(width: capture.isRecording ? 32 : 64, height: capture.isRecording ? 32 : 64)
                        .animation(.easeInOut(duration: 0.15), value: capture.isRecording)
                }
            }
            .accessibilityLabel(capture.isRecording ? "Pause" : "Start")
            Spacer()
            Button(action: finish) {
                Text("Done").font(.headline).frame(width: 64, height: 52)
                    .background(.ultraThinMaterial, in: Capsule())
            }
            .disabled(capture.frameCount < 3 || finishing)
            .opacity(capture.frameCount < 3 ? 0.4 : 1)
        }
        .foregroundStyle(.white)
    }

    private static func sizeLabel(_ metres: Float) -> String {
        metres < 1 ? String(format: "%.0f cm", metres * 100) : String(format: "%.1f m", metres)
    }
}

/// Top-down ring: inner band = photos from the side, outer band = photos from above.
struct CoverageRing: View {
    let coverage: [Bool]

    var body: some View {
        Canvas { context, size in
            let n = CaptureSession.sectors
            let centre = CGPoint(x: size.width / 2, y: size.height / 2)
            let outer = min(size.width, size.height) / 2
            for band in 0..<2 {
                let radius = outer * (band == 0 ? 0.55 : 0.9)
                for i in 0..<n {
                    let a0 = Angle.radians(Double(i) / Double(n) * 2 * .pi)
                    let a1 = Angle.radians((Double(i) + 0.8) / Double(n) * 2 * .pi)
                    var path = Path()
                    path.addArc(center: centre, radius: radius, startAngle: a0, endAngle: a1, clockwise: false)
                    let done = coverage[band * n + i]
                    let colour: Color = done ? Color.green : Color.white.opacity(0.3)
                    context.stroke(path, with: .color(colour), lineWidth: outer * 0.22)
                }
            }
        }
        .accessibilityHidden(true)
    }
}

/// ARSCNView wrapper. Draws the LiDAR mesh as a faint wireframe so scanned surfaces are visible.
struct ARContainer: UIViewRepresentable {
    let capture: CaptureSession

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> ARSCNView {
        let view = ARSCNView(frame: .zero)
        view.session = capture.session
        view.delegate = context.coordinator
        view.scene = SCNScene()
        view.automaticallyUpdatesLighting = true
        view.rendersMotionBlur = false
        capture.sceneView = view
        return view
    }

    func updateUIView(_ uiView: ARSCNView, context: Context) {}

    final class Coordinator: NSObject, ARSCNViewDelegate {
        func renderer(_ renderer: SCNSceneRenderer, nodeFor anchor: ARAnchor) -> SCNNode? {
            guard let mesh = anchor as? ARMeshAnchor else { return nil }
            return Self.node(for: mesh.geometry)
        }

        func renderer(_ renderer: SCNSceneRenderer, didUpdate node: SCNNode, for anchor: ARAnchor) {
            guard let mesh = anchor as? ARMeshAnchor else { return }
            let fresh = Self.node(for: mesh.geometry)
            node.geometry = fresh.geometry
            node.childNodes.forEach { $0.removeFromParentNode() }
            fresh.childNodes.forEach { node.addChildNode($0) }
        }

        /// Translucent shaded surface plus a faint wireframe: a live preview of what has been scanned
        /// that never hides the camera image.
        private static func node(for mesh: ARMeshGeometry) -> SCNNode {
            let surface = Self.geometry(from: mesh)
            let fill = SCNMaterial()
            fill.diffuse.contents = UIColor(red: 0.82, green: 0.95, blue: 0.29, alpha: 0.16)
            fill.lightingModel = .lambert
            fill.isDoubleSided = true
            fill.writesToDepthBuffer = false
            surface.materials = [fill]

            let wire = Self.geometry(from: mesh)
            let line = SCNMaterial()
            line.diffuse.contents = UIColor(white: 1, alpha: 0.18)
            line.fillMode = .lines
            line.lightingModel = .constant
            wire.materials = [line]

            let node = SCNNode(geometry: surface)
            node.addChildNode(SCNNode(geometry: wire))
            return node
        }

        private static func geometry(from mesh: ARMeshGeometry) -> SCNGeometry {
            let vertices = SCNGeometrySource(
                buffer: mesh.vertices.buffer, vertexFormat: mesh.vertices.format, semantic: .vertex,
                vertexCount: mesh.vertices.count, dataOffset: mesh.vertices.offset, dataStride: mesh.vertices.stride)
            let faces = mesh.faces
            let data = Data(bytes: faces.buffer.contents(), count: faces.buffer.length)
            let element = SCNGeometryElement(data: data, primitiveType: .triangles, primitiveCount: faces.count,
                                             bytesPerIndex: faces.bytesPerIndex)
            let normals = SCNGeometrySource(
                buffer: mesh.normals.buffer, vertexFormat: mesh.normals.format, semantic: .normal,
                vertexCount: mesh.normals.count, dataOffset: mesh.normals.offset, dataStride: mesh.normals.stride)
            return SCNGeometry(sources: [vertices, normals], elements: [element])
        }
    }
}
