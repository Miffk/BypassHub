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
    /// Что расширению велели запустить последним (режим и TG) и идёт ли сейчас перезапуск.
    var reqMode: String?
    var reqTG: Bool?
    var reqBypass: Bool?
    var busy: Bool?
}

/// Управляет VPN-профилем BypassHub: на iOS VPN, обход и TG-прокси работают в одном расширении-туннеле.
/// Расширение получает RunRequest и запускает нужное; смена режима — командой run, без переподключения.
final class Tunnel: ObservableObject {
    static let shared = Tunnel()

    @Published private(set) var status: NEVPNStatus = .invalid
    @Published private(set) var core = CoreState()
    @Published var error: String?
    /// Что должно работать: VPN, обход и TG-прокси — в любом сочетании. Источник правды — приложение;
    /// расширение догоняет его командой run (или запуском туннеля с этими настройками).
    @Published private(set) var vpn = false
    @Published private(set) var bypass = false
    @Published private(set) var tg = false
    /// Туннель запускается (до состояния «подключено»).
    @Published private var starting = false
    /// Туннель остановлен, чтобы сразу запустить его с новым набором режимов.
    @Published private var restarting = false
    /// Набор поменялся, пока туннель запускался, — перезапустить, когда подключится.
    private var dirty = false
    /// Растёт при каждом изменении — ответы на старые запросы состояния не должны затирать новое.
    private var generation = 0
    /// Последний журнал расширения — остаётся виден и после отключения.
    private(set) var savedLog = ""
    /// Отвечает ли расширение на сообщения (nil — ещё не проверяли). Для диагностики в журнале.
    @Published private(set) var messagesOK: Bool?

    private var manager: NETunnelProviderManager?
    private var observer: NSObjectProtocol?
    private var timer: Timer?

    var connected: Bool { status == .connected }
    var connecting: Bool { status == .connecting || status == .reasserting || starting }
    var busy: Bool { starting || restarting || dirty }
    /// Туннель поднят или поднимается — тогда новый режим добавляется к уже включённым.
    private var active: Bool { connected || connecting }

    /// Работает ли режим: vpn, bypass или tg.
    func isOn(_ m: String) -> Bool {
        guard connected else { return false }
        return want(m)
    }

    /// Включён ли режим в приложении (туннель может ещё подключаться).
    func want(_ m: String) -> Bool {
        switch m {
        case "vpn": return vpn
        case "bypass": return bypass
        default: return tg
        }
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
        if s == .connected && was != .connected {
            starting = false
            if dirty {
                dirty = false
                restart()
            } else {
                poll()
            }
        }
        if (s == .disconnected || s == .invalid) && restarting {
            // старое расширение остановлено — запускаем заново со всеми включёнными режимами
            restarting = false
            start(request)
            return
        }
        if s == .disconnected || s == .invalid {
            if was == .connecting || was == .connected || was == .reasserting || was == .disconnecting {
                starting = false
            }
            if !starting {
                if was == .connecting { fetchDisconnectError() }
                vpn = false
                bypass = false
                tg = false
                dirty = false
                core = CoreState()
            }
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
        let gen = generation
        send(["cmd": "state"]) { data in
            guard let data = data, let st = try? JSONDecoder().decode(CoreState.self, from: data) else { return }
            self.core = st
            // приложение могло быть закрыто, пока туннель работал, — берём режим у расширения,
            // но только если с момента запроса ничего не меняли и ядро не перезапускается
            if gen == self.generation && !self.busy && st.busy != true, let m = st.reqMode {
                self.vpn = m == "vpn"
                self.bypass = m == "bypass" || (m == "vpn" && st.reqBypass == true)
                self.tg = st.reqTG ?? false
            }
            if let e = st.error, !e.isEmpty, !self.busy {
                // ядро не запустилось — показываем ошибку, забираем журнал и выключаем туннель
                self.error = e
                self.fetchLog { _ in self.stop() }
            }
        }
    }

    // ---------------------------------------------------------------- управление
    /// Включить или выключить режим vpn | bypass | tg. Остальное, что уже включено, остаётся.
    func toggle(_ m: String) {
        error = nil
        var v = active && vpn, b = active && bypass, t = active && tg
        switch m {
        case "vpn": v.toggle()
        case "bypass": b.toggle()
        default: t.toggle()
        }
        apply(vpn: v, bypass: b, tg: t)
    }

    /// Перезапустить с новыми настройками, если работает (сменили сервер, способ обхода, порт…).
    func restartIfRunning() {
        if active { apply(vpn: vpn, bypass: bypass, tg: tg) }
    }

    func apply(vpn v: Bool, bypass b: Bool, tg t: Bool) {
        generation += 1
        vpn = v
        bypass = b
        tg = t
        if !v && !b && !t {
            stop()
            return
        }
        if connected {
            restart()
        } else if active {
            // туннель ещё поднимается — перезапустим, когда подключится
            dirty = true
        } else {
            start(request)
        }
    }

    /// Перезапуск туннеля с новым набором режимов. Перезапуск ядра внутри работающего расширения
    /// (команда run) на iOS выходил за предел памяти расширения — новое расширение начинает с чистого листа.
    private func restart() {
        restarting = true
        manager?.connection.stopVPNTunnel()
    }

    /// RunRequest для расширения из того, что включено.
    private var request: String {
        Store.shared.request(mode: vpn ? "vpn" : bypass ? "bypass" : "none", bypass: vpn && bypass, tg: tg)
    }

    private func start(_ request: String) {
        starting = true
        let gen = generation
        DispatchQueue.main.asyncAfter(deadline: .now() + 40) {
            // iOS так и не начала подключение — не держим «Подключение…» вечно
            if gen == self.generation && self.starting && self.status != .connecting && !self.connected {
                self.fail("Туннель не запустился")
            }
        }
        withManager { m in
            guard let m = m else { return }
            do {
                try m.connection.startVPNTunnel(options: ["request": request as NSString])
            } catch {
                self.fail("Запуск: \(error.localizedDescription)")
            }
        }
    }

    func stop() {
        starting = false
        restarting = false
        dirty = false
        generation += 1
        manager?.connection.stopVPNTunnel()
    }

    private func fail(_ message: String) {
        starting = false
        restarting = false
        dirty = false
        vpn = false
        bypass = false
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
            try session.sendProviderMessage(data) { r in
                DispatchQueue.main.async {
                    if self.connected { self.messagesOK = r != nil }
                    reply(r)
                }
            }
        } catch {
            if connected { messagesOK = false }
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
        // журнал из общей папки — работает, даже если сообщения до расширения не доходят
        if SharedGroup.directory != nil, let t = try? String(contentsOf: SharedGroup.logFile, encoding: .utf8),
           !t.isEmpty {
            savedLog = t
            done(t)
            return
        }
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
