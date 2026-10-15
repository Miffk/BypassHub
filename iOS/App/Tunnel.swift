import Bhcore
import Foundation
import NetworkExtension

/// Состояние ядра в расширении (bhcore.State).
struct CoreState: Decodable {
    var mode: String?
    var kind: String?
    var server: String?
    var running: Bool?
    var selected: String?
    var current: String?
    var currentName: String?
    var delays: [String: Int]?
    var tg: Bool?
    var tgStats: String?
    var error: String?
}

/// Управляет VPN-профилем BypassHub: на iOS VPN, обход и TG-прокси работают в одном расширении-туннеле.
/// Расширение получает RunRequest и запускает нужное; смена режима — командой run, без переподключения.
final class Tunnel: ObservableObject {
    static let shared = Tunnel()

    @Published private(set) var status: NEVPNStatus = .invalid
    @Published private(set) var core = CoreState()
    @Published var error: String?
    /// Что запрошено у расширения сейчас: режим (vpn | bypass | none) и TG-прокси.
    @Published private(set) var mode = "none"
    @Published private(set) var tg = false
    @Published private(set) var busy = false
    /// Последний журнал расширения — остаётся виден и после отключения.
    private(set) var savedLog = ""

    private var manager: NETunnelProviderManager?
    private var observer: NSObjectProtocol?
    private var timer: Timer?

    var connected: Bool { status == .connected }
    var connecting: Bool { status == .connecting || status == .reasserting || (busy && !connected) }

    /// Работает ли режим: vpn, bypass или tg.
    func isOn(_ m: String) -> Bool {
        guard connected else { return false }
        return m == "tg" ? tg : mode == m
    }

    func load() {
        NETunnelProviderManager.loadAllFromPreferences { managers, _ in
            DispatchQueue.main.async {
                self.manager = managers?.first
                if let m = self.manager { self.watch(m) }
                self.update()
            }
        }
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in self?.poll() }
    }

    private func watch(_ m: NETunnelProviderManager) {
        if let o = observer { NotificationCenter.default.removeObserver(o) }
        observer = NotificationCenter.default.addObserver(forName: .NEVPNStatusDidChange, object: m.connection,
                                                          queue: .main) { [weak self] _ in self?.update() }
    }

    private func update() {
        let s = manager?.connection.status ?? .invalid
        let was = status
        status = s
        if s == .connected && was != .connected { poll() }
        if s == .disconnected || s == .invalid {
            if was == .connecting || was == .connected || was == .disconnecting {
                busy = false
            }
            if !busy {
                mode = "none"
                tg = false
                core = CoreState()
            }
            if was == .connecting { fetchDisconnectError() }
        }
    }

    /// Причина отключения, если расширение не запустилось (iOS 16+).
    private func fetchDisconnectError() {
        guard #available(iOS 16.0, *), let c = manager?.connection else { return }
        c.fetchLastDisconnectError { err in
            guard let err = err else { return }
            DispatchQueue.main.async {
                if self.error == nil { self.error = err.localizedDescription }
            }
        }
    }

    private func poll() {
        guard connected else { return }
        send(["cmd": "state"]) { data in
            guard let data = data, let st = try? JSONDecoder().decode(CoreState.self, from: data) else { return }
            self.core = st
            if !self.busy {
                self.mode = st.mode?.isEmpty == false ? st.mode! : "none"
                self.tg = st.tg ?? false
            }
            if let e = st.error, !e.isEmpty {
                // ядро не запустилось — показываем ошибку, забираем журнал и выключаем туннель
                self.error = e
                self.fetchLog { _ in self.stop() }
            }
        }
    }

    // ---------------------------------------------------------------- управление
    /// Включить или выключить режим vpn | bypass | tg.
    func toggle(_ m: String) {
        error = nil
        var newMode = connected ? mode : "none"
        var newTG = connected ? tg : false
        if m == "tg" {
            newTG.toggle()
        } else {
            newMode = (connected && mode == m) ? "none" : m
        }
        apply(mode: newMode, tg: newTG)
    }

    /// Перезапустить с новыми настройками, если работает (сменили сервер, способ обхода, порт…).
    func restartIfRunning() {
        if connected { apply(mode: mode, tg: tg) }
    }

    func apply(mode newMode: String, tg newTG: Bool) {
        if newMode == "none" && !newTG {
            stop()
            return
        }
        let request = Store.shared.request(mode: newMode, tg: newTG)
        mode = newMode
        tg = newTG
        busy = true
        if connected {
            send(["cmd": "run", "request": request]) { data in
                self.busy = false
                let reply = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
                if reply["ok"] as? Bool != true {
                    self.error = reply["error"] as? String ?? "Расширение не ответило"
                    self.fetchLog { _ in self.stop() }
                }
                self.poll()
            }
            return
        }
        start(request)
    }

    private func start(_ request: String) {
        withManager { m in
            guard let m = m else { return }
            do {
                try m.connection.startVPNTunnel(options: ["request": request as NSString])
            } catch {
                self.fail("Запуск: \(error.localizedDescription)")
            }
            self.busy = false
        }
    }

    func stop() {
        busy = false
        manager?.connection.stopVPNTunnel()
    }

    private func fail(_ message: String) {
        busy = false
        mode = "none"
        tg = false
        error = message
    }

    /// Профиль VPN: создаёт при первом включении (iOS спросит разрешение) и включает.
    private func withManager(_ done: @escaping (NETunnelProviderManager?) -> Void) {
        guard let tunnelID = Self.tunnelBundleID() else {
            fail("В приложении нет расширения-туннеля. Подпишите заново и не удаляйте плагины (расширения).")
            done(nil)
            return
        }
        NETunnelProviderManager.loadAllFromPreferences { managers, error in
            DispatchQueue.main.async {
                if let error = error {
                    self.fail("VPN-профили недоступны: \(error.localizedDescription)")
                    done(nil)
                    return
                }
                let m = managers?.first ?? NETunnelProviderManager()
                let proto = (m.protocolConfiguration as? NETunnelProviderProtocol) ?? NETunnelProviderProtocol()
                proto.providerBundleIdentifier = tunnelID
                proto.serverAddress = "BypassHub"
                proto.disconnectOnSleep = false
                m.protocolConfiguration = proto
                m.localizedDescription = "BypassHub"
                m.isEnabled = true
                m.saveToPreferences { error in
                    DispatchQueue.main.async {
                        if let error = error {
                            self.fail("iOS не дала создать VPN-профиль: \(error.localizedDescription)")
                            done(nil)
                            return
                        }
                        m.loadFromPreferences { _ in
                            DispatchQueue.main.async {
                                self.manager = m
                                self.watch(m)
                                self.update()
                                done(m)
                            }
                        }
                    }
                }
            }
        }
    }

    /// Идентификатор расширения читаем из самого приложения: программа подписи может его изменить.
    private static func tunnelBundleID() -> String? {
        guard let dir = Bundle.main.builtInPlugInsURL,
              let items = try? FileManager.default.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)
        else { return nil }
        for url in items where url.pathExtension == "appex" {
            if let b = Bundle(url: url), let id = b.bundleIdentifier { return id }
        }
        return nil
    }

    // ---------------------------------------------------------------- команды расширению
    func send(_ msg: [String: Any], reply: @escaping (Data?) -> Void) {
        guard let session = manager?.connection as? NETunnelProviderSession,
              let data = try? JSONSerialization.data(withJSONObject: msg) else {
            reply(nil)
            return
        }
        do {
            try session.sendProviderMessage(data) { r in DispatchQueue.main.async { reply(r) } }
        } catch {
            reply(nil)
        }
    }

    func selectServer(_ id: String) {
        Store.shared.active = id
        guard isOn("vpn") else { return }
        send(["cmd": "select", "id": id]) { data in
            let ok = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }?["ok"] as? Bool
            // AmneziaWG или сервер не из группы — переключаем перезапуском
            if ok != true { self.restartIfRunning() } else { self.poll() }
        }
    }

    /// Задержки серверов: через работающий VPN (как в sing-box) или TCP-пинг напрямую.
    func delays(_ done: @escaping ([String: Int]) -> Void) {
        if isOn("vpn") && core.kind == "vless" {
            send(["cmd": "delays"]) { data in
                done(data.flatMap { try? JSONDecoder().decode([String: Int].self, from: $0) } ?? [:])
            }
            return
        }
        let profiles = Store.shared.profiles
        DispatchQueue.global().async {
            let json = String(decoding: (try? JSONEncoder().encode(profiles)) ?? Data(), as: UTF8.self)
            let result = Store.decode([String: Int].self, BhcorePingAll(json)) ?? [:]
            DispatchQueue.main.async { done(result) }
        }
    }

    func fetchLog(_ done: @escaping (String) -> Void) {
        guard connected else {
            done(savedLog)
            return
        }
        send(["cmd": "log"]) { data in
            let o = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] } ?? [:]
            if let log = o["log"] as? String, !log.isEmpty { self.savedLog = log }
            done(self.savedLog)
        }
    }
}
