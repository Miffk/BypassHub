package io.github.miffk.bypasshub

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.Looper
import io.github.miffk.bypasshub.vpn.BypassVpnService
import java.util.concurrent.Executors

/** Состояние VPN для экрана, плитки быстрых настроек и уведомления. */
object Vpn {
    enum class Status { STOPPED, STARTING, RUNNING, STOPPING }

    @Volatile var status = Status.STOPPED
        private set
    @Volatile var error: String? = null
    @Volatile var serverName = ""
    @Volatile var kind = ""                 // vless | awg | bypass
    @Volatile var api: ClashApi? = null      // есть, когда запущен sing-box
    /** Локальный HTTP-прокси sing-box (127.0.0.1) — через него само приложение ходит по VPN. 0 — нет. */
    @Volatile var localProxy = 0
    @Volatile var groupTags: List<String> = emptyList()   // id серверов VLESS в запущенном конфиге
    @Volatile var group: ClashApi.GroupState? = null      // выбранный/текущий сервер и задержки

    /** Пинг серверов: id → мс (null — не отвечает); real — через сами серверы (VPN включён). */
    val pings = mutableMapOf<String, Long?>()
    @Volatile var pingsReal = false
    @Volatile var pinging = false

    private val main = Handler(Looper.getMainLooper())
    private val listeners = mutableListOf<() -> Unit>()
    val io = Executors.newCachedThreadPool()

    fun listen(l: () -> Unit) = listeners.add(l)
    fun unlisten(l: () -> Unit) = listeners.remove(l)
    fun notifyChanged() = main.post { listeners.toList().forEach { it() } }

    fun set(s: Status, err: String? = null) {
        status = s
        if (s == Status.STARTING) error = null
        if (err != null) error = err
        if (s == Status.STOPPED) {
            api = null
            localProxy = 0
            group = null
            groupTags = emptyList()
        }
        notifyChanged()
    }

    val running get() = status == Status.RUNNING

    /** Что сейчас работает или запускается: «vpn», «bypass» или "" (выключено). */
    val mode: String get() = when {
        status == Status.STOPPED -> ""
        kind == "bypass" -> "bypass"
        kind.isEmpty() -> Store.mode
        else -> "vpn"
    }

    /** Включить режим (vpn или bypass); работающий другой режим будет заменён. */
    fun startMode(context: Context, m: String) {
        Store.mode = m
        start(context)
    }

    /** Запустить (или перезапустить с новыми настройками). Разрешение VPN должно быть уже получено. */
    fun start(context: Context) {
        Store.wasEnabled = true
        val i = Intent(context, BypassVpnService::class.java).setAction(BypassVpnService.ACTION_START)
        if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(i) else context.startService(i)
    }

    fun stop(context: Context) {
        Store.wasEnabled = false
        if (status == Status.STOPPED) return
        context.startService(Intent(context, BypassVpnService::class.java).setAction(BypassVpnService.ACTION_STOP))
    }

    /** Выбран другой сервер: между серверами VLESS — на лету, иначе — перезапуск. */
    fun switchServer(context: Context) {
        if (!running || kind == "bypass") return
        val target = if (Store.isAuto()) AUTO else Store.activeProfile()?.id ?: return
        val a = api
        if (kind == "vless" && a != null && (target == AUTO || target in groupTags)) {
            io.execute {
                try {
                    a.select(target)
                    serverName = nameOf(target)
                    LogBuffer.add("Сервер: $serverName")
                    refreshGroup()
                } catch (e: Exception) {
                    LogBuffer.add("Переключение не удалось (${e.message}) — перезапуск")
                    main.post { start(context) }
                }
            }
        } else {
            start(context)
        }
    }

    fun nameOf(id: String): String =
        if (id == AUTO) "Авто — лучший сервер" else Store.profiles().firstOrNull { it.id == id }?.name ?: id

    /** Текущий сервер и задержки из sing-box (вызывать не в UI-потоке). */
    fun refreshGroup() {
        val a = api ?: return
        try {
            val g = a.groupState()
            if (g == group) return
            group = g
            synchronized(pings) { g.delays.forEach { (k, v) -> pings[k] = v.toLong() } }
            if (g.delays.isNotEmpty()) pingsReal = true
            notifyChanged()
        } catch (e: Exception) {
            // sing-box ещё стартует
        }
    }

    /** Пинг всех серверов VLESS: через сами серверы, если VPN включён, иначе — время подключения. */
    fun ping() {
        if (pinging) return
        pinging = true
        notifyChanged()
        io.execute {
            val vless = Store.vless()
            val result = mutableMapOf<String, Long?>()
            var real = false
            val a = api
            if (a != null && kind == "vless") {
                try {
                    val delays = a.testDelays()
                    for (p in vless) if (p.id in groupTags) result[p.id] = delays[p.id]?.toLong()
                    real = true
                } catch (e: Exception) {
                    LogBuffer.add("Пинг через sing-box: ${e.message}")
                }
            }
            // Hysteria2 работает по UDP: время TCP-подключения к нему ничего не скажет, оставляем «—»
            val rest = vless.filter { it.id !in result && !Keys.isHysteria2(it.data) }
            val pool = Executors.newFixedThreadPool(8)
            val futures = rest.associate { p ->
                p.id to pool.submit<Long?> {
                    try {
                        val k = Keys.parseVless(p.data)
                        Net.tcpPing(k.server, k.port)
                    } catch (e: Exception) {
                        null
                    }
                }
            }
            futures.forEach { (id, f) -> result[id] = try { f.get() } catch (e: Exception) { null } }
            pool.shutdown()
            synchronized(pings) {
                pings.clear()
                pings.putAll(result)
            }
            pingsReal = real
            pinging = false
            notifyChanged()
        }
    }
}
