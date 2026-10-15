import SwiftUI
import UIKit

struct ServersView: View {
    @EnvironmentObject var store: Store
    @EnvironmentObject var tunnel: Tunnel
    @State private var input = ""
    @State private var adding = false
    @State private var result: String?
    @State private var renaming: Profile?
    @State private var newName = ""
    @State private var removing: Profile?
    @State private var removingSub: String?

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                Card("Добавить сервер") {
                    Hint("Ключ vless://…, ключ AmneziaVPN vpn://… (AmneziaVPN → «Поделиться»), конфиг AmneziaWG " +
                         "или ссылка на подписку https://…. Можно несколько ключей — по одному на строку.")
                    TextEditor(text: $input)
                        .font(.system(.footnote, design: .monospaced))
                        .frame(minHeight: 90)
                        .padding(6)
                        .background(RoundedRectangle(cornerRadius: 12).fill(Theme.surface2))
                        .modifier(HideEditorBackground())
                    HStack(spacing: 8) {
                        BHButton(adding ? "Добавляю…" : "Вставить из буфера") {
                            let text = UIPasteboard.general.string ?? ""
                            if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                                Toast.shared.show("Буфер обмена пуст")
                            } else {
                                add(text)
                            }
                        }
                        BHButton("Добавить", primary: false) {
                            if input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                                Toast.shared.show("Вставьте ключ в поле выше")
                            } else {
                                add(input)
                            }
                        }
                    }
                    .padding(.top, 4)
                    .disabled(adding)
                    Hint("Ключ — это пароль от вашего сервера: не публикуйте его.")
                }

                if !store.profiles.isEmpty {
                    Card("Мои серверы") {
                        ForEach(store.profiles) { p in row(p) }
                    }
                }

                if !store.subscriptions.isEmpty {
                    Card("Подписки") {
                        ForEach(store.subscriptions, id: \.self) { url in
                            HStack {
                                Text(url).font(.footnote).foregroundColor(Theme.muted).lineLimit(1).truncationMode(.middle)
                                Spacer()
                                Button { removingSub = url } label: {
                                    Image(systemName: "xmark").foregroundColor(Theme.muted).padding(8)
                                }
                            }
                        }
                        BHButton("Обновить подписки", primary: false) {
                            Toast.shared.show("Обновляю…")
                            Task {
                                let (n, problems) = await store.refreshSubscriptions()
                                Toast.shared.show("Серверов в подписках: \(n)" +
                                                  (problems.isEmpty ? "" : "\n" + problems.prefix(3).joined(separator: "\n")))
                            }
                        }
                    }
                }

                Card("Раздельное туннелирование") {
                    Toggle("Российское — напрямую", isOn: Binding(get: { store.ruDirect }, set: {
                        store.ruDirect = $0
                        if tunnel.isOn("vpn") { tunnel.restartIfRunning() }
                    }))
                    .tint(Theme.accent)
                    Hint("Через VPN идёт всё зарубежное. Напрямую, с домашнего IP, — сайты .ru, .рф, .su, российские " +
                         "IP-адреса и российские сервисы на «нерусских» доменах (VK, Яндекс, банки и др.) по базам " +
                         "roscomvpn. YouTube, Telegram, GitHub всегда идут через VPN.")
                }
            }
            .frame(maxWidth: 640)
            .padding(16)
            .frame(maxWidth: .infinity)
        }
        .navigationTitle("VPN")
        .navigationBarTitleDisplayMode(.inline)
        .alert("Серверы", isPresented: Binding(get: { result != nil }, set: { if !$0 { result = nil } })) {
            Button("OK") { result = nil }
        } message: {
            Text(result ?? "")
        }
        .alert("Переименовать", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField("Название", text: $newName)
            Button("Сохранить") {
                if let p = renaming { store.rename(p.id, newName) }
                renaming = nil
            }
            Button("Отмена", role: .cancel) { renaming = nil }
        }
        .confirmationDialog("Удалить сервер «\(removing?.name ?? "")»?",
                            isPresented: Binding(get: { removing != nil }, set: { if !$0 { removing = nil } }),
                            titleVisibility: .visible) {
            Button("Удалить", role: .destructive) {
                if let p = removing { remove(p) }
                removing = nil
            }
        }
        .confirmationDialog("Удалить подписку и все её серверы?",
                            isPresented: Binding(get: { removingSub != nil }, set: { if !$0 { removingSub = nil } }),
                            titleVisibility: .visible) {
            Button("Удалить", role: .destructive) {
                if let u = removingSub { store.removeSubscription(u) }
                removingSub = nil
            }
        }
    }

    private func row(_ p: Profile) -> some View {
        let selected = store.isAuto ? "" : store.activeProfile?.id ?? ""
        return HStack(spacing: 6) {
            Button { tunnel.selectServer(p.id) } label: {
                VStack(alignment: .leading, spacing: 2) {
                    Text((p.id == selected ? "● " : "") + p.name).bold()
                        .foregroundColor(p.id == selected ? Theme.focus : Theme.text).lineLimit(1)
                    Text(p.summary ?? p.kindName).font(.caption).foregroundColor(Theme.muted).lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            Button {
                newName = p.name
                renaming = p
            } label: {
                Image(systemName: "pencil").foregroundColor(Theme.text).frame(width: 36, height: 36)
                    .background(RoundedRectangle(cornerRadius: 10).fill(Theme.surface2))
            }
            Button { removing = p } label: {
                Image(systemName: "xmark").foregroundColor(Theme.text).frame(width: 36, height: 36)
                    .background(RoundedRectangle(cornerRadius: 10).fill(Theme.surface2))
            }
        }
        .padding(.vertical, 4)
    }

    private func add(_ text: String) {
        adding = true
        Task {
            let (n, problems) = await store.add(text)
            await MainActor.run {
                adding = false
                if n > 0 { input = "" }
                var msg = n > 0 ? "Добавлено серверов: \(n)" : problems.isEmpty ? "Эти серверы уже есть в списке." : ""
                if !problems.isEmpty {
                    msg += (msg.isEmpty ? "" : "\n\n") + "Не добавлено:\n" + problems.prefix(6).map { "• " + $0 }.joined(separator: "\n")
                }
                result = msg
            }
        }
    }

    private func remove(_ p: Profile) {
        store.remove(p.id)
        if tunnel.isOn("vpn") {
            if store.profiles.isEmpty { tunnel.toggle("vpn") } else { tunnel.restartIfRunning() }
        }
    }
}

/// TextEditor на iOS 15 рисует свой белый фон — убираем его.
struct HideEditorBackground: ViewModifier {
    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(iOS 16.0, *) {
            content.scrollContentBackground(.hidden)
        } else {
            content.onAppear { UITextView.appearance().backgroundColor = .clear }
        }
    }
}
