import SwiftUI

struct BypassView: View {
    @EnvironmentObject var store: Store
    @EnvironmentObject var tunnel: Tunnel
    @State private var user = ""

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                Card("Обход блокировок без VPN") {
                    Hint("Работает без сервера: трафик идёт напрямую, а к сайтам из списков BypassHub разбивает " +
                         "начало защищённого соединения на части — так провайдер не видит, какой сайт открывается, " +
                         "и не замедляет его. Списки — те же, что у zapret для Windows (YouTube, Discord, Twitch и др.). " +
                         "Включается на главной.")
                    Hint("Голосовые звонки Discord и игры так не ускорить — для них нужен VPN. iPhone разрешает " +
                         "одно VPN-подключение, поэтому обход и VPN работают по очереди.")
                }

                Card("Способ") {
                    ForEach(BypassMethod.allCases) { m in
                        let sel = store.bypassMethod == m.rawValue
                        Button {
                            if !sel {
                                store.bypassMethod = m.rawValue
                                restart()
                            }
                        } label: {
                            VStack(alignment: .leading, spacing: 2) {
                                Text((sel ? "● " : "○ ") + m.title).font(.subheadline.bold())
                                    .foregroundColor(sel ? Theme.focus : Theme.text)
                                Text(m.hint).font(.caption).foregroundColor(Theme.muted)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 14).padding(.vertical, 10)
                            .background(RoundedRectangle(cornerRadius: 12).fill(Theme.surface2)
                                .overlay(RoundedRectangle(cornerRadius: 12).stroke(sel ? Theme.accent : .clear, lineWidth: 2)))
                        }
                        .buttonStyle(.plain)
                    }
                    Toggle("Применять ко всем сайтам", isOn: Binding(get: { store.bypassAll }, set: {
                        store.bypassAll = $0
                        restart()
                    }))
                    .tint(Theme.accent)
                    .padding(.top, 6)
                    Hint("Кроме российских. Помогает с сайтами, которых нет в списках; QUIC при этом отключается для всех сайтов.")
                }

                Card("Свои сайты") {
                    Hint("По одному на строку, например example.com — поддомены учитываются автоматически.")
                    TextEditor(text: $user)
                        .font(.system(.footnote, design: .monospaced))
                        .autocapitalization(.none)
                        .disableAutocorrection(true)
                        .frame(minHeight: 90)
                        .padding(6)
                        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.surface2))
                        .modifier(HideEditorBackground())
                    BHButton("Сохранить", primary: false) {
                        let lines = user.split(whereSeparator: \.isNewline)
                            .map { $0.trimmingCharacters(in: .whitespaces).lowercased() }
                            .filter { !$0.isEmpty }
                        store.bypassUser = lines.joined(separator: "\n")
                        user = store.bypassUser
                        Toast.shared.show("Сохранено сайтов: \(lines.count)")
                        restart()
                    }
                }

                Card("Списки сайтов") {
                    Hint("Из zapret-discord-youtube (Flowseal). Обновляются раз в сутки при включении обхода; " +
                         "пока не скачаны — используется встроенный список.")
                }
            }
            .frame(maxWidth: 640)
            .padding(16)
            .frame(maxWidth: .infinity)
        }
        .navigationTitle("Обход")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { user = store.bypassUser }
    }

    private func restart() {
        if tunnel.isOn("bypass") { tunnel.restartIfRunning() }
    }
}
