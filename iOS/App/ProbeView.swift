import SwiftUI

struct ProbeView: View {
    @StateObject private var probe = Probe()

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("BypassHub")
                    .font(.largeTitle.bold())
                    .foregroundStyle(LinearGradient(colors: [Color(red: 0.5, green: 0.35, blue: 0.94),
                                                             Color(red: 0.17, green: 0.71, blue: 0.49)],
                                                    startPoint: .leading, endPoint: .trailing))
                Text("Проверка VPN на iOS")
                    .font(.title3.bold())
                Text("Кнопка создаст VPN-профиль BypassHub и на пару секунд поднимет тестовый туннель. " +
                     "Обычный интернет он не трогает. iOS спросит разрешение на добавление VPN — нажмите «Разрешить».")
                    .foregroundColor(.secondary)
                Button(action: { probe.run() }) {
                    Text(probe.running ? "Проверяю…" : "Проверить")
                        .font(.headline)
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(Color(red: 0.5, green: 0.35, blue: 0.94))
                        .foregroundColor(.white)
                        .cornerRadius(14)
                }
                .disabled(probe.running)
                if !probe.verdict.isEmpty {
                    Text(probe.verdict)
                        .font(.headline)
                        .padding()
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.white.opacity(0.08))
                        .cornerRadius(14)
                }
                ForEach(Array(probe.lines.enumerated()), id: \.offset) { _, line in
                    Text(line)
                        .font(.system(.footnote, design: .monospaced))
                        .textSelection(.enabled)
                }
                Text("Пришлите скриншот этого экрана — по нему будет видно, можно ли делать полную версию.")
                    .font(.footnote)
                    .foregroundColor(.secondary)
            }
            .padding()
        }
    }
}
