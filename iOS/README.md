# BypassHub для iPhone и iPad

Те же три сервиса, что и в версии для Android: **VPN** (VLESS, AmneziaVPN/AmneziaWG, раздельное туннелирование),
**Обход блокировок** без VPN-сервера и **TG WS Proxy** для Telegram. iOS 15 и новее.

> В App Store BypassHub нет: IPA собирается без подписи, и установить его можно **только подписав своим
> сертификатом** (`.p12` + `.mobileprovision`) — например, в ESign, Sideloadly или zsign. Без сертификата
> iOS-версию не установить.

## Установка

1. Скачайте `BypassHub-ios.ipa` со страницы **[Releases](https://github.com/Miffk/BypassHub/releases/latest)**
   (тестовые сборки — в GitHub → **Actions** → **BypassHub build** → **Artifacts** → **BypassHub-ios**).
2. Подпишите IPA своим сертификатом (`.p12` + `.mobileprovision`) в **ESign**, **Sideloadly** или **zsign**:
   - **не удаляйте плагины/расширения** (в ESign — не включайте «Удалить плагины»): VPN работает
     внутри расширения `BypassHub Tunnel`;
   - в профиле (`.mobileprovision`) должно быть право **Network Extensions** (`packet-tunnel-provider`).
     Без него iOS не даст создать VPN-профиль.
3. Установите приложение. При первом включении iOS спросит разрешение добавить VPN-конфигурацию — нажмите
   «Разрешить».

Сам файл `.p12` и пароль от него никому не отправляйте и не выкладывайте.

## Как устроено

| Папка | Что там |
|---|---|
| `App/` | Приложение на SwiftUI: главная, серверы, обход, Telegram, настройки и журнал. |
| `Tunnel/` | VPN-расширение (`NEPacketTunnelProvider`): запускает ядро и настраивает туннель. |
| `../Android/core/` | То же ядро на Go, что у Android ([sing-box](https://github.com/SagerNet/sing-box), amneziawg-go, обход, TG-прокси) — для iOS собирается в `Bhcore.xcframework`. |

На iOS VPN, обход и TG-прокси работают в одном расширении: приложение передаёт ему настройки, и режим
переключается без переподключения. Поэтому, пока включён только TG-прокси, в строке состояния виден значок
VPN, но трафик идёт как обычно.

Проект Xcode генерирует [XcodeGen](https://github.com/yonaskolb/XcodeGen) из `project.yml`.

## Сборка (macOS)

```sh
cd Android/core
go install github.com/sagernet/gomobile/cmd/gomobile@v0.1.13 github.com/sagernet/gomobile/cmd/gobind@v0.1.13
gomobile bind -target ios -iosversion 15.0 \
  -tags with_gvisor,with_quic,with_wireguard,with_utls,with_clash_api,badlinkname,tfogo_checklinkname0 \
  -trimpath -ldflags "-s -w -checklinkname=0" -o ../../iOS/Bhcore.xcframework ./bhcore
cd ../../iOS
xcodegen generate
xcodebuild -project BypassHub.xcodeproj -target BypassHub -configuration Release -sdk iphoneos \
  CODE_SIGNING_ALLOWED=NO build
```

Готовое приложение — `build/Release-iphoneos/BypassHub.app`; IPA — это zip с папкой `Payload/BypassHub.app`.

## Лицензия

Как и Android-версия, использует sing-box (GPL-3.0) и распространяется по лицензии GPL-3.0.
