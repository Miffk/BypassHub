package io.github.miffk.bypasshub

import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** Конфиг sing-box. Тот же, что у BypassHub для Windows (bypasshub/vpn.py), но для Android:
 *  только режим TUN, DNS без «local» (на Android Go не читает системные DNS). */
object Config {
    const val URLTEST_URL = "https://www.gstatic.com/generate_204"
    private val RU_SUFFIXES = listOf("ru", "su", "xn--p1ai")

    // Раздельное туннелирование — базы roscomvpn (см. Rules)
    val PROXY_SETS = listOf("rc-youtube", "rc-telegram", "rc-github", "rc-google-play")
    val DIRECT_SITE_SETS = listOf("rc-category-ru", "rc-whitelist")
    val DIRECT_IP_SETS = listOf("rc-direct-ip")

    /** Несколько серверов: каждый со своим тегом, «auto» — лучший по задержке,
     *  «proxy» — переключатель (его можно менять на лету через API). */
    fun serverGroup(servers: List<JSONObject>, selected: String): List<JSONObject> {
        val tags = servers.map { it.getString("tag") }
        val sel = if (selected == AUTO || selected in tags) selected else tags.first()
        return servers + listOf(
            JSONObject().put("type", "urltest").put("tag", AUTO).put("outbounds", JSONArray(tags))
                .put("url", URLTEST_URL).put("interval", "1m").put("tolerance", 50).put("idle_timeout", "30m"),
            JSONObject().put("type", "selector").put("tag", "proxy").put("outbounds", JSONArray(listOf(AUTO) + tags))
                .put("default", sel).put("interrupt_exist_connections", true),
        )
    }

    /** Исходящее в туннель AmneziaWG (локальный SOCKS5 ядра). */
    fun awgOutbound(socksPort: Int): JSONObject = JSONObject().put("type", "socks").put("tag", "proxy")
        .put("server", "127.0.0.1").put("server_port", socksPort).put("version", "5")

    fun build(proxy: List<JSONObject>, ruDirect: Boolean, rules: Map<String, File>, apiPort: Int, apiSecret: String,
              bypass: List<String> = emptyList()): String {
        val sets = if (ruDirect) rules.filterValues { it.exists() && it.length() > 0 } else emptyMap()
        val has = Rules.SETS.keys.filter { it in sets }
        val bypassCidr = bypass.map { if (it.contains("/")) it else if (it.contains(":")) "$it/128" else "$it/32" }

        val dnsRules = JSONArray()
        val routeRules = JSONArray()
        routeRules.put(JSONObject().put("action", "sniff"))
        routeRules.put(JSONObject().put("protocol", "dns").put("action", "hijack-dns"))
        if (bypassCidr.isNotEmpty()) routeRules.put(JSONObject().put("ip_cidr", JSONArray(bypassCidr)).put("outbound", "direct"))
        routeRules.put(JSONObject().put("ip_is_private", true).put("outbound", "direct"))
        if (ruDirect) {
            val proxySets = PROXY_SETS.filter { it in has }
            if (proxySets.isNotEmpty()) {
                dnsRules.put(JSONObject().put("rule_set", JSONArray(proxySets)).put("server", "dns-remote"))
                routeRules.put(JSONObject().put("rule_set", JSONArray(proxySets)).put("outbound", "proxy"))
            }
            dnsRules.put(JSONObject().put("domain_suffix", JSONArray(RU_SUFFIXES)).put("server", "dns-direct"))
            routeRules.put(JSONObject().put("domain_suffix", JSONArray(RU_SUFFIXES)).put("outbound", "direct"))
            val siteSets = DIRECT_SITE_SETS.filter { it in has }
            if (siteSets.isNotEmpty()) {
                dnsRules.put(JSONObject().put("rule_set", JSONArray(siteSets)).put("server", "dns-direct"))
                routeRules.put(JSONObject().put("rule_set", JSONArray(siteSets)).put("outbound", "direct"))
            }
            val ipSets = DIRECT_IP_SETS.filter { it in has }
            if (ipSets.isNotEmpty()) routeRules.put(JSONObject().put("rule_set", JSONArray(ipSets)).put("outbound", "direct"))
        }

        val route = JSONObject()
            .put("rules", routeRules)
            .put("final", "proxy")
            .put("auto_detect_interface", true)
            .put("default_domain_resolver", "dns-direct")
        if (has.isNotEmpty()) {
            route.put("rule_set", JSONArray(has.map {
                JSONObject().put("type", "local").put("tag", it).put("format", "binary").put("path", sets.getValue(it).path)
            }))
        }
        val cfg = JSONObject()
            .put("log", JSONObject().put("level", "info").put("timestamp", true))
            .put("dns", JSONObject()
                .put("servers", JSONArray()
                    .put(JSONObject().put("type", "https").put("tag", "dns-remote").put("server", "1.1.1.1")
                        .put("detour", "proxy"))
                    // напрямую: Яндекс DNS (работает в России везде, как в профиле roscomvpn)
                    .put(JSONObject().put("type", "udp").put("tag", "dns-direct").put("server", "77.88.8.8")))
                .put("rules", dnsRules)
                .put("final", "dns-remote")
                .put("strategy", "ipv4_only"))
            .put("inbounds", JSONArray().put(JSONObject()
                .put("type", "tun").put("tag", "tun-in")
                // IPv6 тоже в TUN — иначе приложения ходили бы по IPv6 мимо VPN
                .put("address", JSONArray().put("172.19.0.1/30").put("fdfe:dcba:9876::1/126"))
                .put("mtu", 9000).put("auto_route", true)
                // gvisor: весь TCP/UDP обрабатывается внутри ядра. Стек system/mixed принимает TCP локальным
                // сокетом, а BypassHub исключён из своего VPN — ответы этого сокета ушли бы мимо TUN
                .put("stack", "gvisor")))
            .put("outbounds", JSONArray(proxy + JSONObject().put("type", "direct").put("tag", "direct")))
            .put("route", route)
            .put("experimental", JSONObject().put("clash_api", JSONObject()
                .put("external_controller", "127.0.0.1:$apiPort").put("secret", apiSecret)))
        return cfg.toString(2)
    }
}
