import SwiftUI

@main
struct BetBuilderApp: App {
    @StateObject private var subscription = Subscription()
    @AppStorage("ageConfirmed") private var ageConfirmed = false

    var body: some Scene {
        WindowGroup {
            Group {
                if ageConfirmed || ProcessInfo.processInfo.arguments.contains("-demo") {
                    ContentView()
                } else {
                    AgeGate { ageConfirmed = true }
                }
            }
            .environmentObject(subscription)
            .preferredColorScheme(.dark)
            .tint(Theme.blue)
            .task { await subscription.refresh() }
        }
    }
}

struct ContentView: View {
    // Launch with "-startTab 2" to open on a tab (the build machine's screenshots do).
    @State private var tab = UserDefaults.standard.integer(forKey: "startTab")

    var body: some View {
        TabView(selection: $tab) {
            PicksView()
                .tabItem { Label("Picks", systemImage: "list.number") }.tag(0)
            MultiView()
                .tabItem { Label("Multi-Lays", systemImage: "square.stack.3d.up") }.tag(1)
            RecordView()
                .tabItem { Label("Record", systemImage: "chart.bar") }.tag(2)
            SettingsView()
                .tabItem { Label("More", systemImage: "ellipsis.circle") }.tag(3)
        }
    }
}

enum Theme {
    static let background = Color(red: 0.08, green: 0.10, blue: 0.14)
    static let card = Color(red: 0.11, green: 0.14, blue: 0.19)
    static let blue = Color(red: 0.25, green: 0.56, blue: 1.0)
    static let green = Color(red: 0.20, green: 0.78, blue: 0.45)
    static let amber = Color(red: 0.96, green: 0.70, blue: 0.20)
    static let red = Color(red: 0.94, green: 0.33, blue: 0.33)

    /// Chances are colored by how likely they are, like the desktop app.
    static func chance(_ p: Double) -> Color { p >= 0.72 ? green : p >= 0.62 ? blue : amber }

    static func percent(_ p: Double) -> String { "\(Int((p * 100).rounded()))%" }
    static func dollars(_ x: Double) -> String { String(format: "$%.2f", x) }
}
