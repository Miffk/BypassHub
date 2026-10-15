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
    case disorder, recordDisorder = "record-disorder", tcp, record
    var id: String { rawValue }

    var title: String {
        switch self {
        case .disorder: return "Перемешивание пакетов"
        case .recordDisorder: return "TLS-записи и перемешивание"
        case .tcp: return "Разбиение TCP-пакетов"
        case .record: return "Разбиение TLS-записей"
        }
    }

    var hint: String {
        switch self {
        case .disorder: return "Как disorder в ByeDPI: первая часть уходит с TTL=1 и доходит до сайта только повтором. По умолчанию."
        case .recordDisorder: return "То же, но начало соединения ещё и делится на две TLS-записи."
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
                let (keys, extra) = try await Self.fetchSubscription(url)
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

    /// Обновить серверы из подписок (ключи подписки заменяются новыми, имена сохраняются).
    func refreshSubscriptions() async -> (Int, [String]) {
        var problems: [String] = []
        var list: [Profile] = await MainActor.run { self.profiles }
        var total = 0
        let urls: [String] = await MainActor.run { self.subscriptions }
        for url in urls {
            do {
                let (keys, extra) = try await Self.fetchSubscription(url)
                problems += extra
                let names = Dictionary(list.filter { $0.sub == url }.map { ($0.id, $0.name) }, uniquingKeysWith: { a, _ in a })
                let fresh = keys.map { p -> Profile in
                    var p = p
                    if let n = names[p.id] { p.name = n }
                    return p
                }
                list = list.filter { $0.sub != url } + fresh
                total += fresh.count
            } catch {
                problems.append("\(url): \(error.localizedDescription)")
            }
        }
        let result = list
        await MainActor.run { self.profiles = result }
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
    }

    static func decode<T: Decodable>(_ t: T.Type, _ json: String) -> T? {
        try? JSONDecoder().decode(t, from: Data(json.utf8))
    }

    private static func fetchSubscription(_ url: String) async throws -> ([Profile], [String]) {
        guard let u = URL(string: url) else { throw TextError("неверная ссылка") }
        var req = URLRequest(url: u, timeoutInterval: 20)
        req.setValue("BypassHub", forHTTPHeaderField: "User-Agent")
        let (data, resp) = try await URLSession.shared.data(for: req)
        if let h = resp as? HTTPURLResponse, h.statusCode / 100 != 2 { throw TextError("HTTP \(h.statusCode)") }
        let parsed = decode(ParsedSub.self, BhcoreParseSubscription(String(decoding: data, as: UTF8.self), url))
        return (parsed?.profiles ?? [], parsed?.problems ?? [])
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
