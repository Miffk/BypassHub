package io.github.miffk.bypasshub

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Обход блокировок без VPN-сервера и без root: весь трафик идёт напрямую, а к заблокированным
 *  сайтам sing-box разбивает начало TLS-соединения (ClientHello) на части — так провайдерский DPI
 *  не видит имя сайта целиком. QUIC (UDP 443) к этим сайтам блокируется, чтобы приложения
 *  (YouTube и др.) переходили на TCP, где разбиение работает.
 *
 *  Списки сайтов — те же, что у zapret-discord-youtube (Flowseal), плюс свои сайты. */
object Bypass {
    private const val RAW = "https://raw.githubusercontent.com/Flowseal/zapret-discord-youtube/main/lists/"
    val LISTS = listOf("list-general.txt", "list-google.txt")
    private const val MAX_AGE_MS = 24L * 3600 * 1000

    const val METHOD_TCP = "tcp"         // разбивать TCP-сегменты (tls_fragment)
    const val METHOD_RECORD = "record"   // разбивать TLS-записи (tls_record_fragment)

    /** Встроенный список — если списки ещё не скачались. */
    val BUILTIN = listOf(
        "youtube.com", "youtu.be", "googlevideo.com", "ytimg.com", "ggpht.com", "youtube-nocookie.com",
        "youtubei.googleapis.com", "youtube.googleapis.com", "yt3.googleusercontent.com", "jnn-pa.googleapis.com",
        "discord.com", "discord.gg", "discord.media", "discordapp.com", "discordapp.net", "discordcdn.com",
        "discord.app", "discord.dev", "discord.gift", "discordstatus.com", "dis.gd",
        "play.google.com", "twitch.tv", "live-video.net",
    )

    private val RU_SUFFIXES = listOf("ru", "su", "xn--p1ai")

    fun dir(context: Context) = File(context.filesDir, "bypass")

    /** Скачать списки, если их нет или они старше суток (вызывать не в UI-потоке). */
    fun update(context: Context, force: Boolean = false) {
        val now = System.currentTimeMillis()
        for (name in LISTS) {
            val f = File(dir(context), name)
            if (!force && f.exists() && f.length() > 0 && now - f.lastModified() < MAX_AGE_MS) continue
            try {
                Net.download(RAW + name, f)
            } catch (e: Exception) {
                LogBuffer.add("Обход: не удалось скачать $name: ${e.message}")
            }
        }
    }

    fun age(context: Context): Long? =
        LISTS.map { File(dir(context), it) }.filter { it.exists() }.maxOfOrNull { it.lastModified() }

    /** Строки списка → (домены с поддоменами, точные домены). «^домен» в списках zapret — без поддоменов. */
    fun parse(lines: Sequence<String>): Pair<Set<String>, Set<String>> {
        val suffix = linkedSetOf<String>()
        val exact = linkedSetOf<String>()
        for (raw in lines) {
            var s = raw.substringBefore('#').trim().lowercase()
            if (s.isEmpty()) continue
            s = s.removePrefix("http://").removePrefix("https://").substringBefore('/').removePrefix("*.").trimEnd('.')
            val isExact = s.startsWith("^")
            s = s.removePrefix("^").removePrefix(".")
            if (!Regex("^[a-z0-9.-]+\\.[a-z0-9-]{2,}$").matches(s)) continue
            if (isExact) exact.add(s) else suffix.add(s)
        }
        return suffix to exact
    }

    /** Все сайты для обхода: скачанные списки (или встроенный), плюс свои. */
    fun domains(context: Context, user: String): Pair<Set<String>, Set<String>> {
        val files = LISTS.map { File(dir(context), it) }.filter { it.exists() && it.length() > 0 }
        val base = if (files.isEmpty()) BUILTIN.asSequence() else files.asSequence().flatMap { it.readLines().asSequence() }
        val (suffix, exact) = parse(base + BUILTIN.asSequence() + user.lineSequence())
        return suffix to (exact - suffix)
    }

    /** Конфиг sing-box: TUN, всё напрямую, к сайтам из списков — с разбиением TLS. */
    fun config(suffix: Set<String>, exact: Set<String>, allSites: Boolean, method: String,
               apiPort: Int, apiSecret: String): String {
        fun match(o: JSONObject): JSONObject {
            if (suffix.isNotEmpty()) o.put("domain_suffix", JSONArray(suffix.toList()))
            if (exact.isNotEmpty()) o.put("domain", JSONArray(exact.toList()))
            return o
        }
        val fragment = JSONObject().put("action", "route-options")
        if (method == METHOD_RECORD) fragment.put("tls_record_fragment", true) else fragment.put("tls_fragment", true)

        val rules = JSONArray()
        rules.put(JSONObject().put("action", "sniff"))
        rules.put(JSONObject().put("protocol", "dns").put("action", "hijack-dns"))
        rules.put(JSONObject().put("ip_is_private", true).put("outbound", "direct"))
        if (allSites) {
            // российские сайты — без изменений, остальное — с разбиением и без QUIC
            rules.put(JSONObject().put("domain_suffix", JSONArray(RU_SUFFIXES)).put("outbound", "direct"))
            rules.put(JSONObject().put("network", "udp").put("port", 443).put("action", "reject"))
            rules.put(JSONObject(fragment.toString()).put("protocol", "tls"))
        } else {
            rules.put(match(JSONObject().put("network", "udp").put("port", 443).put("action", "reject")))
            rules.put(match(JSONObject(fragment.toString())))
        }

        val cfg = JSONObject()
            .put("log", JSONObject().put("level", "info").put("timestamp", true))
            .put("dns", JSONObject()
                .put("servers", JSONArray()
                    // DNS провайдера может подменять адреса заблокированных сайтов — спрашиваем Google.
                    // Обычный UDP, а не DNS по HTTPS: соединения с Cloudflare и другими DoH в России часто режут
                    .put(JSONObject().put("type", "udp").put("tag", "dns-remote").put("server", "8.8.8.8"))
                    .put(JSONObject().put("type", "udp").put("tag", "dns-direct").put("server", "77.88.8.8")))
                .put("rules", JSONArray().put(JSONObject().put("domain_suffix", JSONArray(RU_SUFFIXES)).put("server", "dns-direct")))
                .put("final", "dns-remote")
                .put("strategy", "ipv4_only"))
            .put("inbounds", JSONArray().put(JSONObject()
                .put("type", "tun").put("tag", "tun-in")
                .put("address", JSONArray().put("172.19.0.1/30").put("fdfe:dcba:9876::1/126"))
                .put("mtu", 9000).put("auto_route", true)
                // gvisor: весь TCP/UDP обрабатывается внутри ядра. Стек system/mixed принимает TCP локальным
                // сокетом, а BypassHub исключён из своего VPN — ответы этого сокета ушли бы мимо TUN
                .put("stack", "gvisor")))
            .put("outbounds", JSONArray().put(JSONObject().put("type", "direct").put("tag", "direct")))
            .put("route", JSONObject()
                .put("rules", rules)
                .put("final", "direct")
                .put("auto_detect_interface", true)
                .put("default_domain_resolver", "dns-direct"))
            .put("experimental", JSONObject().put("clash_api", JSONObject()
                .put("external_controller", "127.0.0.1:$apiPort").put("secret", apiSecret)))
        return cfg.toString(2)
    }
}
