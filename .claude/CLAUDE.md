# BypassHub — заметки для Claude

Код программы — в папке `BypassHub/` (подробности в `BypassHub/README.md`), версия для Android — в `Android/` (`Android/README.md`), для iOS — в `iOS/` (`iOS/README.md`); версия у всех берётся из того же `__version__`. Тесты: `cd BypassHub && python -m pytest -q`.

## Выпуск новой версии

Владелец разрешил доводить работу до релиза самому (разрешения — в `.claude/settings.json`).
Когда работа закончена:
1. Прогони тесты затронутых частей (ядро: `cd Android/core && go test -tags with_gvisor,with_quic,with_wireguard,with_utls,with_clash_api,badlinkname,tfogo_checklinkname0 -ldflags=-checklinkname=0 ./bhcore/`; Windows: `cd BypassHub && python -m pytest -q`).
2. Посмотри последний релиз в Releases и подними `__version__` в `BypassHub/bypasshub/__init__.py` выше него — один номер дважды не выпускать.
3. Закоммить, запушь, открой pull request в основную ветку (`claude/focused-archimedes-3ibw3w`).
4. Дождись зелёной сборки BypassHub build. Красная — чини, не сливай.
5. Слей pull request и запусти релиз: workflow `bypasshub.yml`, ветка — основная, release = номер версии.
6. Проверь, что релиз появился в Releases, и коротко напиши владельцу: что вошло, номер версии, что проверить. Не получилось — скажи, почему.
