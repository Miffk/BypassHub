package io.github.miffk.bypasshub

import org.json.JSONObject

/** Встроенный API sing-box (127.0.0.1): задержки серверов и переключение на лету. */
class ClashApi(private val port: Int, private val secret: String) {
    private val base get() = "http://127.0.0.1:$port"
    private val auth get() = mapOf("Authorization" to "Bearer $secret")

    private fun get(path: String, timeoutMs: Int = 3000): JSONObject =
        JSONObject(Net.request(base + path, "GET", null, auth, timeoutMs).ifEmpty { "{}" })

    data class GroupState(val selected: String, val current: String, val delays: Map<String, Int>)

    /** Выбрано («auto» или id сервера), используется сейчас, последние задержки по проверкам sing-box. */
    fun groupState(): GroupState {
        val proxies = get("/proxies").getJSONObject("proxies")
        val selected = proxies.optJSONObject("proxy")?.optString("now").orEmpty()
        val auto = proxies.optJSONObject(AUTO)
        val current = if (selected == AUTO) auto?.optString("now").orEmpty() else selected
        val delays = mutableMapOf<String, Int>()
        val all = auto?.optJSONArray("all")
        if (all != null) {
            for (i in 0 until all.length()) {
                val tag = all.getString(i)
                val hist = proxies.optJSONObject(tag)?.optJSONArray("history") ?: continue
                if (hist.length() > 0) {
                    val d = hist.getJSONObject(hist.length() - 1).optInt("delay")
                    if (d > 0) delays[tag] = d
                }
            }
        }
        return GroupState(selected, current, delays)
    }

    /** Проверить задержку всех серверов группы через сами серверы. Спрашиваем у переключателя «proxy»,
     *  а не у «auto»: «auto» сам проверяет серверы (сразу после запуска и раз в минуту) и, пока занят,
     *  отвечает пустым списком; переключатель проверяет каждый сервер напрямую. */
    fun testDelays(timeoutMs: Int = 5000): Map<String, Int> {
        var out = emptyMap<String, Int>()
        for (attempt in 0 until 2) {
            if (attempt > 0) Thread.sleep(1000)
            val res = get("/group/proxy/delay?url=${Config.URLTEST_URL}&timeout=$timeoutMs", timeoutMs + 3000)
            out = res.keys().asSequence().associateWith { res.optInt(it) }
                .filter { (k, v) -> v > 0 && k != AUTO && k != "proxy" }
            if (out.isNotEmpty()) break
        }
        return out
    }

    fun select(tag: String) {
        Net.request("$base/proxies/proxy", "PUT", JSONObject().put("name", tag).toString(), auth, 3000)
    }
}
