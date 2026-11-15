import ARKit
import SceneKit
import SwiftUI

/// The LiDAR mesh ARKit builds while scanning, copied out of its anchors (world coordinates).
struct MeshCopy {
    var vertices: [simd_float3] = []
    var faces: [UInt32] = []

    init(_ anchor: ARMeshAnchor) {
        let geometry = anchor.geometry
        let source = geometry.vertices
        let base = source.buffer.contents().advanced(by: source.offset)
        vertices.reserveCapacity(source.count)
        for i in 0..<source.count {
            let p = base.advanced(by: i * source.stride).assumingMemoryBound(to: Float.self)
            let w = anchor.transform * simd_float4(p[0], p[1], p[2], 1)
            vertices.append(simd_float3(w.x, w.y, w.z))
        }
        let element = geometry.faces
        let count = element.count * element.indexCountPerPrimitive
        let raw = element.buffer.contents()
        faces.reserveCapacity(count)
        if element.bytesPerIndex == 4 {
            let p = raw.assumingMemoryBound(to: UInt32.self)
            for i in 0..<count { faces.append(p[i]) }
        } else {
            let p = raw.assumingMemoryBound(to: UInt16.self)
            for i in 0..<count { faces.append(UInt32(p[i])) }
        }
    }

    /// Write the meshes as one OBJ, keeping only triangles inside the crop box (plus a margin) when there is one.
    static func writeOBJ(_ meshes: [MeshCopy], box: CropBox?, to url: URL) {
        func inside(_ p: simd_float3) -> Bool {
            guard let box, box.center.count == 3, box.size.count == 3 else { return true }
            let d = p - simd_float3(box.center[0], box.center[1], box.center[2])
            let c = cos(-box.yaw), s = sin(-box.yaw)
            let x = c * d.x + s * d.z, z = -s * d.x + c * d.z
            let half = simd_float3(box.size[0], box.size[1], box.size[2]) / 2 + 0.05
            return abs(x) <= half.x && d.y >= -half.y - 0.05 && d.y <= half.y && abs(z) <= half.z
        }
        var text = "# Calco LiDAR preview mesh (ARKit world, metres)\n"
        var offset: UInt32 = 1
        for mesh in meshes {
            var keep = [Bool](repeating: false, count: mesh.vertices.count)
            for (i, v) in mesh.vertices.enumerated() {
                keep[i] = inside(v)
                text += "v \(v.x) \(v.y) \(v.z)\n"
            }
            var i = 0
            while i + 2 < mesh.faces.count {
                let a = mesh.faces[i], b = mesh.faces[i + 1], c = mesh.faces[i + 2]
                if keep[Int(a)] && keep[Int(b)] && keep[Int(c)] {
                    text += "f \(a + offset) \(b + offset) \(c + offset)\n"
                }
                i += 3
            }
            offset += UInt32(mesh.vertices.count)
        }
        try? text.write(to: url, atomically: true, encoding: .utf8)
    }
}

/// A quick look at the scan on the phone: the LiDAR mesh in clay, orbit with one finger.
/// The real model is built on the computer; this only shows what the scan covered.
struct MeshPreviewView: View {
    let capture: CaptureSummary
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Group {
                if let scene = Self.scene(url: capture.meshURL) {
                    SceneView(scene: scene, options: [.allowsCameraControl, .autoenablesDefaultLighting])
                        .ignoresSafeArea(edges: .bottom)
                } else {
                    ContentUnavailableView("No 3D preview", systemImage: "cube.transparent",
                                           description: Text("Scans made with app 0.5 or later keep a LiDAR preview."))
                }
            }
            .navigationTitle(capture.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
    }

    static func scene(url: URL) -> SCNScene? {
        guard FileManager.default.fileExists(atPath: url.path),
              let scene = try? SCNScene(url: url, options: nil) else { return nil }
        let clay = SCNMaterial()
        clay.diffuse.contents = UIColor(white: 0.72, alpha: 1)
        clay.lightingModel = .physicallyBased
        clay.roughness.contents = 0.85
        clay.isDoubleSided = true
        scene.rootNode.enumerateChildNodes { node, _ in
            node.geometry?.materials = [clay]
        }
        scene.background.contents = UIColor.systemBackground
        return scene
    }
}
