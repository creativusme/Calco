import Foundation
import UIKit

/// One capture bundle on disk (Documents/<id>.calco).
struct CaptureSummary: Identifiable, Equatable {
    let id: String
    var name: String
    var mode: String
    var created: Date
    var frames: Int
    var bytes: Int64
    var url: URL
    var uploaded: Bool

    var previewURL: URL { url.appendingPathComponent("preview.jpg") }
}

final class CaptureStore: ObservableObject {
    @Published var captures: [CaptureSummary] = []

    static var root: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    }

    static func bundleURL(id: String) -> URL {
        root.appendingPathComponent("\(id).calco", isDirectory: true)
    }

    func reload() {
        let fm = FileManager.default
        let dirs = (try? fm.contentsOfDirectory(at: Self.root, includingPropertiesForKeys: nil)) ?? []
        let iso = ISO8601DateFormatter()
        var out: [CaptureSummary] = []
        for dir in dirs where dir.pathExtension == "calco" {
            guard let data = try? Data(contentsOf: dir.appendingPathComponent("manifest.json")),
                  let m = try? JSONDecoder().decode(Manifest.self, from: data) else { continue }
            out.append(CaptureSummary(
                id: m.id, name: m.name, mode: m.mode,
                created: iso.date(from: m.created) ?? Date(),
                frames: m.frames.count, bytes: Self.size(of: dir), url: dir,
                uploaded: fm.fileExists(atPath: dir.appendingPathComponent(".uploaded").path)))
        }
        captures = out.sorted { $0.created > $1.created }
    }

    func delete(_ capture: CaptureSummary) {
        try? FileManager.default.removeItem(at: capture.url)
        reload()
    }

    func rename(_ capture: CaptureSummary, to name: String) {
        let url = capture.url.appendingPathComponent("manifest.json")
        guard let data = try? Data(contentsOf: url),
              var m = try? JSONDecoder().decode(Manifest.self, from: data) else { return }
        m.name = name
        if let out = try? JSONEncoder().encode(m) {
            try? out.write(to: url, options: .atomic)
            // the computer must receive the new manifest
            try? FileManager.default.removeItem(at: capture.url.appendingPathComponent(".uploaded"))
        }
        reload()
    }

    func markUploaded(_ capture: CaptureSummary) {
        FileManager.default.createFile(atPath: capture.url.appendingPathComponent(".uploaded").path, contents: Data())
        reload()
    }

    static func size(of dir: URL) -> Int64 {
        var total: Int64 = 0
        if let e = FileManager.default.enumerator(at: dir, includingPropertiesForKeys: [.fileSizeKey]) {
            for case let f as URL in e {
                total += Int64((try? f.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0)
            }
        }
        return total
    }
}
