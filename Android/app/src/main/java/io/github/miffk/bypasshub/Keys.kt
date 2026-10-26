package io.github.miffk.bypasshub

import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.URLDecoder
import java.security.MessageDigest
import java.util.zip.Inflater

/** Ключи VPN: VLESS (vless://), AmneziaVPN (vpn://), конфиг AmneziaWG (.conf), подписки.
 *  Логика та же, что в BypassHub для Windows (bypasshub/vpn.py и awg.py). */
class KeyException(message: String) : Exception(message)

data class VlessKey(
    val uri: String,
    val name: String,
    val server: String,
    val port: Int,
    val uuid: String,
    val security: String = "none",
    val transport: String = "tcp",
    val flow: String = "",
    val sni: String = "",
    val fingerprint: String = "",
    val publicKey: String = "",
    val shortId: String = "",
    val alpn: List<String> = emptyList(),
    val insecure: Boolean = false,
    val path: String = "",
    val host: String = "",
    val serviceName: String = "",
) {
    val summary: String
        get() {
            val sec = mapOf("reality" to "Reality", "tls" to "TLS", "none" to "без TLS")[security] ?: security
            val tr = mapOf("tcp" to "TCP", "ws" to "WebSocket", "grpc" to "gRPC", "http" to "HTTP/2",
                "httpupgrade" to "HTTPUpgrade")[transport] ?: transport
            return "$server:$port · $sec · $tr"
        }

    /** Исходящее sing-box для этого сервера. */
    fun outbound(tag: String): JSONObject {
        val ob = JSONObject()
            .put("type", "vless").put("tag", tag).put("server", server).put("server_port", port)
            .put("uuid", uuid).put("packet_encoding", "xudp")
        if (flow.isNotEmpty()) ob.put("flow", flow)
        if (security == "tls" || security == "reality") {
            val tls = JSONObject().put("enabled", true)
            val serverName = sni.ifEmpty { if (isIp(server)) "" else server }
            if (serverName.isNotEmpty()) tls.put("server_name", serverName)
            if (alpn.isNotEmpty()) tls.put("alpn", JSONArray(alpn))
            if (insecure) tls.put("insecure", true)
            val fp = if (fingerprint in UTLS_FINGERPRINTS) fingerprint else ""
            if (security == "reality") {
                tls.put("utls", JSONObject().put("enabled", true).put("fingerprint", fp.ifEmpty { "chrome" }))
                tls.put("reality", JSONObject().put("enabled", true).put("public_key", publicKey)
                    .put("short_id", shortId))
            } else if (fp.isNotEmpty()) {
                tls.put("utls", JSONObject().put("enabled", true).put("fingerprint", fp))
            }
            ob.put("tls", tls)
        }
        when (transport) {
            "ws" -> {
                val tr = JSONObject().put("type", "ws")
                var p = path.ifEmpty { "/" }
                val ed = Regex("[?&]ed=(\\d+)").find(p)
                if (ed != null) {
                    p = p.replace(Regex("[?&]ed=\\d+"), "").ifEmpty { "/" }
                    tr.put("max_early_data", ed.groupValues[1].toInt())
                    tr.put("early_data_header_name", "Sec-WebSocket-Protocol")
                }
                tr.put("path", p)
                if (host.isNotEmpty()) tr.put("headers", JSONObject().put("Host", host))
                ob.put("transport", tr)
            }
            "grpc" -> ob.put("transport", JSONObject().put("type", "grpc").put("service_name", serviceName))
            "http" -> {
                val tr = JSONObject().put("type", "http").put("path", path.ifEmpty { "/" })
                val hosts = host.split(",").map { it.trim() }.filter { it.isNotEmpty() }
                if (hosts.isNotEmpty()) tr.put("host", JSONArray(hosts))
                ob.put("transport", tr)
            }
            "httpupgrade" -> {
                val tr = JSONObject().put("type", "httpupgrade").put("path", path.ifEmpty { "/" })
                if (host.isNotEmpty()) tr.put("host", host)
                ob.put("transport", tr)
            }
        }
        return ob
    }

    companion object {
        val UTLS_FINGERPRINTS = setOf("chrome", "firefox", "edge", "safari", "360", "qq", "ios", "android",
            "random", "randomized")
    }
}

/** Конфиг AmneziaWG/WireGuard. */
data class AwgConf(val name: String, val conf: String, val endpoint: String, val address: String) {
    val summary: String get() = "$endpoint · AmneziaWG" + if (address.isNotEmpty()) " · $address" else ""
}

object Keys {
    private val TRANSPORTS = setOf("tcp", "raw", "ws", "grpc", "http", "h2", "httpupgrade")

    fun isIp(s: String): Boolean =
        Regex("^\\d{1,3}(\\.\\d{1,3}){3}$").matches(s) || (s.contains(":") && !s.contains("."))

    private fun decode(s: String): String = try {
        URLDecoder.decode(s.replace("+", "%2B"), "UTF-8")
    } catch (e: Exception) {
        s
    }

    fun parseVless(uriText: String): VlessKey {
        val raw = uriText.trim()
        if (!raw.lowercase().startsWith("vless://")) throw KeyException("Это не ключ VLESS — он должен начинаться с vless://")
        val hashIdx = raw.indexOf('#')
        val fragment = if (hashIdx >= 0) decode(raw.substring(hashIdx + 1)) else ""
        val body = if (hashIdx >= 0) raw.substring(0, hashIdx) else raw
        val noScheme = body.substring(8)
        val authority = noScheme.substringBefore('?').substringBefore('/')
        val query = if (noScheme.contains('?')) noScheme.substringAfter('?') else ""
        val at = authority.lastIndexOf('@')
        if (at <= 0) throw KeyException("В ключе нет идентификатора (UUID)")
        val uuid = decode(authority.substring(0, at)).trim()
        val hostPort = authority.substring(at + 1)
        val server: String
        val portText: String
        if (hostPort.startsWith("[")) {
            server = hostPort.substringAfter("[").substringBefore("]")
            portText = hostPort.substringAfter("]:", "")
        } else {
            server = hostPort.substringBeforeLast(":")
            portText = if (hostPort.contains(":")) hostPort.substringAfterLast(":") else ""
        }
        val port = portText.toIntOrNull() ?: -1
        if (uuid.isEmpty() || server.isEmpty() || port <= 0 || port > 65535) {
            throw KeyException("В ключе нет адреса сервера, порта или идентификатора (UUID)")
        }
        val q = mutableMapOf<String, String>()
        query.split("&").filter { it.isNotEmpty() }.forEach {
            val k = it.substringBefore("=")
            val v = if (it.contains("=")) it.substringAfter("=") else ""
            q[decode(k)] = decode(v).trim()
        }
        val encryption = (q["encryption"] ?: "none").lowercase()
        if (encryption.isNotEmpty() && encryption != "none") {
            throw KeyException("Шифрование VLESS «$encryption» пока не поддерживается")
        }
        var transport = (q["type"] ?: "tcp").lowercase().ifEmpty { "tcp" }
        if (transport == "xhttp" || transport == "splithttp") {
            throw KeyException("Ключи с транспортом XHTTP пока не поддерживаются. Попросите у VPN-сервиса ключ " +
                "с TCP (Reality), WebSocket или gRPC.")
        }
        if (transport !in TRANSPORTS) throw KeyException("Транспорт «$transport» не поддерживается")
        transport = mapOf("raw" to "tcp", "h2" to "http")[transport] ?: transport
        if (transport == "tcp" && (q["headerType"] ?: "none").lowercase() !in setOf("", "none")) {
            throw KeyException("Маскировка TCP под HTTP (headerType=http) не поддерживается")
        }
        val security = (q["security"] ?: "none").lowercase().ifEmpty { "none" }
        if (security !in setOf("none", "tls", "reality")) throw KeyException("Тип защиты «$security» не поддерживается")
        var flow = q["flow"] ?: ""
        if (flow.startsWith("xtls-rprx-vision")) flow = "xtls-rprx-vision"
        else if (flow.isNotEmpty()) throw KeyException("Режим flow «$flow» не поддерживается")
        val key = VlessKey(
            uri = raw, name = fragment.trim().ifEmpty { "$server:$port" }, server = server, port = port, uuid = uuid,
            security = security, transport = transport, flow = flow,
            sni = q["sni"].orEmpty().ifEmpty { q["peer"].orEmpty() }, fingerprint = (q["fp"] ?: "").lowercase(),
            publicKey = q["pbk"].orEmpty(), shortId = q["sid"].orEmpty(),
            alpn = (q["alpn"] ?: "").split(",").map { it.trim() }.filter { it.isNotEmpty() },
            insecure = (q["allowInsecure"] ?: q["insecure"] ?: "").lowercase() in setOf("1", "true"),
            path = q["path"].orEmpty(), host = q["host"].orEmpty(), serviceName = q["serviceName"].orEmpty(),
        )
        if (security == "reality") {
            if (key.publicKey.isEmpty()) throw KeyException("В ключе Reality нет открытого ключа сервера (pbk)")
            if (key.sni.isEmpty()) throw KeyException("В ключе Reality нет имени сайта для маскировки (sni)")
        }
        return key
    }

    // ---------------------------------------------------------------- AmneziaVPN vpn://
    private fun b64(text: String): ByteArray {
        val compact = text.replace(Regex("\\s"), "")
        return try {
            Base64.decode(compact, Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
        } catch (e: IllegalArgumentException) {
            Base64.decode(compact, Base64.DEFAULT)
        }
    }

    private fun inflate(data: ByteArray): ByteArray? = try {
        val inf = Inflater()
        inf.setInput(data)
        val out = ByteArrayOutputStream()
        val buf = ByteArray(8192)
        while (!inf.finished()) {
            val n = inf.inflate(buf)
            if (n == 0 && (inf.needsInput() || inf.needsDictionary())) break
            out.write(buf, 0, n)
        }
        inf.end()
        if (inf.finished()) out.toByteArray() else null
    } catch (e: Exception) {
        null
    }

    fun decodeAmneziaKey(key: String): JSONObject {
        var body = key.trim()
        if (body.lowercase().startsWith("vpn://")) body = body.substring(6)
        val raw = try {
            b64(body)
        } catch (e: Exception) {
            throw KeyException("Ключ AmneziaVPN повреждён — скопируйте его целиком")
        }
        val json = (if (raw.size > 4) inflate(raw.copyOfRange(4, raw.size)) else null) ?: inflate(raw) ?: raw
        return try {
            JSONObject(String(json, Charsets.UTF_8))
        } catch (e: Exception) {
            throw KeyException("Ключ AmneziaVPN повреждён — скопируйте его целиком")
        }
    }

    fun confFromAmneziaKey(key: String): AwgConf {
        val data = decodeAmneziaKey(key)
        val name = data.optString("description").ifEmpty { data.optString("hostName") }.ifEmpty { "AmneziaWG" }
        val dns1 = data.optString("dns1").ifEmpty { "1.1.1.1" }
        val dns2 = data.optString("dns2")
        val containers = data.optJSONArray("containers") ?: JSONArray()
        for (i in 0 until containers.length()) {
            val container = containers.optJSONObject(i) ?: continue
            for (proto in container.keys()) {
                val value = container.optJSONObject(proto) ?: continue
                if (!value.has("last_config")) continue
                val lastRaw = value.get("last_config")
                val last = try {
                    if (lastRaw is JSONObject) lastRaw else JSONObject(lastRaw.toString())
                } catch (e: Exception) {
                    continue
                }
                var conf = last.optString("config")
                if (!conf.contains("[Interface]")) continue
                if (proto != "awg" && proto != "wireguard") {
                    throw KeyException("В ключе протокол «$proto» — BypassHub поддерживает AmneziaWG и VLESS")
                }
                conf = conf.replace("\$PRIMARY_DNS", dns1).replace("\$SECONDARY_DNS", dns2)
                    .replace(Regex("(?im)^(\\s*DNS\\s*=.*?)[,\\s]+$"), "$1")
                val mtu = last.optString("mtu").trim()
                if (mtu.all { it.isDigit() } && mtu.isNotEmpty() && !Regex("(?im)^\\s*MTU\\s*=").containsMatchIn(conf)) {
                    conf = conf.replaceFirst(Regex("(?im)^(\\s*\\[Interface\\]\\s*)$"), "$1\nMTU = $mtu")
                }
                return parseConf(conf, name)
            }
        }
        if (data.has("api_config") || data.has("auth_data") || data.has("api_key")) {
            throw KeyException("Это ключ подписки Amnezia Premium — в нём нет настроек сервера. " +
                "Подойдёт ключ от своего сервера (AmneziaVPN → «Поделиться»).")
        }
        throw KeyException("В ключе нет настроек AmneziaWG. В AmneziaVPN откройте «Поделиться» и выберите AmneziaWG.")
    }

    fun looksLikeConf(text: String): Boolean =
        text.lowercase().contains("[interface]") && text.lowercase().contains("[peer]")

    fun parseConf(text: String, name: String = ""): AwgConf {
        val conf = text.replace("\r\n", "\n").trim() + "\n"
        var section = ""
        val iface = mutableMapOf<String, String>()
        val peer = mutableMapOf<String, String>()
        var peers = 0
        for (rawLine in conf.lines()) {
            val line = rawLine.substringBefore("#").trim()
            if (line.isEmpty()) continue
            val m = Regex("^\\[(\\w+)\\]$").find(line)
            if (m != null) {
                section = m.groupValues[1].lowercase()
                if (section == "peer") peers++
                continue
            }
            if (!line.contains("=")) continue
            val k = line.substringBefore("=").trim().lowercase()
            val v = line.substringAfter("=").trim()
            if (section == "interface") iface[k] = v else if (section == "peer" && peers == 1) peer[k] = v
        }
        if (iface["privatekey"].isNullOrEmpty()) throw KeyException("В конфиге нет [Interface] с PrivateKey")
        if (peer["publickey"].isNullOrEmpty() || peer["endpoint"].isNullOrEmpty()) {
            throw KeyException("В конфиге нет [Peer] с PublicKey и Endpoint")
        }
        val endpoint = peer["endpoint"]!!
        return AwgConf(name.trim().ifEmpty { endpoint.substringBeforeLast(":") }, conf, endpoint, iface["address"] ?: "")
    }

    // ---------------------------------------------------------------- профили
    fun profileId(kind: String, data: String): String {
        val digest = MessageDigest.getInstance("SHA-1").digest(data.trim().toByteArray())
        return "$kind:" + digest.joinToString("") { "%02x".format(it) }.substring(0, 12)
    }

    fun vlessProfile(key: VlessKey, sub: String = "") = Profile(profileId("vless", key.uri), "vless", key.name, key.uri, sub)
    fun awgProfile(c: AwgConf) = Profile(profileId("awg", c.conf), "awg", c.name, c.conf)

    data class Parsed(val profiles: MutableList<Profile> = mutableListOf(),
                      val subscriptions: MutableList<String> = mutableListOf(),
                      val problems: MutableList<String> = mutableListOf())

    private fun decodeBase64Text(text: String): String? = try {
        String(b64(text), Charsets.UTF_8).takeIf { it.contains("://") }
    } catch (e: Exception) {
        null
    }

    /** Разобранная подписка: ключи, предупреждения и сообщения сервиса вместо серверов. */
    data class SubParse(val keys: List<VlessKey>, val problems: List<String>, val notices: List<String>)

    // Адреса, на которых сервис присылает не сервер, а сообщение в названии «ключа» (Remnawave так отвечает,
    // когда достигнут лимит устройств, программа не прислала код устройства, подписка истекла и т.п.)
    private val PLACEHOLDER_HOSTS = setOf("0.0.0.0", "127.0.0.1", "::", "::1", "localhost")

    fun isPlaceholder(k: VlessKey) = k.server.lowercase() in PLACEHOLDER_HOSTS || k.port <= 1

    private const val BROKEN_VLESS = "vless?"  // сервер VLESS, который не удалось разобрать

    private val PROXY_PROTOCOLS = setOf("vmess", "trojan", "shadowsocks", "hysteria", "hysteria2", "tuic",
        "wireguard", "socks", "http")

    /** Строка из JSON; пусто, если поля нет или там null (org.json отдал бы «null»). */
    private fun JSONObject.s(key: String): String = if (isNull(key)) "" else optString(key, "")

    private fun enc(s: String): String = java.net.URLEncoder.encode(s, "UTF-8").replace("+", "%20")

    /** Подписка в формате Xray JSON (так её отдают Happ и Streisand): из каждого конфига берётся исходящее
     *  VLESS и пересобирается в ссылку vless://. null — это не JSON. В skipped — протоколы конфигов без VLESS. */
    fun xrayJsonLinks(text: String, skipped: MutableList<String>): List<String>? {
        val t = text.trim()
        if (!t.startsWith("[") && !t.startsWith("{")) return null
        val configs: List<JSONObject> = try {
            if (t.startsWith("[")) JSONArray(t).let { a -> (0 until a.length()).mapNotNull { a.optJSONObject(it) } }
            else listOf(JSONObject(t))
        } catch (e: Exception) {
            return null
        }
        val links = mutableListOf<String>()
        for (cfg in configs) {
            val name = cfg.s("remarks")
            val outbounds = cfg.optJSONArray("outbounds") ?: JSONArray()
            var found = false
            var other = ""
            for (i in 0 until outbounds.length()) {
                val ob = outbounds.optJSONObject(i) ?: continue
                val proto = ob.s("protocol")
                if (proto != "vless") {
                    if (other.isEmpty() && proto in PROXY_PROTOCOLS) other = proto
                    continue
                }
                val settings = ob.optJSONObject("settings") ?: JSONObject()
                val vnext = settings.optJSONArray("vnext")
                // упрощённая запись Xray 25+: адрес, порт и id прямо в settings
                val srv = if (vnext != null && vnext.length() > 0) vnext.optJSONObject(0) else settings
                val user = if (vnext != null && vnext.length() > 0) srv?.optJSONArray("users")?.optJSONObject(0) else settings
                val host = srv?.s("address").orEmpty()
                val port = srv?.optInt("port", -1) ?: -1
                val uid = user?.s("id").orEmpty()
                if (srv == null || user == null || host.isEmpty() || uid.isEmpty() || port <= 0) {
                    other = BROKEN_VLESS
                    continue
                }
                val ss = ob.optJSONObject("streamSettings") ?: JSONObject()
                val net = ss.s("network").ifEmpty { "tcp" }
                val sec = ss.s("security").ifEmpty { "none" }
                val q = linkedMapOf("encryption" to user.s("encryption").ifEmpty { "none" },
                    "type" to net, "security" to sec, "flow" to user.s("flow"))
                if (sec == "reality") {
                    val r = ss.optJSONObject("realitySettings") ?: JSONObject()
                    q["sni"] = r.s("serverName")
                    q["pbk"] = r.s("publicKey")
                    q["sid"] = r.s("shortId")
                    q["fp"] = r.s("fingerprint")
                    q["spx"] = r.s("spiderX")
                } else if (sec == "tls") {
                    val tl = ss.optJSONObject("tlsSettings") ?: JSONObject()
                    q["sni"] = tl.s("serverName")
                    q["fp"] = tl.s("fingerprint")
                    val alpn = tl.optJSONArray("alpn")
                    if (alpn != null) q["alpn"] = (0 until alpn.length()).joinToString(",") { alpn.optString(it) }
                    if (tl.optBoolean("allowInsecure", false)) q["allowInsecure"] = "1"
                }
                when (net) {
                    "ws" -> {
                        val w = ss.optJSONObject("wsSettings") ?: JSONObject()
                        q["path"] = w.s("path")
                        q["host"] = w.optJSONObject("headers")?.s("Host").orEmpty().ifEmpty { w.s("host") }
                    }
                    "grpc" -> q["serviceName"] = ss.optJSONObject("grpcSettings")?.s("serviceName").orEmpty()
                    "httpupgrade" -> {
                        val h = ss.optJSONObject("httpupgradeSettings") ?: JSONObject()
                        q["path"] = h.s("path")
                        q["host"] = h.s("host")
                    }
                    "xhttp" -> {
                        val x = ss.optJSONObject("xhttpSettings") ?: JSONObject()
                        q["path"] = x.s("path")
                        q["host"] = x.s("host")
                    }
                }
                val query = q.filterValues { it.isNotEmpty() }.entries.joinToString("&") { "${it.key}=${enc(it.value)}" }
                val hostPart = if (host.contains(":")) "[$host]" else host
                links.add("vless://${enc(uid)}@$hostPart:$port?$query#${enc(name.ifEmpty { host })}")
                found = true
                break  // в конфиге одно исходящее «proxy», остальные — direct/block
            }
            if (!found && other.isNotEmpty()) skipped.add(other)
        }
        return links
    }

    /** Ключи VLESS из ответа подписки (ссылки, base64 или Xray JSON). Заглушки сервиса — в notices. */
    fun parseSubscription(text: String): SubParse {
        var t = text
        val jsonSkipped = mutableListOf<String>()
        xrayJsonLinks(t, jsonSkipped)?.let { t = it.joinToString("\n") }
        if (!t.contains("://")) decodeBase64Text(t)?.let { t = it }
        val keys = mutableListOf<VlessKey>()
        val problems = mutableListOf<String>()
        val notices = mutableListOf<String>()
        var other = 0
        for (rawLine in t.lines()) {
            val line = rawLine.trim()
            if (line.isEmpty() || !line.contains("://")) continue
            if (!line.lowercase().startsWith("vless://")) {
                other++
                continue
            }
            try {
                val k = parseVless(line)
                if (isPlaceholder(k)) notices.add(k.name) else keys.add(k)
            } catch (e: KeyException) {
                val name = if (line.contains("#")) decode(line.substringAfterLast("#")) else line.take(40) + "…"
                problems.add("$name: ${e.message}")
            }
        }
        val broken = jsonSkipped.count { it == BROKEN_VLESS }
        jsonSkipped.removeAll { it == BROKEN_VLESS }
        if (broken > 0) problems.add("не удалось разобрать серверов VLESS в формате Xray JSON: $broken")
        other += jsonSkipped.size
        if (other > 0) {
            val kinds = if (jsonSkipped.isNotEmpty()) " (${jsonSkipped.toSortedSet().joinToString(", ")})" else ""
            problems.add("пропущено ключей других типов (не VLESS)$kinds: $other")
        }
        return SubParse(keys, problems, notices)
    }

    /** Ключи VLESS из вставленного текста: сообщения сервиса идут в предупреждения. */
    fun parseVlessList(text: String): Pair<List<VlessKey>, List<String>> {
        val p = parseSubscription(text)
        return p.keys to (p.problems + p.notices.map { "сервис подписки сообщает: «$it»" })
    }

    /** Что угодно из буфера обмена: vless://, vpn://, .conf, ссылка на подписку. */
    fun parseInput(text: String): Parsed {
        val out = Parsed()
        val t = text.trim()
        if (t.isEmpty()) return out
        if (looksLikeConf(t)) {
            try {
                out.profiles.add(awgProfile(parseConf(t)))
            } catch (e: KeyException) {
                out.problems.add(e.message ?: "ошибка")
            }
            return out
        }
        val rest = mutableListOf<String>()
        for (rawLine in t.lines()) {
            val line = rawLine.trim()
            val low = line.lowercase()
            when {
                low.startsWith("vpn://") -> try {
                    out.profiles.add(awgProfile(confFromAmneziaKey(line)))
                } catch (e: KeyException) {
                    out.problems.add(e.message ?: "ошибка")
                }
                low.startsWith("http://") || low.startsWith("https://") -> out.subscriptions.add(line)
                line.isNotEmpty() -> rest.add(line)
            }
        }
        if (rest.isNotEmpty()) {
            val (keys, problems) = parseVlessList(rest.joinToString("\n"))
            keys.forEach { out.profiles.add(vlessProfile(it)) }
            out.problems.addAll(problems)
            if (keys.isEmpty() && problems.isEmpty() && out.profiles.isEmpty() && out.subscriptions.isEmpty()) {
                out.problems.add("Не нашёл ключей. Подходят vless://…, vpn://… (AmneziaVPN), конфиг AmneziaWG или ссылка на подписку.")
            }
        }
        return out
    }
}

private fun isIp(s: String) = Keys.isIp(s)
