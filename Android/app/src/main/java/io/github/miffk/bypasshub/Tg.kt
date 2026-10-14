package io.github.miffk.bypasshub

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.Looper
import io.github.miffk.bypasshub.vpn.TgProxyService

/** TG WS Proxy: локальный MTProto-прокси, который пускает Telegram через WebSocket (как web.telegram.org).
 *  Работает отдельно от VPN и обхода — его можно держать включённым вместе с ними. */
object Tg {
    enum class Status { STOPPED, STARTING, RUNNING }

    const val HOST = "127.0.0.1"
    const val CF_LIST_URL = "https://raw.githubusercontent.com/Flowseal/tg-ws-proxy/main/.github/cfproxy-domains.txt"

    @Volatile var status = Status.STOPPED
        private set
    @Volatile var error: String? = null

    private val main = Handler(Looper.getMainLooper())
    private val listeners = mutableListOf<() -> Unit>()

    fun listen(l: () -> Unit) = listeners.add(l)
    fun unlisten(l: () -> Unit) = listeners.remove(l)

    fun set(s: Status, err: String? = null) {
        status = s
        if (s == Status.STARTING) error = null
        if (err != null) error = err
        main.post { listeners.toList().forEach { it() } }
    }

    val running get() = status == Status.RUNNING

    /** Ссылка, по которой Telegram добавит прокси (секрет dd — со случайным выравниванием пакетов). */
    fun link(): String = "tg://proxy?server=$HOST&port=${Store.tgPort}&secret=dd${Store.tgSecret}"

    fun start(context: Context) {
        Store.tgEnabled = true
        val i = Intent(context, TgProxyService::class.java).setAction(TgProxyService.ACTION_START)
        if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(i) else context.startService(i)
    }

    fun stop(context: Context) {
        Store.tgEnabled = false
        if (status == Status.STOPPED) return
        context.startService(Intent(context, TgProxyService::class.java).setAction(TgProxyService.ACTION_STOP))
    }

    fun restartIfRunning(context: Context) {
        if (status != Status.STOPPED) start(context)
    }
}
