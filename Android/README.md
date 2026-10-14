# BypassHub для Android

Приложение для телефонов, планшетов и Android TV (Android 7.0 и новее). На главной — две кнопки:
**VPN** и **Обход блокировок**.

### Обход блокировок — без VPN и без root

- YouTube, Discord, Twitch и другие сайты из списков [zapret-discord-youtube](https://github.com/Flowseal/zapret-discord-youtube)
  открываются без VPN-сервера: трафик идёт напрямую, а к этим сайтам приложение разбивает начало защищённого
  соединения (TLS ClientHello) на части — провайдер не видит имя сайта и не замедляет его;
- QUIC к этим сайтам отключается, чтобы приложения (YouTube и др.) шли по TCP, где разбиение работает;
- два способа разбиения — TCP-пакеты и TLS-записи: если один не помогает у вашего провайдера, попробуйте другой;
- свои сайты и режим «применять ко всем сайтам» (кроме российских);
- DNS — через Cloudflare по HTTPS, чтобы провайдер не подменял адреса заблокированных сайтов.

Голосовые звонки Discord и игры (UDP) так не ускорить — для них нужен VPN. Android разрешает только одно
VPN-подключение, поэтому обход и VPN работают по очереди: включение одного выключает другое.

### VPN — тот же, что в BypassHub для Windows

- ключи **VLESS** (`vless://…`, в том числе Reality, WebSocket, gRPC), списки ключей и ссылки-подписки;
- ключи **AmneziaVPN** (`vpn://…`) и конфиги **AmneziaWG** (`.conf`, в том числе AmneziaWG 3.1);
- **раздельное туннелирование**: зарубежное — через VPN, российское (`.ru`, `.рф`, `.su`, российские IP
  и российские сервисы на других доменах, по базам roscomvpn) — напрямую;
- несколько серверов VLESS: пинг, выбор сервера и **«Авто»** — подключение к лучшему, переключение без
  разрыва VPN;
- плитка в шторке быстрых настроек, ссылки `vless://` и `vpn://` открываются сразу в приложении;
- на Android TV — значок в лаунчере и управление пультом;
- обновляется само из [релизов](https://github.com/Miffk/BypassHub/releases/latest).

Root не нужен. TG WS Proxy появится в следующих версиях.

## Установка

1. Скачайте `BypassHub-android.apk` со страницы **[Releases](https://github.com/Miffk/BypassHub/releases/latest)**.
2. Откройте файл и разрешите установку из этого источника, если Android спросит.
3. Для обхода блокировок ничего настраивать не нужно — нажмите **Обход блокировок** на главной.
   Для своего VPN — вставьте ключ на вкладке **VPN** (или нажмите «Вставить из буфера») и нажмите **VPN** на главной.
   При первом включении Android попросит разрешение на VPN-подключение — так работают оба режима.

**Android TV.** Удобнее всего: скачайте APK на телефон и передайте на телевизор через
[Send Files to TV](https://play.google.com/store/apps/details?id=com.yablio.sendfilestotv) или флешку
и установите любым файловым менеджером. Ключ можно ввести пультом или вставить через «Вставить из буфера»,
если клавиатура телевизора синхронизирована с телефоном.

## Как устроено

| Папка | Что там |
|---|---|
| `core/` | Ядро на Go: [sing-box](https://github.com/SagerNet/sing-box) и [amneziawg-go](https://github.com/amnezia-vpn/amneziawg-go). Собирается в `bhcore.aar` через [gomobile](https://github.com/SagerNet/gomobile). |
| `app/` | Приложение на Kotlin, без сторонних библиотек: интерфейс, `VpnService`, конфиг sing-box, обновления. |

sing-box получает TUN от `VpnService` и маршрутизирует трафик. AmneziaWG работает в userspace
(netstack) и отдаёт sing-box локальный SOCKS5 — так раздельное туннелирование одинаково работает и для VLESS,
и для AmneziaWG. Само приложение исключено из VPN, чтобы его соединения (сервер AmneziaWG, обновления)
не зацикливались.

Версия приложения берётся из `BypassHub/bypasshub/__init__.py` — у Windows и Android она общая, и оба файла
выходят в одном релизе.

## Сборка

Нужны JDK 17, Go (версия — в `core/go.mod`), Android SDK и NDK.

```sh
cd Android/core
go install github.com/sagernet/gomobile/cmd/gomobile@v0.1.13 github.com/sagernet/gomobile/cmd/gobind@v0.1.13
gomobile bind -target android/arm,android/arm64,android/amd64 -androidapi 21 \
  -javapkg=io.github.miffk.bypasshub \
  -tags with_gvisor,with_quic,with_wireguard,with_utls,with_clash_api,badlinkname,tfogo_checklinkname0 \
  -trimpath -ldflags "-s -w -checklinkname=0" -o ../app/libs/bhcore.aar ./bhcore
cd ..
./gradlew assembleRelease
```

Тесты ядра: `cd core && go test -tags with_gvisor,with_quic,with_wireguard,with_utls,with_clash_api,badlinkname,tfogo_checklinkname0 -ldflags=-checklinkname=0 ./bhcore/`.

## Ключ подписи для релизов (один раз, для владельца репозитория)

Android ставит обновление поверх старой версии, только если оба APK подписаны **одним и тем же ключом**.
Поэтому CI подписывает APK вашим ключом из секретов репозитория. Пока секретов нет, APK собирается,
но в релиз не попадает (в Actions будет предупреждение).

1. Создайте ключ (нужна Java: например, `winget install EclipseAdoptium.Temurin.17.JDK`):

   ```bat
   keytool -genkeypair -v -keystore bypasshub.jks -alias bypasshub -keyalg RSA -keysize 4096 -validity 36500
   ```

   Если `keytool` не находится — закройте и снова откройте PowerShell (после установки Java). Придумайте
   пароль и запомните его. Имя, организация и город попадут в сертификат, который виден в APK, — можно указать
   просто псевдоним, остальное оставить пустым.

2. Превратите файл в текст (PowerShell, результат окажется в буфере обмена):

   ```powershell
   [Convert]::ToBase64String([IO.File]::ReadAllBytes("$PWD\bypasshub.jks")) | Set-Clipboard
   ```

3. GitHub → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**, четыре секрета:

   | Имя | Значение |
   |---|---|
   | `BH_KEYSTORE_BASE64` | текст из шага 2 |
   | `BH_KEYSTORE_PASSWORD` | пароль из шага 1 |
   | `BH_KEY_ALIAS` | `bypasshub` |
   | `BH_KEY_PASSWORD` | тот же пароль |

4. Сохраните `bypasshub.jks` и пароль в надёжном месте и **никуда не выкладывайте**. Если ключ потерять,
   новые версии не встанут поверх старых — придётся удалять приложение и ставить заново.

## Лицензия

Android-версия использует sing-box (GPL-3.0), поэтому распространяется по лицензии
[GPL-3.0](LICENSE). amneziawg-go — MIT, базы раздельного туннелирования —
[roscomvpn](https://github.com/hydraponique/roscomvpn-routing).
