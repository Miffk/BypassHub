import SwiftUI
import Bhcore

struct TelegramView: View {
    @EnvironmentObject var store: Store
    @EnvironmentObject var tunnel: Tunnel
    @State private var port = ""
    @State private var askSecret = false

    var body: some View {
        let on = tunnel.isOn("tg")
        ScrollView {
            VStack(spacing: 14) {
                Card("TG WS Proxy") {
                    Hint("Локальный прокси для Telegram на этом iPhone. Он пускает Telegram через WebSocket — так же, " +
                         "как работает web.telegram.org, — и провайдер его не замедляет. Работает и вместе с VPN или " +
                         "обходом. Основан на tg-ws-proxy (Flowseal).")
                    Hint("На iPhone прокси работает внутри VPN-подключения BypassHub, поэтому, пока он включён, " +
                         "в строке состояния виден значок VPN — без VPN-сервера трафик идёт как обычно.")
                    Text(on ? "Работает · 127.0.0.1:\(String(store.tgPort))" : "Выключен")
                        .font(.subheadline.bold()).foregroundColor(on ? Theme.green : Theme.muted)
                        .padding(.vertical, 4)
                    HStack(spacing: 8) {
                        BHButton(on ? "Выключить" : "Включить") { tunnel.toggle("tg") }
                        BHButton("Открыть в Telegram", primary: false) { openTelegram(store: store, tunnel: tunnel) }
                    }
                }

                Card("Настройки") {
                    Toggle("Запасной путь через Cloudflare", isOn: Binding(get: { store.tgCF }, set: {
                        store.tgCF = $0
                        restart()
                    }))
                    .tint(Theme.accent)
                    Hint("Если WebSocket Telegram недоступен, прокси подключится через домены tg-ws-proxy на Cloudflare, " +
                         "а потом — напрямую.")
                    Text("Порт").font(.subheadline.bold()).foregroundColor(Theme.text).padding(.top, 8)
                    TextField("1443", text: $port)
                        .keyboardType(.numberPad)
                        .padding(10)
                        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.surface2))
                    HStack(spacing: 8) {
                        BHButton("Сохранить порт", primary: false) { savePort() }
                        BHButton("Новый секрет", primary: false) { askSecret = true }
                    }
                    .padding(.top, 4)
                }
            }
            .frame(maxWidth: 640)
            .padding(16)
            .frame(maxWidth: .infinity)
        }
        .navigationTitle("Telegram")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { port = String(store.tgPort) }
        .confirmationDialog("Создать новый секрет? Старый прокси в Telegram перестанет работать — его нужно будет " +
                            "добавить заново.", isPresented: $askSecret, titleVisibility: .visible) {
            Button("Создать") {
                store.tgSecret = BhcoreNewTGSecret()
                restart()
            }
        }
    }

    private func savePort() {
        guard let p = Int(port.trimmingCharacters(in: .whitespaces)), (1024...65535).contains(p) else {
            Toast.shared.show("Порт — число от 1024 до 65535")
            return
        }
        if p != store.tgPort {
            store.tgPort = p
            restart()
            Toast.shared.show("Сохранено. Добавьте прокси в Telegram заново — кнопкой «Открыть в Telegram»")
        }
    }

    private func restart() {
        if tunnel.isOn("tg") { tunnel.restartIfRunning() }
    }
}
