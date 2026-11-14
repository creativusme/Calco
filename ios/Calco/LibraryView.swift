import SwiftUI

/// Home screen: connection to the computer, the scans stored on the phone, starting a new one.
struct LibraryView: View {
    @StateObject private var store = CaptureStore()
    @StateObject private var uploader = Uploader()
    @StateObject private var server = PhoneServer()
    @Environment(\.scenePhase) private var scenePhase
    @State private var pickingMode = false
    @State private var scanning: ScanMode?
    @State private var showSettings = false
    @State private var renaming: CaptureSummary?
    @State private var newName = ""
    @State private var selecting = false
    @State private var selection = Set<String>()
    @State private var confirmDelete: Set<String>?
    @State private var now = Date()
    private let tick = Timer.publish(every: 2, on: .main, in: .common).autoconnect()

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                ConnectionBar(uploader: uploader, server: server, now: now) { showSettings = true }
                content
            }
            .navigationTitle("Calco")
            .toolbar { toolbar }
            .safeAreaInset(edge: .bottom) { bottomBar }
        }
        .tint(.calcoAccent)
        .onAppear {
            store.reload()
            uploader.start()
            server.start()
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { server.start(); uploader.start(); store.reload() }
            if phase == .background { uploader.stop() }
        }
        .onReceive(tick) { now = $0 }
        .onReceive(NotificationCenter.default.publisher(for: .calcoCapturesChanged)) { _ in store.reload() }
        .onReceive(NotificationCenter.default.publisher(for: .calcoComputerFound)) { _ in
            Task { await uploader.sendPending(store: store) }
        }
        .confirmationDialog("What are you scanning?", isPresented: $pickingMode, titleVisibility: .visible) {
            ForEach(ScanMode.allCases) { mode in
                Button(mode.title) { scanning = mode }
            }
        }
        .fullScreenCover(item: $scanning) { mode in
            CaptureView(mode: mode) {
                store.reload()
                Task { await uploader.sendPending(store: store) }
            }
        }
        .sheet(isPresented: $showSettings) { SettingsView(uploader: uploader, server: server, store: store) }
        .alert("Rename scan", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField("Name", text: $newName)
            Button("Save") { if let r = renaming, !newName.isEmpty { store.rename(r, to: newName) } }
            Button("Cancel", role: .cancel) {}
        }
        .confirmationDialog(deleteTitle, isPresented: Binding(get: { confirmDelete != nil }, set: { if !$0 { confirmDelete = nil } }),
                            titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                if let ids = confirmDelete { store.delete(ids) }
                selection.removeAll()
                selecting = false
            }
        } message: {
            Text(deleteMessage)
        }
    }

    // MARK: Pieces

    @ViewBuilder private var content: some View {
        if store.captures.isEmpty {
            ContentUnavailableView("No scans yet", systemImage: "cube.transparent",
                                   description: Text("Tap New scan and walk around the object. Scans go to the computer by cable or Wi-Fi."))
        } else {
            List(selection: selecting ? $selection : nil) {
                Section {
                    ForEach(store.captures) { capture in
                        CaptureRow(capture: capture, uploader: uploader, server: server) {
                            Task { await uploader.send(capture, store: store) }
                        }
                        .tag(capture.id)
                        .swipeActions {
                            Button("Delete", role: .destructive) { confirmDelete = [capture.id] }
                            Button("Rename") { newName = capture.name; renaming = capture }
                        }
                        .contextMenu {
                            Button("Rename", systemImage: "pencil") { newName = capture.name; renaming = capture }
                            Button("Send to computer", systemImage: "arrow.up.circle") { Task { await uploader.send(capture, store: store) } }
                            Button("Delete", systemImage: "trash", role: .destructive) { confirmDelete = [capture.id] }
                        }
                    }
                } footer: {
                    Text("\(store.captures.count) scans · \(ByteCountFormatter.string(fromByteCount: store.totalBytes, countStyle: .file)) · \(ByteCountFormatter.string(fromByteCount: CaptureStore.freeBytes, countStyle: .file)) free")
                }
            }
            .environment(\.editMode, .constant(selecting ? .active : .inactive))
        }
    }

    @ToolbarContentBuilder private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            if !store.captures.isEmpty {
                Button(selecting ? "Done" : "Select") {
                    selecting.toggle()
                    selection.removeAll()
                }
            }
        }
        ToolbarItem(placement: .topBarTrailing) {
            Menu {
                let sent = Set(store.captures.filter(\.uploaded).map(\.id))
                Button("Delete sent scans (\(sent.count))", systemImage: "trash", role: .destructive) { confirmDelete = sent }
                    .disabled(sent.isEmpty)
                Button("Settings", systemImage: "gearshape") { showSettings = true }
            } label: {
                Image(systemName: "ellipsis.circle")
            }
            .accessibilityLabel("More")
        }
    }

    @ViewBuilder private var bottomBar: some View {
        if selecting {
            HStack {
                Button(selection.count == store.captures.count ? "Select none" : "Select all") {
                    selection = selection.count == store.captures.count ? [] : Set(store.captures.map(\.id))
                }
                Spacer()
                Button("Delete (\(selection.count))", role: .destructive) { confirmDelete = selection }
                    .disabled(selection.isEmpty)
                    .buttonStyle(.borderedProminent).tint(.red)
            }
            .padding()
            .background(.bar)
        } else {
            Button { pickingMode = true } label: {
                Label("New scan", systemImage: "plus").font(.headline).frame(maxWidth: .infinity).padding(.vertical, 6)
            }
            .buttonStyle(.borderedProminent)
            .foregroundStyle(.black)
            .padding()
        }
    }

    private var deleteTitle: String {
        let n = confirmDelete?.count ?? 0
        return n == 1 ? "Delete this scan?" : "Delete \(n) scans?"
    }

    private var deleteMessage: String {
        let ids = confirmDelete ?? []
        let unsent = store.captures.filter { ids.contains($0.id) && !$0.uploaded }.count
        return unsent > 0
            ? "\(unsent) of them have not reached the computer yet and will be lost."
            : "They are already on the computer. This only frees space on the phone."
    }
}

/// One line that always says whether the computer is reachable, and how.
private struct ConnectionBar: View {
    @ObservedObject var uploader: Uploader
    @ObservedObject var server: PhoneServer
    let now: Date
    let openSettings: () -> Void

    var body: some View {
        Button(action: openSettings) {
            HStack(spacing: 10) {
                Circle().fill(color).frame(width: 9, height: 9)
                VStack(alignment: .leading, spacing: 1) {
                    Text(title).font(.subheadline.weight(.semibold))
                    Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
                Spacer()
                Image(systemName: icon).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 16).padding(.vertical, 10)
            .background(color.opacity(0.12))
        }
        .buttonStyle(.plain)
        .accessibilityHint("Opens connection settings")
    }

    private var cable: Bool {
        guard let t = server.lastContact else { return false }
        return now.timeIntervalSince(t) < 8
    }

    private var color: Color {
        if cable || uploader.isConnected { return .green }
        if case .unreachable = uploader.wifi { return .orange }
        return .gray
    }

    private var icon: String {
        if cable { return server.link == "cable" ? "cable.connector" : "wifi" }
        return uploader.isConnected ? "wifi" : "wifi.slash"
    }

    private var title: String {
        if cable {
            if let s = server.sending { return "Sending \(s)…" }
            return server.link == "cable" ? "Connected by cable" : "Connected (Wi-Fi sync)"
        }
        if case let .connected(host) = uploader.wifi { return "Connected by Wi-Fi · \(host)" }
        return "Not connected to the computer"
    }

    private var detail: String {
        if cable { return "New scans go to the computer by themselves." }
        switch uploader.wifi {
        case .connected:
            return uploader.autoSend ? "New scans are sent automatically." : "Tap Send on a scan to transfer it."
        case .unreachable(let why):
            return why
        case .searching:
            return "Open Calco on the computer. Then plug in the cable, or use the same Wi-Fi."
        }
    }
}

private struct CaptureRow: View {
    let capture: CaptureSummary
    @ObservedObject var uploader: Uploader
    @ObservedObject var server: PhoneServer
    let send: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            AsyncImage(url: capture.previewURL) { image in
                image.resizable().scaledToFill()
            } placeholder: {
                Color.secondary.opacity(0.2)
            }
            .frame(width: 64, height: 48).clipShape(RoundedRectangle(cornerRadius: 6))

            VStack(alignment: .leading, spacing: 2) {
                Text(capture.name).font(.body.weight(.medium)).lineLimit(1)
                Text("\(capture.frames) photos · \(ByteCountFormatter.string(fromByteCount: capture.bytes, countStyle: .file))")
                    .font(.caption).foregroundStyle(.secondary)
                if let error = uploader.errors[capture.id] {
                    Text(error).font(.caption).foregroundStyle(.red).lineLimit(3)
                }
            }
            Spacer()
            if let p = uploader.progress[capture.id] {
                ProgressView(value: p).progressViewStyle(.circular)
                Text("\(Int(p * 100))%").font(.caption).monospacedDigit().frame(width: 38, alignment: .trailing)
            } else if server.sending == capture.name {
                ProgressView().progressViewStyle(.circular)
            } else if capture.uploaded {
                Label("On computer", systemImage: "checkmark.circle.fill").labelStyle(.iconOnly)
                    .foregroundStyle(.green).accessibilityLabel("On the computer")
            } else {
                Button("Send", action: send).buttonStyle(.bordered)
            }
        }
        .padding(.vertical, 2)
    }
}

private struct SettingsView: View {
    @ObservedObject var uploader: Uploader
    @ObservedObject var server: PhoneServer
    @ObservedObject var store: CaptureStore
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    LabeledContent("Cable", value: server.isConnected ? "Connected" : (server.listening ? "Waiting for the computer" : "Starting…"))
                } header: {
                    Text("USB cable")
                } footer: {
                    Text("Plug the iPhone into the computer with Calco open on both. Missing scans move by themselves, no setup needed. The computer needs Apple Devices or iTunes installed (Sideloadly already requires it).")
                }
                Section {
                    switch uploader.wifi {
                    case .connected(let host): LabeledContent("Status", value: "Connected to \(host)")
                    case .unreachable(let why): Text(why).foregroundStyle(.orange)
                    case .searching: Text("Searching the Wi-Fi network…").foregroundStyle(.secondary)
                    }
                    ForEach(uploader.discovered, id: \.self) { address in
                        Label(address, systemImage: "desktopcomputer")
                    }
                    TextField("Address, e.g. 192.168.1.20:7878", text: $uploader.manualAddress)
                        .keyboardType(.numbersAndPunctuation)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                    Toggle("Send new scans automatically", isOn: $uploader.autoSend)
                    Button("Test connection") { Task { await uploader.ping() } }
                } header: {
                    Text("Wi-Fi")
                } footer: {
                    Text("Phone and computer on the same Wi-Fi. The address is only needed when the computer is not found by itself; it is shown at the bottom left of Calco on the computer. If it does not answer, Windows Firewall is usually the reason: Calco on the computer shows how to fix it.")
                }
                Section {
                    LabeledContent("Scans", value: "\(store.captures.count)")
                    LabeledContent("Used", value: ByteCountFormatter.string(fromByteCount: store.totalBytes, countStyle: .file))
                    LabeledContent("Free on iPhone", value: ByteCountFormatter.string(fromByteCount: CaptureStore.freeBytes, countStyle: .file))
                } header: {
                    Text("Storage")
                }
                Section {
                    LabeledContent("Version", value: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "-")
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
    }
}

extension Color {
    /// Calco lime, the same signal colour as the desktop app.
    static let calcoAccent = Color(red: 0.82, green: 0.95, blue: 0.29)
}
