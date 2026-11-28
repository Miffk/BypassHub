"""Страница Zapret — всё, что умеет service.bat, кнопками (как qtui/zapret_page.py)."""
from __future__ import annotations

import os
import subprocess

from ... import winutil, zapret_diag
from ...zapret import APP_EXCLUSIONS, FAKE_TARGETS, USER_LISTS, GameFilter, validate_ports
from .. import ui
from .home import zapret_state
from .sheets import DiagnosticsSheet, StrategySheet, TextSheet

GAME_LABELS = {"disabled": "Выключен", "all": "TCP и UDP", "tcp": "Только TCP", "udp": "Только UDP"}
IPSET = ["none", "loaded", "any"]
if winutil.IS_LINUX:
    MODE_LABELS = {"process": "Программа (nfqws)", "service": "Служба systemd"}
    ENGINE, ENGINE_INFO = "nfqws", "(nfqws + nftables)"
    MODE_HINT = ("«Программа» — nfqws работает, пока запущен BypassHub (при включении система спросит пароль "
                 "администратора — один раз за сеанс). «Служба systemd» — обход работает сам с момента включения "
                 "компьютера, без BypassHub и без пароля.")
else:
    MODE_LABELS = {"process": "Программа (winws.exe)", "service": "Служба Windows"}
    ENGINE, ENGINE_INFO = "winws.exe", "(winws.exe + WinDivert)"
    MODE_HINT = ("«Программа» — winws.exe работает, пока запущен BypassHub. «Служба Windows» — как Install "
                 "Service в service.bat: обход работает сам с момента включения ПК.")


def _versions(app):
    """Список версий zapret — загружается один раз, при первом открытии страницы."""
    st = app.page_state["zapret"]
    if "releases" not in st and "versions" not in app.busy:
        def done(releases):
            st["releases"] = list(releases or [])
        app.run("versions", app.core.zapret_versions, done, "", "Версии zapret")
    return st.get("releases")


def build(app):
    core, zs = app.core, app.zs
    zm = core.zapret
    installed = zm.is_installed()
    head = zapret_state(app)
    color = "yellow" if head.get("busy") or head.get("warn") else ("green" if head["on"] else "muted")
    details = ""
    if zs.running:
        mode = ("служба systemd" if winutil.IS_LINUX else "служба Windows") if zs.service_installed else "программа"
        details = f"Режим: {mode} · версия {zs.version}"
        if zs.foreign_processes:
            details += f". Внимание: запущен {zm.engine_name} из другой папки: " + ", ".join(zs.foreign_processes)
    blocks = [ui.service("zapret", "Zapret", "shield", head["on"], head["status"], color,
                         "Обход блокировок Discord, YouTube и других сервисов " + ENGINE_INFO, details,
                         busy=bool(head.get("busy")),
                         buttons=[ui.button("pick", "Подобрать стратегию", primary=True, disabled=not installed),
                                  ui.button("check", "Проверить доступность")])]
    if not installed:
        blocks.append(ui.notice("zapret ещё не загружен — скачается сам или по кнопке во вкладке «Обновления»."))

    names = zm.strategies() if installed else []
    cur = core.strategy
    service_line = ""
    if zs.service_installed:
        service_line = f"Служба zapret: {zs.service_state}" + (f" · стратегия {zs.service_strategy}"
                                                               if zs.service_strategy else "")
    blocks.append(ui.section("Стратегия", [
        ui.select("strategy", names or ["—"], cur if cur in names else (names[0] if names else "—"), "Стратегия"),
        ui.segmented("mode", list(MODE_LABELS.values()), MODE_LABELS[core.zapret_mode], "Режим"),
        ui.text(service_line, "muted") if service_line else None,
    ], help=MODE_HINT))

    sel, custom = zm.app_exclusions() if installed else ([], [])
    learned = len(core.learner.learned())
    n = len(sel) + len(custom)
    blocks.append(ui.section("Без обхода", [
        ui.checks("apps", [{"value": a, "label": a, "checked": a in sel} for a in APP_EXCLUSIONS]),
        ui.field("domains", ", ".join(custom), "Свои домены", "example.com, game.net"),
        ui.toggle("learn", "Также исключать IP-адреса этих программ (эксперимент)",
                  core.settings.get("zapret", "learn_apps"),
                  help="Раз в 8 секунд BypassHub смотрит, к каким адресам подключены отмеченные программы (только "
                       "локальная таблица соединений — сеть не нагружается), и добавляет их в "
                       "ipset-exclude-user.txt. Первое подключение к новому адресу ещё идёт через обход. Адреса "
                       "Discord и YouTube не исключаются."),
        ui.text(f"Выучено адресов: {learned}", "small") if learned else None,
        ui.buttons(ui.button("apps.apply", "Применить", primary=True, collect=True),
                   ui.button("learn.clear", "Забыть выученные адреса")),
    ], hint="Программы и домены, которым zapret не нужен.",
        help=f"Например, если Steam с включённым обходом грузит медленнее. {ENGINE} работает на уровне сетевых "
             "пакетов и не видит, какой программе они принадлежат, поэтому исключаются домены этих программ.",
        collapsible=True, key="zapret.apps", note=f"исключено: {n}" if n else ""))

    blocks.append(ui.group("Дополнительно"))
    gf = zm.load_game_filter()
    blocks.append(ui.section("Game Filter", [
        ui.segmented("game.mode", list(GAME_LABELS.values()), GAME_LABELS.get(gf.mode, GAME_LABELS["disabled"]),
                     "Режим", local=True),
        ui.field("game.tcp", gf.tcp, "Порты TCP", "1024-65535"),
        ui.field("game.udp", gf.udp, "Порты UDP", "1024-65535",
                 help="Диапазон или несколько через запятую: 1024-1934,1936-65535"),
        ui.buttons(ui.button("game.save", "Сохранить", primary=True, collect=True)),
    ], help="Обход для игр и других сервисов на портах TCP/UDP выше 1023. Включайте, только если не работает игра.",
        collapsible=True, key="zapret.game",
        note=GAME_LABELS.get(gf.mode, "").lower() if gf.mode != "disabled" else "выключен"))

    ipset = zm.ipset_status() if installed else ""
    blocks.append(ui.section("IPSet Filter", [
        ui.segmented("ipset", IPSET, ipset, "Режим"),
        ui.buttons(ui.button("ipset.update", "Обновить IPSet-список", disabled="ipset" in app.busy)),
    ], help="none — IP-адреса не проверяются; loaded — обход для IP из списка ipset-all.txt; any — для любых IP. "
            "Если перестало работать то, что работает без zapret, поставьте none.",
        collapsible=True, key="zapret.ipset", note=ipset))

    if installed:
        zm.ensure_user_lists()
    st = app.page_state["zapret"]
    tab = st.get("list", next(iter(USER_LISTS)))
    desc = USER_LISTS[tab][0]
    blocks.append(ui.section("Свои списки", [
        ui.segmented("list.tab", list(USER_LISTS), tab),
        ui.text(desc, "muted"),
        ui.textarea("list.text", zm.read_user_list(tab) if installed else "", rows=7),
        ui.buttons(ui.button("list.save", "Сохранить", primary=True, collect=True)),
    ], help="Свои домены и исключения. Файлы сохраняются при обновлении zapret.", collapsible=True,
        key="zapret.lists"))

    hosts = st.get("hosts", ("Статус не проверялся", ""))
    blocks.append(ui.section("Файл hosts", [
        ui.text(hosts[0], color=hosts[1]),
        ui.buttons(ui.button("hosts.check", "Проверить"),
                   ui.button("hosts.apply", "Обновить", primary=True,
                             confirm="Добавить в hosts актуальные записи из репозитория zapret-discord-youtube?\n\n"
                                     "Некоторые антивирусы реагируют на изменение hosts — в этом случае используйте "
                                     "ручной вариант."),
                   ui.button("hosts.manual", "Вручную" if winutil.IS_LINUX else "Вручную (блокнот)"),
                   ui.button("hosts.remove", "Удалить записи")),
    ], help="Нужен для веб-версии Telegram и голосовых каналов Discord (аналог «Update Hosts File»). Записи "
            "добавляются отдельным блоком, перед изменением создаётся копия "
            + ("/etc/bypasshub/hosts.bak." if winutil.IS_LINUX else "hosts.bypasshub.bak."),
        collapsible=True, key="zapret.hosts", note=hosts[0] if len(hosts[0]) <= 40 else ""))

    pinned = core.settings.get("zapret", "pinned_version")
    vtext = (f"Установлена версия {zm.local_version()} — " + ("выбрана вручную, не обновляется" if pinned
                                                               else "обновляется автоматически")
             if installed else "zapret ещё не загружен")
    releases = _versions(app) if (app.core.settings.get("ui", "expanded") or []).count("zapret.version") else \
        st.get("releases")
    if releases:
        local = zm.local_version() if installed else ""
        opts = [r.version + (" — последняя" if i == 0 else "") for i, r in enumerate(releases)]
        current = next((o for o, r in zip(opts, releases) if r.version == local), opts[0])
        version_items = [ui.select("version", opts, st.get("version_pick", current), "Версия"),
                         ui.buttons(ui.button("version.install", "Установить", primary=True,
                                              disabled="zapret" in app.busy),
                                    ui.button("version.reload", "Обновить список"))]
    else:
        version_items = [ui.text("Загрузка списка версий…" if "versions" in app.busy or releases is None
                                 else "Не удалось загрузить список версий", "muted"),
                         ui.buttons(ui.button("version.reload", "Обновить список"))]
    blocks.append(ui.section("Версия zapret", [ui.text(vtext, "muted")] + version_items,
                             help="Если на новой версии обход стал работать хуже, поставьте одну из прошлых. "
                                  "Выбранная версия не обновляется сама; чтобы снова получать обновления, "
                                  "установите последнюю.",
                             collapsible=True, key="zapret.version", note=zm.local_version() if installed else ""))

    fakes = zm.fake_files() if installed else []
    active = zm.active_fakes() if installed else {}
    fake_items = []
    for kind, (_, title) in FAKE_TARGETS.items():
        values = list(fakes or ["—"])
        cur = active.get(kind) or "(свой файл)"
        if cur not in values:
            values = [cur] + values
        fake_items.append(ui.select(f"fake.{kind}", values, cur, title))
    blocks.append(ui.section("Активные фейки", fake_items,
                             help="Какой файл из папки bin подставляется в стратегии как ACTIVE_*.bin (аналог "
                                  "«Replace active fakes»).", collapsible=True, key="zapret.fakes"))

    tools = [ui.button("diag", "Диагностика", icon="stethoscope"),
             ui.button("services.remove", "Удалить службу" if winutil.IS_LINUX else "Удалить службы",
                       confirm="Остановить обход и удалить службу zapret?" if winutil.IS_LINUX
                       else "Остановить обход и удалить службы zapret и WinDivert?"),
             ui.button("folder", "Папка zapret", icon="folder"),
             ui.button("winws.log", f"Лог {ENGINE}"), ui.button("command", "Показать команду", icon="terminal")]
    if winutil.IS_WINDOWS:
        tools.append(ui.button("tests", "Тест в PowerShell",
                               confirm="Расширенный тест из zapret-discord-youtube (в том числе DPI checkers). Текущий "
                                       "обход будет выключен, откроется окно PowerShell — следуйте его инструкциям. "
                                       "Для обычного подбора удобнее «Подобрать стратегию».\n\nНачать?"))
    blocks.append(ui.section("Инструменты", [ui.buttons(*tools)], collapsible=True, key="zapret.tools"))
    return ui.page("Zapret", blocks)


# ------------------------------------------------------------------ действия

def _need_installed(app):
    if not app.core.zapret.is_installed():
        return ui.error("zapret ещё не загружен")
    return None


def _strategy(app, value):
    if value and value != "—":
        app.core.settings.set("zapret", "strategy", value)
        app.apply_zapret("стратегия")


def _mode(app, value):
    app.core.settings.set("zapret", "mode", "service" if value == MODE_LABELS["service"] else "process")
    app.apply_zapret("режим")


def _apps_apply(app, form):
    err = _need_installed(app)
    if err:
        return err
    form = form or {}
    names, _ = app.core.zapret.app_exclusions()  # флажки программ сохраняются сразу
    custom = [d for d in str(form.get("domains", "")).replace(";", ",").replace(" ", ",").split(",") if d.strip()]
    app.core.zapret.set_app_exclusions(names, custom)
    app.core.update_learner()
    return ui.done("Исключения применены — zapret сам перечитывает списки", clear=["domains"])


def _apps_check(app, value):
    """Флажок программы — сразу, без кнопки «Применить» (остальное — из текущих настроек)."""
    zm = app.core.zapret
    if not zm.is_installed():
        return ui.error("zapret ещё не загружен")
    names, custom = zm.app_exclusions()
    v = value or {}
    names = [n for n in APP_EXCLUSIONS if (n in names and n != v.get("value")) or (n == v.get("value") and v.get("on"))]
    zm.set_app_exclusions(names, custom)
    app.core.update_learner()
    return {}


def _learn(app, on):
    app.core.settings.set("zapret", "learn_apps", bool(on))
    app.core.update_learner()


def _learn_clear(app, _):
    app.core.learner.clear()
    return ui.done("Выученные адреса забыты")


def _game_save(app, form):
    form = form or {}
    mode = next((k for k, v in GAME_LABELS.items() if v == form.get("game.mode")), "disabled")
    tcp, udp = validate_ports(str(form.get("game.tcp", ""))), validate_ports(str(form.get("game.udp", "")))
    if tcp is None or udp is None:
        return ui.error("Неверный диапазон портов. Пример: 1024-65535 или 1024-1934,1936-65535")
    app.core.zapret.save_game_filter(GameFilter(mode, tcp, udp))
    app.apply_zapret("Game Filter")
    return ui.done("Game Filter сохранён", clear=["game.tcp", "game.udp"])


def _ipset(app, mode):
    zm = app.core.zapret
    try:
        zm.set_ipset_mode(mode)
    except FileNotFoundError:
        return ui.ask("Сохранённого списка IP нет. Скачать актуальный ipset-all.txt?", "ipset.update")
    return {}


def _ipset_update(app, _):
    app.run("ipset", app.core.zapret.update_ipset_list,
            lambda count: f"IPSet-список обновлён ({count} записей). Режим: loaded.", "", "IPSet")


def _list_tab(app, name):
    if name in USER_LISTS:
        app.page_state["zapret"]["list"] = name
    return {"clear": ["list.text"]}


def _list_save(app, form):
    name = app.page_state["zapret"].get("list", next(iter(USER_LISTS)))
    app.core.zapret.write_user_list(name, str((form or {}).get("list.text", "")))
    return ui.done(f"{name} сохранён — zapret подхватит изменения сам", clear=["list.text"])


def _hosts_set(app, text, color):
    app.page_state["zapret"]["hosts"] = (text, color)


def _hosts_check(app, _):
    def done(res):
        needs, _ = res
        _hosts_set(app, "hosts нужно обновить" if needs else "hosts актуален", "yellow" if needs else "green")
    app.run("hosts", app.core.zapret.hosts_check, done, "", "Файл hosts")


def _hosts_apply(app, _):
    zm = app.core.zapret

    def work():
        _, block = zm.hosts_check()
        zm.hosts_apply(block)

    def done(_):
        _hosts_set(app, "hosts обновлён", "green")
        return "hosts обновлён"
    app.run("hosts", work, done, "", "Файл hosts")


def _hosts_manual(app, _):
    zm = app.core.zapret

    def work():
        _, block = zm.hosts_check()
        tmp = app.core.paths.downloads / "zapret_hosts.txt"
        tmp.write_text(block, encoding="utf-8")
        return tmp

    def done(tmp):
        if winutil.IS_WINDOWS:
            subprocess.Popen(["notepad", str(tmp)])
            subprocess.Popen(["explorer", "/select,", str(zm.hosts_path())])
            app.alert("Скопируйте текст из блокнота в конец файла hosts (редактор нужно открыть от имени "
                      "администратора) и сохраните его.", "Файл hosts")
        else:
            winutil.open_path(tmp)
            app.alert(f"Скопируйте текст из открывшегося файла в конец /etc/hosts (например, командой «sudo nano "
                      f"/etc/hosts» или sudoedit) и сохраните его.\n\nФайл с записями: {tmp}", "Файл hosts")
    app.run("hosts", work, done, "", "Файл hosts")


def _hosts_remove(app, _):
    app.run("hosts", app.core.zapret.hosts_remove,
            lambda removed: "Записи удалены" if removed else "Записей BypassHub в hosts нет", "", "Файл hosts")


def _version_pick(app, value):
    app.page_state["zapret"]["version_pick"] = value


def _version_install(app, _):
    st = app.page_state["zapret"]
    releases = st.get("releases") or []
    pick = st.get("version_pick")
    opts = [r.version + (" — последняя" if i == 0 else "") for i, r in enumerate(releases)]
    i = opts.index(pick) if pick in opts else 0
    if not releases:
        return {}
    rel, latest = releases[i], i == 0
    app.run("zapret", lambda: app.core.zapret_install_version(rel, latest),
            lambda _: app.alert(f"Установлен zapret {rel.version}." + (
                "" if latest else " Он не будет обновляться автоматически, пока вы не установите последнюю версию."),
                "Zapret"), f"Установка zapret {rel.version}…", "Zapret")


def _version_reload(app, _):
    app.page_state["zapret"].pop("releases", None)
    _versions(app)


def _fake(kind):
    def fn(app, value):
        if value in ("—", "(свой файл)"):
            return {}
        app.core.zapret.set_active_fake(kind, value)
        app.apply_zapret("фейк")
        return {}
    return fn


def _pick(app, _):
    err = _need_installed(app)
    if err:
        return err
    app.sheet = StrategySheet(app)
    return {}


def _diag(app, _):
    root = app.core.zapret.root

    def done(checks):
        app.sheet = DiagnosticsSheet(app, checks)
    app.run("diag", lambda: zapret_diag.run_diagnostics(root), done, "", "Диагностика")


def _services_remove(app, _):
    def work():
        app.core.settings.set("zapret", "enabled", False)
        app.core.zapret.remove_services()
    app.run("zapret", work, lambda _: "Службы удалены", "Удаление служб…", "Zapret")


def _winws_log(app, _):
    p = app.core.paths.winws_log
    if p.exists():
        winutil.open_path(p)
        return {}
    return ui.info("Лог появится после запуска в режиме «Программа».")


def _command(app, _):
    cmd = app.core.zapret.command_line(app.core.strategy)
    app.sheet = TextSheet(app, f"Команда запуска {ENGINE}", cmd.replace(" --new ", " --new" + os.linesep))
    return {}


def _tests(app, _):
    err = _need_installed(app)
    if err:
        return err

    def work():
        app.core.zapret_disable()
        app.core.zapret.run_tests()
    app.run("zapret", work, None, "Тест в PowerShell…", "Zapret")


ACTIONS = {
    "strategy": _strategy, "mode": _mode, "apps": _apps_check, "apps.apply": _apps_apply, "learn": _learn,
    "learn.clear": _learn_clear, "game.save": _game_save, "ipset": _ipset, "ipset.update": _ipset_update,
    "list.tab": _list_tab, "list.save": _list_save, "hosts.check": _hosts_check, "hosts.apply": _hosts_apply,
    "hosts.manual": _hosts_manual, "hosts.remove": _hosts_remove, "version": _version_pick,
    "version.install": _version_install, "version.reload": _version_reload, "pick": _pick, "diag": _diag,
    "services.remove": _services_remove, "folder": lambda app, v: winutil.open_path(app.core.zapret.root),
    "winws.log": _winws_log, "command": _command, "tests": _tests,
    **{f"fake.{k}": _fake(k) for k in FAKE_TARGETS},
}
