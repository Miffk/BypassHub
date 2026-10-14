package io.github.miffk.bypasshub

import android.content.Context
import android.content.SharedPreferences
import org.json.JSONArray
import org.json.JSONObject

/** Сервер в списке: kind = vless (data — ключ vless://) или awg (data — текст .conf). */
data class Profile(val id: String, val kind: String, var name: String, val data: String, val sub: String = "") {
    val summary: String
        get() = try {
            if (kind == "awg") Keys.parseConf(data, name).summary else Keys.parseVless(data).summary
        } catch (e: KeyException) {
            "ошибка: ${e.message}"
        }

    fun toJson(): JSONObject = JSONObject().put("id", id).put("kind", kind).put("name", name).put("data", data)
        .put("sub", sub)

    companion object {
        fun fromJson(o: JSONObject): Profile? = try {
            Profile(o.getString("id"), o.getString("kind"), o.optString("name"), o.getString("data"), o.optString("sub"))
        } catch (e: Exception) {
            null
        }
    }
}

const val AUTO = "auto"

/** Настройки и серверы (SharedPreferences). */
object Store {
    private lateinit var prefs: SharedPreferences

    fun init(context: Context) {
        prefs = context.getSharedPreferences("bypasshub", Context.MODE_PRIVATE)
    }

    // ---------------------------------------------------------------- настройки
    var active: String
        get() = prefs.getString("active", "") ?: ""
        set(value) = prefs.edit().putString("active", value).apply()

    /** Раздельное туннелирование: российское — напрямую. */
    var ruDirect: Boolean
        get() = prefs.getBoolean("ru_direct", true)
        set(value) = prefs.edit().putBoolean("ru_direct", value).apply()

    /** Что запускает служба: «vpn» — свой VPN-сервер, «bypass» — обход блокировок без сервера.
     *  На Android одновременно может работать только одно VPN-подключение. */
    var mode: String
        get() = prefs.getString("mode", "vpn") ?: "vpn"
        set(value) = prefs.edit().putString("mode", value).apply()

    /** Обход: способ разбиения (Bypass.METHOD_*), все сайты или только из списков, свои сайты. */
    var bypassMethod: String
        get() = prefs.getString("bypass_method", Bypass.METHOD_TCP) ?: Bypass.METHOD_TCP
        set(value) = prefs.edit().putString("bypass_method", value).apply()

    var bypassAll: Boolean
        get() = prefs.getBoolean("bypass_all", false)
        set(value) = prefs.edit().putBoolean("bypass_all", value).apply()

    var bypassUser: String
        get() = prefs.getString("bypass_user", "") ?: ""
        set(value) = prefs.edit().putString("bypass_user", value).apply()

    /** Включать VPN при запуске приложения, если он был включён. */
    var wasEnabled: Boolean
        get() = prefs.getBoolean("enabled", false)
        set(value) = prefs.edit().putBoolean("enabled", value).apply()

    var lastUpdateCheck: Long
        get() = prefs.getLong("update_check", 0)
        set(value) = prefs.edit().putLong("update_check", value).apply()

    var subscriptions: List<String>
        get() = jsonStrings(prefs.getString("subscriptions", "[]"))
        set(value) = prefs.edit().putString("subscriptions", JSONArray(value).toString()).apply()

    private fun jsonStrings(raw: String?): List<String> = try {
        val a = JSONArray(raw ?: "[]")
        (0 until a.length()).map { a.getString(it) }
    } catch (e: Exception) {
        emptyList()
    }

    // ---------------------------------------------------------------- серверы
    fun profiles(): List<Profile> = try {
        val a = JSONArray(prefs.getString("profiles", "[]"))
        (0 until a.length()).mapNotNull { Profile.fromJson(a.getJSONObject(it)) }
    } catch (e: Exception) {
        emptyList()
    }

    private fun save(list: List<Profile>) {
        prefs.edit().putString("profiles", JSONArray(list.map { it.toJson() }).toString()).apply()
    }

    /** Серверы VLESS с правильными ключами — из них собирается группа и «Авто». */
    fun vless(): List<Profile> = profiles().filter {
        it.kind == "vless" && try {
            Keys.parseVless(it.data); true
        } catch (e: KeyException) {
            false
        }
    }

    fun isAuto(): Boolean = active == AUTO && vless().size >= 2

    /** Выбранный сервер; null — серверов нет или выбран режим «Авто». */
    fun activeProfile(): Profile? {
        if (isAuto()) return null
        val list = profiles()
        return list.firstOrNull { it.id == active } ?: list.firstOrNull { it.kind == "vless" } ?: list.firstOrNull()
    }

    fun select(id: String) {
        active = id
    }

    fun rename(id: String, name: String) {
        if (name.isBlank()) return
        save(profiles().map { if (it.id == id) it.copy(name = name.trim()) else it })
    }

    fun remove(id: String) = save(profiles().filter { it.id != id })

    /** Добавляет серверы из текста; подписки скачиваются сразу (вызывать не в UI-потоке).
     *  Возвращает (новые серверы, предупреждения). */
    fun add(text: String): Pair<List<Profile>, List<String>> {
        val parsed = Keys.parseInput(text)
        val found = parsed.profiles.toMutableList()
        val problems = parsed.problems.toMutableList()
        val subs = subscriptions.toMutableList()
        for (url in parsed.subscriptions) {
            try {
                val (keys, extra) = Keys.parseVlessList(Net.get(url))
                if (keys.isEmpty()) problems.add("в подписке нет ключей VLESS")
                found.addAll(keys.map { Keys.vlessProfile(it, url) })
                problems.addAll(extra)
                if (url !in subs) subs.add(url)
            } catch (e: Exception) {
                problems.add("подписка не скачалась: ${e.message}")
            }
        }
        subscriptions = subs
        val list = profiles().toMutableList()
        val known = list.map { it.id }.toMutableSet()
        val added = found.filter { known.add(it.id) }
        list.addAll(added)
        save(list)
        if (added.isNotEmpty() && list.none { it.id == active } && active != AUTO) active = added.first().id
        return added to problems
    }

    /** Обновить серверы из подписок (старые ключи подписки заменяются новыми). */
    fun refreshSubscriptions(): Pair<Int, List<String>> {
        val problems = mutableListOf<String>()
        var list = profiles()
        var total = 0
        for (url in subscriptions) {
            try {
                val (keys, extra) = Keys.parseVlessList(Net.get(url))
                problems.addAll(extra)
                val names = list.filter { it.sub == url }.associate { it.id to it.name }
                val fresh = keys.map { Keys.vlessProfile(it, url) }.map { p -> names[p.id]?.let { p.copy(name = it) } ?: p }
                list = list.filter { it.sub != url } + fresh
                total += fresh.size
            } catch (e: Exception) {
                problems.add("$url: ${e.message}")
            }
        }
        save(list)
        return total to problems
    }

    fun removeSubscription(url: String) {
        subscriptions = subscriptions.filter { it != url }
        save(profiles().filter { it.sub != url })
    }
}
