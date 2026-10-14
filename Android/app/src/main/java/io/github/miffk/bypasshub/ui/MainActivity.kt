package io.github.miffk.bypasshub.ui

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import io.github.miffk.bypasshub.AUTO
import io.github.miffk.bypasshub.BuildConfig
import io.github.miffk.bypasshub.Bypass
import io.github.miffk.bypasshub.LogBuffer
import io.github.miffk.bypasshub.Profile
import io.github.miffk.bypasshub.Rules
import io.github.miffk.bypasshub.Store
import io.github.miffk.bypasshub.Updater
import io.github.miffk.bypasshub.Vpn
import io.github.miffk.bypasshub.bhcore.Bhcore
import io.github.miffk.bypasshub.ui.Ui.dp
import io.github.miffk.bypasshub.ui.Ui.margins
import io.github.miffk.bypasshub.vpn.vpnPrepareIntent
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : Activity() {
    companion object {
        private const val REQ_VPN = 1
        private const val PING_MAX_AGE_MS = 5 * 60 * 1000L
    }

    private lateinit var content: FrameLayout
    private val tabs = linkedMapOf<String, TextView>()
    private var page = "home"
    private val main = Handler(Looper.getMainLooper())
    private var lastPing = 0L
    // живые данные (состояние, текущий сервер, пинг) есть только на вкладке VPN; остальные вкладки
    // перестраиваются по действиям — иначе стирался бы вводимый ключ и сбивалась прокрутка
    private val renderer = { if (page == "home") render() }
    private val logRenderer = { if (page == "log") renderLogText() }
    private var logView: TextView? = null
    private val poll = object : Runnable {
        override fun run() {
            if (Vpn.running && Vpn.kind != "bypass") Vpn.io.execute { Vpn.refreshGroup() }
            main.postDelayed(this, 3000)
        }
    }

    // ---------------------------------------------------------------- жизненный цикл
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = Ui.BG
        window.navigationBarColor = Ui.BG
        val root = Ui.vbox(this).apply { setBackgroundColor(Ui.BG) }

        val header = Ui.hbox(this).apply { setPadding(dp(20), dp(16), dp(20), dp(8)) }
        header.addView(Ui.text(this, "BypassHub", 24f, Ui.TEXT, true).apply {
            paint.shader = android.graphics.LinearGradient(0f, 0f, dp(150).toFloat(), 0f, Ui.ACCENT, Ui.ACCENT2,
                android.graphics.Shader.TileMode.CLAMP)
        })
        root.addView(header)

        // на узких экранах вкладки прокручиваются, на широких — растягиваются на всю ширину
        val tabScroll = android.widget.HorizontalScrollView(this).apply {
            isFillViewport = true
            isHorizontalScrollBarEnabled = false
        }
        val tabRow = Ui.hbox(this).apply { setPadding(dp(14), 0, dp(14), dp(8)) }
        tabScroll.addView(tabRow)
        for ((key, label) in listOf("home" to "Главная", "vpn" to "VPN", "bypass" to "Обход",
                                    "settings" to "Настройки", "log" to "Журнал")) {
            val t = Ui.text(this, label, 15f, Ui.MUTED, true).apply {
                gravity = Gravity.CENTER
                setPadding(dp(12), dp(10), dp(12), dp(10))
                isFocusable = true
                isClickable = true
                setOnClickListener { show(key) }
            }
            tabs[key] = t
            tabRow.addView(t, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginEnd = dp(6) })
        }
        root.addView(tabScroll)

        content = FrameLayout(this)
        root.addView(content, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        setContentView(root)

        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 2)
        }
        show("home")
        handleIntent(intent)
        Updater.checkInBackground(this)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    override fun onResume() {
        super.onResume()
        Vpn.listen(renderer)
        LogBuffer.listen(logRenderer)
        main.post(poll)
        render()
        maybePing()
    }

    override fun onPause() {
        Vpn.unlisten(renderer)
        LogBuffer.unlisten(logRenderer)
        main.removeCallbacks(poll)
        super.onPause()
    }

    @Deprecated("Activity API")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQ_VPN) {
            if (resultCode == RESULT_OK) Vpn.start(this)
            else toast("Без разрешения Android не даст включить VPN")
        }
    }

    /** Ссылки vless://… и vpn://… («Открыть в BypassHub»). */
    private fun handleIntent(intent: Intent?) {
        val data = intent?.dataString ?: return
        if (!data.startsWith("vless://") && !data.startsWith("vpn://")) return
        intent.data = null
        AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
            .setTitle("Добавить сервер?")
            .setMessage(data.take(80) + if (data.length > 80) "…" else "")
            .setPositiveButton("Добавить") { _, _ -> addServers(data) }
            .setNegativeButton("Отмена", null)
            .show()
    }

    // ---------------------------------------------------------------- вкладки
    private fun show(key: String) {
        page = key
        for ((k, t) in tabs) {
            t.setTextColor(if (k == key) Color.WHITE else Ui.MUTED)
            t.background = Ui.stateful(this, if (k == key) Ui.gradient(this, 12) else Ui.rounded(this, Color.TRANSPARENT, 12), 12)
        }
        render()
        if (key == "home") maybePing()
    }

    private var renderedPage = ""

    private fun render() {
        if (isFinishing) return
        val oldScroll = (content.getChildAt(0) as? ScrollView)?.scrollY?.takeIf { renderedPage == page } ?: 0
        renderedPage = page
        val scroll = ScrollView(this).apply { isFillViewport = true }
        val column = Ui.vbox(this).apply { setPadding(dp(16), dp(4), dp(16), dp(24)) }
        // на планшетах и ТВ — колонка ограниченной ширины по центру
        val holder = FrameLayout(this)
        holder.addView(column, FrameLayout.LayoutParams(
            minOf(resources.displayMetrics.widthPixels, dp(760)), ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER_HORIZONTAL))
        scroll.addView(holder)
        when (page) {
            "home" -> buildHome(column)
            "vpn" -> buildServers(column)
            "bypass" -> buildBypass(column)
            "settings" -> buildSettings(column)
            "log" -> buildLog(column)
        }
        val focused = currentFocus?.let { it.tag }
        content.removeAllViews()
        content.addView(scroll)
        if (focused != null) content.findViewWithTag<View>(focused)?.requestFocus()
        if (oldScroll > 0) scroll.post { scroll.scrollTo(0, oldScroll) }
    }

    // ---------------------------------------------------------------- главная
    /** Включить или выключить режим. VPN и обход — оба через VpnService, поэтому работает что-то одно. */
    private fun toggle(m: String) {
        val current = Vpn.mode
        if (current == m) {
            if (Vpn.status != Vpn.Status.STOPPING) Vpn.stop(this)
            return
        }
        if (m == "vpn" && Store.profiles().isEmpty()) {
            toast("Сначала добавьте свой сервер")
            show("vpn")
            return
        }
        if (current.isNotEmpty()) {
            toast(if (m == "vpn") "Обход выключен: на Android одновременно работает только одно VPN-подключение"
                  else "VPN выключен: на Android одновременно работает только одно VPN-подключение")
        }
        Store.mode = m
        val prepare = vpnPrepareIntent(this)
        @Suppress("DEPRECATION")
        if (prepare != null) startActivityForResult(prepare, REQ_VPN) else Vpn.start(this)
    }

    /** (текст, цвет) состояния режима для плитки. */
    private fun modeStatus(m: String): Pair<String, Int> {
        val active = Vpn.mode == m
        return when {
            active && Vpn.status == Vpn.Status.RUNNING -> if (m == "bypass") "Включён" to Ui.GREEN else {
                val g = Vpn.group
                val cur = g?.current?.takeIf { it.isNotEmpty() }
                val name = if (Vpn.kind == "vless" && cur != null) Vpn.nameOf(cur) else Vpn.serverName
                val ms = cur?.let { Vpn.pings[it] }
                val prefix = if (g?.selected == AUTO) "Авто: " else ""
                "Подключён: $prefix$name" + (if (ms != null) " · $ms мс" else "") to Ui.GREEN
            }
            active && Vpn.status == Vpn.Status.STARTING -> "Подключение…" to Ui.YELLOW
            active && Vpn.status == Vpn.Status.STOPPING -> "Отключение…" to Ui.YELLOW
            Vpn.status == Vpn.Status.STOPPED && Vpn.error != null && Store.mode == m -> "Ошибка: ${Vpn.error}" to Ui.RED
            else -> "Выключен" to Ui.MUTED
        }
    }

    /** Плитка режима: вся карточка — кнопка (удобно и пальцем, и пультом). */
    private fun tile(m: String, title: String, description: String): View {
        val on = Vpn.mode == m && Vpn.status == Vpn.Status.RUNNING
        val busy = Vpn.mode == m && (Vpn.status == Vpn.Status.STARTING || Vpn.status == Vpn.Status.STOPPING)
        val row = Ui.hbox(this).apply {
            tag = "tile:$m"
            val p = dp(18)
            setPadding(p, p, p, p)
            background = Ui.stateful(context, Ui.rounded(context, Ui.SURFACE, 18, if (on) Ui.ACCENT2 else Ui.SURFACE,
                if (on) 2 else 0), 18, Ui.SURFACE2)
            isFocusable = true
            isClickable = true
            setOnClickListener { toggle(m) }
        }
        val left = Ui.vbox(this)
        left.addView(Ui.text(this, title, 19f, Ui.TEXT, true))
        val (status, color) = modeStatus(m)
        left.addView(Ui.text(this, status, 14f, color, true), Ui.lp(top = dp(4)))
        left.addView(Ui.text(this, description, 13f, Ui.MUTED), Ui.lp(top = dp(4)))
        row.addView(left, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        val size = dp(64)
        val power = TextView(this).apply {
            text = if (busy) "…" else "⏻"
            gravity = Gravity.CENTER
            setTextColor(if (on) Color.WHITE else Ui.MUTED)
            textSize = 26f
            background = if (on) Ui.gradient(context, 0, shape = GradientDrawable.OVAL)
                         else GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(Ui.SURFACE2) }
        }
        row.addView(power, LinearLayout.LayoutParams(size, size).apply { marginStart = dp(12) })
        return row
    }

    private fun buildHome(col: LinearLayout) {
        val profiles = Store.profiles()
        val vpnDesc = when {
            profiles.isEmpty() -> "Свой сервер VLESS или AmneziaVPN. Добавьте ключ на вкладке VPN."
            Store.ruDirect -> "Зарубежное — через VPN, российское — напрямую"
            else -> "Весь трафик — через VPN"
        }
        col.addView(tile("vpn", "VPN", vpnDesc), Ui.lp(top = dp(8)))
        val bypassDesc = if (Store.bypassAll) "Без VPN и без root: разбиение TLS для всех сайтов"
                         else "Без VPN и без root: YouTube, Discord и другие сайты из списков"
        col.addView(tile("bypass", "Обход блокировок", bypassDesc), Ui.lp(top = dp(12), bottom = dp(16)))

        if (profiles.isEmpty()) return
        val c = Ui.card(this)
        val head = Ui.hbox(this)
        head.addView(Ui.text(this, "Серверы", 17f, Ui.TEXT, true), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        if (Vpn.pings.isNotEmpty()) {
            head.addView(Ui.text(this, if (Vpn.pingsReal) "через VPN" else "до сервера", 12f, Ui.MUTED).margins(end = 8))
        }
        head.addView(Ui.button(this, if (Vpn.pinging) "Проверка…" else "Пинг", primary = false) {
            Vpn.ping()
            lastPing = System.currentTimeMillis()
        }.apply { tag = "ping" })
        c.addView(head)
        serverList(c, profiles, editable = false)
        col.addView(c)
    }

    /** Строки серверов: нажатие выбирает сервер, справа — пинг. «Авто» — если VLESS-серверов два и больше. */
    private fun serverList(parent: LinearLayout, profiles: List<Profile>, editable: Boolean) {
        val autoOk = Store.vless().size >= 2
        val selected = if (Store.isAuto()) AUTO else Store.activeProfile()?.id ?: ""
        val g = Vpn.group
        val live = Vpn.running && Vpn.kind == "vless"
        if (autoOk && !editable) {
            val cur = if (live && g?.selected == AUTO) g.current else ""
            val sub = if (cur.isNotEmpty()) "сейчас: ${Vpn.nameOf(cur)}" else "Сам выбирает самый быстрый сервер VLESS"
            parent.addView(serverRow(AUTO, "⚡ Авто — лучший сервер", sub, selected == AUTO,
                if (cur.isNotEmpty()) cur else null, null))
        }
        for (p in profiles) {
            val sub = if (editable) p.summary else (if (p.kind == "awg") "AmneziaWG" else "VLESS") +
                (if (live && g?.selected == AUTO && g.current == p.id) " · ● сейчас" else "")
            parent.addView(serverRow(p.id, p.name, sub, p.id == selected, if (p.kind == "vless") p.id else null,
                if (editable) p else null))
        }
    }

    private fun serverRow(id: String, title: String, subtitle: String, active: Boolean, pingId: String?,
                          editable: Profile?): View {
        val row = Ui.hbox(this).apply {
            tag = "row:$id"
            val normal = Ui.rounded(context, Ui.SURFACE2, 12, if (active) Ui.ACCENT else Ui.SURFACE2, if (active) 2 else 0)
            background = Ui.stateful(context, normal, 12, Ui.BORDER)
            setPadding(dp(14), dp(10), dp(10), dp(10))
            isFocusable = editable == null
            isClickable = editable == null
            if (editable == null) setOnClickListener { selectServer(id) }
        }
        val left = Ui.vbox(this)
        left.addView(Ui.text(this, (if (active) "● " else "") + title, 15f, if (active) Ui.FOCUS else Ui.TEXT, true))
        left.addView(Ui.text(this, subtitle, 12f, Ui.MUTED))
        row.addView(left, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        if (pingId != null) {
            val (t, color) = Ui.pingText(Vpn.pings[pingId], Vpn.pings.containsKey(pingId))
            row.addView(Ui.text(this, t, 14f, color, true).margins(start = 8, end = 4))
        }
        if (editable != null) {
            row.addView(Ui.button(this, "✎", primary = false) { rename(editable) }.apply { tag = "edit:$id" }.margins(start = 6))
            row.addView(Ui.button(this, "✕", primary = false) { remove(editable) }.apply { tag = "del:$id" }.margins(start = 6))
        }
        row.layoutParams = Ui.lp(top = dp(8))
        return row
    }

    private fun selectServer(id: String) {
        if (Store.active == id) return
        Store.select(id)
        render()
        Vpn.switchServer(this)
    }

    private fun maybePing() {
        if (System.currentTimeMillis() - lastPing > PING_MAX_AGE_MS && Store.vless().isNotEmpty()) {
            lastPing = System.currentTimeMillis()
            Vpn.ping()
        }
    }

    // ---------------------------------------------------------------- серверы
    private fun buildServers(col: LinearLayout) {
        val add = Ui.card(this)
        add.addView(Ui.text(this, "Добавить сервер", 17f, Ui.TEXT, true))
        add.addView(Ui.text(this, "Ключ vless://…, ключ AmneziaVPN vpn://… (AmneziaVPN → «Поделиться»), конфиг " +
            "AmneziaWG или ссылка на подписку https://…. Можно несколько ключей — по одному на строку.", 13f, Ui.MUTED),
            Ui.lp(top = dp(4), bottom = dp(10)))
        val input = Ui.input(this, "Вставьте ключ сюда", multiline = true).apply { tag = "input" }
        add.addView(input)
        val bar = Ui.hbox(this)
        bar.addView(Ui.button(this, "Вставить из буфера") {
            val clip = (getSystemService(CLIPBOARD_SERVICE) as ClipboardManager).primaryClip
            val text = clip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)?.toString().orEmpty()
            if (text.isBlank()) toast("Буфер обмена пуст") else addServers(text)
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginEnd = dp(8) })
        bar.addView(Ui.button(this, "Добавить", primary = false) {
            val text = input.text.toString()
            if (text.isBlank()) toast("Вставьте ключ в поле выше") else addServers(text)
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        add.addView(bar, Ui.lp(top = dp(10)))
        add.addView(Ui.text(this, "Ключ — это пароль от вашего сервера: не публикуйте его.", 12f, Ui.MUTED), Ui.lp(top = dp(8)))
        col.addView(add)

        val profiles = Store.profiles()
        if (profiles.isNotEmpty()) {
            val list = Ui.card(this)
            list.addView(Ui.text(this, "Мои серверы", 17f, Ui.TEXT, true))
            serverList(list, profiles, editable = true)
            col.addView(list.margins(top = 14))
        }
        val subs = Store.subscriptions
        if (subs.isNotEmpty()) {
            val c = Ui.card(this)
            c.addView(Ui.text(this, "Подписки", 17f, Ui.TEXT, true))
            for (url in subs) {
                val row = Ui.hbox(this)
                row.addView(Ui.text(this, if (url.length > 48) url.take(45) + "…" else url, 13f, Ui.MUTED),
                    LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
                row.addView(Ui.button(this, "✕", primary = false) {
                    confirm("Удалить подписку и все её серверы?") {
                        Store.removeSubscription(url)
                        render()
                    }
                })
                c.addView(row, Ui.lp(top = dp(8)))
            }
            c.addView(Ui.button(this, "Обновить подписки", primary = false) {
                Vpn.io.execute {
                    val (n, problems) = Store.refreshSubscriptions()
                    main.post {
                        toast("Серверов в подписках: $n" + if (problems.isNotEmpty()) "\n" + problems.take(3).joinToString("\n") else "")
                        render()
                    }
                }
            }, Ui.lp(top = dp(10)))
            col.addView(c.margins(top = 14))
        }
        val split = Ui.card(this)
        split.addView(Ui.text(this, "Раздельное туннелирование", 17f, Ui.TEXT, true))
        split.addView(Ui.switch(this, "Российское — напрямую", Store.ruDirect) {
            Store.ruDirect = it
            if (Vpn.running && Vpn.mode == "vpn") Vpn.start(this)
        }.apply { tag = "ru" }, Ui.lp(top = dp(6)))
        split.addView(Ui.text(this, "Через VPN идёт всё зарубежное. Напрямую, с домашнего IP, — сайты .ru, .рф, .su, " +
            "российские IP-адреса и российские сервисы на «нерусских» доменах (VK, Яндекс, банки и др.) по базам " +
            "roscomvpn. YouTube, Telegram, GitHub и Google Play всегда идут через VPN.", 13f, Ui.MUTED), Ui.lp(top = dp(4)))
        val age = Rules.age(this)?.let { SimpleDateFormat("dd.MM.yyyy", Locale.US).format(Date(it)) }
        split.addView(Ui.text(this, "Базы: " + (age?.let { "от $it" } ?: "скачаются при включении"), 12f, Ui.MUTED), Ui.lp(top = dp(6)))
        col.addView(split.margins(top = 14))
    }

    private fun addServers(text: String) {
        toast("Добавляю…")
        Vpn.io.execute {
            val (added, problems) = try {
                Store.add(text)
            } catch (e: Exception) {
                emptyList<Profile>() to listOf(e.message ?: "ошибка")
            }
            main.post {
                val msg = StringBuilder()
                if (added.isNotEmpty()) msg.append("Добавлено серверов: ${added.size}\n").append(added.take(8).joinToString("\n") { "• ${it.name}" })
                else if (problems.isEmpty()) msg.append("Эти серверы уже есть в списке.")
                if (problems.isNotEmpty()) msg.append(if (msg.isNotEmpty()) "\n\n" else "").append("Не добавлено:\n")
                    .append(problems.take(6).joinToString("\n") { "• $it" })
                AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                    .setTitle("Серверы").setMessage(msg.toString()).setPositiveButton("OK", null).show()
                content.findViewWithTag<android.widget.EditText>("input")?.setText("")
                lastPing = 0
                render()
                maybePing()
            }
        }
    }

    private fun rename(p: Profile) {
        val input = Ui.input(this, "Название").apply { setText(p.name) }
        AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
            .setTitle("Переименовать")
            .setView(FrameLayout(this).apply { setPadding(dp(20), dp(8), dp(20), 0); addView(input) })
            .setPositiveButton("Сохранить") { _, _ ->
                Store.rename(p.id, input.text.toString())
                render()
            }
            .setNegativeButton("Отмена", null)
            .show()
    }

    private fun remove(p: Profile) = confirm("Удалить сервер «${p.name}»?") {
        Store.remove(p.id)
        if (Vpn.running && Vpn.mode == "vpn") {
            if (Store.profiles().isEmpty()) Vpn.stop(this) else if (p.kind == "vless" || Vpn.serverName == p.name) Vpn.start(this)
        }
        render()
    }

    // ---------------------------------------------------------------- настройки
    private fun buildSettings(col: LinearLayout) {
        val a = Ui.card(this)
        a.addView(Ui.text(this, "Постоянная VPN", 17f, Ui.TEXT, true))
        a.addView(Ui.text(this, "Чтобы VPN включался сам после перезагрузки, в настройках Android выберите " +
            "BypassHub и включите «Постоянная VPN».", 13f, Ui.MUTED), Ui.lp(top = dp(4), bottom = dp(10)))
        a.addView(Ui.button(this, "Открыть настройки VPN", primary = false) {
            try {
                startActivity(Intent(Settings.ACTION_VPN_SETTINGS))
            } catch (e: Exception) {
                toast("На этом устройстве нет таких настроек")
            }
        })
        col.addView(a)

        val u = Ui.card(this)
        u.addView(Ui.text(this, "Обновления", 17f, Ui.TEXT, true))
        val core = try { Bhcore.version() } catch (e: Throwable) { "?" }
        u.addView(Ui.text(this, "BypassHub ${BuildConfig.VERSION_NAME} · sing-box $core", 13f, Ui.MUTED), Ui.lp(top = dp(4), bottom = dp(10)))
        u.addView(Ui.button(this, "Проверить обновления", primary = false) { Updater.check(this, manual = true) })
        col.addView(u.margins(top = 14))

        val about = Ui.card(this)
        about.addView(Ui.text(this, "О программе", 17f, Ui.TEXT, true))
        about.addView(Ui.text(this, "BypassHub — свой VPN (VLESS, AmneziaWG) с раздельным туннелированием. " +
            "Движки: sing-box (SagerNet, GPL-3.0) и AmneziaWG (Amnezia, MIT). Исходный код: github.com/Miffk/BypassHub",
            13f, Ui.MUTED), Ui.lp(top = dp(4)))
        col.addView(about.margins(top = 14))
    }

    // ---------------------------------------------------------------- обход
    private fun restartBypass() {
        if (Vpn.mode == "bypass" && Vpn.status == Vpn.Status.RUNNING) Vpn.start(this)
    }

    private fun buildBypass(col: LinearLayout) {
        val how = Ui.card(this)
        how.addView(Ui.text(this, "Обход блокировок без VPN", 17f, Ui.TEXT, true))
        how.addView(Ui.text(this, "Работает без сервера и без root: трафик идёт напрямую, а к сайтам из списков " +
            "BypassHub разбивает начало защищённого соединения на части — так провайдер не видит, какой сайт " +
            "открывается, и не замедляет его. Списки — те же, что у zapret для Windows (YouTube, Discord, " +
            "Twitch и др.). Включается на главной.", 13f, Ui.MUTED), Ui.lp(top = dp(4)))
        how.addView(Ui.text(this, "Голосовые звонки Discord и игры так не ускорить — для них нужен VPN. " +
            "Если сайт всё равно не открывается, попробуйте другой способ ниже.", 13f, Ui.MUTED), Ui.lp(top = dp(6)))
        col.addView(how)

        val method = Ui.card(this)
        method.addView(Ui.text(this, "Способ", 17f, Ui.TEXT, true))
        for ((key, title, sub) in listOf(
            Triple(Bypass.METHOD_TCP, "Разбивать TCP-пакеты", "Рекомендуется — работает у большинства провайдеров"),
            Triple(Bypass.METHOD_RECORD, "Разбивать TLS-записи", "Если первый способ не помог"))) {
            method.addView(optionRow("method:$key", title, sub, Store.bypassMethod == key) {
                if (Store.bypassMethod != key) {
                    Store.bypassMethod = key
                    render()
                    restartBypass()
                }
            })
        }
        method.addView(Ui.switch(this, "Применять ко всем сайтам", Store.bypassAll) {
            Store.bypassAll = it
            restartBypass()
        }.apply { tag = "all" }, Ui.lp(top = dp(10)))
        method.addView(Ui.text(this, "Кроме российских. Помогает с сайтами, которых нет в списках; QUIC при этом " +
            "отключается для всех сайтов.", 12f, Ui.MUTED))
        col.addView(method.margins(top = 14))

        val user = Ui.card(this)
        user.addView(Ui.text(this, "Свои сайты", 17f, Ui.TEXT, true))
        user.addView(Ui.text(this, "По одному на строку, например example.com — поддомены учитываются автоматически.",
            13f, Ui.MUTED), Ui.lp(top = dp(4), bottom = dp(10)))
        val input = Ui.input(this, "example.com", multiline = true).apply {
            tag = "bypass-user"
            setText(Store.bypassUser)
        }
        user.addView(input)
        user.addView(Ui.button(this, "Сохранить", primary = false) {
            val (suffix, exact) = Bypass.parse(input.text.lineSequence())
            Store.bypassUser = (suffix + exact.map { "^$it" }).joinToString("\n")
            input.setText(Store.bypassUser)
            toast("Сохранено сайтов: ${suffix.size + exact.size}")
            restartBypass()
        }, Ui.lp(top = dp(10)))
        col.addView(user.margins(top = 14))

        val lists = Ui.card(this)
        lists.addView(Ui.text(this, "Списки сайтов", 17f, Ui.TEXT, true))
        val age = Bypass.age(this)?.let { SimpleDateFormat("dd.MM.yyyy HH:mm", Locale.US).format(Date(it)) }
        lists.addView(Ui.text(this, "Из zapret-discord-youtube (Flowseal), обновляются раз в сутки при включении. " +
            (age?.let { "Скачаны: $it." } ?: "Ещё не скачаны — пока используется встроенный список."), 13f, Ui.MUTED),
            Ui.lp(top = dp(4), bottom = dp(10)))
        lists.addView(Ui.button(this, "Обновить списки", primary = false) {
            toast("Обновляю списки…")
            Vpn.io.execute {
                Bypass.update(this, force = true)
                main.post {
                    toast(if (Bypass.age(this) != null) "Списки обновлены" else "Не удалось скачать списки — см. журнал")
                    render()
                    restartBypass()
                }
            }
        })
        col.addView(lists.margins(top = 14))
    }

    /** Строка выбора одного варианта из нескольких. */
    private fun optionRow(tag: String, title: String, subtitle: String, selected: Boolean, onClick: () -> Unit): View {
        val row = Ui.vbox(this).apply {
            this.tag = tag
            val normal = Ui.rounded(context, Ui.SURFACE2, 12, if (selected) Ui.ACCENT else Ui.SURFACE2, if (selected) 2 else 0)
            background = Ui.stateful(context, normal, 12, Ui.BORDER)
            setPadding(dp(14), dp(10), dp(14), dp(10))
            isFocusable = true
            isClickable = true
            setOnClickListener { onClick() }
        }
        row.addView(Ui.text(this, (if (selected) "● " else "○ ") + title, 15f, if (selected) Ui.FOCUS else Ui.TEXT, true))
        row.addView(Ui.text(this, subtitle, 12f, Ui.MUTED))
        row.layoutParams = Ui.lp(top = dp(8))
        return row
    }

    // ---------------------------------------------------------------- журнал
    private fun buildLog(col: LinearLayout) {
        val bar = Ui.hbox(this)
        bar.addView(Ui.button(this, "Копировать", primary = false) {
            (getSystemService(CLIPBOARD_SERVICE) as ClipboardManager)
                .setPrimaryClip(android.content.ClipData.newPlainText("BypassHub", LogBuffer.text()))
            toast("Журнал скопирован")
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginEnd = dp(8) })
        bar.addView(Ui.button(this, "Очистить", primary = false) { LogBuffer.clear() },
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        col.addView(bar)
        val t = Ui.text(this, "", 11f, Ui.TEXT).apply {
            typeface = Typeface.MONOSPACE
            setTextIsSelectable(true)
            background = Ui.rounded(context, Ui.SURFACE, 12)
            setPadding(dp(12), dp(12), dp(12), dp(12))
        }
        logView = t
        renderLogText()
        col.addView(t, Ui.lp(top = dp(10)))
    }

    private fun renderLogText() {
        logView?.text = LogBuffer.text().ifEmpty { "Журнал пуст" }
    }

    // ---------------------------------------------------------------- помощники
    private fun toast(text: String) = Toast.makeText(this, text, Toast.LENGTH_LONG).show()

    private fun confirm(message: String, onYes: () -> Unit) {
        AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
            .setMessage(message)
            .setPositiveButton("Да") { _, _ -> onYes() }
            .setNegativeButton("Отмена", null)
            .show()
    }
}
