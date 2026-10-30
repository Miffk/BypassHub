# BypassHub — заметки для разработчиков

> Описание для пользователей, скриншоты и ответы на частые вопросы — в [README в корне репозитория](../README.md).

Программа для Windows и Linux, в которой собраны в одном окне:

- **[zapret-discord-youtube](https://github.com/Flowseal/zapret-discord-youtube)** — обход блокировок Discord, YouTube и др.;
- **[tg-ws-proxy](https://github.com/Flowseal/tg-ws-proxy)** — локальный MTProto-прокси, ускоряющий Telegram Desktop.
- **свой VPN** — ключи VLESS (sing-box) и AmneziaVPN/AmneziaWG (официальный клиент AmneziaWG), см. раздел «VPN» ниже.

## Возможности

**Главная** (`gui/home_page.py`) — три плитки `W.PowerTile` (Zapret, TG WS Proxy, VPN): нажатие включает или выключает сервис. Состояние каждого сервиса хранит `ServiceControl`: он обновляет плитку и шапку `ServiceCard` вверху страницы сервиса (переключатель + подробный статус). Логика переключения (`toggle_*`, `apply_vpn`, проверка доступности) живёт в `HomePage` — её вызывают страницы, трей и горячие клавиши.

**Zapret** — всё, что умеет `service.bat`, но кнопками:
- **автоподбор стратегии** прямо в программе: все стратегии запускаются по очереди, проверяются сайты из `utils/targets.txt` (TLS 1.2 и 1.3), лучшая применяется одной кнопкой;
- выбор стратегии (`general*.bat`) и режима запуска: «Программа» (winws.exe работает, пока открыт BypassHub) или «Служба Windows» (как *Install Service*);
- Game Filter (режим и диапазоны портов TCP/UDP), IPSet Filter (none / loaded / any), обновление IPSet-списка;
- замена активных фейков (Discord UDP, Game Filter UDP);
- **исключение программ** (Steam, Epic Games, Battle.net, Riot, EA, Ubisoft, FACEIT, Xbox) и своих доменов, плюс экспериментальное «обучение» по IP-адресам этих программ;
- редактор пользовательских списков (`list-general-user.txt`, `list-exclude-user.txt`, `ipset-exclude-user.txt`);
- обновление файла hosts (автоматически или вручную), диагностика с удалением конфликтующих служб и очисткой кэша Discord, тест стратегий, удаление служб.

**Telegram на телефоне** — кнопка «Подключить телефон»: BypassHub открывает прокси для домашней сети, сам добавляет правило в брандмауэр Windows и показывает QR-код — наведите камеру телефона, откройте ссылку в Telegram и подключитесь. Адрес компьютера выбирается автоматически (адаптеры VPN и виртуальных машин пропускаются). Работает, пока телефон в той же Wi-Fi-сети, а компьютер включён; «Отключить доступ» всё возвращает.

**TG WS Proxy** — все настройки из окна настроек прокси: адрес, порт, secret, DC → IP, Cloudflare-прокси (HTTP/2, свой домен), Cloudflare Worker, verbose, буфер, пул, размер лога, тестовые DC, тема и язык.

**Обновления** — программа сама проверяет GitHub обоих проектов и свои релизы (самообновление BypassHub) (при запуске и по расписанию), скачивает и ставит новые версии (SHA-256 сверяется с GitHub). Старые версии и временные файлы удаляются. Ваши настройки zapret (списки, Game Filter, IPSet, фейки, свои стратегии) переносятся, работающий обход перезапускается.

**Оформление** — тёмная и светлая темы (или как в Windows), готовые наборы цветов и свои цвета (в окне выбора: до трёх цветов градиента прямо в нём, квадрат насыщенности/яркости, полоса оттенка, HEX; изменения видны сразу). Созданные темы сохраняются в «Мои темы» — их можно выбрать одним кликом, переименовать (✎ или двойной клик по имени) или удалить. Выбранные цвета окрашивают всё окно: фон страниц и боковой панели становится плавным градиентом, карточки и кнопки подстраиваются под него. Ползунок «Насыщенность фона» регулирует силу окраски. Тема и цвета меняются на лету, с плавным переходом.

**Горячие клавиши** — работают в любой программе, даже когда окно свёрнуто в трей. По умолчанию: `Ctrl+Alt+Z` — zapret вкл/выкл, `Ctrl+Alt+T` — TG WS Proxy вкл/выкл, `Ctrl+Alt+B` — показать/скрыть окно; для проверки доступности сервисов сочетание можно назначить самому. Все сочетания меняются в «Настройках» (нажать на сочетание и затем новые клавиши), отключаются по отдельности или все сразу. Если сочетание занято другой программой, BypassHub об этом скажет. Когда окно скрыто, результат переключения показывается уведомлением в трее.

**Резервная копия** — экспорт и импорт всех настроек (BypassHub, zapret, TG WS Proxy) в один JSON-файл.

**Прочее** — автозапуск при включении компьютера (через Планировщик заданий, без запроса UAC), значок в трее с быстрыми переключателями, журнал событий.

## Установка

1. Скачайте `BypassHub.exe` со страницы релизов (собирается GitHub Actions — см. ниже).
2. Запустите. Программа попросит права администратора: они нужны zapret (драйвер WinDivert), службам и hosts.
3. При первом запуске zapret и tg-ws-proxy скачаются сами.

Данные хранятся в `C:\ProgramData\BypassHub` (путь без кириллицы и вне OneDrive — так zapret работает надёжнее):

```
C:\ProgramData\BypassHub\
  settings.json          настройки BypassHub
  zapret\                релиз zapret-discord-youtube (с вашими списками и настройками)
  tgwsproxy\             TgWsProxy.exe + TgWsProxy_data\config.json (портативный режим)
  logs\                  bypasshub.log, winws.log
```

Папку данных можно сменить параметром `--data-dir` или переменной окружения `BYPASSHUB_DATA`.

## Про исключение программ

winws (zapret) работает на уровне сетевых пакетов через WinDivert и не знает, какой программе принадлежит пакет, — фильтра «по процессу» у него нет. Поэтому исключение программы — это исключение её доменов (они дописываются отдельным блоком в `list-exclude-user.txt`). Если включён Game Filter, трафик игр, который ловится по IP-спискам, всё равно может попадать под обход.

Экспериментальный режим «обучения» дополнительно раз в 8 секунд смотрит локальную таблицу соединений Windows (сеть не нагружается) и дописывает адреса, к которым подключены отмеченные программы, в `ipset-exclude-user.txt`. zapret перечитывает списки сам, перезапуск не нужен. Первое подключение к новому адресу ещё проходит через обход; адреса Discord и YouTube не исключаются никогда.

## Самообновление

BypassHub проверяет релизы этого репозитория с тегами `bypasshub-v*` и, если там есть более новый `BypassHub.exe`, скачивает его (с проверкой SHA-256), подменяет файл и перезапускается. Чтобы выпустить версию: поменять `__version__` в `bypasshub/__init__.py`, затем на GitHub открыть **Actions → BypassHub build → Run workflow**, выбрать ветку и вписать ту же версию (например `1.1.0`) — workflow соберёт exe и опубликует релиз с тегом `bypasshub-v1.1.0`. Можно и запушить такой тег вручную.

## Сборка из исходников

Нужен Windows и Python 3.10+:

```bat
cd BypassHub
build.bat
```

Готовый файл: `dist\BypassHub.exe`. В репозитории есть workflow `.github/workflows/bypasshub.yml`: он запускает тесты и собирает exe при каждом пуше (артефакт `BypassHub-windows`), а при теге `bypasshub-v*` публикует релиз.

Запуск без сборки: `pip install -r requirements.txt` и `python main.py`. Тесты: `python -m pytest`.

## Интерфейс

- Окно написано на Qt (PySide6): `bypasshub/qtui/` — `app.py` (главное окно, трей, фоновые задачи),
  `style.py` (таблица стилей из палитры темы), `widgets.py` (карточки, переключатели, плитки),
  страницы `home.py`, `zapret_page.py`, `tg_page.py`, `vpn_page.py`, `updates_page.py`, `settings_page.py`,
  `log_page.py` и окна `dialogs.py`. Цвета тем — `bypasshub/palette.py` (общие для обоих интерфейсов).
- Прежнее окно на customtkinter (`bypasshub/gui/`) осталось запасным: оно открывается, если Qt не смог
  загрузиться, или при запуске с ключом `--classic-ui`.
- Проверка плавности на настоящей Windows — workflow `ui-check.yml` (`tools/ui_check.py`): время переключения
  вкладок и шага прокрутки, снимки окна.

## VPN

- `bypasshub/vpn.py` — ключи VLESS (`parse_vless`), подписки, профили (`Profile`, `parse_input`), сборка config.json
  для sing-box (`build_config`) и `VpnManager` (запуск sing-box, системный прокси Windows, базы roscomvpn).
- Кроме VLESS, sing-box получает серверы Hysteria2: ключи `hysteria2://` / `hy2://` и исходящие `hysteria` из
  подписок в формате Xray JSON (`parse_hysteria2`, `_xray_hysteria_link`). Задержку до них без VPN не меряем — это UDP.
- `bypasshub/awg.py` — ключи AmneziaVPN `vpn://` (base64url + qCompress + JSON, конфиг в `containers[].awg.last_config`),
  `.conf`, `AwgManager`: `amneziawg.exe` достаётся из официального .msi (`msiexec /a`), туннель ставится службой
  `amneziawg.exe /installtunnelservice` (служба `AmneziaWGTunnel$BypassHub`).
- Раздельное туннелирование для AmneziaWG: туннель с `Table = off` + маршрут через него с метрикой 9000, sing-box
  (TUN) отправляет «зарубежное» в outbound `direct` с `bind_interface: BypassHub`, адрес сервера AmneziaWG — мимо TUN.
- sing-box обновляется только в пределах `vpn.MIN_VERSION`…`vpn.MAX_VERSION` — формат конфига проверен на 1.14.

## Linux

- `bypasshub/linux.py` — всё, что отличается в Linux: один экземпляр (flock), ярлыки `.desktop` (меню и автозапуск),
  системный прокси KDE Plasma (`kwriteconfig6`) и GNOME (`gsettings`), правила перехвата для nfqws
  (`firewall_script`: nftables, запасной путь — iptables), службы systemd, ufw/firewalld для доступа с телефона.
- Окно работает от пользователя. Права root — у **помощника**: `pkexec <программа> --root-helper <папка данных>`
  (`RootHelper` ↔ `serve`, одна JSON-строка на запрос/ответ по stdin/stdout). Команды — в `linux.HANDLERS`: запуск
  программ только из папки данных (`spawn`, `kill`), запись только в `/etc/hosts`, `/etc/bypasshub/`, `/run/bypasshub/`
  и `/etc/systemd/system/bypasshub-*.service`, внешние команды — только из `linux.RUN_ALLOWED`.
- zapret: релиз Flowseal даёт стратегии, списки и фейки, а nfqws берётся из релиза bol-van/zapret
  (`zapret-<тег>-openwrt-embedded.tar.gz`, `binaries/linux-x86_64/nfqws`) и кладётся в `zapret/bin/nfqws`.
  `to_nfqws_args` переводит аргументы winws: `--wf-tcp/--wf-udp` становятся портами в правилах nftables,
  остальные параметры у nfqws те же. nfqws запускается с `--qnum=200 --uid=<пользователь>`; служба —
  `bypasshub-zapret.service` с копией nfqws в `/usr/local/lib/bypasshub/`.
- sing-box: `sing-box-<версия>-linux-amd64.tar.gz`; «Весь трафик» (TUN) запускает помощник, «Системный прокси» —
  от пользователя. AmneziaWG — системный `awg-quick` (amneziawg-tools), через помощника.
- Горячие клавиши: `BypassHub --action toggle_zapret|toggle_tg|toggle_vpn|show_window|check_services` — запущенная
  копия выполняет действие (файл `action-*.flag` в папке данных).
- Сборка: `./build.sh` или job `linux` в `bypasshub.yml` (Ubuntu 22.04, файл `BypassHub-linux-x86_64`).
