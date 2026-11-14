import Foundation
import UIKit

/// Wi-Fi link to the Calco engine on the computer: connects only to the computer paired with a
/// network code and password (no automatic discovery), checks every few seconds that it answers,
/// and uploads captures to it.
/// Uploads are per file and resumable: files the computer already has are skipped.
final class Uploader: ObservableObject {
    enum WifiState: Equatable {
        case notPaired            // no code / password typed yet
        case searching            // paired, first check not answered yet
        case connected(String)    // computer name
        case unreachable(String)  // found or typed, but it does not answer: why
    }

    @Published private(set) var wifi: WifiState = Pairing.isPaired ? .searching : .notPaired
    @Published var progress: [String: Double] = [:]   // capture id -> 0...1 while uploading
    @Published var errors: [String: String] = [:]     // capture id -> last error
    @Published var autoSend: Bool {
        didSet { UserDefaults.standard.set(autoSend, forKey: "autoSend") }
    }

    private var timer: Timer?
    private let deviceName = UIDevice.current.name
    private lazy var session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 8
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }()

    init() {
        autoSend = UserDefaults.standard.object(forKey: "autoSend") as? Bool ?? true
    }

    /// The paired computer's address, from its network code.
    var server: String? {
        guard Pairing.isPaired, let ip = Pairing.address(fromCode: Pairing.code) else { return nil }
        return "\(ip):7878"
    }

    /// Save a new code + password and check them right away. Returns true when the computer accepts them.
    @MainActor
    func pair(code: String, password: String) async -> Bool {
        Pairing.code = Pairing.format(code)
        Pairing.password = password.filter(\.isNumber)
        wifi = Pairing.isPaired ? .searching : .notPaired
        await ping()
        return isConnected
    }

    @MainActor
    func unpair() {
        Pairing.password = ""
        Pairing.code = ""
        wifi = .notPaired
    }

    private func authorize(_ request: inout URLRequest) {
        request.setValue(deviceName, forHTTPHeaderField: "X-Calco-Device")
        request.setValue(Pairing.password, forHTTPHeaderField: "X-Calco-Pin")
    }

    var isConnected: Bool {
        if case .connected = wifi { return true }
        return false
    }

    // MARK: Heartbeat

    func start() {
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

    private struct Hello: Decodable { var hostname: String }

    @MainActor
    func ping() async {
        guard let server, let url = URL(string: "http://\(server)/api/hello") else {
            wifi = .notPaired
            return
        }
        var request = URLRequest(url: url)
        authorize(&request)
        request.timeoutInterval = 3
        do {
            let (data, response) = try await session.data(for: request)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            if status == 401 {
                wifi = .unreachable("Wrong password: type the 6 digits shown in Calco on the computer.")
                return
            }
            guard status == 200 else {
                wifi = .unreachable("That network code is not a Calco computer.")
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
            return "The computer does not answer. Is Calco running on it, on the same Wi-Fi? Windows Firewall may block it (Calco on the computer shows how to fix it). The cable always works."
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
            case .noServer: return "Not paired with a computer: Settings → Connect, then type the code and password shown on the computer."
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
        authorize(&haveRequest)
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
                authorize(&request)
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
        authorize(&done)
        let (body, response) = try await session.data(for: done)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw UploadError.rejected(String(data: body, encoding: .utf8) ?? "The computer rejected the capture")
        }
    }
}

extension Notification.Name {
    static let calcoComputerFound = Notification.Name("calcoComputerFound")
}
