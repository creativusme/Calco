import Foundation

// Mirrors docs/capture-format.md (version 1). Optional fields are omitted from the JSON when nil.

struct Manifest: Codable {
    var format = "calco-capture"
    var version = 1
    var id: String
    var name: String
    var created: String
    var mode: String
    var device: DeviceInfo
    var crop_box: CropBox?
    var frames: [FrameRecord]
    /// One entry per side when the part was turned over during the scan (Flip).
    var sides: [SideRecord]? = nil
}

struct SideRecord: Codable {
    var crop_box: CropBox?
}

struct DeviceInfo: Codable {
    var model: String
    var os: String
    var app: String
}

struct CropBox: Codable, Equatable {
    var center: [Float]
    var size: [Float]
    var yaw: Float
}

struct DepthRecord: Codable {
    var file: String
    var confidence: String?
    var width: Int
    var height: Int
    /// Pose and intrinsics of the video frame the depth map belongs to (it is not the photo's frame).
    var pose: [Float]?
    var intrinsics: [Float]?
}

struct Exposure: Codable {
    var duration: Double
}

struct FrameRecord: Codable {
    var id: Int
    var image: String
    var width: Int
    var height: Int
    var timestamp: Double
    var intrinsics: [Float]
    var pose: [Float]?
    var tracking: String
    var depth: DepthRecord?
    var exposure: Exposure?
    /// 0 = as placed, 1 = after the first Flip, ...
    var side: Int? = nil
}

enum ScanMode: String, CaseIterable, Identifiable {
    case object, person, space
    var id: String { rawValue }

    var title: String {
        switch self {
        case .object: return "Object"
        case .person: return "Person"
        case .space: return "Space"
        }
    }

    var subtitle: String {
        switch self {
        case .object: return "Parts and things you can walk around. Use the scan mat for exact scale."
        case .person: return "Busts and people sitting still."
        case .space: return "Rooms, large machines, exteriors."
        }
    }

    /// Camera movement (metres) and rotation (degrees) between automatic photos.
    var shutterStep: (move: Float, angle: Float) {
        switch self {
        case .object: return (0.04, 8)
        case .person: return (0.06, 8)
        case .space: return (0.25, 15)
        }
    }

    var defaultSize: Float {
        switch self {
        case .object: return 0.3
        case .person: return 0.8
        case .space: return 3.0
        }
    }
}
