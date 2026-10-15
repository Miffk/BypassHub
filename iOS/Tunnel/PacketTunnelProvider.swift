import Bhcore
import Foundation
import Network
import NetworkExtension

/// VPN-расширение BypassHub: поднимает ядро (sing-box, AmneziaWG, обход, TG-прокси) по настройкам из приложения.
/// Настройки — тот же RunRequest, что понимает ядро (bhcore/app.go): при запуске из приложения приходят в options,
/// при смене режима — командой run, а при запуске из «Настроек» iOS берутся последние сохранённые.
class PacketTunnelProvider: NEPacketTunnelProvider {
    private let log = TunnelLog()
    private lazy var platform = TunnelPlatform(provider: self, log: log)
    private let queue = DispatchQueue(label: "bypasshub.core")
    private let pathMonitor = NWPathMonitor()
    private var lastError = ""

    private var caches: URL { FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0] }
    private var workDir: URL { caches.appendingPathComponent("core", isDirectory: true) }
    private var lastRequestFile: URL { caches.appendingPathComponent("last-request.json") }

    override func startTunnel(options: [String: NSObject]?, completionHandler: @escaping (Error?) -> Void) {
        let fromApp = options?["request"] as? String
        let saved = try? String(contentsOf: lastRequestFile, encoding: .utf8)
        let configured = (protocolConfiguration as? NETunnelProviderProtocol)?.providerConfiguration?["request"] as? String
        guard let request = fromApp ?? saved ?? configured else {
            completionHandler(TunnelError("Нет настроек — включите режим в приложении BypassHub"))
            return
        }
        let temp = caches.appendingPathComponent("tmp", isDirectory: true)
        pathMonitor.pathUpdateHandler = { [weak self] path in self?.queue.async { self?.reportPath(path) } }
        pathMonitor.start(queue: DispatchQueue(label: "bypasshub.path"))

        queue.async {
            BhcoreSetMemoryLimit(40)
            var err: NSError?
            _ = BhcoreSetup(self.workDir.path, temp.path, &err)
            if let error = self.runCore(request) {
                if fromApp == nil {
                    completionHandler(error)
                    return
                }
                // запуск из приложения: туннель остаётся поднятым, чтобы приложение забрало ошибку и журнал
                self.lastError = error.localizedDescription
                self.setSettings(TunnelPlatform.idleSettings())
            }
            completionHandler(nil)
        }
    }

    override func stopTunnel(with reason: NEProviderStopReason, completionHandler: @escaping () -> Void) {
        pathMonitor.cancel()
        queue.async {
            BhcoreStopAll()
            completionHandler()
        }
    }

    /// Запускает режим из RunRequest (вызывать на queue). nil — получилось.
    private func runCore(_ request: String) -> Error? {
        var req = (try? JSONSerialization.jsonObject(with: Data(request.utf8))) as? [String: Any] ?? [:]
        // папка для баз и списков — расширения (приложение не знает, где его контейнер)
        req["dataDir"] = workDir.path
        let mode = req["mode"] as? String ?? "none"
        guard let data = try? JSONSerialization.data(withJSONObject: req),
              let json = String(data: data, encoding: .utf8) else {
            return TunnelError("Неверные настройки")
        }
        lastError = ""
        if mode != "vpn" && mode != "bypass" {
            // только TG-прокси: туннель без маршрутов, чтобы расширение работало в фоне
            BhcoreStopAll()
            if let e = setSettings(TunnelPlatform.idleSettings()) { return e }
        }
        var err: NSError?
        if !BhcoreRun(json, platform, &err) {
            let e = err ?? NSError(domain: "BypassHub", code: 1,
                                  userInfo: [NSLocalizedDescriptionKey: "Ядро не запустилось"])
            log.add("Ошибка: \(e.localizedDescription)")
            if mode == "vpn" || mode == "bypass" {
                // маршруты остались от прежнего режима, а читать туннель некому — убираем их
                setSettings(TunnelPlatform.idleSettings())
            }
            return TunnelError(e.localizedDescription)
        }
        try? request.write(to: lastRequestFile, atomically: true, encoding: .utf8)
        reportPath(pathMonitor.currentPath)
        return nil
    }

    @discardableResult
    private func setSettings(_ s: NEPacketTunnelNetworkSettings) -> Error? {
        let semaphore = DispatchSemaphore(value: 0)
        var setErr: Error?
        setTunnelNetworkSettings(s) { e in
            setErr = e
            semaphore.signal()
        }
        semaphore.wait()
        return setErr
    }

    /// Основная сеть устройства (Wi-Fi, сотовая) — ядру для выбора маршрута.
    private func reportPath(_ path: NWPath) {
        guard path.status == .satisfied,
              let iface = path.availableInterfaces.first(where: { $0.type != .other && $0.type != .loopback })
        else { return }
        BhcoreUpdateDefaultInterface(iface.name, Int32(iface.index), path.isExpensive)
    }

    /// Команды из приложения: run, state, select, delays, log.
    override func handleAppMessage(_ messageData: Data, completionHandler: ((Data?) -> Void)?) {
        let msg = (try? JSONSerialization.jsonObject(with: messageData)) as? [String: Any] ?? [:]
        let cmd = msg["cmd"] as? String ?? "state"
        let reply: (String) -> Void = { completionHandler?($0.data(using: .utf8)) }
        switch cmd {
        case "run":
            queue.async {
                if let e = self.runCore(msg["request"] as? String ?? "{}") {
                    self.lastError = e.localizedDescription
                    reply(Self.json(["ok": false, "error": e.localizedDescription]))
                } else {
                    reply(Self.json(["ok": true]))
                }
            }
        case "log":
            reply(Self.json(["log": log.text()]))
        default:
            DispatchQueue.global().async {
                switch cmd {
                case "select":
                    reply(Self.json(["ok": BhcoreSelectServer(msg["id"] as? String ?? "")]))
                case "delays":
                    reply(BhcoreTestDelays())
                default:
                    var state = (try? JSONSerialization.jsonObject(with: Data(BhcoreState().utf8))) as? [String: Any] ?? [:]
                    state["error"] = self.lastError
                    reply(Self.json(state))
                }
            }
        }
    }

    private static func json(_ v: [String: Any]) -> String {
        String(data: (try? JSONSerialization.data(withJSONObject: v)) ?? Data(), encoding: .utf8) ?? "{}"
    }
}

struct TunnelError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

/// Журнал расширения (последние строки) — приложение забирает его командой log.
final class TunnelLog {
    private var lines: [String] = []
    private let lock = NSLock()
    private let formatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()

    func add(_ message: String) {
        let text = message.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty { return }
        // строки sing-box уже со временем (+0300 2026-…), свои — помечаем
        let line = text.first?.isNumber == true || text.hasPrefix("+") ? text
            : "\(formatter.string(from: Date()))  \(text)"
        lock.lock()
        lines.append(line)
        if lines.count > 400 { lines.removeFirst(lines.count - 400) }
        lock.unlock()
    }

    func text() -> String {
        lock.lock()
        defer { lock.unlock() }
        return lines.joined(separator: "\n")
    }
}

/// Платформа для ядра: настраивает туннель iOS по запросу sing-box.
final class TunnelPlatform: NSObject, BhcorePlatformProtocol {
    private weak var provider: NEPacketTunnelProvider?
    private let log: TunnelLog

    init(provider: NEPacketTunnelProvider, log: TunnelLog) {
        self.provider = provider
        self.log = log
    }

    /// Туннель без маршрутов в интернет (только TG-прокси или ошибка запуска).
    static func idleSettings() -> NEPacketTunnelNetworkSettings {
        let s = NEPacketTunnelNetworkSettings(tunnelRemoteAddress: "127.0.0.1")
        let v4 = NEIPv4Settings(addresses: ["172.19.0.1"], subnetMasks: ["255.255.255.252"])
        v4.includedRoutes = [NEIPv4Route(destinationAddress: "198.18.0.0", subnetMask: "255.254.0.0")]
        s.ipv4Settings = v4
        s.mtu = 1500
        return s
    }

    private static func cidr(_ s: String) -> (String, Int) {
        let parts = s.split(separator: "/")
        return (String(parts[0]), parts.count > 1 ? Int(parts[1]) ?? 32 : 32)
    }

    private static func mask(_ bits: Int) -> String {
        let m: UInt32 = bits <= 0 ? 0 : ~UInt32(0) << UInt32(32 - min(bits, 32))
        return "\(m >> 24 & 255).\(m >> 16 & 255).\(m >> 8 & 255).\(m & 255)"
    }

    func openTun(_ optionsJSON: String?, ret0_: UnsafeMutablePointer<Int32>?) throws {
        guard let provider = provider else { throw TunnelError("расширение остановлено") }
        let o = (try? JSONSerialization.jsonObject(with: Data((optionsJSON ?? "{}").utf8))) as? [String: Any] ?? [:]
        let list = { (key: String) -> [String] in (o[key] as? [String]) ?? [] }
        let s = NEPacketTunnelNetworkSettings(tunnelRemoteAddress: "127.0.0.1")
        s.mtu = NSNumber(value: (o["mtu"] as? Int) ?? 4064)
        let a4 = list("inet4Address").map(Self.cidr)
        if !a4.isEmpty {
            let v4 = NEIPv4Settings(addresses: a4.map { $0.0 }, subnetMasks: a4.map { Self.mask($0.1) })
            let r4 = list("inet4Route").map(Self.cidr)
            v4.includedRoutes = r4.isEmpty ? [NEIPv4Route.default()]
                : r4.map { NEIPv4Route(destinationAddress: $0.0, subnetMask: Self.mask($0.1)) }
            s.ipv4Settings = v4
        }
        let a6 = list("inet6Address").map(Self.cidr)
        if !a6.isEmpty {
            let v6 = NEIPv6Settings(addresses: a6.map { $0.0 }, networkPrefixLengths: a6.map { NSNumber(value: $0.1) })
            let r6 = list("inet6Route").map(Self.cidr)
            v6.includedRoutes = r6.isEmpty ? [NEIPv6Route.default()]
                : r6.map { NEIPv6Route(destinationAddress: $0.0, networkPrefixLength: NSNumber(value: $0.1)) }
            s.ipv6Settings = v6
        }
        let dns = list("dnsServers")
        if !dns.isEmpty {
            let d = NEDNSSettings(servers: dns)
            d.matchDomains = [""]
            s.dnsSettings = d
        }
        let semaphore = DispatchSemaphore(value: 0)
        var setErr: Error?
        provider.setTunnelNetworkSettings(s) { e in
            setErr = e
            semaphore.signal()
        }
        semaphore.wait()
        if let setErr = setErr { throw setErr }
        // fd utun ядро найдёт само (bhcore/tun_darwin.go)
        ret0_?.pointee = -1
    }

    func protect(_ fd: Int32) -> Bool { true }

    func interfacesJSON() -> String { "[]" }

    func writeLog(_ message: String?) {
        log.add(message ?? "")
    }
}
