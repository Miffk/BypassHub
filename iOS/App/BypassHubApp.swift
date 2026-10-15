import SwiftUI

@main
struct BypassHubApp: App {
    var body: some Scene {
        WindowGroup {
            ProbeView()
                .preferredColorScheme(.dark)
        }
    }
}
