package io.github.miffk.bypasshub

import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URL

/** Простые HTTP-запросы (подписки, обновления, базы, API sing-box). Вызывать не в UI-потоке. */
object Net {
    private const val UA = "BypassHub-Android/${BuildConfig.VERSION_NAME}"

    /** proxyPort > 0 — через локальный HTTP-прокси sing-box, то есть через включённый VPN
     *  (само приложение исключено из своего VPN и без этого ходит напрямую). */
    private fun open(url: String, method: String = "GET", timeoutMs: Int = 20000,
                     headers: Map<String, String> = emptyMap(), proxyPort: Int = 0,
                     follow: Boolean = true): HttpURLConnection {
        val u = URL(url)
        val c = (if (proxyPort > 0) u.openConnection(java.net.Proxy(java.net.Proxy.Type.HTTP,
                    InetSocketAddress("127.0.0.1", proxyPort)))
                 else u.openConnection()) as HttpURLConnection
        c.requestMethod = method
        c.connectTimeout = timeoutMs
        c.readTimeout = timeoutMs
        c.instanceFollowRedirects = follow
        c.setRequestProperty("User-Agent", UA)
        headers.forEach { (k, v) -> c.setRequestProperty(k, v) }
        return c
    }

    fun get(url: String, timeoutMs: Int = 20000, headers: Map<String, String> = emptyMap(), proxyPort: Int = 0): String {
        val c = open(url, timeoutMs = timeoutMs, headers = headers, proxyPort = proxyPort)
        try {
            if (c.responseCode !in 200..299) throw IOException("HTTP ${c.responseCode}")
            return c.inputStream.use { it.readBytes().toString(Charsets.UTF_8) }
        } finally {
            c.disconnect()
        }
    }

    fun request(url: String, method: String, body: String?, headers: Map<String, String>, timeoutMs: Int): String {
        val c = open(url, method, timeoutMs, headers)
        try {
            if (body != null) {
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                c.outputStream.use { it.write(body.toByteArray()) }
            }
            val code = c.responseCode
            val stream = if (code in 200..299) c.inputStream else c.errorStream
            val text = stream?.use { it.readBytes().toString(Charsets.UTF_8) } ?: ""
            if (code !in 200..299) throw IOException("HTTP $code $text")
            return text
        } finally {
            c.disconnect()
        }
    }

    /** Скачать файл; progress(готово, всего) — всего может быть -1. */
    fun download(url: String, dest: File, progress: ((Long, Long) -> Unit)? = null, proxyPort: Int = 0) {
        val c = open(url, timeoutMs = 30000, proxyPort = proxyPort)
        try {
            if (c.responseCode !in 200..299) throw IOException("HTTP ${c.responseCode}")
            val total = c.contentLengthLong
            val tmp = File(dest.path + ".part")
            dest.parentFile?.mkdirs()
            c.inputStream.use { input ->
                tmp.outputStream().use { out ->
                    val buf = ByteArray(64 * 1024)
                    var done = 0L
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        done += n
                        progress?.invoke(done, total)
                    }
                }
            }
            if (!tmp.renameTo(dest)) {
                tmp.copyTo(dest, overwrite = true)
                tmp.delete()
            }
        } finally {
            c.disconnect()
        }
    }

    fun getJson(url: String): JSONObject = JSONObject(get(url))

    /** Куда перенаправляет адрес (заголовок Location) — без перехода. */
    fun redirect(url: String, proxyPort: Int = 0, timeoutMs: Int = 15000): String? {
        val c = open(url, timeoutMs = timeoutMs, proxyPort = proxyPort, follow = false)
        try {
            c.responseCode
            return c.getHeaderField("Location")
        } finally {
            c.disconnect()
        }
    }

    /** Порты для запроса: сначала через VPN (если он включён), потом напрямую. */
    fun routes(): List<Int> = listOfNotNull(Vpn.localProxy.takeIf { it > 0 }, 0)

    /** GET с запасным путём: через VPN, затем напрямую. */
    fun getAny(url: String, timeoutMs: Int = 20000, headers: Map<String, String> = emptyMap()): String {
        var last: Exception? = null
        for (port in routes()) {
            try {
                return get(url, timeoutMs, headers, port)
            } catch (e: Exception) {
                last = e
            }
        }
        throw last ?: IOException("нет сети")
    }

    /** Время TCP-подключения к серверу, мс (null — не отвечает). */
    fun tcpPing(host: String, port: Int, timeoutMs: Int = 4000): Long? = try {
        val start = System.nanoTime()
        Socket().use { it.connect(InetSocketAddress(host, port), timeoutMs) }
        (System.nanoTime() - start) / 1_000_000
    } catch (e: Exception) {
        null
    }
}
