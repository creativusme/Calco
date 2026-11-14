import SwiftUI

/// Home screen: the scans stored on the phone, sending them to the computer, starting a new one.
struct LibraryView: View {
    @StateObject private var store = CaptureStore()
    @StateObject private var uploader = Uploader()
    @State private var pickingMode = false
    @State private var scanning: ScanMode?
    @State private var showSettings = false
    @State private var renaming: CaptureSummary?
    @State private var newName = ""

    var body: some View {
        NavigationStack {
            Group {
                if store.captures.isEmpty {
                    ContentUnavailableView("No scans yet", systemImage: "cube.transparent",
                                           description: Text("Tap New scan, walk around the object, then send it to your computer."))
                } else {
                    List {
                        ForEach(store.captures) { capture in
                            CaptureRow(capture: capture, uploader: uploader) {
                                Task { await uploader.send(capture, store: store) }
                            }
                            .swipeActions {
                                Button("Delete", role: .destructive) { store.delete(capture) }
                                Button("Rename") { newName = capture.name; renaming = capture }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Calco")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { showSettings = true } label: { Image(systemName: "gearshape") }
                        .accessibilityLabel("Settings")
                }
            }
            .safeAreaInset(edge: .bottom) {
                Button { pickingMode = true } label: {
                    Label("New scan", systemImage: "plus").font(.headline).frame(maxWidth: .infinity).padding(.vertical, 6)
                }
                .buttonStyle(.borderedProminent).tint(.orange)
                .padding()
            }
        }
        .onAppear { store.reload(); uploader.startBrowsing() }
        .confirmationDialog("What are you scanning?", isPresented: $pickingMode, titleVisibility: .visible) {
            ForEach(ScanMode.allCases) { mode in
                Button(mode.title) { scanning = mode }
            }
        }
        .fullScreenCover(item: $scanning) { mode in
            CaptureView(mode: mode) { store.reload() }
        }
        .sheet(isPresented: $showSettings) { SettingsView(uploader: uploader) }
        .alert("Rename scan", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField("Name", text: $newName)
            Button("Save") { if let r = renaming, !newName.isEmpty { store.rename(r, to: newName) } }
            Button("Cancel", role: .cancel) {}
        }
    }
}

private struct CaptureRow: View {
    let capture: CaptureSummary
    @ObservedObject var uploader: Uploader
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
                    Text(error).font(.caption).foregroundStyle(.red)
                }
            }
            Spacer()
            if let p = uploader.progress[capture.id] {
                ProgressView(value: p).progressViewStyle(.circular)
                Text("\(Int(p * 100))%").font(.caption).monospacedDigit().frame(width: 38, alignment: .trailing)
            } else if capture.uploaded {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green).accessibilityLabel("Sent")
            } else {
                Button("Send", action: send).buttonStyle(.bordered)
            }
        }
        .padding(.vertical, 2)
    }
}

private struct SettingsView: View {
    @ObservedObject var uploader: Uploader
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    if uploader.discovered.isEmpty {
                        Text("Searching the Wi-Fi network…").foregroundStyle(.secondary)
                    }
                    ForEach(uploader.discovered, id: \.self) { address in
                        Label(address, systemImage: "desktopcomputer")
                    }
                } header: {
                    Text("Computers found")
                } footer: {
                    Text("Open Calco on the computer. Phone and computer must be on the same Wi-Fi.")
                }
                Section {
                    TextField("192.168.1.20:7878", text: $uploader.manualAddress)
                        .keyboardType(.numbersAndPunctuation)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                } header: {
                    Text("Address (optional)")
                } footer: {
                    Text("Only needed when the computer is not found automatically. The address is shown at the bottom left of Calco on the computer.")
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
