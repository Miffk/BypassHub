import SwiftUI
import UIKit

/// Цвета и общие элементы — как в версии для Android.
enum Theme {
    static let bg = Color(hex: 0x101418)
    static let surface = Color(hex: 0x1b2129)
    static let surface2 = Color(hex: 0x262d37)
    static let border = Color(hex: 0x36404c)
    static let text = Color(hex: 0xe8eaed)
    static let muted = Color(hex: 0x9aa3ad)
    static let accent = Color(hex: 0x7f5af0)
    static let accent2 = Color(hex: 0x2cb67d)
    static let green = Color(hex: 0x3ddc84)
    static let yellow = Color(hex: 0xf6c343)
    static let red = Color(hex: 0xff6b6b)
    static let softRed = Color(hex: 0xff8f80)
    static let focus = Color(hex: 0xc9b8ff)
    static let glass = Color(hex: 0x1b2129).opacity(0.8)
    static let gradient = LinearGradient(colors: [Color(hex: 0x7f5af0), Color(hex: 0x2cb67d)],
                                         startPoint: .leading, endPoint: .trailing)

    static func ping(_ ms: Int?) -> (String, Color) {
        guard let ms = ms else { return ("", muted) }
        if ms < 0 { return ("нет ответа", red) }
        return ("\(ms) мс", ms < 150 ? green : ms < 400 ? yellow : red)
    }
}

extension Color {
    init(hex: UInt32) {
        self.init(red: Double(hex >> 16 & 255) / 255, green: Double(hex >> 8 & 255) / 255, blue: Double(hex & 255) / 255)
    }
}

/// Фон со свечением: синее — выключено, зелёное — работает.
struct GlowBackground: View {
    var on: Bool
    var body: some View {
        ZStack {
            Theme.bg
            RadialGradient(colors: [on ? Color(red: 0.17, green: 0.71, blue: 0.49).opacity(0.3)
                                       : Color(red: 0.23, green: 0.36, blue: 0.75).opacity(0.35), .clear],
                           center: .top, startRadius: 0, endRadius: 520)
        }
        .ignoresSafeArea()
        .animation(.easeInOut(duration: 0.4), value: on)
    }
}

/// Карточка с заголовком.
struct Card<Content: View>: View {
    var title: String?
    @ViewBuilder var content: Content

    init(_ title: String? = nil, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let title = title {
                Text(title).font(.headline).foregroundColor(Theme.text)
            }
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(RoundedRectangle(cornerRadius: 20).fill(Theme.glass))
    }
}

struct Hint: View {
    var text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(text).font(.footnote).foregroundColor(Theme.muted).fixedSize(horizontal: false, vertical: true)
    }
}

/// Кнопка: основная — с градиентом, вторичная — серая.
struct BHButton: View {
    var title: String
    var primary = true
    var action: () -> Void

    init(_ title: String, primary: Bool = true, action: @escaping () -> Void) {
        self.title = title
        self.primary = primary
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.subheadline.weight(.semibold))
                .foregroundColor(.white)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
                .background(
                    Group {
                        if primary {
                            RoundedRectangle(cornerRadius: 14).fill(Theme.gradient)
                        } else {
                            RoundedRectangle(cornerRadius: 14).fill(Theme.surface2)
                        }
                    }
                )
        }
        .buttonStyle(.plain)
    }
}

/// Переключатель «Выкл | Вкл» с кнопкой питания.
struct PowerSwitch: View {
    var on: Bool
    var busy: Bool
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack(alignment: on ? .trailing : .leading) {
                Capsule().fill(Color(hex: 0x0a0d11))
                // подпись на дорожке — та сторона, куда можно переключить
                Text(on ? "Выкл" : "Вкл")
                    .foregroundColor(Theme.muted)
                    .frame(width: 100)
                    .frame(maxWidth: .infinity, alignment: on ? .leading : .trailing)
                HStack(spacing: 8) {
                    ZStack {
                        Circle().fill(on ? Color.white.opacity(0.24) : Color(hex: 0x55657c))
                        Image(systemName: "power").font(.system(size: 22, weight: .bold)).foregroundColor(.white)
                    }
                    .frame(width: 52, height: 52)
                    Text(busy ? "…" : on ? "Вкл" : "Выкл")
                        .font(.headline).foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                }
                .padding(.leading, 6).padding(.trailing, 14)
                .frame(width: 150, height: 64)
                .background(
                    Group {
                        if on {
                            Capsule().fill(Theme.gradient)
                        } else {
                            Capsule().fill(Color(hex: 0x3a4658))
                        }
                    }
                )
                .padding(6)
            }
            .frame(width: 260, height: 76)
            .shadow(color: .black.opacity(0.4), radius: 10, y: 4)
            .animation(.spring(response: 0.3, dampingFraction: 0.8), value: on)
        }
        .buttonStyle(.plain)
    }
}

/// Сообщение-всплывашка (как Toast на Android).
final class Toast: ObservableObject {
    static let shared = Toast()
    @Published var text: String?
    private var work: DispatchWorkItem?

    func show(_ s: String) {
        DispatchQueue.main.async {
            self.text = s
            self.work?.cancel()
            let w = DispatchWorkItem { self.text = nil }
            self.work = w
            DispatchQueue.main.asyncAfter(deadline: .now() + 3, execute: w)
        }
    }
}

struct ToastView: View {
    @ObservedObject var toast = Toast.shared
    var body: some View {
        VStack {
            Spacer()
            if let t = toast.text {
                Text(t)
                    .font(.footnote)
                    .foregroundColor(.white)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 16).padding(.vertical, 10)
                    .background(Capsule().fill(Color.black.opacity(0.85)))
                    .padding(.bottom, 70)
                    .transition(.opacity)
            }
        }
        .padding(.horizontal, 16)
        .animation(.easeInOut(duration: 0.2), value: toast.text)
        .allowsHitTesting(false)
    }
}

/// Убрать клавиатуру.
func hideKeyboard() {
    UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
}

/// Кнопка «Готово» над клавиатурой и скрытие клавиатуры прокруткой: у многострочного поля и цифровой
/// клавиатуры нет своей кнопки, которая её убирает.
struct KeyboardDismiss: ViewModifier {
    func body(content: Content) -> some View {
        Group {
            if #available(iOS 16.0, *) {
                content.scrollDismissesKeyboard(.interactively)
            } else {
                content
            }
        }
        .toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Готово") { hideKeyboard() }.font(.body.bold())
            }
        }
    }
}
