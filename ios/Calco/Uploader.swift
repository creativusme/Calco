import Foundation
import Network

/// Finds the Calco engine on the local network (Bonjour `_calco._tcp`) and uploads capture
/// bundles to it. Uploads are per file and resumable: files the computer already has are skipped.
final class Uploader: ObservableObject {
    @Published var discovered: [String] = []          // "192.168.1.20:7878"
    @Published var progress: [String: Double] = [:]   // capture id -> 0...1 while uploading
    @Published var errors: [String: String] = [:]     // capture id -> last error
    @Published var manualAddress: String {
        didSet { UserDefaults.standard.set(manualAddress, forKey: "serverAddress") }
    }

    private var browser: NWBrowser?

    init() {
        manualAddress = UserDefaults.standard.string(forKey: "serverAddress") ?? ""
    }

    /// The address uploads go to: the one typed in Settings, else the first computer found.
    var server: String? {
        let typed = manualAddress.trimmingCharacters(in: .whitespaces)
        if !typed.isEmpty { return typed.contains(":") ? typed : "\(typed):7878" }
        return discovered.first
    }

    // MARK: Discovery

    func startBrowsing() {
        guard browser == nil else { return }
        let b = NWBrowser(for: .bonjour(type: "_calco._tcp", domain: nil), using: .tcp)
        b.browseResultsChangedHandler = { [weak self] results, _ in
            for result in results { self?.resolve(result.endpoint) }
        }
        b.start(queue: .main)
        browser = b
    }

    /// Bonjour gives a service name; connect once to learn its IPv4 address and port.
    private func resolve(_ endpoint: NWEndpoint) {
        let params = NWParameters.tcp
        if let ip = params.defaultProtocolStack.internetProtocol as? NWProtocolIP.Options {
            ip.version = .v4
        }
        let connection = NWConnection(to: endpoint, using: params)
        connection.stateUpdateHandler = { [weak self] state in
            switch state {
            case .ready:
                if case let .hostPort(host, port)? = connection.currentPath?.remoteEndpoint {
                    var name = "\(host)"
                    if let scope = name.firstIndex(of: "%") { name = String(name[..<scope]) }
                    let address = "\(name):\(port.rawValue)"
                    if let self, !self.discovered.contains(address) { self.discovered.append(address) }
                }
                connection.cancel()
            case .failed:
                connection.cancel()
            default:
                break
            }
        }
        connection.start(queue: .main)
    }

    // MARK: Upload

    private struct Have: Decodable {
        var files: [String: Int64]
    }

    enum UploadError: LocalizedError {
        case noServer
        case rejected(String)

        var errorDescription: String? {
            switch self {
            case .noServer: return "No computer found. Open Calco on the computer, same Wi-Fi, or type its address in Settings."
            case .rejected(let why): return why
            }
        }
    }

    @MainActor
    func send(_ capture: CaptureSummary, store: CaptureStore) async {
        guard progress[capture.id] == nil else { return }
        errors[capture.id] = nil
        progress[capture.id] = 0
        do {
            try await upload(capture)
            store.markUploaded(capture)
        } catch {
            errors[capture.id] = error.localizedDescription
        }
        progress[capture.id] = nil
    }

    @MainActor
    private func upload(_ capture: CaptureSummary) async throws {
        guard let server, let base = URL(string: "http://\(server)") else { throw UploadError.noServer }
        let api = base.appendingPathComponent("api/captures/\(capture.id)")

        let (haveData, _) = try await URLSession.shared.data(from: api.appendingPathComponent("have"))
        let have = (try? JSONDecoder().decode(Have.self, from: haveData))?.files ?? [:]

        // every file in the bundle, manifest last so the computer never sees a half-sent capture as complete
        var files: [(path: String, url: URL, size: Int64)] = []
        let root = capture.url.standardizedFileURL.path
        if let walker = FileManager.default.enumerator(at: capture.url, includingPropertiesForKeys: [.fileSizeKey, .isRegularFileKey]) {
            for case let url as URL in walker {
                let values = try? url.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey])
                guard values?.isRegularFile == true else { continue }
                let full = url.standardizedFileURL.path
                guard full.hasPrefix(root) else { continue }
                let relative = String(full.dropFirst(root.count)).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
                if relative.hasPrefix(".") { continue }
                files.append((relative, url, Int64(values?.fileSize ?? 0)))
            }
        }
        files.sort { ($0.path == "manifest.json" ? 1 : 0, $0.path) < ($1.path == "manifest.json" ? 1 : 0, $1.path) }

        let total = max(1, files.reduce(0) { $0 + $1.size })
        var sent: Int64 = 0
        for file in files {
            if have[file.path] != file.size || file.path == "manifest.json" {
                var request = URLRequest(url: api.appendingPathComponent("files/\(file.path)"))
                request.httpMethod = "PUT"
                request.timeoutInterval = 120
                let (_, response) = try await URLSession.shared.upload(for: request, fromFile: file.url)
                guard (response as? HTTPURLResponse)?.statusCode == 200 else {
                    throw UploadError.rejected("The computer refused \(file.path)")
                }
            }
            sent += file.size
            progress[capture.id] = Double(sent) / Double(total)
        }

        var done = URLRequest(url: api.appendingPathComponent("complete"))
        done.httpMethod = "POST"
        let (body, response) = try await URLSession.shared.data(for: done)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw UploadError.rejected(String(data: body, encoding: .utf8) ?? "The computer rejected the capture")
        }
    }
}
