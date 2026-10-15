import Foundation
import NetworkExtension

/// Проверка: можно ли с этим сертификатом создать VPN-профиль и поднять туннель BypassHub.
final class Probe: ObservableObject {
    @Published var lines: [String] = []
    @Published var verdict: String = ""
    @Published var running = false
    private var manager: NETunnelProviderManager?
    private var observer: NSObjectProtocol?

    private func add(_ s: String) {
        DispatchQueue.main.async { self.lines.append(s) }
    }

    private func finish(_ s: String) {
        DispatchQueue.main.async {
            self.verdict = s
            self.running = false
        }
    }

    /// Идентификатор расширения-туннеля читаем из самого приложения: программа подписи может его изменить.
    private func tunnelBundleID() -> String? {
        guard let dir = Bundle.main.builtInPlugInsURL,
              let items = try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)
        else { return nil }
        for url in items where url.pathExtension == "appex" {
            if let b = Bundle(url: url), let id = b.bundleIdentifier { return id }
        }
        return nil
    }

    func run() {
        lines = []
        verdict = ""
        running = true
        add("Приложение: \(Bundle.main.bundleIdentifier ?? "?")")
        guard let tunnelID = tunnelBundleID() else {
            add("❌ В приложении нет расширения-туннеля — программа подписи удалила плагины.")
            finish("Включите в ESign/Sideloadly сохранение плагинов (расширений) и подпишите заново.")
            return
        }
        add("Расширение: \(tunnelID)")
        NETunnelProviderManager.loadAllFromPreferences { managers, error in
            if let error = error {
                self.add("❌ Чтение VPN-профилей: \(error.localizedDescription)")
                self.finish("Нет доступа к VPN-профилям — в подписи нет прав Network Extension.")
                return
            }
            let m = managers?.first ?? NETunnelProviderManager()
            let proto = NETunnelProviderProtocol()
            proto.providerBundleIdentifier = tunnelID
            proto.serverAddress = "BypassHub"
            m.protocolConfiguration = proto
            m.localizedDescription = "BypassHub (проверка)"
            m.isEnabled = true
            m.saveToPreferences { error in
                if let error = error {
                    self.add("❌ Создание VPN-профиля: \(error.localizedDescription)")
                    self.finish("iOS не дала создать VPN-профиль: подпись без прав packet-tunnel-provider.")
                    return
                }
                self.add("✅ VPN-профиль создан")
                m.loadFromPreferences { _ in
                    self.manager = m
                    self.watch(m)
                    do {
                        try m.connection.startVPNTunnel()
                        self.add("Запуск туннеля…")
                    } catch {
                        self.add("❌ Запуск: \(error.localizedDescription)")
                        self.finish("Туннель не запустился.")
                    }
                }
            }
        }
    }

    private func watch(_ m: NETunnelProviderManager) {
        if let o = observer { NotificationCenter.default.removeObserver(o) }
        observer = NotificationCenter.default.addObserver(forName: .NEVPNStatusDidChange,
                                                          object: m.connection, queue: .main) { [weak self] _ in
            guard let self = self else { return }
            switch m.connection.status {
            case .connecting: self.add("…подключение")
            case .connected:
                self.add("✅ Туннель поднят")
                self.ask(m)
            case .disconnected:
                if self.running {
                    self.add("❌ Туннель отключился")
                    self.finish("Расширение не запустилось — скорее всего, iOS не приняла его подпись.")
                }
            default: break
            }
        }
    }

    private func ask(_ m: NETunnelProviderManager) {
        guard let session = m.connection as? NETunnelProviderSession else { return }
        do {
            try session.sendProviderMessage("ping".data(using: .utf8)!) { data in
                let reply = data.flatMap { String(data: $0, encoding: .utf8) } ?? "нет ответа"
                self.add("Ответ: \(reply)")
                self.finish("🎉 VPN на iOS работает с вашим сертификатом — можно делать полное приложение.")
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) { m.connection.stopVPNTunnel() }
            }
        } catch {
            self.add("❌ Связь с расширением: \(error.localizedDescription)")
            self.finish("Туннель поднялся, но расширение не отвечает.")
        }
    }
}
