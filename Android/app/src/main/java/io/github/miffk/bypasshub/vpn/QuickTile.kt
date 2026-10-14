package io.github.miffk.bypasshub.vpn

import android.content.Intent
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService
import io.github.miffk.bypasshub.Store
import io.github.miffk.bypasshub.Vpn
import io.github.miffk.bypasshub.ui.MainActivity

/** Плитка в шторке быстрых настроек: включить/выключить VPN одним касанием. */
class QuickTile : TileService() {
    private val listener = { update() }

    override fun onStartListening() {
        Vpn.listen(listener)
        update()
    }

    override fun onStopListening() {
        Vpn.unlisten(listener)
    }

    override fun onClick() {
        when {
            Vpn.status == Vpn.Status.RUNNING || Vpn.status == Vpn.Status.STARTING -> Vpn.stop(this)
            // нет разрешения или серверов — открыть приложение
            vpnPrepareIntent(this) != null || (Store.mode == "vpn" && Store.profiles().isEmpty()) -> openApp()
            else -> try {
                Vpn.start(this)
            } catch (e: IllegalStateException) {
                // Android может запретить запуск службы из фона — тогда через приложение
                openApp()
            }
        }
    }

    @Suppress("DEPRECATION")
    private fun openApp() {
        val i = Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (android.os.Build.VERSION.SDK_INT >= 34) {
            startActivityAndCollapse(android.app.PendingIntent.getActivity(this, 0, i,
                android.app.PendingIntent.FLAG_IMMUTABLE))
        } else {
            startActivityAndCollapse(i)
        }
    }

    private fun update() {
        val tile = qsTile ?: return
        tile.state = when (Vpn.status) {
            Vpn.Status.RUNNING -> Tile.STATE_ACTIVE
            Vpn.Status.STARTING, Vpn.Status.STOPPING -> Tile.STATE_ACTIVE
            Vpn.Status.STOPPED -> Tile.STATE_INACTIVE
        }
        if (android.os.Build.VERSION.SDK_INT >= 29) {
            tile.subtitle = when (Vpn.status) {
                Vpn.Status.RUNNING -> Vpn.serverName
                Vpn.Status.STARTING -> "Подключение…"
                Vpn.Status.STOPPING -> "Отключение…"
                Vpn.Status.STOPPED -> "Выключен"
            }
        }
        tile.updateTile()
    }
}
