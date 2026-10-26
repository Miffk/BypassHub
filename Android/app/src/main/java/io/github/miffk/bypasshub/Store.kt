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

/** Поддержать проект — ссылка внизу главной (та же, что в README и у Windows-версии). */
const val SUPPORT_URL = "https://www.donationalerts.com/r/m1ffk"

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
        get() = prefs.getString("bypass_method", Bypass.METHOD_DISORDER) ?: Bypass.METHOD_DISORDER
        set(value) = prefs.edit().putString("bypass_method", value).apply()

    var bypassAll: Boolean
        get() = prefs.getBoolean("bypass_all", false)
        set(value) = prefs.edit().putBoolean("bypass_all", value).apply()

    var bypassUser: String
        get() = prefs.getString("bypass_user", "") ?: ""
        set(value) = prefs.edit().putString("bypass_user", value).apply()

    /** TG WS Proxy: был ли включён, порт, секрет (32 hex), запасной путь через Cloudflare. */
    var tgEnabled: Boolean
        get() = prefs.getBoolean("tg_enabled", false)
        set(value) = prefs.edit().putBoolean("tg_enabled", value).apply()

    var tgPort: Int
        get() = prefs.getInt("tg_port", 1443)
        set(value) = prefs.edit().putInt("tg_port", value).apply()

    var tgSecret: String
        get() = prefs.getString("tg_secret", null) ?: io.github.miffk.bypasshub.bhcore.Bhcore.newTGSecret().also { tgSecret = it }
        set(value) = prefs.edit().putString("tg_secret", value).apply()

    var tgCF: Boolean
        get() = prefs.getBoolean("tg_cf", true)
        set(value) = prefs.edit().putBoolean("tg_cf", value).apply()

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

    /** Когда подписки обновлялись последний раз (мс). */
    var subsChecked: Long
        get() = prefs.getLong("subs_checked", 0)
        set(value) = prefs.edit().putLong("subs_checked", value).apply()

    /** Подписки обновляются сами раз в несколько часов (как в Happ): VPN-сервисы добавляют серверы. */
    const val SUBS_REFRESH_MS = 6L * 3600 * 1000

    fun subsDue() = subscriptions.isNotEmpty() && System.currentTimeMillis() - subsChecked >= SUBS_REFRESH_MS

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

    /** Код устройства для подписок, как его присылают Happ и v2RayTun: панели с лимитом устройств
     *  (Remnawave и др.) без него отдают вместо серверов заглушку. Случайный, создаётся один раз. */
    private val hwid: String
        get() = prefs.getString("hwid", "").orEmpty().ifEmpty {
            java.util.UUID.randomUUID().toString().replace("-", "").take(16).also {
                prefs.edit().putString("hwid", it).apply()
            }
        }

    /** Сервисы подписок разным программам отдают разное: полный список часто получают только известные
     *  (Happ, v2rayNG). Подписка запрашивается от имени каждой и берётся самый полный ответ. */
    private val SUB_USER_AGENTS = listOf("BypassHub-Android/${BuildConfig.VERSION_NAME}", "v2rayNG/1.10.2", "Happ/3.9.0")

    /** Скачать подписку: (ключи VLESS, предупреждения). Вызывать не в UI-потоке. */
    fun fetchSubscription(url: String): Pair<List<VlessKey>, List<String>> {
        val device = mapOf("x-hwid" to hwid, "x-device-os" to "Android",
            "x-ver-os" to android.os.Build.VERSION.RELEASE, "x-device-model" to android.os.Build.MODEL,
            "Accept" to "*/*", "Cache-Control" to "no-cache")
        var best: Keys.SubParse? = null
        val notices = linkedSetOf<String>()
        var lastError: Exception? = null
        for (ua in SUB_USER_AGENTS) {
            val body = try {
                Net.getAny(url, headers = device + ("User-Agent" to ua))
            } catch (e: Exception) {
                lastError = e
                if (e.message?.startsWith("HTTP") == true) continue  // этому клиенту не отвечает — пробуем следующего
                if (best == null) throw e  // сервис недоступен вовсе — другие попытки не помогут
                break
            }
            val p = Keys.parseSubscription(body)
            notices.addAll(p.notices)
            if (best == null || p.keys.size > best.keys.size) best = p
        }
        val b = best ?: throw (lastError ?: java.io.IOException("нет ответа"))
        val stubs = if (b.keys.size <= 1) notices.map { "сервис подписки сообщает: «$it»" } else emptyList()
        return b.keys to (b.problems + stubs)
    }

    /** Добавляет серверы из текста; подписки скачиваются сразу (вызывать не в UI-потоке).
     *  Возвращает (новые серверы, предупреждения). */
    fun add(text: String): Pair<List<Profile>, List<String>> {
        val parsed = Keys.parseInput(text)
        val found = parsed.profiles.toMutableList()
        val problems = parsed.problems.toMutableList()
        val subs = subscriptions.toMutableList()
        for (url in parsed.subscriptions) {
            try {
                val (keys, extra) = fetchSubscription(url)
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
        val current = activeProfile()
        var total = 0
        for (url in subscriptions) {
            try {
                val (keys, extra) = fetchSubscription(url)
                problems.addAll(extra)
                if (keys.isEmpty()) {  // пустой ответ (сбой сервиса) — старые серверы не трогаем
                    problems.add("$url: в ответе нет ключей VLESS, оставлены прежние серверы")
                    continue
                }
                val names = list.filter { it.sub == url }.associate { it.id to it.name }
                val fresh = keys.map { Keys.vlessProfile(it, url) }.map { p -> names[p.id]?.let { p.copy(name = it) } ?: p }
                list = list.filter { it.sub != url } + fresh
                total += fresh.size
                // у выбранного сервера сменился ключ — остаёмся на сервере с тем же названием
                if (current != null && current.sub == url && active != AUTO && fresh.none { it.id == current.id }) {
                    fresh.firstOrNull { it.name == current.name }?.let { active = it.id }
                }
            } catch (e: Exception) {
                problems.add("$url: ${e.message}")
            }
        }
        save(list)
        subsChecked = System.currentTimeMillis()
        return total to problems
    }

    fun removeSubscription(url: String) {
        subscriptions = subscriptions.filter { it != url }
        save(profiles().filter { it.sub != url })
    }
}
