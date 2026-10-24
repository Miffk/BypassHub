import SwiftUI

@main
struct BypassHubApp: App {
    @StateObject private var store = Store.shared
    @StateObject private var tunnel = Tunnel.shared
    @Environment(\.scenePhase) private var phase

    init() {
        let tab = UITabBarAppearance()
        tab.configureWithOpaqueBackground()
        tab.backgroundColor = UIColor(red: 0.04, green: 0.06, blue: 0.075, alpha: 1)
        UITabBar.appearance().standardAppearance = tab
        UITabBar.appearance().scrollEdgeAppearance = tab
        let nav = UINavigationBarAppearance()
        nav.configureWithTransparentBackground()
        nav.titleTextAttributes = [.foregroundColor: UIColor.white]
        nav.largeTitleTextAttributes = [.foregroundColor: UIColor.white]
        UINavigationBar.appearance().standardAppearance = nav
        UINavigationBar.appearance().scrollEdgeAppearance = nav
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(store)
                .environmentObject(tunnel)
                .preferredColorScheme(.dark)
                .onAppear { tunnel.load() }
                .onChange(of: phase) { p in
                    if p == .active {
                        tunnel.load()
                        // подписки — сами, раз в несколько часов: новые серверы появляются без кнопки
                        if store.subsDue { Task { _ = await store.refreshSubscriptions() } }
                    }
                }
        }
    }
}

final class Router: ObservableObject {
    static let shared = Router()
    @Published var tab = "home"
    /// Ключ из ссылки vless:// или vpn:// — спросить, добавить ли сервер.
    @Published var pendingKey: String?
}

struct RootView: View {
    @EnvironmentObject var store: Store
    @EnvironmentObject var tunnel: Tunnel
    @StateObject private var router = Router.shared
    @StateObject private var keyboard = KeyboardObserver()

    var body: some View {
        ZStack {
            TabView(selection: $router.tab) {
                screen(HomeView()).tabItem { Label("Главная", systemImage: "house.fill") }.tag("home")
                screen(ServersView()).tabItem { Label("VPN", systemImage: "server.rack") }.tag("vpn")
                screen(BypassView()).tabItem { Label("Обход", systemImage: "bolt.horizontal.fill") }.tag("bypass")
                screen(TelegramView()).tabItem { Label("Telegram", systemImage: "paperplane.fill") }.tag("tg")
                screen(SettingsView()).tabItem { Label("Настройки", systemImage: "gearshape.fill") }.tag("settings")
            }
            .accentColor(.white)
            ToastView()
            if keyboard.visible {
                VStack {
                    Spacer()
                    HStack {
                        Spacer()
                        Button { hideKeyboard() } label: {
                            Label("Скрыть клавиатуру", systemImage: "keyboard.chevron.compact.down")
                                .font(.subheadline.weight(.semibold))
                                .foregroundColor(.white)
                                .padding(.horizontal, 14).padding(.vertical, 9)
                                .background(Capsule().fill(Theme.accent))
                        }
                        .buttonStyle(.plain)
                    }
                    .padding(.horizontal, 12).padding(.bottom, 8)
                }
            }
        }
        .onAppear { TapToHideKeyboard.shared.install() }
        .onChange(of: keyboard.visible) { _ in TapToHideKeyboard.shared.install() }
        .environmentObject(router)
        .onOpenURL { url in
            let s = url.absoluteString
            if s.lowercased().hasPrefix("vless://") || s.lowercased().hasPrefix("vpn://") {
                router.pendingKey = s
            }
        }
        .alert("Добавить сервер?", isPresented: Binding(get: { router.pendingKey != nil },
                                                        set: { if !$0 { router.pendingKey = nil } })) {
            Button("Добавить") {
                if let k = router.pendingKey { addServers(k) }
                router.pendingKey = nil
            }
            Button("Отмена", role: .cancel) { router.pendingKey = nil }
        } message: {
            Text(String((router.pendingKey ?? "").prefix(80)))
        }
    }

    private func screen<V: View>(_ v: V) -> some View {
        NavigationView {
            v.background(GlowBackground(on: tunnel.connected))
        }
        .navigationViewStyle(.stack)
    }

    private func addServers(_ text: String) {
        Toast.shared.show("Добавляю…")
        Task {
            let (n, problems) = await store.add(text)
            Toast.shared.show(addResult(n, problems))
        }
    }
}

/// Текст итога добавления серверов.
func addResult(_ added: Int, _ problems: [String]) -> String {
    var parts: [String] = []
    if added > 0 {
        parts.append("Добавлено серверов: \(added)")
    } else if problems.isEmpty {
        parts.append("Эти серверы уже есть в списке")
    }
    if !problems.isEmpty {
        parts.append("Не добавлено: " + problems.prefix(3).joined(separator: "; "))
    }
    return parts.joined(separator: "\n")
}
