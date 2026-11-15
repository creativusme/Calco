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
    @State private var previewing: CaptureSummary?

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                ConnectionBar(uploader: uploader, server: server) { showSettings = true }
                content
            }
            .navigationTitle("Calco")
            .toolbar { toolbar }
            .safeAreaInset(edge: .bottom) { bottomBar }
            .animation(.snappy(duration: 0.25), value: selecting)
            .animation(.easeInOut(duration: 0.2), value: store.captures)
        }
        .tint(.calcoAccent)
        .onAppear {
            store.reload()
            uploader.start()
            server.start()
            if !Pairing.isPaired { showSettings = true }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { server.start(); uploader.start(); store.reload() }
            if phase == .background { uploader.stop() }
        }
        .onReceive(NotificationCenter.default.publisher(for: .calcoCapturesChanged)) { _ in store.reload() }
        .onReceive(NotificationCenter.default.publisher(for: .calcoComputerFound)) { _ in
            Task { await uploader.sendPending(store: store) }
        }
        .confirmationDialog("What are you scanning?", isPresented: $pickingMode, titleVisibility: .visible) {
            ForEach(ScanMode.allCases) { mode in
                Button(mode.title) { scanning = mode }
            }
        } message: {
            Text("Get as close as you like: when the main lens can no longer focus, the close-up lens takes over by itself.")
        }
        .fullScreenCover(item: $scanning) { mode in
            CaptureView(mode: mode) {
                store.reload()
                Task { await uploader.sendPending(store: store) }
            }
        }
        .sheet(isPresented: $showSettings) { SettingsView(uploader: uploader, server: server, store: store) }
        .sheet(item: $previewing) { MeshPreviewView(capture: $0) }
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
                        .onTapGesture { if !selecting { previewing = capture } }
                        .swipeActions {
                            Button("Delete", role: .destructive) { confirmDelete = [capture.id] }
                            Button("Rename") { newName = capture.name; renaming = capture }
                        }
                        .contextMenu {
                            Button("Rename", systemImage: "pencil") { newName = capture.name; renaming = capture }
                            Button("3D preview", systemImage: "cube") { previewing = capture }
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
            Button { showSettings = true } label: { Image(systemName: "gearshape") }
                .accessibilityLabel("Settings")
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
            .transition(.move(edge: .bottom).combined(with: .opacity))
        } else {
            Button { pickingMode = true } label: {
                Label("New scan", systemImage: "plus").font(.headline).frame(maxWidth: .infinity).padding(.vertical, 6)
            }
            .buttonStyle(.borderedProminent)
            .foregroundStyle(.black)
            .padding()
            .transition(.opacity)
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
/// It re-reads the clock every 2 s on its own, so the rest of the screen never redraws for it.
private struct ConnectionBar: View {
    @ObservedObject var uploader: Uploader
    @ObservedObject var server: PhoneServer
    let openSettings: () -> Void

    var body: some View {
        TimelineView(.periodic(from: .now, by: 2)) { context in
            let state = BarState(uploader: uploader, server: server, now: context.date)
            Button(action: openSettings) {
                HStack(spacing: 10) {
                    Circle().fill(state.color).frame(width: 9, height: 9)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(state.title).font(.subheadline.weight(.semibold))
                        Text(state.detail).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                    }
                    Spacer()
                    Image(systemName: state.icon).foregroundStyle(.secondary)
                }
                .padding(.horizontal, 16).padding(.vertical, 10)
                .background(state.color.opacity(0.12))
                .animation(.easeInOut(duration: 0.3), value: state.title)
            }
            .buttonStyle(.plain)
            .accessibilityHint("Opens connection settings")
        }
    }

    struct BarState {
        let color: Color, icon: String, title: String, detail: String

        init(uploader: Uploader, server: PhoneServer, now: Date) {
            let cable = server.lastContact.map { now.timeIntervalSince($0) < 8 } ?? false
            let refused = server.refused.map { now.timeIntervalSince($0) < 8 } ?? false
            if cable {
                color = .green
                icon = server.link == "cable" ? "cable.connector" : "wifi"
                title = server.sending.map { "Sending \($0)…" } ?? (server.link == "cable" ? "Connected by cable" : "Connected (Wi-Fi sync)")
                detail = "New scans go to the computer by themselves."
                return
            }
            if refused {
                color = .orange
                icon = "lock"
                title = "A computer is asking for your scans"
                detail = "Pair first: tap here and type the code and password shown on the computer."
                return
            }
            switch uploader.wifi {
            case .connected(let host):
                color = .green
                icon = "wifi"
                title = "Connected by Wi-Fi · \(host)"
                detail = uploader.autoSend ? "New scans are sent automatically." : "Tap Send on a scan to transfer it."
            case .unreachable(let why):
                color = .orange
                icon = "wifi.exclamationmark"
                title = "Paired computer not reachable"
                detail = why
            case .searching:
                color = .gray
                icon = "wifi"
                title = "Connecting…"
                detail = "Checking the paired computer."
            case .notPaired:
                color = .gray
                icon = "link"
                title = "Not paired with a computer"
                detail = "Tap here and type the network code and password shown in Calco on the computer."
            }
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
            Thumbnail(url: capture.previewURL)
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
            Group {
                if let p = uploader.progress[capture.id] {
                    ProgressView(value: p).progressViewStyle(.circular)
                    Text("\(Int(p * 100))%").font(.caption).monospacedDigit().frame(width: 38, alignment: .trailing)
                        .contentTransition(.numericText())
                } else if server.sending == capture.name {
                    ProgressView().progressViewStyle(.circular)
                } else if capture.uploaded {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(.green).accessibilityLabel("On the computer")
                } else {
                    Button("Send", action: send).buttonStyle(.bordered)
                }
            }
            .transition(.opacity)
        }
        .padding(.vertical, 2)
        .animation(.easeInOut(duration: 0.2), value: capture.uploaded)
    }
}

/// Preview image loaded once and cached, so list refreshes never make it flicker.
private struct Thumbnail: View {
    let url: URL
    @State private var image: UIImage?
    private static let cache = NSCache<NSURL, UIImage>()

    var body: some View {
        ZStack {
            Color.secondary.opacity(0.15)
            if let image {
                Image(uiImage: image).resizable().scaledToFill().transition(.opacity)
            }
        }
        .task(id: url) {
            if let cached = Self.cache.object(forKey: url as NSURL) {
                image = cached
                return
            }
            let loaded = await Task.detached(priority: .utility) { UIImage(contentsOfFile: url.path) }.value
            if let loaded {
                Self.cache.setObject(loaded, forKey: url as NSURL)
                withAnimation(.easeOut(duration: 0.2)) { image = loaded }
            }
        }
    }
}

private struct SettingsView: View {
    @ObservedObject var uploader: Uploader
    @ObservedObject var server: PhoneServer
    @ObservedObject var store: CaptureStore
    @Environment(\.dismiss) private var dismiss
    @State private var code = Pairing.code
    @State private var password = Pairing.password
    @State private var checking = false
    @State private var result: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Network code, e.g. 30AG-0EK", text: $code)
                        .font(.body.monospaced())
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                    SecureField("Password (6 digits)", text: $password)
                        .keyboardType(.numberPad)
                        .font(.body.monospaced())
                    Button {
                        checking = true
                        result = nil
                        Task {
                            let ok = await uploader.pair(code: code, password: password)
                            checking = false
                            result = ok ? "Connected." : nil
                        }
                    } label: {
                        HStack {
                            Text(Pairing.isPaired ? "Save and test" : "Connect")
                            if checking { Spacer(); ProgressView() }
                        }
                    }
                    .disabled(Pairing.address(fromCode: code) == nil || password.filter(\.isNumber).count != 6 || checking)
                    statusLine
                    if Pairing.isPaired {
                        Button("Forget this computer", role: .destructive) {
                            uploader.unpair()
                            code = ""
                            password = ""
                            result = nil
                        }
                    }
                } header: {
                    Text("Connect")
                } footer: {
                    Text("Both are shown at the bottom left of Calco on the computer. They are needed for Wi-Fi and for the cable: only the paired computer can receive your scans.")
                }
                Section {
                    LabeledContent("Cable", value: server.isConnected ? "Connected" : (server.listening ? "Waiting for the computer" : "Starting…"))
                    Toggle("Send new scans automatically", isOn: $uploader.autoSend)
                } header: {
                    Text("Transfer")
                } footer: {
                    Text("By cable, the computer collects missing scans by itself while Calco is open on both. Over Wi-Fi, new scans are sent when the paired computer answers.")
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
            .animation(.easeInOut(duration: 0.2), value: uploader.wifi)
        }
    }

    @ViewBuilder private var statusLine: some View {
        switch uploader.wifi {
        case .connected(let host): Label("Connected to \(host)", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
        case .unreachable(let why): Text(why).foregroundStyle(.orange).font(.callout)
        case .searching: Text("Checking…").foregroundStyle(.secondary)
        case .notPaired: EmptyView()
        }
    }
}

extension Color {
    /// Calco lime, the same signal colour as the desktop app.
    static let calcoAccent = Color(red: 0.82, green: 0.95, blue: 0.29)
}
