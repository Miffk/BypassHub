package io.github.miffk.bypasshub

import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.widget.Toast
import org.json.JSONArray
import java.io.File
import java.security.MessageDigest

/** Обновление из релизов GitHub — тех же, что у версии для Windows (теги bypasshub-v*). */
object Updater {
    private const val REPO = "Miffk/BypassHub"
    private const val TAG_PREFIX = "bypasshub-v"
    const val ASSET = "BypassHub-android.apk"
    private const val CHECK_EVERY_MS = 12 * 60 * 60 * 1000L

    data class Release(val version: String, val url: String, val sha256: String?, val page: String)

    @Volatile private var busy = false

    fun parseVersion(v: String): List<Int> = v.trim().removePrefix("v").split(".", "-")
        .map { part -> part.takeWhile { it.isDigit() }.toIntOrNull() ?: 0 }

    fun isNewer(candidate: String, current: String): Boolean {
        val a = parseVersion(candidate)
        val b = parseVersion(current)
        for (i in 0 until maxOf(a.size, b.size)) {
            val x = a.getOrElse(i) { 0 }
            val y = b.getOrElse(i) { 0 }
            if (x != y) return x > y
        }
        return false
    }

    /** Последний релиз BypassHub, в котором есть APK (вызывать не в UI-потоке). */
    fun latest(): Release? {
        val data = JSONArray(Net.get("https://api.github.com/repos/$REPO/releases?per_page=30",
            headers = mapOf("Accept" to "application/vnd.github+json")))
        var best: Release? = null
        for (i in 0 until data.length()) {
            val rel = data.getJSONObject(i)
            val tag = rel.optString("tag_name")
            if (!tag.startsWith(TAG_PREFIX) || rel.optBoolean("draft") || rel.optBoolean("prerelease")) continue
            val assets = rel.optJSONArray("assets") ?: continue
            for (j in 0 until assets.length()) {
                val a = assets.getJSONObject(j)
                if (a.optString("name") != ASSET) continue
                val digest = a.optString("digest")
                val r = Release(tag.removePrefix(TAG_PREFIX), a.getString("browser_download_url"),
                    if (digest.startsWith("sha256:")) digest.substringAfter(":") else null, rel.optString("html_url"))
                if (best == null || isNewer(r.version, best.version)) best = r
            }
        }
        return best
    }

    /** Тихая проверка при запуске — не чаще раза в 12 часов. */
    fun checkInBackground(activity: Activity) {
        val prefs = activity.getSharedPreferences("updater", Context.MODE_PRIVATE)
        val now = System.currentTimeMillis()
        if (now - prefs.getLong("last", 0) < CHECK_EVERY_MS) return
        prefs.edit().putLong("last", now).apply()
        check(activity, manual = false)
    }

    fun check(activity: Activity, manual: Boolean) {
        if (busy) return
        busy = true
        if (manual) toast(activity, "Проверяю обновления…")
        Vpn.io.execute {
            val result = try {
                Result.success(latest())
            } catch (e: Exception) {
                Result.failure(e)
            }
            busy = false
            activity.runOnUiThread {
                if (activity.isFinishing) return@runOnUiThread
                val rel = result.getOrNull()
                when {
                    result.isFailure -> if (manual) toast(activity, "Не удалось проверить: ${result.exceptionOrNull()?.message}")
                    rel == null || !isNewer(rel.version, BuildConfig.VERSION_NAME) ->
                        if (manual) toast(activity, "У вас последняя версия (${BuildConfig.VERSION_NAME})")
                    else -> offer(activity, rel)
                }
            }
        }
    }

    private fun offer(activity: Activity, rel: Release) {
        AlertDialog.Builder(activity, android.R.style.Theme_DeviceDefault_Dialog_Alert)
            .setTitle("Доступна версия ${rel.version}")
            .setMessage("Сейчас установлена ${BuildConfig.VERSION_NAME}. Скачать и установить обновление?")
            .setPositiveButton("Обновить") { _, _ -> download(activity, rel) }
            .setNegativeButton("Позже", null)
            .show()
    }

    private fun download(activity: Activity, rel: Release) {
        val dialog = AlertDialog.Builder(activity, android.R.style.Theme_DeviceDefault_Dialog_Alert)
            .setTitle("Обновление ${rel.version}")
            .setMessage("Скачивание…")
            .setCancelable(false)
            .show()
        Vpn.io.execute {
            val dir = ApkProvider.dir(activity)
            dir.mkdirs()
            dir.listFiles()?.forEach { it.delete() }
            val apk = File(dir, "BypassHub-${rel.version}.apk")
            val error = try {
                var lastPct = -1
                Net.download(rel.url, apk) { done, total ->
                    val pct = if (total > 0) (done * 100 / total).toInt() else -1
                    if (pct != lastPct) {
                        lastPct = pct
                        activity.runOnUiThread {
                            dialog.setMessage(if (pct >= 0) "Скачивание… $pct%" else "Скачивание… ${done / 1024 / 1024} МБ")
                        }
                    }
                }
                if (rel.sha256 != null && sha256(apk) != rel.sha256.lowercase()) {
                    apk.delete()
                    "файл повреждён (контрольная сумма не совпала)"
                } else null
            } catch (e: Exception) {
                e.message ?: e.toString()
            }
            activity.runOnUiThread {
                dialog.dismiss()
                if (error != null) toast(activity, "Обновление не скачалось: $error")
                else install(activity, apk)
            }
        }
    }

    private fun sha256(f: File): String {
        val md = MessageDigest.getInstance("SHA-256")
        f.inputStream().use { input ->
            val buf = ByteArray(64 * 1024)
            while (true) {
                val n = input.read(buf)
                if (n < 0) break
                md.update(buf, 0, n)
            }
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }

    fun install(activity: Activity, apk: File) {
        if (Build.VERSION.SDK_INT >= 26 && !activity.packageManager.canRequestPackageInstalls()) {
            AlertDialog.Builder(activity, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle("Нужно разрешение")
                .setMessage("Разрешите BypassHub устанавливать приложения — так Android позволит поставить обновление. " +
                    "Потом нажмите «Проверить обновления» ещё раз.")
                .setPositiveButton("Открыть настройки") { _, _ ->
                    try {
                        activity.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                            Uri.parse("package:${activity.packageName}")))
                    } catch (e: ActivityNotFoundException) {
                        toast(activity, "Откройте настройки безопасности и разрешите установку из неизвестных источников")
                    }
                }
                .setNegativeButton("Отмена", null)
                .show()
            return
        }
        val intent = Intent(Intent.ACTION_VIEW)
            .setDataAndType(ApkProvider.uri(activity, apk), "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            activity.startActivity(intent)
        } catch (e: ActivityNotFoundException) {
            toast(activity, "На устройстве нет установщика приложений")
        }
    }

    private fun toast(context: Context, text: String) = Toast.makeText(context, text, Toast.LENGTH_LONG).show()
}
