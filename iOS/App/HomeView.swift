import SwiftUI
import UIKit

struct HomeView: View {
    @EnvironmentObject var store: Store
    @EnvironmentObject var tunnel: Tunnel
    @EnvironmentObject var router: Router
    @State private var pings: [String: Int] = [:]
    @State private var pingsReal = false
    @State private var pinging = false
    @State private var lastPing = Date.distantPast

    private var m: String { store.homeMode }

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                segments.padding(.top, 6)
                if m == "tg" { tgHome } else { modeHome }
            }
            .frame(maxWidth: 640)
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
            .frame(maxWidth: .infinity)
        }
        .navigationTitle("BypassHub")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { maybePing() }
        .onChange(of: tunnel.connected) { _ in maybePing(force: true) }
    }

    // ---------------------------------------------------------------- переключатель режима
    private var segments: some View {
        HStack(spacing: 4) {
            ForEach(["vpn", "bypass", "tg"], id: \.self) { key in
                let label = ["vpn": "VPN", "bypass": "Обход", "tg": "Telegram"][key] ?? key
                let sel = key == m
                let running = tunnel.isOn(key)
                Button {
                    store.homeMode = key
                } label: {
                    Text((running ? "● " : "") + label)
                        .font(.subheadline.weight(.semibold))
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)
                        .foregroundColor(sel ? .white : running ? Theme.green : Theme.muted)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                        .background(
                            Group {
                                if sel {
                                    RoundedRectangle(cornerRadius: 20).fill(Theme.gradient)
                                } else {
                                    RoundedRectangle(cornerRadius: 20).fill(Color.clear)
                                }
                            }
                        )
                }
                .buttonStyle(.plain)
            }
        }
        .padding(4)
        .background(RoundedRectangle(cornerRadius: 24).fill(Theme.glass))
    }

    // ---------------------------------------------------------------- VPN и обход
    private var modeHome: some View {
        let on = tunnel.isOn(m)
        let busy = tunnel.connecting || (tunnel.busy && tunnel.mode == m)
        let hint: (String, Color) = on ? (m == "vpn" ? "Трафик идёт через ваш сервер"
                                                     : "Заблокированные сайты открываются напрямую", Theme.green)
            : (m == "vpn" ? "Без VPN провайдер видит, какие сайты вы открываете"
                          : "YouTube и Discord могут замедляться провайдером", Theme.softRed)
        let title = busy && !on ? "Подключение…" : on ? (m == "vpn" ? "VPN подключён" : "Обход работает")
            : (m == "vpn" ? "Вы не подключены" : "Обход выключен")
        return VStack(spacing: 0) {
            Text(hint.0).font(.footnote).foregroundColor(hint.1).multilineTextAlignment(.center).padding(.top, 14)
            pick.padding(.top, 12)
            Text(title).font(.title3.bold()).foregroundColor(Theme.text).padding(.top, 40)
            if let e = tunnel.error, !on {
                Text("Ошибка: \(e)").font(.footnote).foregroundColor(Theme.red)
                    .multilineTextAlignment(.center).padding(.top, 6)
            }
            PowerSwitch(on: on, busy: busy) { toggle(m) }.padding(.top, 18)
            Text(m == "bypass" ? "Без VPN-сервера · " + methodTitle
                 : store.ruDirect ? "Зарубежное — через VPN, российское — напрямую" : "Весь трафик — через VPN")
                .font(.footnote).foregroundColor(Theme.muted).multilineTextAlignment(.center)
                .padding(.top, 14).padding(.bottom, 32)
            if m == "bypass" {
                Button { router.tab = "bypass" } label: {
                    Card("Сайт всё равно не открывается?") {
                        Hint("Смените способ разбиения или добавьте свои сайты на вкладке «Обход». " +
                             "Голосовые звонки Discord и игры без VPN не ускорить.")
                    }
                }.buttonStyle(.plain)
            } else if store.profiles.isEmpty {
                Card("Серверов пока нет") {
                    Hint("Добавьте ключ vless://… от вашего VPN-сервиса или ключ AmneziaVPN vpn://… " +
                         "(AmneziaVPN → «Поделиться»).")
                    BHButton("Добавить сервер") { router.tab = "vpn" }.padding(.top, 4)
                }
            } else {
                servers
            }
        }
    }

    private var methodTitle: String {
        (BypassMethod(rawValue: store.bypassMethod) ?? .disorder).title.lowercased()
    }

    /// Выбранный сервер (или что делает обход) — нажатие открывает вкладку режима.
    private var pick: some View {
        let on = tunnel.isOn(m)
        let caption: String
        let value: String
        if m == "bypass" {
            caption = "Сайты для обхода"
            value = store.bypassAll ? "Все, кроме российских" : "YouTube, Discord и другие"
        } else if store.profiles.isEmpty {
            caption = "Сервер не добавлен"
            value = "Нажмите, чтобы добавить"
        } else {
            caption = "Выбранный сервер"
            let live = tunnel.isOn("vpn") && tunnel.core.selected == autoID ? tunnel.core.current ?? "" : ""
            value = store.isAuto ? (live.isEmpty ? "Авто — лучший сервер" : "Авто · \(store.name(of: live))")
                : store.activeProfile?.name ?? "—"
        }
        let icon = m == "bypass" ? "bolt.horizontal.fill" : store.isAuto ? "sparkles" : "globe"
        return Button { router.tab = m } label: {
            HStack(spacing: 14) {
                ZStack {
                    Circle().fill(Theme.surface2)
                    Image(systemName: icon).foregroundColor(on ? Theme.accent2 : Theme.text)
                }
                .frame(width: 46, height: 46)
                VStack(alignment: .leading, spacing: 2) {
                    Text(caption).font(.footnote).foregroundColor(Theme.muted)
                    Text(value).font(.headline).foregroundColor(Theme.text).lineLimit(1)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").foregroundColor(Theme.muted)
            }
            .padding(.vertical, 12).padding(.leading, 14).padding(.trailing, 18)
            .background(Capsule().fill(Theme.glass).overlay(Capsule().stroke(Theme.border, lineWidth: 1)))
        }
        .buttonStyle(.plain)
    }

    /// «Авто» и список серверов: нажатие выбирает сервер, справа — пинг.
    private var servers: some View {
        let selected = store.isAuto ? autoID : store.activeProfile?.id ?? ""
        let liveAuto = tunnel.isOn("vpn") && tunnel.core.selected == autoID ? tunnel.core.current ?? "" : ""
        return VStack(spacing: 12) {
            if store.vless.count >= 2 {
                Button { tunnel.selectServer(autoID) } label: {
                    HStack(spacing: 12) {
                        Image(systemName: "sparkles").foregroundColor(Theme.focus)
                        Text("Авто — лучший сервер").font(.headline).foregroundColor(Theme.focus)
                        Spacer(minLength: 4)
                        Text(liveAuto.isEmpty ? "Рекомендуем" : "сейчас: \(store.name(of: liveAuto))")
                            .font(.footnote).foregroundColor(Theme.muted).lineLimit(1)
                    }
                    .padding(16)
                    .background(RoundedRectangle(cornerRadius: 20).fill(Theme.glass)
                        .overlay(RoundedRectangle(cornerRadius: 20)
                            .stroke(selected == autoID ? Theme.accent : .clear, lineWidth: 1.5)))
                }
                .buttonStyle(.plain)
            }
            VStack(spacing: 0) {
                HStack {
                    Text("Серверы").font(.subheadline).foregroundColor(Theme.muted)
                    Spacer()
                    if !pings.isEmpty {
                        Text(pingsReal ? "пинг через VPN" : "пинг до сервера").font(.caption).foregroundColor(Theme.muted)
                    }
                    Button(pinging ? "Проверка…" : "Пинг") { ping() }
                        .font(.subheadline.weight(.semibold)).foregroundColor(Theme.accent2)
                        .padding(.leading, 8)
                }
                .padding(.bottom, 4)
                ForEach(Array(store.profiles.enumerated()), id: \.element.id) { i, p in
                    if i > 0 { Divider().background(Theme.muted.opacity(0.25)) }
                    Button { tunnel.selectServer(p.id) } label: {
                        HStack {
                            (Text((p.id == selected ? "● " : "") + p.name).bold()
                             + Text(" - " + p.kindName + (liveAuto == p.id ? " · сейчас" : "")).foregroundColor(Theme.muted))
                                .foregroundColor(p.id == selected ? Theme.focus : Theme.text)
                                .lineLimit(1)
                            Spacer(minLength: 8)
                            if p.kind == "vless" {
                                let t = Theme.ping(pings[p.id])
                                Text(t.0).font(.subheadline.bold()).foregroundColor(t.1)
                            }
                        }
                        .padding(.vertical, 14)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 18).padding(.top, 12).padding(.bottom, 4)
            .background(RoundedRectangle(cornerRadius: 20).fill(Theme.glass))
        }
    }

    private func toggle(_ mode: String) {
        if mode == "vpn" && store.profiles.isEmpty && !tunnel.isOn("vpn") {
            Toast.shared.show("Сначала добавьте свой сервер")
            router.tab = "vpn"
            return
        }
        if tunnel.connected && mode != "tg" && tunnel.mode != "none" && tunnel.mode != mode {
            Toast.shared.show(mode == "vpn" ? "Обход выключен: на iPhone работает только одно VPN-подключение"
                              : "VPN выключен: на iPhone работает только одно VPN-подключение")
        }
        tunnel.toggle(mode)
    }

    private func maybePing(force: Bool = false) {
        guard m == "vpn", store.vless.count > 0, !pinging else { return }
        if force || Date().timeIntervalSince(lastPing) > 60 { ping() }
    }

    private func ping() {
        pinging = true
        lastPing = Date()
        let real = tunnel.isOn("vpn") && tunnel.core.kind == "vless"
        tunnel.delays { result in
            pinging = false
            pings = result
            pingsReal = real
            if real {
                // серверы без ответа через VPN — тоже показываем
                for p in store.vless where result[p.id] == nil { pings[p.id] = -1 }
            }
        }
    }

    // ---------------------------------------------------------------- Telegram
    private var tgHome: some View {
        let on = tunnel.isOn("tg")
        let busy = tunnel.busy && tunnel.tg && !on
        return VStack(spacing: 0) {
            Text(on ? "Telegram идёт через WebSocket — как web.telegram.org" : "Telegram может замедляться провайдером")
                .font(.footnote).foregroundColor(on ? Theme.green : Theme.softRed)
                .multilineTextAlignment(.center).padding(.top, 14)
            Button { router.tab = "tg" } label: {
                HStack(spacing: 14) {
                    ZStack {
                        Circle().fill(Theme.surface2)
                        Image(systemName: "paperplane.fill").foregroundColor(on ? Theme.accent2 : Theme.text)
                    }
                    .frame(width: 46, height: 46)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Прокси для Telegram").font(.footnote).foregroundColor(Theme.muted)
                        Text("127.0.0.1:\(String(store.tgPort))").font(.headline).foregroundColor(Theme.text)
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right").foregroundColor(Theme.muted)
                }
                .padding(.vertical, 12).padding(.leading, 14).padding(.trailing, 18)
                .background(Capsule().fill(Theme.glass).overlay(Capsule().stroke(Theme.border, lineWidth: 1)))
            }
            .buttonStyle(.plain)
            .padding(.top, 12)
            Text(busy ? "Запуск…" : on ? "Прокси работает" : "Прокси выключен")
                .font(.title3.bold()).foregroundColor(Theme.text).padding(.top, 40)
            if let e = tunnel.error, !on {
                Text("Ошибка: \(e)").font(.footnote).foregroundColor(Theme.red)
                    .multilineTextAlignment(.center).padding(.top, 6)
            }
            PowerSwitch(on: on, busy: busy) { tunnel.toggle("tg") }.padding(.top, 18)
            Text(on && !(tunnel.core.tgStats ?? "").isEmpty ? tunnel.core.tgStats!
                 : "Работает вместе с VPN или обходом — можно держать включённым")
                .font(.footnote).foregroundColor(Theme.muted).multilineTextAlignment(.center)
                .padding(.top, 14).padding(.bottom, 32)
            Card("Подключить Telegram") {
                Hint("Один раз: Telegram откроется и предложит добавить прокси — нажмите «Подключить». " +
                     "Дальше достаточно включать прокси здесь.")
                BHButton("Открыть в Telegram") { openTelegram(store: store, tunnel: tunnel) }.padding(.top, 4)
            }
        }
    }
}

/// Открывает ссылку на прокси в Telegram (если Telegram нет — копирует её).
func openTelegram(store: Store, tunnel: Tunnel) {
    if !tunnel.isOn("tg") {
        Toast.shared.show("Сначала включите прокси")
        tunnel.toggle("tg")
    }
    guard let url = URL(string: store.tgLink) else { return }
    UIApplication.shared.open(url) { ok in
        if !ok {
            UIPasteboard.general.string = store.tgLink
            Toast.shared.show("Telegram не найден — ссылка на прокси скопирована")
        }
    }
}
