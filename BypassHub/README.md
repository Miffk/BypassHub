# BypassHub

Программа для Windows, в которой собраны в одном окне:

- **[zapret-discord-youtube](https://github.com/Flowseal/zapret-discord-youtube)** — обход блокировок Discord, YouTube и др.;
- **[tg-ws-proxy](https://github.com/Flowseal/tg-ws-proxy)** — локальный MTProto-прокси, ускоряющий Telegram Desktop.

## Возможности

**Главная** — два переключателя «вкл/выкл» (zapret и TG WS Proxy), состояние, выбор стратегии, кнопка «Открыть в Telegram».

**Zapret** — всё, что умеет `service.bat`, но кнопками:
- выбор стратегии (`general*.bat`) и режима запуска: «Программа» (winws.exe работает, пока открыт BypassHub) или «Служба Windows» (как *Install Service*);
- Game Filter (режим и диапазоны портов TCP/UDP), IPSet Filter (none / loaded / any), обновление IPSet-списка;
- замена активных фейков (Discord UDP, Game Filter UDP);
- **исключение программ** (Steam, Epic Games, Battle.net, Riot, EA, Ubisoft, FACEIT, Xbox) и своих доменов;
- редактор пользовательских списков (`list-general-user.txt`, `list-exclude-user.txt`, `ipset-exclude-user.txt`);
- обновление файла hosts (автоматически или вручную), диагностика с удалением конфликтующих служб и очисткой кэша Discord, тест стратегий, удаление служб.

**TG WS Proxy** — все настройки из окна настроек прокси: адрес, порт, secret, DC → IP, Cloudflare-прокси (HTTP/2, свой домен), Cloudflare Worker, verbose, буфер, пул, размер лога, тестовые DC, тема и язык.

**Обновления** — программа сама проверяет GitHub обоих проектов (при запуске и по расписанию), скачивает и ставит новые версии (SHA-256 сверяется с GitHub). Старые версии и временные файлы удаляются. Ваши настройки zapret (списки, Game Filter, IPSet, фейки, свои стратегии) переносятся, работающий обход перезапускается.

**Оформление** — тёмная и светлая темы (или как в Windows), готовые наборы цветов и свои цвета: если выбрать 2–3 цвета, кнопки, переключатели, меню и шапка заливаются градиентом. Прозрачный фон окна: ползунок непрозрачности, размытие того, что под окном (вкл/выкл и ползунок силы).

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

## Сборка из исходников

Нужен Windows и Python 3.10+:

```bat
cd BypassHub
build.bat
```

Готовый файл: `dist\BypassHub.exe`. В репозитории есть workflow `.github/workflows/bypasshub.yml`: он запускает тесты и собирает exe при каждом пуше (артефакт `BypassHub-windows`), а при теге `bypasshub-v*` публикует релиз.

Запуск без сборки: `pip install -r requirements.txt` и `python main.py`. Тесты: `python -m pytest`.
