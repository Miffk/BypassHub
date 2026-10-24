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
import io.github.miffk.bypasshub.R
import io.github.miffk.bypasshub.Rules
import io.github.miffk.bypasshub.SUPPORT_URL
import io.github.miffk.bypasshub.Store
import io.github.miffk.bypasshub.Tg
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
    private lateinit var glow: GradientDrawable
    /** Нижняя панель: ключ → (значок, подпись). «Журнал» открывается из «Настроек». */
    private val nav = linkedMapOf<String, Pair<android.widget.ImageView, TextView>>()
    private var page = "home"
    /** Какой режим показывает главная (VPN или обход) — переключается сегментом вверху. */
    private var homeMode = "vpn"
    private var lastVpnMode = ""
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
            if (Tg.running && page == "home" && homeMode == "tg") render()
            main.postDelayed(this, 3000)
        }
    }

    // ---------------------------------------------------------------- жизненный цикл
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // рисуем под системными панелями сами и отступаем на их высоту (на Android 15 это обязательно)
        if (Build.VERSION.SDK_INT >= 30) {
            window.setDecorFitsSystemWindows(false)
        } else {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_LAYOUT_STABLE or
                View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
        }
        @Suppress("DEPRECATION")
        window.statusBarColor = Color.TRANSPARENT
        @Suppress("DEPRECATION")
        window.navigationBarColor = Color.TRANSPARENT
        if (Build.VERSION.SDK_INT >= 29) window.isNavigationBarContrastEnforced = false

        // фон: тёмный с мягким свечением посередине (цвет свечения зависит от состояния)
        glow = GradientDrawable().apply {
            gradientType = GradientDrawable.RADIAL_GRADIENT
            gradientRadius = maxOf(resources.displayMetrics.widthPixels, dp(360)) * 0.85f
            setGradientCenter(0.5f, 0.36f)
        }
        setGlow(false)
        val root = Ui.vbox(this).apply {
            background = android.graphics.drawable.LayerDrawable(arrayOf(android.graphics.drawable.ColorDrawable(Ui.BG), glow))
        }

        val header = Ui.hbox(this).apply { setPadding(dp(20), dp(16), dp(20), dp(4)) }
        header.addView(Ui.text(this, "BypassHub", 22f, Ui.TEXT, true).apply {
            paint.shader = android.graphics.LinearGradient(0f, 0f, dp(140).toFloat(), 0f, Ui.ACCENT, Ui.ACCENT2,
                android.graphics.Shader.TileMode.CLAMP)
        })
        root.addView(header)

        content = FrameLayout(this)
        root.addView(content, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))

        // нижняя панель — до неё удобно дотянуться большим пальцем
        val bar = Ui.hbox(this).apply {
            setBackgroundColor(Ui.NAV)
            setPadding(dp(6), dp(6), dp(6), dp(6))
        }
        for ((key, label, icon) in listOf(
            Triple("home", "Главная", R.drawable.ic_nav_home),
            Triple("vpn", "Серверы", R.drawable.ic_nav_servers),
            Triple("bypass", "Обход", R.drawable.ic_nav_bypass),
            Triple("tg", "Telegram", R.drawable.ic_nav_telegram),
            Triple("settings", "Настройки", R.drawable.ic_nav_settings))) {
            val item = Ui.vbox(this).apply {
                gravity = Gravity.CENTER
                setPadding(0, dp(6), 0, dp(6))
                background = Ui.stateful(context, Ui.rounded(context, Color.TRANSPARENT, 14), 14, Ui.SURFACE2)
                isFocusable = true
                isClickable = true
                setOnClickListener { show(key) }
            }
            val image = android.widget.ImageView(this).apply { setImageResource(icon) }
            item.addView(image, LinearLayout.LayoutParams(dp(24), dp(24)))
            val text = Ui.text(this, label, 12f, Ui.MUTED).apply {
                isSingleLine = true
                gravity = Gravity.CENTER
            }
            item.addView(text, Ui.lp(w = ViewGroup.LayoutParams.WRAP_CONTENT, top = dp(3)))
            nav[key] = image to text
            bar.addView(item, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                marginStart = dp(2)
                marginEnd = dp(2)
            })
        }
        root.addView(bar)

        root.setOnApplyWindowInsetsListener { _, insets ->
            var top: Int
            var bottom: Int
            var left = 0
            var right = 0
            var ime = 0
            if (Build.VERSION.SDK_INT >= 30) {
                val bars = insets.getInsets(android.view.WindowInsets.Type.systemBars() or
                    android.view.WindowInsets.Type.displayCutout())
                top = bars.top
                bottom = bars.bottom
                left = bars.left
                right = bars.right
                ime = insets.getInsets(android.view.WindowInsets.Type.ime()).bottom
            } else {
                @Suppress("DEPRECATION")
                top = insets.systemWindowInsetTop
                @Suppress("DEPRECATION")
                bottom = insets.systemWindowInsetBottom
                // в старых Android клавиатура входит в нижний отступ
                if (bottom > dp(150)) {
                    ime = bottom
                    bottom = 0
                }
            }
            root.setPadding(left, 0, right, 0)
            header.setPadding(dp(20), top + dp(10), dp(20), dp(4))
            // пока открыта клавиатура, панель не нужна — поле ввода остаётся видно
            bar.visibility = if (ime > 0) View.GONE else View.VISIBLE
            bar.setPadding(dp(6), dp(6), dp(6), bottom + dp(6))
            content.setPadding(0, 0, 0, if (ime > 0) ime else 0)
            insets
        }
        setContentView(root)
        homeMode = Vpn.mode.ifEmpty { Store.mode }
        lastVpnMode = Vpn.mode

        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 2)
        }
        show("home")
        handleIntent(intent)
        Updater.checkInBackground(this)
        // прокси для Telegram был включён — поднимаем снова (например, после перезапуска телефона)
        if (Store.tgEnabled && Tg.status == Tg.Status.STOPPED) Tg.start(this)
    }

    @Deprecated("Activity API")
    override fun onBackPressed() {
        when (page) {
            "home" -> @Suppress("DEPRECATION") super.onBackPressed()
            "log" -> show("settings")
            else -> show("home")
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    override fun onResume() {
        super.onResume()
        Vpn.listen(renderer)
        Tg.listen(renderer)
        LogBuffer.listen(logRenderer)
        main.post(poll)
        render()
        maybePing()
        maybeRefreshSubs()
    }

    override fun onPause() {
        Vpn.unlisten(renderer)
        Tg.unlisten(renderer)
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

    // ---------------------------------------------------------------- навигация
    private fun show(key: String) {
        page = key
        val active = if (key == "log") "settings" else key
        for ((k, v) in nav) {
            val (image, text) = v
            val on = k == active
            image.setColorFilter(if (on) Color.WHITE else Ui.MUTED)
            text.setTextColor(if (on) Color.WHITE else Ui.MUTED)
            text.typeface = if (on) Typeface.DEFAULT_BOLD else Typeface.DEFAULT
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
        val column = Ui.vbox(this).apply { setPadding(dp(16), dp(4), dp(16), dp(20)) }
        // на планшетах и ТВ — колонка ограниченной ширины по центру
        val holder = FrameLayout(this)
        holder.addView(column, FrameLayout.LayoutParams(
            minOf(resources.displayMetrics.widthPixels, dp(640)), ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.CENTER_HORIZONTAL))
        scroll.addView(holder)
        when (page) {
            "home" -> {
                buildHome(column)
                supportLink(column)
            }
            "vpn" -> buildServers(column)
            "bypass" -> buildBypass(column)
            "tg" -> buildTg(column)
            "settings" -> buildSettings(column)
            "log" -> buildLog(column)
        }
        setGlow(if (page == "home") modeOn(homeMode) else Vpn.status == Vpn.Status.RUNNING || Tg.running)
        val focused = currentFocus?.let { it.tag }
        content.removeAllViews()
        content.addView(scroll)
        if (focused != null) content.findViewWithTag<View>(focused)?.requestFocus()
        if (oldScroll > 0) scroll.post { scroll.scrollTo(0, oldScroll) }
    }

    /** Свечение фона: синее — выключено, зелёное — работает. */
    private fun setGlow(on: Boolean) {
        glow.colors = intArrayOf(if (on) Color.argb(70, 44, 182, 125) else Color.argb(80, 58, 92, 190), Color.TRANSPARENT)
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

    /** Полупрозрачная карточка — сквозь неё видно свечение фона. */
    private fun glass(radiusDp: Int = 22, stroke: Int = 0): GradientDrawable =
        Ui.rounded(this, Ui.GLASS, radiusDp, stroke, if (stroke != 0) 1 else 0)

    private fun clickable(v: View, tag: String, radiusDp: Int, normal: android.graphics.drawable.Drawable, onClick: () -> Unit) {
        v.tag = tag
        v.background = Ui.stateful(this, normal, radiusDp, Ui.SURFACE2)
        v.isFocusable = true
        v.isClickable = true
        v.setOnClickListener { onClick() }
    }

    private fun icon(res: Int, color: Int, sizeDp: Int = 22): android.widget.ImageView =
        android.widget.ImageView(this).apply {
            setImageResource(res)
            setColorFilter(color)
            layoutParams = LinearLayout.LayoutParams(dp(sizeDp), dp(sizeDp))
        }

    private fun buildHome(col: LinearLayout) {
        // главная показывает тот режим, который только что включили (в том числе из шторки)
        if (Vpn.mode != lastVpnMode) {
            if (Vpn.mode.isNotEmpty()) homeMode = Vpn.mode
            lastVpnMode = Vpn.mode
        }
        val m = homeMode
        if (m == "tg") {
            buildHomeTg(col)
            return
        }
        val active = Vpn.mode == m
        val on = active && Vpn.status == Vpn.Status.RUNNING
        val busy = active && (Vpn.status == Vpn.Status.STARTING || Vpn.status == Vpn.Status.STOPPING)
        val profiles = Store.profiles()
        segments(col, m)

        // подсказка, как в VPN-приложениях: что сейчас с трафиком
        val (hint, hintColor) = when {
            on && m == "vpn" -> "Трафик идёт через ваш сервер" to Ui.GREEN
            on -> "Заблокированные сайты открываются напрямую" to Ui.GREEN
            m == "vpn" -> "Без VPN провайдер видит, какие сайты вы открываете" to Ui.SOFT_RED
            else -> "YouTube и Discord могут замедляться провайдером" to Ui.SOFT_RED
        }
        col.addView(Ui.text(this, hint, 13f, hintColor).apply { gravity = Gravity.CENTER }, Ui.lp(top = dp(14)))

        // выбранный сервер (или что делает обход) — нажатие открывает настройки режима
        val pick = Ui.hbox(this).apply { setPadding(dp(14), dp(12), dp(18), dp(12)) }
        clickable(pick, "pick:$m", 30, glass(30, Ui.BORDER)) { show(if (m == "vpn") "vpn" else "bypass") }
        val badge = FrameLayout(this).apply { background = GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(Ui.SURFACE2)
        } }
        val auto = Store.isAuto()
        val badgeIcon = when {
            m == "bypass" -> R.drawable.ic_nav_bypass
            auto -> R.drawable.ic_auto
            else -> R.drawable.ic_globe
        }
        badge.addView(icon(badgeIcon, if (on) Ui.ACCENT2 else Ui.TEXT, 24),
            FrameLayout.LayoutParams(dp(24), dp(24), Gravity.CENTER))
        pick.addView(badge, LinearLayout.LayoutParams(dp(46), dp(46)))
        val texts = Ui.vbox(this)
        val (caption, value) = if (m == "bypass") {
            "Сайты для обхода" to (if (Store.bypassAll) "Все, кроме российских" else "YouTube, Discord и другие")
        } else if (profiles.isEmpty()) {
            "Сервер не добавлен" to "Нажмите, чтобы добавить"
        } else {
            val g = Vpn.group
            val live = Vpn.running && Vpn.kind == "vless" && g?.selected == AUTO && g.current.isNotEmpty()
            "Выбранный сервер" to when {
                auto && live -> "Авто · ${Vpn.nameOf(g!!.current)}"
                auto -> "Авто — лучший сервер"
                else -> Store.activeProfile()?.name ?: "—"
            }
        }
        texts.addView(Ui.text(this, caption, 13f, Ui.MUTED))
        texts.addView(Ui.text(this, value, 17f, Ui.TEXT, true).apply {
            isSingleLine = true
            ellipsize = android.text.TextUtils.TruncateAt.END
        })
        pick.addView(texts, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = dp(14) })
        pick.addView(Ui.text(this, "›", 26f, Ui.MUTED))
        col.addView(pick, Ui.lp(top = dp(12)))

        // состояние и большой переключатель
        val title = when {
            busy && Vpn.status == Vpn.Status.STOPPING -> "Отключение…"
            busy -> "Подключение…"
            on && m == "vpn" -> "VPN подключён"
            on -> "Обход работает"
            m == "vpn" -> "Вы не подключены"
            else -> "Обход выключен"
        }
        col.addView(Ui.text(this, title, 20f, Ui.TEXT, true).apply { gravity = Gravity.CENTER }, Ui.lp(top = dp(44)))
        val err = Vpn.error
        if (!active && Vpn.status == Vpn.Status.STOPPED && err != null && Store.mode == m) {
            col.addView(Ui.text(this, "Ошибка: $err", 13f, Ui.RED).apply { gravity = Gravity.CENTER }, Ui.lp(top = dp(6)))
        }
        col.addView(powerSwitch(m, on || (busy && Vpn.status == Vpn.Status.STARTING), busy).apply {
            layoutParams = LinearLayout.LayoutParams(dp(260), dp(76)).apply {
                gravity = Gravity.CENTER_HORIZONTAL
                topMargin = dp(18)
            }
        })
        val desc = when {
            m == "bypass" -> "Без VPN и без root · " + Bypass.methodName(Store.bypassMethod)
            Store.ruDirect -> "Зарубежное — через VPN, российское — напрямую"
            else -> "Весь трафик — через VPN"
        }
        col.addView(Ui.text(this, desc, 13f, Ui.MUTED).apply { gravity = Gravity.CENTER }, Ui.lp(top = dp(14), bottom = dp(40)))

        if (m == "bypass") {
            val c = Ui.vbox(this).apply { setPadding(dp(18), dp(16), dp(18), dp(16)) }
            clickable(c, "bypass-help", 22, glass()) { show("bypass") }
            c.addView(Ui.text(this, "Сайт всё равно не открывается?", 16f, Ui.TEXT, true))
            c.addView(Ui.text(this, "Смените способ разбиения или добавьте свои сайты на вкладке «Обход». " +
                "Голосовые звонки Discord и игры без VPN не ускорить.", 13f, Ui.MUTED), Ui.lp(top = dp(4)))
            col.addView(c)
            return
        }
        if (profiles.isEmpty()) {
            val c = Ui.vbox(this).apply {
                background = glass()
                setPadding(dp(18), dp(16), dp(18), dp(18))
            }
            c.addView(Ui.text(this, "Серверов пока нет", 17f, Ui.TEXT, true))
            c.addView(Ui.text(this, "Добавьте ключ vless://… от вашего VPN-сервиса или ключ AmneziaVPN vpn://… " +
                "(AmneziaVPN → «Поделиться»).", 13f, Ui.MUTED), Ui.lp(top = dp(4), bottom = dp(12)))
            c.addView(Ui.button(this, "Добавить сервер") { show("vpn") }.apply { tag = "add" })
            col.addView(c)
            return
        }
        homeServers(col, profiles)
    }

    /** Небольшая ссылка «Поддержать проект» внизу главной. */
    private fun supportLink(col: LinearLayout) {
        val t = Ui.text(this, "❤️  Поддержать проект", 14f, Ui.MUTED, true).apply {
            gravity = Gravity.CENTER
            setPadding(dp(16), dp(10), dp(16), dp(10))
        }
        clickable(t, "support", 18, Ui.rounded(this, Color.TRANSPARENT, 18)) {
            try {
                startActivity(Intent(Intent.ACTION_VIEW, android.net.Uri.parse(SUPPORT_URL)))
            } catch (e: android.content.ActivityNotFoundException) {
                toast("Не найден браузер: $SUPPORT_URL")
            }
        }
        col.addView(t, LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
            ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            gravity = Gravity.CENTER_HORIZONTAL
            topMargin = dp(24)
        })
    }

    /** Работает ли режим (для точки на сегменте и свечения фона). */
    private fun modeOn(m: String): Boolean =
        if (m == "tg") Tg.running else Vpn.mode == m && Vpn.status == Vpn.Status.RUNNING

    private fun segments(col: LinearLayout, m: String) {
        // переключатель режима: VPN | Обход | Telegram
        val seg = Ui.hbox(this).apply {
            background = Ui.rounded(context, Ui.GLASS, 24)
            setPadding(dp(4), dp(4), dp(4), dp(4))
        }
        for ((key, label) in listOf("vpn" to "VPN", "bypass" to "Обход", "tg" to "Telegram")) {
            val sel = key == m
            val running = modeOn(key)
            val t = Ui.text(this, (if (running) "● " else "") + label, 15f, if (sel) Color.WHITE else Ui.MUTED, true).apply {
                gravity = Gravity.CENTER
                isSingleLine = true
                setPadding(dp(8), dp(10), dp(8), dp(10))
            }
            clickable(t, "seg:$key", 20, if (sel) Ui.gradient(this, 20) else Ui.rounded(this, Color.TRANSPARENT, 20)) {
                if (homeMode != key) {
                    homeMode = key
                    render()
                }
            }
            if (running && !sel) t.setTextColor(Ui.GREEN)
            seg.addView(t, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        }
        col.addView(seg, Ui.lp(top = dp(6)))

    }

    // ---------------------------------------------------------------- Telegram
    private fun buildHomeTg(col: LinearLayout) {
        segments(col, "tg")
        val on = Tg.running
        val busy = Tg.status == Tg.Status.STARTING
        val (hint, hintColor) = if (on) "Telegram идёт через WebSocket — как web.telegram.org" to Ui.GREEN
                                else "Telegram может замедляться провайдером" to Ui.SOFT_RED
        col.addView(Ui.text(this, hint, 13f, hintColor).apply { gravity = Gravity.CENTER }, Ui.lp(top = dp(14)))

        val pick = Ui.hbox(this).apply { setPadding(dp(14), dp(12), dp(18), dp(12)) }
        clickable(pick, "pick:tg", 30, glass(30, Ui.BORDER)) { show("tg") }
        val badge = FrameLayout(this).apply { background = GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(Ui.SURFACE2)
        } }
        badge.addView(icon(R.drawable.ic_nav_telegram, if (on) Ui.ACCENT2 else Ui.TEXT, 24),
            FrameLayout.LayoutParams(dp(24), dp(24), Gravity.CENTER))
        pick.addView(badge, LinearLayout.LayoutParams(dp(46), dp(46)))
        val texts = Ui.vbox(this)
        texts.addView(Ui.text(this, "Прокси для Telegram", 13f, Ui.MUTED))
        texts.addView(Ui.text(this, "${Tg.HOST}:${Store.tgPort}", 17f, Ui.TEXT, true))
        pick.addView(texts, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = dp(14) })
        pick.addView(Ui.text(this, "›", 26f, Ui.MUTED))
        col.addView(pick, Ui.lp(top = dp(12)))

        val title = when {
            busy -> "Запуск…"
            on -> "Прокси работает"
            else -> "Прокси выключен"
        }
        col.addView(Ui.text(this, title, 20f, Ui.TEXT, true).apply { gravity = Gravity.CENTER }, Ui.lp(top = dp(44)))
        val err = Tg.error
        if (Tg.status == Tg.Status.STOPPED && err != null) {
            col.addView(Ui.text(this, "Ошибка: $err", 13f, Ui.RED).apply { gravity = Gravity.CENTER }, Ui.lp(top = dp(6)))
        }
        col.addView(powerSwitch("tg", on || busy, busy).apply {
            layoutParams = LinearLayout.LayoutParams(dp(260), dp(76)).apply {
                gravity = Gravity.CENTER_HORIZONTAL
                topMargin = dp(18)
            }
        })
        val stats = if (on) try { Bhcore.tgStats() } catch (e: Throwable) { "" } else ""
        col.addView(Ui.text(this, stats.ifEmpty { "Работает вместе с VPN или обходом — можно держать включённым" },
            13f, Ui.MUTED).apply { gravity = Gravity.CENTER }, Ui.lp(top = dp(14), bottom = dp(40)))

        val c = Ui.vbox(this).apply {
            background = glass()
            setPadding(dp(18), dp(16), dp(18), dp(18))
        }
        c.addView(Ui.text(this, "Подключить Telegram", 16f, Ui.TEXT, true))
        c.addView(Ui.text(this, "Один раз: Telegram откроется и предложит добавить прокси — нажмите «Подключить». " +
            "Дальше достаточно включать прокси здесь.", 13f, Ui.MUTED), Ui.lp(top = dp(4), bottom = dp(12)))
        c.addView(Ui.button(this, "Открыть в Telegram") { openTelegramLink() }.apply { tag = "tg-open" })
        col.addView(c)
    }

    private fun openTelegramLink() {
        if (!Tg.running) {
            toast("Сначала включите прокси")
            Tg.start(this)
        }
        try {
            startActivity(Intent(Intent.ACTION_VIEW, android.net.Uri.parse(Tg.link())))
        } catch (e: android.content.ActivityNotFoundException) {
            (getSystemService(CLIPBOARD_SERVICE) as ClipboardManager)
                .setPrimaryClip(android.content.ClipData.newPlainText("TG proxy", Tg.link()))
            toast("Telegram не найден — ссылка на прокси скопирована")
        }
    }

    private fun buildTg(col: LinearLayout) {
        val how = Ui.card(this)
        how.addView(Ui.text(this, "TG WS Proxy", 17f, Ui.TEXT, true))
        how.addView(Ui.text(this, "Локальный прокси для Telegram на этом телефоне. Он пускает Telegram через " +
            "WebSocket — так же, как работает web.telegram.org, — и провайдер его не замедляет. VPN не нужен; " +
            "работает и вместе с VPN или обходом. Основан на tg-ws-proxy (Flowseal).", 13f, Ui.MUTED), Ui.lp(top = dp(4)))
        val status = when (Tg.status) {
            Tg.Status.RUNNING -> "Работает · ${Tg.HOST}:${Store.tgPort}" to Ui.GREEN
            Tg.Status.STARTING -> "Запуск…" to Ui.YELLOW
            Tg.Status.STOPPED -> (Tg.error?.let { "Ошибка: $it" } ?: "Выключен") to (if (Tg.error != null) Ui.RED else Ui.MUTED)
        }
        how.addView(Ui.text(this, status.first, 14f, status.second, true), Ui.lp(top = dp(10), bottom = dp(10)))
        val bar = Ui.hbox(this)
        bar.addView(Ui.button(this, if (Tg.status == Tg.Status.STOPPED) "Включить" else "Выключить") {
            if (Tg.status == Tg.Status.STOPPED) Tg.start(this) else Tg.stop(this)
            main.postDelayed({ render() }, 300)
        }.apply { tag = "tg-toggle" }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginEnd = dp(8) })
        bar.addView(Ui.button(this, "Открыть в Telegram", primary = false) { openTelegramLink() },
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        how.addView(bar)
        col.addView(how)

        val opt = Ui.card(this)
        opt.addView(Ui.text(this, "Настройки", 17f, Ui.TEXT, true))
        opt.addView(Ui.switch(this, "Запасной путь через Cloudflare", Store.tgCF) {
            Store.tgCF = it
            Tg.restartIfRunning(this)
        }.apply { tag = "tg-cf" }, Ui.lp(top = dp(6)))
        opt.addView(Ui.text(this, "Если WebSocket Telegram недоступен, прокси подключится через домены tg-ws-proxy " +
            "на Cloudflare, а потом — напрямую.", 12f, Ui.MUTED))
        opt.addView(Ui.text(this, "Порт", 14f, Ui.TEXT, true), Ui.lp(top = dp(14), bottom = dp(6)))
        val port = Ui.input(this, "1443").apply {
            tag = "tg-port"
            setText(Store.tgPort.toString())
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
        }
        opt.addView(port)
        val row = Ui.hbox(this)
        row.addView(Ui.button(this, "Сохранить порт", primary = false) {
            val p = port.text.toString().trim().toIntOrNull()
            if (p == null || p !in 1024..65535) {
                toast("Порт — число от 1024 до 65535")
            } else if (p != Store.tgPort) {
                Store.tgPort = p
                Tg.restartIfRunning(this)
                toast("Сохранено. Добавьте прокси в Telegram заново — кнопкой «Открыть в Telegram»")
                render()
            }
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginEnd = dp(8) })
        row.addView(Ui.button(this, "Новый секрет", primary = false) {
            confirm("Создать новый секрет? Старый прокси в Telegram перестанет работать — его нужно будет добавить заново.") {
                Store.tgSecret = Bhcore.newTGSecret()
                Tg.restartIfRunning(this)
                render()
            }
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        opt.addView(row, Ui.lp(top = dp(10)))
        col.addView(opt.margins(top = 14))
    }

    /** Переключатель «Выкл | Вкл» с кнопкой питания; бегунок переезжает при нажатии. */
    private fun powerSwitch(m: String, right: Boolean, busy: Boolean): View {
        val track = FrameLayout(this).apply {
            tag = "power:$m"
            val bg = Ui.rounded(context, Ui.TRACK, 38)
            val ring = Ui.rounded(context, Color.TRANSPARENT, 38, Ui.FOCUS, 3)
            background = android.graphics.drawable.StateListDrawable().apply {
                addState(intArrayOf(android.R.attr.state_focused), android.graphics.drawable.LayerDrawable(arrayOf(bg, ring)))
                addState(intArrayOf(), bg)
            }
            elevation = dp(10).toFloat()
            isFocusable = true
            isClickable = true
        }
        // подписи на дорожке — та сторона, куда можно переключить
        track.addView(Ui.text(this, if (right) "Выкл" else "Вкл", 16f, Ui.MUTED).apply { gravity = Gravity.CENTER },
            FrameLayout.LayoutParams(dp(100), ViewGroup.LayoutParams.MATCH_PARENT,
                if (right) Gravity.START or Gravity.CENTER_VERTICAL else Gravity.END or Gravity.CENTER_VERTICAL))
        val thumb = Ui.hbox(this).apply {
            background = if (right) Ui.gradient(context, 32) else Ui.rounded(context, Ui.THUMB, 32)
            setPadding(dp(6), 0, dp(14), 0)
        }
        val circle = FrameLayout(this).apply {
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(if (right) Color.argb(60, 255, 255, 255) else Ui.THUMB_CIRCLE)
            }
        }
        circle.addView(icon(R.drawable.ic_power, Color.WHITE, 26), FrameLayout.LayoutParams(dp(26), dp(26), Gravity.CENTER))
        thumb.addView(circle, LinearLayout.LayoutParams(dp(52), dp(52)))
        thumb.addView(Ui.text(this, if (busy) "…" else if (right) "Вкл" else "Выкл", 17f, Color.WHITE, true).apply {
            gravity = Gravity.CENTER
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        track.addView(thumb, FrameLayout.LayoutParams(dp(150), dp(64),
            (if (right) Gravity.END else Gravity.START) or Gravity.CENTER_VERTICAL).apply {
            leftMargin = dp(6)
            rightMargin = dp(6)
        })
        track.setOnClickListener {
            if (m != "tg" && Vpn.status == Vpn.Status.STOPPING) return@setOnClickListener
            // бегунок сразу едет на другую сторону, а состояние догонит его, когда служба ответит
            val shift = (dp(260) - dp(12) - dp(150)).toFloat()
            thumb.animate().translationX(if (right) -shift else shift).setDuration(220).start()
            if (m == "tg") {
                if (Tg.status == Tg.Status.STOPPED) Tg.start(this) else Tg.stop(this)
            } else {
                toggle(m)
            }
        }
        return track
    }

    /** «Авто» и список серверов на главной: нажатие выбирает сервер, справа — пинг. */
    private fun homeServers(col: LinearLayout, profiles: List<Profile>) {
        val selected = if (Store.isAuto()) AUTO else Store.activeProfile()?.id ?: ""
        val g = Vpn.group
        val live = Vpn.running && Vpn.kind == "vless"
        if (Store.vless().size >= 2) {
            val autoRow = Ui.hbox(this).apply { setPadding(dp(18), dp(16), dp(18), dp(16)) }
            val sel = selected == AUTO
            clickable(autoRow, "row:$AUTO", 22, glass(22, if (sel) Ui.ACCENT else 0)) { selectServer(AUTO) }
            autoRow.addView(icon(R.drawable.ic_auto, Ui.FOCUS, 24))
            autoRow.addView(Ui.text(this, "Авто — лучший сервер", 16f, Ui.FOCUS, true),
                LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply { marginStart = dp(12) })
            val cur = if (live && g?.selected == AUTO) g.current else ""
            autoRow.addView(Ui.text(this, if (cur.isNotEmpty()) "сейчас: ${Vpn.nameOf(cur)}" else "Рекомендуем", 13f,
                Ui.MUTED).apply {
                isSingleLine = true
                ellipsize = android.text.TextUtils.TruncateAt.END
                maxWidth = dp(150)
            })
            col.addView(autoRow)
        }

        val list = Ui.vbox(this).apply {
            background = glass()
            setPadding(dp(18), dp(12), dp(18), dp(6))
        }
        val head = Ui.hbox(this)
        head.addView(Ui.text(this, "Серверы", 14f, Ui.MUTED), LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        if (Vpn.pings.isNotEmpty()) head.addView(Ui.text(this, if (Vpn.pingsReal) "пинг через VPN" else "пинг до сервера",
            12f, Ui.MUTED).margins(end = 6))
        val pingBtn = Ui.text(this, if (Vpn.pinging) "Проверка…" else "Пинг", 14f, Ui.ACCENT2, true).apply {
            setPadding(dp(10), dp(6), dp(10), dp(6))
        }
        clickable(pingBtn, "ping", 10, Ui.rounded(this, Color.TRANSPARENT, 10)) {
            Vpn.ping()
            lastPing = System.currentTimeMillis()
        }
        head.addView(pingBtn)
        list.addView(head)
        profiles.forEachIndexed { i, p ->
            if (i > 0) list.addView(View(this).apply { setBackgroundColor(Ui.DIVIDER) },
                LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(1)))
            val sel = p.id == selected
            val now = live && g?.selected == AUTO && g.current == p.id
            val row = Ui.hbox(this).apply { setPadding(dp(4), dp(14), dp(4), dp(14)) }
            clickable(row, "row:${p.id}", 12, Ui.rounded(this, Color.TRANSPARENT, 12)) { selectServer(p.id) }
            val name = android.text.SpannableStringBuilder().apply {
                append(if (sel) "● " else "")
                append(p.name)
                setSpan(android.text.style.StyleSpan(Typeface.BOLD), 0, length, 0)
                val kind = " - " + (if (p.kind == "awg") "AmneziaWG" else "VLESS") + if (now) " · сейчас" else ""
                val start = length
                append(kind)
                setSpan(android.text.style.ForegroundColorSpan(Ui.MUTED), start, length, 0)
            }
            row.addView(Ui.text(this, name, 16f, if (sel) Ui.FOCUS else Ui.TEXT).apply {
                isSingleLine = true
                ellipsize = android.text.TextUtils.TruncateAt.END
            }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
            if (p.kind == "vless") {
                val (t, color) = Ui.pingText(Vpn.pings[p.id], Vpn.pings.containsKey(p.id))
                row.addView(Ui.text(this, t, 14f, color, true).margins(start = 8))
            }
            list.addView(row)
        }
        col.addView(list, Ui.lp(top = dp(12)))
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

    /** Подписки — сами, раз в несколько часов: новые серверы VPN-сервиса появляются без кнопки. */
    private fun maybeRefreshSubs() {
        if (!Store.subsDue() || refreshingSubs) return
        refreshingSubs = true
        Vpn.io.execute {
            val before = Store.profiles()
            try {
                Store.refreshSubscriptions()
            } catch (e: Exception) {
            }
            main.post {
                refreshingSubs = false
                if (Store.profiles() != before) render()
            }
        }
    }

    private var refreshingSubs = false

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
            c.addView(Ui.text(this, "Обновляются сами раз в несколько часов", 12f, Ui.MUTED), Ui.lp(top = dp(6)))
            c.addView(Ui.button(this, "⟳ Обновить подписки", primary = false) {
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

        val j = Ui.card(this)
        j.addView(Ui.text(this, "Журнал", 17f, Ui.TEXT, true))
        j.addView(Ui.text(this, "Что делали VPN и обход — пригодится, если что-то не работает.", 13f, Ui.MUTED),
            Ui.lp(top = dp(4), bottom = dp(10)))
        j.addView(Ui.button(this, "Открыть журнал", primary = false) { show("log") }.apply { tag = "open-log" })
        col.addView(j.margins(top = 14))

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
            "Если сайт всё равно не открывается, попробуйте другой способ ниже — в мобильной сети чаще " +
            "помогают «Подставной пакет» и «Внеполосный байт».", 13f, Ui.MUTED), Ui.lp(top = dp(6)))
        col.addView(how)

        val method = Ui.card(this)
        method.addView(Ui.text(this, "Способ", 17f, Ui.TEXT, true))
        for ((key, title, sub) in listOf(
            Triple(Bypass.METHOD_DISORDER, "Перемешивать пакеты",
                "Рекомендуется. Как disorder в ByeDPI: начало соединения провайдер видит не по порядку"),
            Triple(Bypass.METHOD_RECORD_DISORDER, "TLS-записи и перемешивание", "Ещё сильнее — если первый способ не помог"),
            Triple(Bypass.METHOD_FAKE, "Подставной пакет",
                "Для мобильного интернета: провайдер сначала видит соединение с другим сайтом. Как fake в ByeDPI"),
            Triple(Bypass.METHOD_OOB, "Внеполосный байт",
                "Для мобильного интернета: в имя сайта вставляется лишний байт, который сайт отбрасывает. Как oob в ByeDPI"),
            Triple(Bypass.METHOD_TCP, "Разбивать TCP-пакеты", "Простое разбиение — самое быстрое"),
            Triple(Bypass.METHOD_RECORD, "Разбивать TLS-записи", "Простое разбиение на две TLS-записи"))) {
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
        col.addView(Ui.button(this, "‹ Настройки", primary = false) { show("settings") }.apply { tag = "back" },
            Ui.lp(w = ViewGroup.LayoutParams.WRAP_CONTENT, bottom = dp(10)))
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
