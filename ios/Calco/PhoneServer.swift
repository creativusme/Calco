import Foundation
import Network
import UIKit

/// Serves the captures stored on the phone so the computer can pull them over the USB cable.
///
/// The computer reaches this port through Apple's USB service (usbmuxd), so nothing has to be
/// configured on either side: plug in the cable with Calco open and the scans move by themselves.
/// Protocol (HTTP/1.1, one request per connection):
///   GET  /calco/hello                -> {"name", "app"}
///   GET  /calco/captures             -> [{"id", "name", "sent", "files": {path: size}}]
///   GET  /calco/file/<id>/<path>     -> raw file
///   POST /calco/sent/<id>            -> marks the capture as delivered
final class PhoneServer: ObservableObject {
    static let port: UInt16 = 7879

    /// Last time the computer talked to us, and over which link ("cable" or "wifi-sync").
    @Published private(set) var lastContact: Date?
    @Published private(set) var link: String = "cable"
    @Published private(set) var sending: String?   // name of the capture being pulled
    @Published private(set) var listening = false

    private var listener: NWListener?
    private let queue = DispatchQueue(label: "calco.phone-server")

    func start() {
        guard listener == nil, let port = NWEndpoint.Port(rawValue: Self.port) else { return }
        do {
            let l = try NWListener(using: .tcp, on: port)
            l.newConnectionHandler = { [weak self] conn in self?.handle(conn) }
            l.stateUpdateHandler = { [weak self] state in
                DispatchQueue.main.async {
                    switch state {
                    case .ready: self?.listening = true
                    case .failed, .cancelled:
                        self?.listening = false
                        self?.listener = nil
                        // iOS tears listeners down in the background: try again shortly
                        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self?.start() }
                    default: break
                    }
                }
            }
            l.start(queue: queue)
            listener = l
        } catch {
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.start() }
        }
    }

    func stop() {
        listener?.cancel()
        listener = nil
    }

    var isConnected: Bool {
        guard let t = lastContact else { return false }
        return Date().timeIntervalSince(t) < 8
    }

    // MARK: Request handling

    private func handle(_ conn: NWConnection) {
        conn.start(queue: queue)
        receiveHeader(conn, buffer: Data())
    }

    private func receiveHeader(_ conn: NWConnection, buffer: Data) {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 16384) { [weak self] data, _, done, error in
            guard let self else { return }
            var buf = buffer
            if let data { buf.append(data) }
            if let end = buf.range(of: Data("\r\n\r\n".utf8)) {
                let head = String(decoding: buf[..<end.lowerBound], as: UTF8.self)
                self.route(conn, head: head)
            } else if done || error != nil || buf.count > 65536 {
                conn.cancel()
            } else {
                self.receiveHeader(conn, buffer: buf)
            }
        }
    }

    private func route(_ conn: NWConnection, head: String) {
        let lines = head.components(separatedBy: "\r\n")
        let parts = lines.first?.split(separator: " ").map(String.init) ?? []
        guard parts.count >= 2 else { return respond(conn, status: 400, body: Data()) }
        let method = parts[0]
        let path = parts[1].removingPercentEncoding ?? parts[1]
        var headers: [String: String] = [:]
        for line in lines.dropFirst() {
            if let colon = line.firstIndex(of: ":") {
                headers[line[..<colon].lowercased()] = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
            }
        }
        let link = headers["x-calco-link"] ?? "cable"
        DispatchQueue.main.async {
            self.lastContact = Date()
            self.link = link
        }

        switch (method, path) {
        case ("GET", "/calco/hello"):
            let info: [String: String] = [
                "name": UIDevice.current.name,
                "app": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0",
            ]
            respond(conn, status: 200, body: (try? JSONSerialization.data(withJSONObject: info)) ?? Data())
        case ("GET", "/calco/captures"):
            respond(conn, status: 200, body: capturesJSON())
        case ("GET", _) where path.hasPrefix("/calco/file/"):
            sendFile(conn, path: String(path.dropFirst("/calco/file/".count)))
        case ("POST", _) where path.hasPrefix("/calco/sent/"):
            let id = String(path.dropFirst("/calco/sent/".count))
            let dir = CaptureStore.bundleURL(id: id)
            if FileManager.default.fileExists(atPath: dir.path) {
                FileManager.default.createFile(atPath: dir.appendingPathComponent(".uploaded").path, contents: Data())
                DispatchQueue.main.async {
                    self.sending = nil
                    NotificationCenter.default.post(name: .calcoCapturesChanged, object: nil)
                }
            }
            respond(conn, status: 200, body: Data("{}".utf8))
        default:
            respond(conn, status: 404, body: Data())
        }
    }

    private func capturesJSON() -> Data {
        let fm = FileManager.default
        var out: [[String: Any]] = []
        let dirs = (try? fm.contentsOfDirectory(at: CaptureStore.root, includingPropertiesForKeys: nil)) ?? []
        for dir in dirs where dir.pathExtension == "calco" {
            // skip scans still being recorded or without a manifest
            if fm.fileExists(atPath: dir.appendingPathComponent(".recording").path) { continue }
            guard let data = try? Data(contentsOf: dir.appendingPathComponent("manifest.json")),
                  let manifest = try? JSONDecoder().decode(Manifest.self, from: data) else { continue }
            var files: [String: Int] = [:]
            let root = dir.standardizedFileURL.path
            if let walker = fm.enumerator(at: dir, includingPropertiesForKeys: [.fileSizeKey, .isRegularFileKey]) {
                for case let url as URL in walker {
                    let values = try? url.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey])
                    guard values?.isRegularFile == true else { continue }
                    let rel = String(url.standardizedFileURL.path.dropFirst(root.count))
                        .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
                    if rel.hasPrefix(".") { continue }
                    files[rel] = values?.fileSize ?? 0
                }
            }
            out.append([
                "id": manifest.id, "name": manifest.name, "files": files,
                "sent": fm.fileExists(atPath: dir.appendingPathComponent(".uploaded").path),
            ])
        }
        return (try? JSONSerialization.data(withJSONObject: out)) ?? Data("[]".utf8)
    }

    private func sendFile(_ conn: NWConnection, path: String) {
        // "<id>/<relative path>", confined to that capture's folder
        guard let slash = path.firstIndex(of: "/") else { return respond(conn, status: 400, body: Data()) }
        let id = String(path[..<slash])
        let rel = String(path[path.index(after: slash)...])
        let dir = CaptureStore.bundleURL(id: id).standardizedFileURL
        let file = dir.appendingPathComponent(rel).standardizedFileURL
        guard !rel.contains(".."), file.path.hasPrefix(dir.path + "/"),
              let handle = try? FileHandle(forReadingFrom: file),
              let size = (try? fm.attributesOfItem(atPath: file.path)[.size] as? NSNumber)?.intValue else {
            return respond(conn, status: 404, body: Data())
        }
        if rel.hasPrefix("frames/"), let name = try? JSONDecoder().decode(Manifest.self, from: Data(contentsOf: dir.appendingPathComponent("manifest.json"))).name {
            DispatchQueue.main.async { self.sending = name }
        }
        let header = "HTTP/1.1 200 OK\r\nContent-Length: \(size)\r\nContent-Type: application/octet-stream\r\nConnection: close\r\n\r\n"
        conn.send(content: Data(header.utf8), completion: .contentProcessed { [weak self] error in
            if error != nil { try? handle.close(); conn.cancel(); return }
            self?.pump(conn, handle)
        })
    }

    private var fm: FileManager { FileManager.default }

    /// Stream a file in 1 MB pieces, each sent after the previous one was accepted.
    private func pump(_ conn: NWConnection, _ handle: FileHandle) {
        let chunk = (try? handle.read(upToCount: 1 << 20)) ?? nil
        guard let chunk, !chunk.isEmpty else {
            try? handle.close()
            conn.send(content: nil, contentContext: .finalMessage, isComplete: true, completion: .contentProcessed { _ in conn.cancel() })
            return
        }
        conn.send(content: chunk, completion: .contentProcessed { [weak self] error in
            if error != nil { try? handle.close(); conn.cancel(); return }
            self?.pump(conn, handle)
        })
    }

    private func respond(_ conn: NWConnection, status: Int, body: Data) {
        let reason = [200: "OK", 400: "Bad Request", 404: "Not Found"][status] ?? "Error"
        var data = Data("HTTP/1.1 \(status) \(reason)\r\nContent-Length: \(body.count)\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n".utf8)
        data.append(body)
        conn.send(content: data, contentContext: .finalMessage, isComplete: true, completion: .contentProcessed { _ in conn.cancel() })
    }
}

extension Notification.Name {
    static let calcoCapturesChanged = Notification.Name("calcoCapturesChanged")
}
