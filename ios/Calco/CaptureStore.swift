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

    /// A scan interrupted by a crash or by iOS killing the app keeps its ".recording" marker.
    /// Nothing records at launch, so clear them: whatever was saved becomes a normal capture.
    static func recoverInterrupted() {
        let fm = FileManager.default
        for dir in (try? fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)) ?? [] where dir.pathExtension == "calco" {
            let marker = dir.appendingPathComponent(".recording")
            guard fm.fileExists(atPath: marker.path) else { continue }
            if fm.fileExists(atPath: dir.appendingPathComponent("manifest.json").path) {
                try? fm.removeItem(at: marker)
            } else {
                try? fm.removeItem(at: dir)  // not even one manifest written: nothing usable
            }
        }
    }

    func reload() {
        let fm = FileManager.default
        let dirs = (try? fm.contentsOfDirectory(at: Self.root, includingPropertiesForKeys: nil)) ?? []
        let iso = ISO8601DateFormatter()
        var out: [CaptureSummary] = []
        for dir in dirs where dir.pathExtension == "calco" {
            if fm.fileExists(atPath: dir.appendingPathComponent(".recording").path) { continue }
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

    func delete(_ ids: Set<String>) {
        for capture in captures where ids.contains(capture.id) {
            try? FileManager.default.removeItem(at: capture.url)
        }
        reload()
    }

    func delete(_ capture: CaptureSummary) {
        delete([capture.id])
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

    var totalBytes: Int64 { captures.reduce(0) { $0 + $1.bytes } }

    static var freeBytes: Int64 {
        let values = try? root.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
        return values?.volumeAvailableCapacityForImportantUsage ?? 0
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
