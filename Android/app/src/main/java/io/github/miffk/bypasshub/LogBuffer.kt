package io.github.miffk.bypasshub

import android.os.Handler
import android.os.Looper
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Журнал в памяти (последние строки) для экрана «Журнал». */
object LogBuffer {
    private const val MAX = 600
    private val lines = ArrayDeque<String>()
    private val listeners = mutableListOf<() -> Unit>()
    private val main = Handler(Looper.getMainLooper())
    private val time = SimpleDateFormat("HH:mm:ss", Locale.US)

    @Synchronized
    fun add(message: String) {
        val text = message.trimEnd()
        if (text.isEmpty()) return
        // строки sing-box уже со временем; свои помечаем сами
        lines.addLast(if (Regex("^[A-Z]+\\[").containsMatchIn(text) || text.startsWith("+")) text
                      else "${time.format(Date())}  $text")
        while (lines.size > MAX) lines.removeFirst()
        main.post { listeners.toList().forEach { it() } }
    }

    @Synchronized
    fun text(): String = lines.joinToString("\n")

    @Synchronized
    fun clear() {
        lines.clear()
        main.post { listeners.toList().forEach { it() } }
    }

    fun listen(l: () -> Unit) {
        listeners.add(l)
    }

    fun unlisten(l: () -> Unit) {
        listeners.remove(l)
    }
}
