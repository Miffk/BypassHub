package io.github.miffk.bypasshub

import android.content.Context
import java.io.File

/** Базы раздельного туннелирования roscomvpn (hydraponique/roscomvpn-geosite и -geoip). */
object Rules {
    private const val BASE = "https://raw.githubusercontent.com/hydraponique/roscomvpn-%s/release/sing-box/%s.srs"
    private const val MAX_AGE_MS = 3L * 24 * 3600 * 1000

    val SETS = linkedMapOf(
        // всегда через VPN — даже если сервер оказался на российском IP
        "rc-youtube" to BASE.format("geosite", "youtube"),
        "rc-telegram" to BASE.format("geosite", "telegram"),
        "rc-github" to BASE.format("geosite", "github"),
        "rc-google-play" to BASE.format("geosite", "google-play"),
        // напрямую
        "rc-category-ru" to BASE.format("geosite", "category-ru"),
        "rc-whitelist" to BASE.format("geosite", "whitelist"),
        "rc-direct-ip" to BASE.format("geoip", "direct"),
    )

    fun dir(context: Context) = File(context.filesDir, "rules")

    fun files(context: Context): Map<String, File> = SETS.keys.associateWith { File(dir(context), "$it.srs") }

    /** Скачать недостающие или устаревшие базы (несколько небольших файлов). Ошибки не критичны. */
    fun update(context: Context, onlyMissing: Boolean = false) {
        val now = System.currentTimeMillis()
        for ((tag, url) in SETS) {
            val f = File(dir(context), "$tag.srs")
            if (f.exists() && f.length() > 0 && (onlyMissing || now - f.lastModified() < MAX_AGE_MS)) continue
            try {
                Net.download(url, f)
            } catch (e: Exception) {
                LogBuffer.add("Базы: не удалось скачать $tag: ${e.message}")
            }
        }
    }

    fun age(context: Context): Long? = files(context).values.filter { it.exists() }.maxOfOrNull { it.lastModified() }
}
