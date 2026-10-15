import Bhcore
import SwiftUI
import UIKit

let appVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
let releasesURL = URL(string: "https://github.com/Miffk/BypassHub/releases/latest")!
/// Поддержать проект — ссылка внизу главной (та же, что в README и у Windows-версии).
let supportURL = URL(string: "https://www.donationalerts.com/r/m1ffk")!

struct SettingsView: View {
    @State private var checking = false
    @State private var update: String?

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                Card("Журнал") {
                    Hint("Что делали VPN, обход и прокси — пригодится, если что-то не работает.")
                    NavigationLink(destination: LogView()) {
                        Text("Открыть журнал")
                            .font(.subheadline.weight(.semibold)).foregroundColor(.white)
                            .frame(maxWidth: .infinity).padding(.vertical, 12)
                            .background(RoundedRectangle(cornerRadius: 14).fill(Theme.surface2))
                    }
                    .buttonStyle(.plain)
                }

                Card("Обновления") {
                    Hint("BypassHub \(appVersion) · sing-box \(BhcoreVersion())")
                    if let u = update { Text(u).font(.footnote).foregroundColor(Theme.text) }
                    BHButton(checking ? "Проверка…" : "Проверить обновления", primary: false) { check() }
                    Hint("Новую версию скачайте из релизов (BypassHub-ios.ipa) и подпишите так же, как первую — " +
                         "настройки и серверы сохранятся.")
                }

                Card("О программе") {
                    Hint("BypassHub — свой VPN (VLESS, AmneziaWG) с раздельным туннелированием, обход блокировок без " +
                         "VPN и прокси для Telegram. Движки: sing-box (SagerNet, GPL-3.0), AmneziaWG (Amnezia, MIT), " +
                         "tg-ws-proxy (Flowseal). Исходный код: github.com/Miffk/BypassHub")
                    Hint("BypassHub бесплатный и без рекламы. Если программа вам помогает, можно поддержать её " +
                         "развитие — буду очень благодарен! ❤️")
                }
            }
            .frame(maxWidth: 640)
            .padding(16)
            .frame(maxWidth: .infinity)
        }
        .navigationTitle("Настройки")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func check() {
        checking = true
        update = nil
        var req = URLRequest(url: URL(string: "https://api.github.com/repos/Miffk/BypassHub/releases/latest")!,
                             timeoutInterval: 15)
        req.setValue("BypassHub", forHTTPHeaderField: "User-Agent")
        URLSession.shared.dataTask(with: req) { data, _, error in
            let o = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
            let tag = (o["tag_name"] as? String ?? "").replacingOccurrences(of: "bypasshub-v", with: "")
            DispatchQueue.main.async {
                checking = false
                if tag.isEmpty {
                    update = "Не удалось проверить: \(error?.localizedDescription ?? "нет ответа")"
                } else if tag.compare(appVersion, options: .numeric) == .orderedDescending {
                    update = "Есть новая версия \(tag)"
                    UIApplication.shared.open(releasesURL)
                } else {
                    update = "У вас последняя версия"
                }
            }
        }.resume()
    }
}

struct LogView: View {
    @EnvironmentObject var tunnel: Tunnel
    @State private var text = ""
    @State private var timer: Timer?

    var body: some View {
        ScrollView {
            VStack(spacing: 10) {
                HStack(spacing: 8) {
                    BHButton("Копировать", primary: false) {
                        UIPasteboard.general.string = text
                        Toast.shared.show("Журнал скопирован")
                    }
                    BHButton("Обновить", primary: false) { refresh() }
                }
                Text(text.isEmpty ? (tunnel.connected ? "Журнал пуст" : "Журнал ведёт VPN-расширение — включите любой режим на главной. Если оно перед этим остановилось с " +
                     "ошибкой, в начале журнала будет «прошлый запуск»")
                     : text)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundColor(Theme.text)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
                    .background(RoundedRectangle(cornerRadius: 12).fill(Theme.surface))
            }
            .padding(16)
        }
        .background(GlowBackground(on: tunnel.connected))
        .navigationTitle("Журнал")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear {
            refresh()
            timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { _ in refresh() }
        }
        .onDisappear { timer?.invalidate() }
    }

    private func refresh() {
        tunnel.fetchLog { text = $0 }
    }
}
