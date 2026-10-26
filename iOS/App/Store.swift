import Bhcore
import Foundation

/// Сервер в списке: kind = vless (data — ключ vless://) или awg (data — текст .conf). Разбирает ключи ядро (bhcore/keys.go).
struct Profile: Codable, Identifiable, Equatable {
    var id: String
    var kind: String
    var name: String
    var data: String
    var sub: String?
    var summary: String?

    var kindName: String { kind == "awg" ? "AmneziaWG" : "VLESS" }
}

let autoID = "auto"

enum BypassMethod: String, CaseIterable, Identifiable {
    // «Подставной пакет» (fake) не предлагаем: подмена данных после отправки есть только в ядре Linux (Android)
    case disorder, recordDisorder = "record-disorder", oob, tcp, record
    var id: String { rawValue }

    var title: String {
        switch self {
        case .disorder: return "Перемешивание пакетов"
        case .recordDisorder: return "TLS-записи и перемешивание"
        case .oob: return "Внеполосный байт"
        case .tcp: return "Разбиение TCP-пакетов"
        case .record: return "Разбиение TLS-записей"
        }
    }

    var hint: String {
        switch self {
        case .disorder: return "Как disorder в ByeDPI: первая часть уходит с TTL=1 и доходит до сайта только повтором. По умолчанию."
        case .recordDisorder: return "То же, но начало соединения ещё и делится на две TLS-записи."
        case .oob: return "Для мобильного интернета: в имя сайта вставляется лишний байт срочных данных — " +
            "сайт его отбрасывает, а провайдер видит искажённое имя. Как oob в ByeDPI."
        case .tcp: return "Начало соединения режется на несколько TCP-пакетов."
        case .record: return "Начало соединения делится на несколько TLS-записей."
        }
    }
}

/// Настройки и серверы (UserDefaults приложения). Расширению всё передаётся в запросе RunRequest.
final class Store: ObservableObject {
    static let shared = Store()
    private let d = UserDefaults.standard

    @Published var profiles: [Profile] { didSet { save(profiles, "profiles") } }
    @Published var subscriptions: [String] { didSet { d.set(subscriptions, forKey: "subscriptions") } }
    @Published var active: String { didSet { d.set(active, forKey: "active") } }
    @Published var ruDirect: Bool { didSet { d.set(ruDirect, forKey: "ruDirect") } }
    @Published var bypassMethod: String { didSet { d.set(bypassMethod, forKey: "bypassMethod") } }
    @Published var bypassAll: Bool { didSet { d.set(bypassAll, forKey: "bypassAll") } }
    @Published var bypassUser: String { didSet { d.set(bypassUser, forKey: "bypassUser") } }
    @Published var tgPort: Int { didSet { d.set(tgPort, forKey: "tgPort") } }
    @Published var tgCF: Bool { didSet { d.set(tgCF, forKey: "tgCF") } }
    @Published var homeMode: String { didSet { d.set(homeMode, forKey: "homeMode") } }
    @Published var tgSecret: String { didSet { d.set(tgSecret, forKey: "tgSecret") } }

    private init() {
        let d = UserDefaults.standard // свойство self.d до конца init недоступно
        d.register(defaults: ["ruDirect": true, "bypassMethod": BypassMethod.disorder.rawValue, "tgPort": 1443,
                              "tgCF": true, "homeMode": "vpn"])
        profiles = (d.data(forKey: "profiles").flatMap { try? JSONDecoder().decode([Profile].self, from: $0) }) ?? []
        subscriptions = d.stringArray(forKey: "subscriptions") ?? []
        active = d.string(forKey: "active") ?? ""
        ruDirect = d.bool(forKey: "ruDirect")
        bypassMethod = d.string(forKey: "bypassMethod") ?? BypassMethod.disorder.rawValue
        bypassAll = d.bool(forKey: "bypassAll")
        bypassUser = d.string(forKey: "bypassUser") ?? ""
        tgPort = d.integer(forKey: "tgPort")
        tgCF = d.bool(forKey: "tgCF")
        homeMode = d.string(forKey: "homeMode") ?? "vpn"
        let secret = d.string(forKey: "tgSecret") ?? ""
        tgSecret = secret.count == 32 ? secret : BhcoreNewTGSecret()
        d.set(tgSecret, forKey: "tgSecret")
    }

    private func save<T: Encodable>(_ v: T, _ key: String) {
        if let data = try? JSONEncoder().encode(v) { d.set(data, forKey: key) }
    }

    // ---------------------------------------------------------------- серверы
    var vless: [Profile] { profiles.filter { $0.kind == "vless" } }
    var isAuto: Bool { active == autoID && vless.count >= 2 }

    /// Выбранный сервер; nil — серверов нет или выбран режим «Авто».
    var activeProfile: Profile? {
        if isAuto { return nil }
        return profiles.first { $0.id == active } ?? vless.first ?? profiles.first
    }

    func name(of id: String) -> String { profiles.first { $0.id == id }?.name ?? id }

    func rename(_ id: String, _ name: String) {
        let n = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !n.isEmpty, let i = profiles.firstIndex(where: { $0.id == id }) else { return }
        profiles[i].name = n
    }

    func remove(_ id: String) { profiles.removeAll { $0.id == id } }

    func removeSubscription(_ url: String) {
        subscriptions.removeAll { $0 == url }
        profiles.removeAll { $0.sub == url }
    }

    /// Добавляет серверы из текста (ключи, конфиг, подписки). Возвращает (сколько добавлено, предупреждения).
    func add(_ text: String) async -> (Int, [String]) {
        let parsed = Self.decode(ParsedInput.self, BhcoreParseInput(text)) ?? ParsedInput()
        var found = parsed.profiles
        var problems = parsed.problems
        var subs = subscriptions
        for url in parsed.subscriptions {
            do {
                let (keys, extra) = try await Self.fetchSubscription(url, hwid: Self.hwid)
                if keys.isEmpty { problems.append("в подписке нет ключей VLESS") }
                found += keys
                problems += extra
                if !subs.contains(url) { subs.append(url) }
            } catch {
                problems.append("подписка не скачалась: \(error.localizedDescription)")
            }
        }
        let (newSubs, newFound, allProblems) = (subs, found, problems)
        let count: Int = await MainActor.run {
            self.subscriptions = newSubs
            var known = Set(self.profiles.map { $0.id })
            let added = newFound.filter { known.insert($0.id).inserted }
            self.profiles += added
            if let first = added.first, !self.profiles.contains(where: { $0.id == self.active }), self.active != autoID {
                self.active = first.id
            }
            return added.count
        }
        return (count, allProblems)
    }

    /// Подписки обновляются сами раз в несколько часов (как в Happ): VPN-сервисы добавляют серверы.
    static let subsRefreshInterval: TimeInterval = 6 * 3600

    var subsDue: Bool {
        !subscriptions.isEmpty && Date().timeIntervalSince1970 - d.double(forKey: "subsChecked") >= Self.subsRefreshInterval
    }

    /// Обновить серверы из подписок (ключи подписки заменяются новыми, имена сохраняются).
    func refreshSubscriptions() async -> (Int, [String]) {
        var problems: [String] = []
        var list: [Profile] = await MainActor.run { self.profiles }
        var total = 0
        let urls: [String] = await MainActor.run { self.subscriptions }
        let current: Profile? = await MainActor.run { self.isAuto ? nil : self.activeProfile }
        var newActive: String?
        for url in urls {
            do {
                let (keys, extra) = try await Self.fetchSubscription(url, hwid: Self.hwid)
                problems += extra
                if keys.isEmpty { // пустой ответ (сбой сервиса) — старые серверы не трогаем
                    problems.append("\(url): в ответе нет ключей VLESS, оставлены прежние серверы")
                    continue
                }
                let names = Dictionary(list.filter { $0.sub == url }.map { ($0.id, $0.name) }, uniquingKeysWith: { a, _ in a })
                let fresh = keys.map { p -> Profile in
                    var p = p
                    if let n = names[p.id] { p.name = n }
                    return p
                }
                list = list.filter { $0.sub != url } + fresh
                total += fresh.count
                // у выбранного сервера сменился ключ — остаёмся на сервере с тем же названием
                if let cur = current, cur.sub == url, !fresh.contains(where: { $0.id == cur.id }),
                   let same = fresh.first(where: { $0.name == cur.name }) {
                    newActive = same.id
                }
            } catch {
                problems.append("\(url): \(error.localizedDescription)")
            }
        }
        let result = list
        let selected = newActive
        await MainActor.run {
            self.profiles = result
            if let id = selected { self.active = id }
            self.d.set(Date().timeIntervalSince1970, forKey: "subsChecked")
        }
        return (total, problems)
    }

    private struct ParsedInput: Decodable {
        var profiles: [Profile] = []
        var subscriptions: [String] = []
        var problems: [String] = []
    }

    private struct ParsedSub: Decodable {
        var profiles: [Profile]
        var problems: [String]
        var notices: [String]? // сообщения сервиса вместо серверов (заглушки)
    }

    static func decode<T: Decodable>(_ t: T.Type, _ json: String) -> T? {
        try? JSONDecoder().decode(t, from: Data(json.utf8))
    }

    /// Код устройства для подписок, как его присылают Happ и v2RayTun: панели с лимитом устройств
    /// (Remnawave и др.) без него отдают вместо серверов заглушку. Случайный, создаётся один раз.
    static var hwid: String {
        let d = UserDefaults.standard
        if let id = d.string(forKey: "hwid"), !id.isEmpty { return id }
        let id = String(UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased().prefix(16))
        d.set(id, forKey: "hwid")
        return id
    }

    /// Сервисы подписок разным программам отдают разное: полный список часто получают только известные
    /// (Happ, v2rayN). Подписка запрашивается от имени каждой и берётся самый полный ответ.
    static let subUserAgents = ["BypassHub-iOS/\(appVersion)", "v2rayN/7.15.4", "Happ/3.9.0"]

    private static func fetchSubscription(_ url: String, hwid: String) async throws -> ([Profile], [String]) {
        guard let u = URL(string: url) else { throw TextError("неверная ссылка") }
        let os = ProcessInfo.processInfo.operatingSystemVersion
        var best: ParsedSub?
        var notices: [String] = []
        var lastError: Error?
        for ua in subUserAgents {
            var req = URLRequest(url: u, timeoutInterval: 20)
            req.setValue(ua, forHTTPHeaderField: "User-Agent")
            req.setValue("*/*", forHTTPHeaderField: "Accept")
            req.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
            req.setValue(hwid, forHTTPHeaderField: "x-hwid")
            req.setValue("iOS", forHTTPHeaderField: "x-device-os")
            req.setValue("\(os.majorVersion).\(os.minorVersion)", forHTTPHeaderField: "x-ver-os")
            req.setValue("iPhone", forHTTPHeaderField: "x-device-model")
            let data: Data
            do {
                let (body, resp) = try await URLSession.shared.data(for: req)
                if let h = resp as? HTTPURLResponse, h.statusCode / 100 != 2 {
                    lastError = TextError("HTTP \(h.statusCode)")
                    continue // этому клиенту сервис не отвечает — пробуем от имени следующего
                }
                data = body
            } catch {
                if best == nil { throw error } // сервис недоступен вовсе — другие попытки не помогут
                break
            }
            guard let parsed = decode(ParsedSub.self, BhcoreParseSubscription(String(decoding: data, as: UTF8.self), url))
            else { continue }
            for n in parsed.notices ?? [] where !notices.contains(n) { notices.append(n) }
            if best == nil || parsed.profiles.count > best!.profiles.count { best = parsed }
        }
        guard let result = best else { throw lastError ?? TextError("нет ответа") }
        let stubs = result.profiles.count <= 1 ? notices.map { "сервис подписки сообщает: «\($0)»" } : []
        return (result.profiles, result.problems + stubs)
    }

    // ---------------------------------------------------------------- запрос для ядра
    /// RunRequest для bhcore.Run: mode = vpn | bypass | none (только TG-прокси); bypass — обход вместе с VPN.
    func request(mode: String, bypass: Bool, tg: Bool) -> String {
        let req: [String: Any] = [
            "mode": mode,
            "bypass": bypass,
            "profiles": profiles.map { ["id": $0.id, "kind": $0.kind, "name": $0.name, "data": $0.data] },
            "selected": isAuto ? autoID : (activeProfile?.id ?? ""),
            "ruDirect": ruDirect,
            "bypassMethod": bypassMethod,
            "bypassAll": bypassAll,
            "bypassUser": bypassUser,
            "tg": ["enabled": tg, "port": tgPort, "secret": tgSecret, "cf": tgCF],
        ]
        let data = (try? JSONSerialization.data(withJSONObject: req)) ?? Data()
        return String(decoding: data, as: UTF8.self)
    }

    /// Ссылка, по которой Telegram добавит прокси (секрет dd — со случайным выравниванием пакетов).
    var tgLink: String { "tg://proxy?server=127.0.0.1&port=\(tgPort)&secret=dd\(tgSecret)" }
}

struct TextError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}
