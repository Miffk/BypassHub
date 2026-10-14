package io.github.miffk.bypasshub.vpn

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import io.github.miffk.bypasshub.LogBuffer
import io.github.miffk.bypasshub.Net
import io.github.miffk.bypasshub.R
import io.github.miffk.bypasshub.Store
import io.github.miffk.bypasshub.Tg
import io.github.miffk.bypasshub.bhcore.Bhcore
import io.github.miffk.bypasshub.bhcore.Logger
import io.github.miffk.bypasshub.ui.MainActivity
import java.util.concurrent.Executors

/** Служба TG WS Proxy: держит прокси запущенным в фоне (с уведомлением, как требует Android). */
class TgProxyService : Service() {
    companion object {
        const val ACTION_START = "io.github.miffk.bypasshub.TG_START"
        const val ACTION_STOP = "io.github.miffk.bypasshub.TG_STOP"
        private const val CHANNEL = "tg"
        private const val NOTIFICATION_ID = 2
    }

    private val worker = Executors.newSingleThreadExecutor()
    private var foreground = false
    private val logger = object : Logger {
        override fun writeLog(message: String) = LogBuffer.add(message)
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            worker.execute { shutdown() }
            return START_NOT_STICKY
        }
        showNotification("Запуск…")
        worker.execute { startProxy() }
        return START_STICKY
    }

    override fun onDestroy() {
        try {
            Bhcore.stopTG()
        } catch (e: Throwable) {
        }
        if (Tg.status != Tg.Status.STOPPED) Tg.set(Tg.Status.STOPPED)
        worker.shutdown()
        super.onDestroy()
    }

    private fun startProxy() {
        Tg.set(Tg.Status.STARTING)
        try {
            // свежий список запасных доменов (если GitHub недоступен — встроенный в ядро)
            val cf = if (Store.tgCF) try {
                Net.get(Tg.CF_LIST_URL, timeoutMs = 8000)
            } catch (e: Exception) {
                ""
            } else ""
            Bhcore.startTG("${Tg.HOST}:${Store.tgPort}", Store.tgSecret, cf, Store.tgCF, logger)
            Tg.set(Tg.Status.RUNNING)
            showNotification("Работает · ${Tg.HOST}:${Store.tgPort}")
        } catch (e: Exception) {
            val msg = e.message ?: e.toString()
            LogBuffer.add("TG: не запустился: $msg")
            shutdown(msg)
        }
    }

    private fun shutdown(error: String? = null) {
        try {
            Bhcore.stopTG()
        } catch (e: Throwable) {
        }
        if (foreground) {
            if (Build.VERSION.SDK_INT >= 24) stopForeground(STOP_FOREGROUND_REMOVE) else @Suppress("DEPRECATION") stopForeground(true)
            foreground = false
        }
        Tg.set(Tg.Status.STOPPED, error)
        stopSelf()
    }

    private fun showNotification(text: String) {
        val nm = getSystemService(NotificationManager::class.java)!!
        if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "TG WS Proxy", NotificationManager.IMPORTANCE_LOW))
        }
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        val open = PendingIntent.getActivity(this, 2, Intent(this, MainActivity::class.java), flags)
        val stop = PendingIntent.getService(this, 3, Intent(this, TgProxyService::class.java).setAction(ACTION_STOP), flags)
        val builder = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, CHANNEL)
                      else @Suppress("DEPRECATION") Notification.Builder(this)
        val n = builder
            .setSmallIcon(R.drawable.ic_nav_telegram)
            .setContentTitle("TG WS Proxy")
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Остановить", stop).build())
            .build()
        if (!foreground) {
            if (Build.VERSION.SDK_INT >= 34) {
                startForeground(NOTIFICATION_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
            } else {
                startForeground(NOTIFICATION_ID, n)
            }
            foreground = true
        } else {
            nm.notify(NOTIFICATION_ID, n)
        }
    }
}
