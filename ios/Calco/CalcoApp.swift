import SwiftUI

@main
struct CalcoApp: App {
    init() {
        CaptureStore.recoverInterrupted()
    }

    var body: some Scene {
        WindowGroup {
            LibraryView()
        }
    }
}
