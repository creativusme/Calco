import Foundation
import Network
import UIKit

/// Wi-Fi link to the Calco engine on the computer: finds it (Bonjour `_calco._tcp` or a typed
/// address), checks every few seconds that it answers, and uploads captures to it.
/// Uploads are per file and resumable: files the computer already has are skipped.
final class Uploader: ObservableObject {
    enum WifiState: Equatable {
        case searching            // nothing found yet
        case connected(String)    // computer name
        case unreachable(String)  // found or typed, but it does not answer: why
    }

    @Published var discovered: [String] = []          // "192.168.1.20:7878"
    @Published private(set) var wifi: WifiState = .searching
    @Published var progress: [String: Double] = [:]   // capture id -> 0...1 while uploading
    @Published var errors: [String: String] = [:]     // capture id -> last error
    @Published var manualAddress: String {
        didSet { UserDefaults.standard.set(manualAddress, forKey: "serverAddress"); wifi = .searching }
    }
    @Published var autoSend: Bool {
        didSet { UserDefaults.standard.set(autoSend, forKey: "autoSend") }
    }

    private var browser: NWBrowser?
    private var timer: Timer?
    private let deviceName = UIDevice.current.name
    private lazy var session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 8
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }()

    init() {
        manualAddress = UserDefaults.standard.string(forKey: "serverAddress") ?? ""
        autoSend = UserDefaults.standard.object(forKey: "autoSend") as? Bool ?? true
    }

    /// The address uploads go to: the one typed in Settings, else the first computer found.
    var server: String? {
        let typed = manualAddress.trimmingCharacters(in: .whitespaces)
        if !typed.isEmpty { return typed.contains(":") ? typed : "\(typed):7878" }
        return discovered.first
    }

    var isConnected: Bool {
        if case .connected = wifi { return true }
        return false
    }

    // MARK: Discovery and heartbeat

    func start() {
        startBrowsing()
        guard timer == nil else { return }
        let t = Timer(timeInterval: 3, repeats: true) { [weak self] _ in Task { await self?.ping() } }
        RunLoop.main.add(t, forMode: .common)
        timer = t
        Task { await ping() }
    }

    func stop() {
        timer?.invalidate()
        timer = nil
    }

    private func startBrowsing() {
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

    private struct Hello: Decodable { var hostname: String }

    @MainActor
    func ping() async {
        guard let server, let url = URL(string: "http://\(server)/api/hello") else {
            wifi = .searching
            return
        }
        var request = URLRequest(url: url)
        request.setValue(deviceName, forHTTPHeaderField: "X-Calco-Device")
        request.timeoutInterval = 3
        do {
            let (data, response) = try await session.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else {
                wifi = .unreachable("\(server) is not Calco: check the address.")
                return
            }
            let host = (try? JSONDecoder().decode(Hello.self, from: data))?.hostname ?? server
            let wasConnected = isConnected
            wifi = .connected(host)
            if !wasConnected { NotificationCenter.default.post(name: .calcoComputerFound, object: nil) }
        } catch {
            wifi = .unreachable(Self.explain(error, server: server))
        }
    }

    static func explain(_ error: Error, server: String) -> String {
        let code = (error as? URLError)?.code
        switch code {
        case .timedOut, .cannotConnectToHost, .networkConnectionLost:
            return "\(server) does not answer. On the computer: is Calco running? Windows Firewall may block it (see Calco on the computer). The USB cable always works."
        case .notConnectedToInternet:
            return "This phone is not on a Wi-Fi network."
        case .appTransportSecurityRequiresSecureConnection:
            return "iOS blocked the connection (app settings)."
        default:
            return "\(server): \(error.localizedDescription)"
        }
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
            case .noServer: return "No computer connected. Plug in the cable, or open Calco on the computer on the same Wi-Fi."
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
            errors[capture.id] = (error as? UploadError)?.errorDescription ?? Self.explain(error, server: server ?? "computer")
        }
        progress[capture.id] = nil
    }

    /// Send every capture the computer has not received yet (used when it is found on Wi-Fi).
    @MainActor
    func sendPending(store: CaptureStore) async {
        guard autoSend, isConnected else { return }
        for capture in store.captures where !capture.uploaded && errors[capture.id] == nil {
            await send(capture, store: store)
        }
    }

    @MainActor
    private func upload(_ capture: CaptureSummary) async throws {
        guard let server, let base = URL(string: "http://\(server)") else { throw UploadError.noServer }
        let api = base.appendingPathComponent("api/captures/\(capture.id)")

        var haveRequest = URLRequest(url: api.appendingPathComponent("have"))
        haveRequest.setValue(deviceName, forHTTPHeaderField: "X-Calco-Device")
        let (haveData, _) = try await session.data(for: haveRequest)
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
                request.setValue(deviceName, forHTTPHeaderField: "X-Calco-Device")
                let (_, response) = try await session.upload(for: request, fromFile: file.url)
                guard (response as? HTTPURLResponse)?.statusCode == 200 else {
                    throw UploadError.rejected("The computer refused \(file.path)")
                }
            }
            sent += file.size
            progress[capture.id] = Double(sent) / Double(total)
        }

        var done = URLRequest(url: api.appendingPathComponent("complete"))
        done.httpMethod = "POST"
        done.setValue(deviceName, forHTTPHeaderField: "X-Calco-Device")
        let (body, response) = try await session.data(for: done)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw UploadError.rejected(String(data: body, encoding: .utf8) ?? "The computer rejected the capture")
        }
    }
}

extension Notification.Name {
    static let calcoComputerFound = Notification.Name("calcoComputerFound")
}
