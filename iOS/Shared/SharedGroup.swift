import Foundation

/// Общая папка приложения и VPN-расширения (App Group). Имя группы не зашито в код: его задаёт профиль
/// подписи (.mobileprovision), которым пользователь подписывает IPA в ESign и т. п., — читаем его оттуда.
/// Через эту папку приложение читает журнал расширения: сообщения NETunnelProviderSession после
/// переподписи работают не везде.
enum SharedGroup {
    static let directory: URL? = {
        for id in groupIDs() {
            guard let url = FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: id) else { continue }
            let probe = url.appendingPathComponent(".bypasshub-probe")
            if (try? Data("ok".utf8).write(to: probe)) != nil {
                try? FileManager.default.removeItem(at: probe)
                return url
            }
        }
        return nil
    }()

    /// Журнал расширения: в общей папке, если она есть, иначе — в своей (тогда его видит только расширение).
    static var logFile: URL {
        (directory ?? FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0])
            .appendingPathComponent("tunnel-log.txt")
    }

    /// Группы из профиля подписи этого бандла (у расширения — и из профиля приложения).
    static func groupIDs() -> [String] {
        var bundles = [Bundle.main.bundleURL]
        if Bundle.main.bundleURL.pathExtension == "appex" {
            bundles.append(Bundle.main.bundleURL.deletingLastPathComponent().deletingLastPathComponent())
        }
        var ids: [String] = []
        for b in bundles {
            for g in groups(inProvision: b.appendingPathComponent("embedded.mobileprovision")) where !ids.contains(g) {
                ids.append(g)
            }
        }
        return ids
    }

    private static func groups(inProvision url: URL) -> [String] {
        // .mobileprovision — подписанный CMS-контейнер, внутри которого лежит обычный XML-plist
        guard let data = try? Data(contentsOf: url),
              let start = data.range(of: Data("<?xml".utf8)),
              let end = data.range(of: Data("</plist>".utf8), in: start.lowerBound..<data.endIndex),
              let plist = try? PropertyListSerialization.propertyList(
                  from: data.subdata(in: start.lowerBound..<end.upperBound), options: [], format: nil)
                  as? [String: Any],
              let ent = plist["Entitlements"] as? [String: Any]
        else { return [] }
        return (ent["com.apple.security.application-groups"] as? [String] ?? []).filter { !$0.contains("*") }
    }
}
