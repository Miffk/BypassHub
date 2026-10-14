package io.github.miffk.bypasshub.vpn

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
import android.net.NetworkCapabilities
import android.net.VpnService
import android.os.Build
import android.os.ParcelFileDescriptor
import io.github.miffk.bypasshub.AUTO
import io.github.miffk.bypasshub.Bypass
import io.github.miffk.bypasshub.ClashApi
import io.github.miffk.bypasshub.Config
import io.github.miffk.bypasshub.Keys
import io.github.miffk.bypasshub.LogBuffer
import io.github.miffk.bypasshub.R
import io.github.miffk.bypasshub.Rules
import io.github.miffk.bypasshub.Store
import io.github.miffk.bypasshub.Vpn
import io.github.miffk.bypasshub.bhcore.Bhcore
import io.github.miffk.bypasshub.bhcore.Platform
import io.github.miffk.bypasshub.ui.MainActivity
import org.json.JSONArray
import org.json.JSONObject
import java.net.Inet4Address
import java.net.InetAddress
import java.net.NetworkInterface
import java.net.ServerSocket
import java.security.SecureRandom
import java.util.concurrent.Executors

/** VPN-служба: поднимает ядро (sing-box и, если нужно, AmneziaWG) и отдаёт ему TUN. */
class BypassVpnService : VpnService(), Platform {
    companion object {
        const val ACTION_START = "io.github.miffk.bypasshub.START"
        const val ACTION_STOP = "io.github.miffk.bypasshub.STOP"
        private const val CHANNEL = "vpn"
        private const val NOTIFICATION_ID = 1
    }

    private val worker = Executors.newSingleThreadExecutor()
    private var tunPfd: ParcelFileDescriptor? = null
    private var netCallback: ConnectivityManager.NetworkCallback? = null
    private var foreground = false

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            Vpn.set(Vpn.Status.STOPPING)
            worker.execute { shutdown() }
            return START_NOT_STICKY
        }
        // ACTION_START, а также запуск системой («Постоянная VPN»)
        showNotification(getString(R.string.vpn_connecting))
        worker.execute { startVpn() }
        return START_STICKY
    }

    override fun onRevoke() {
        LogBuffer.add("VPN отключён системой (включён другой VPN?)")
        worker.execute { shutdown() }
    }

    override fun onDestroy() {
        if (Vpn.status != Vpn.Status.STOPPED) worker.execute { stopCore() }
        worker.shutdown()
        super.onDestroy()
    }

    // ---------------------------------------------------------------- запуск
    private fun startVpn() {
        Vpn.kind = ""
        Vpn.set(Vpn.Status.STARTING)
        stopCore()
        try {
            if (Store.mode == "bypass") {
                startBypass()
                return
            }
            val profiles = Store.profiles()
            if (profiles.isEmpty()) throw Exception("Сначала добавьте свой сервер: ключ vless://… или ключ AmneziaVPN vpn://…")
            val auto = Store.isAuto()
            val active = Store.activeProfile()
            val proxy: List<JSONObject>
            var bypass = emptyList<String>()
            if (active != null && active.kind == "awg") {
                val (conf, endpointIp) = resolveEndpoint(Keys.parseConf(active.data, active.name).conf)
                val socksPort = Bhcore.startAWG(conf)
                proxy = listOf(Config.awgOutbound(socksPort))
                bypass = listOf(endpointIp)
                Vpn.kind = "awg"
                Vpn.groupTags = emptyList()
                Vpn.serverName = active.name
            } else {
                // все серверы VLESS — одной группой: переключение на лету и «Авто»
                val vless = Store.vless()
                if (vless.isEmpty()) throw Exception("Нет рабочих ключей VLESS")
                val servers = vless.map { Keys.parseVless(it.data).outbound(it.id) }
                val selected = if (auto || active == null) AUTO else active.id
                proxy = Config.serverGroup(servers, selected)
                Vpn.kind = "vless"
                Vpn.groupTags = vless.map { it.id }
                Vpn.serverName = Vpn.nameOf(if (selected in Vpn.groupTags || selected == AUTO) selected else vless[0].id)
            }
            if (Store.ruDirect) Rules.update(this, onlyMissing = true)
            val (apiPort, secret) = apiEndpoint()
            val localProxy = apiEndpoint().first
            val config = Config.build(proxy, Store.ruDirect, Rules.files(this), apiPort, secret, bypass, localProxy)
            LogBuffer.add("Запуск VPN: ${Vpn.serverName}")
            runCore(config, apiPort, secret, getString(R.string.vpn_connected, Vpn.serverName))
            Vpn.localProxy = localProxy
            Thread.sleep(1500)
            Vpn.refreshGroup()
        } catch (e: Exception) {
            val msg = e.message ?: e.toString()
            LogBuffer.add("Ошибка: $msg")
            shutdown(msg)
        }
    }

    /** Обход блокировок: без сервера, всё напрямую, к сайтам из списков — с разбиением TLS. */
    private fun startBypass() {
        Vpn.kind = "bypass"
        Vpn.groupTags = emptyList()
        Vpn.serverName = "Обход блокировок"
        Bypass.update(this)
        val (suffix, exact) = Bypass.domains(this, Store.bypassUser)
        val (apiPort, secret) = apiEndpoint()
        val method = Store.bypassMethod
        val desyncPort = if (Bypass.usesDesync(method)) Bhcore.startDesync(method).toInt() else 0
        val config = Bypass.config(suffix, exact, Store.bypassAll, method, apiPort, secret, desyncPort)
        LogBuffer.add("Запуск обхода (${Bypass.methodName(method)}): сайтов в списке ${suffix.size + exact.size}" +
            (if (Store.bypassAll) ", применяется ко всем сайтам" else ""))
        runCore(config, apiPort, secret, getString(R.string.bypass_on))
    }

    private fun apiEndpoint(): Pair<Int, String> {
        val port = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).use { it.localPort }
        val secret = ByteArray(16).also { SecureRandom().nextBytes(it) }.joinToString("") { "%02x".format(it) }
        return port to secret
    }

    private fun runCore(config: String, apiPort: Int, secret: String, notification: String) {
        Bhcore.start(config, this)
        Vpn.api = ClashApi(apiPort, secret)
        registerNetworkMonitor()
        Vpn.set(Vpn.Status.RUNNING)
        showNotification(notification)
    }

    /** Endpoint из .conf — сразу IP: так ядру не нужен DNS, а адрес сервера идёт мимо туннеля. */
    private fun resolveEndpoint(conf: String): Pair<String, String> {
        val m = Regex("(?im)^(\\s*Endpoint\\s*=\\s*)(.+?):(\\d+)\\s*$").find(conf)
            ?: throw Exception("В конфиге нет Endpoint")
        val host = m.groupValues[2].trim().removePrefix("[").removeSuffix("]")
        val ip = InetAddress.getAllByName(host).firstOrNull { it is Inet4Address }?.hostAddress
            ?: throw Exception("Не удалось узнать адрес сервера $host")
        val fixed = conf.replaceRange(m.range, "${m.groupValues[1]}$ip:${m.groupValues[3]}")
        return fixed to ip
    }

    private fun stopCore() {
        unregisterNetworkMonitor()
        try {
            Bhcore.stop()
        } catch (e: Throwable) {
        }
        try {
            Bhcore.stopAWG()
        } catch (e: Throwable) {
        }
        try {
            Bhcore.stopDesync()
        } catch (e: Throwable) {
        }
        try {
            tunPfd?.close()
        } catch (e: Exception) {
        }
        tunPfd = null
    }

    private fun shutdown(error: String? = null) {
        stopCore()
        if (foreground) {
            if (Build.VERSION.SDK_INT >= 24) stopForeground(STOP_FOREGROUND_REMOVE) else @Suppress("DEPRECATION") stopForeground(true)
            foreground = false
        }
        Vpn.set(Vpn.Status.STOPPED, error)
        if (error == null) LogBuffer.add(if (Vpn.kind == "bypass") "Обход выключен" else "VPN выключен")
        stopSelf()
    }

    // ---------------------------------------------------------------- уведомление
    private fun showNotification(text: String) {
        val nm = getSystemService(NotificationManager::class.java)!!
        if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(NotificationChannel(CHANNEL, getString(R.string.channel_vpn),
                NotificationManager.IMPORTANCE_LOW))
        }
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), flags)
        val stop = PendingIntent.getService(this, 1, Intent(this, BypassVpnService::class.java).setAction(ACTION_STOP), flags)
        val builder = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, CHANNEL)
                      else @Suppress("DEPRECATION") Notification.Builder(this)
        val n = builder
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("BypassHub")
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, getString(R.string.action_disconnect), stop).build())
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

    // ---------------------------------------------------------------- основная сеть
    private fun registerNetworkMonitor() {
        val cm = getSystemService(ConnectivityManager::class.java)!!
        val cb = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) = push(cm, network)
            override fun onLinkPropertiesChanged(network: Network, lp: LinkProperties) = push(cm, network)
            override fun onLost(network: Network) {
                if (cm.activeNetwork == null) Bhcore.updateDefaultInterface("", -1, false)
            }
        }
        cm.registerDefaultNetworkCallback(cb)
        netCallback = cb
        cm.activeNetwork?.let { push(cm, it) }
    }

    private fun push(cm: ConnectivityManager, network: Network) {
        val name = cm.getLinkProperties(network)?.interfaceName ?: return
        val index = try {
            NetworkInterface.getByName(name)?.index ?: return
        } catch (e: Exception) {
            return
        }
        val caps = cm.getNetworkCapabilities(network)
        val metered = caps != null && !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)
        Bhcore.updateDefaultInterface(name, index, metered)
    }

    private fun unregisterNetworkMonitor() {
        val cb = netCallback ?: return
        try {
            getSystemService(ConnectivityManager::class.java)!!.unregisterNetworkCallback(cb)
        } catch (e: Exception) {
        }
        netCallback = null
    }

    // ---------------------------------------------------------------- Platform (вызывает ядро)
    override fun openTun(optionsJSON: String): Int {
        val o = JSONObject(optionsJSON)
        val b = Builder().setSession("BypassHub").setMtu(o.optInt("mtu", 9000))
        fun each(key: String, f: (String) -> Unit) {
            val a = o.optJSONArray(key) ?: JSONArray()
            for (i in 0 until a.length()) f(a.getString(i))
        }
        fun cidr(s: String): Pair<String, Int> = s.substringBefore("/") to s.substringAfter("/").toInt()
        each("inet4Address") { val (ip, p) = cidr(it); b.addAddress(ip, p) }
        each("inet6Address") { val (ip, p) = cidr(it); b.addAddress(ip, p) }
        var routes4 = 0
        each("inet4Route") { val (ip, p) = cidr(it); b.addRoute(ip, p); routes4++ }
        if (routes4 == 0) b.addRoute("0.0.0.0", 0)
        each("inet6Route") { val (ip, p) = cidr(it); b.addRoute(ip, p) }
        each("dnsServers") { b.addDnsServer(it) }
        val include = o.optJSONArray("includePackage") ?: JSONArray()
        if (include.length() > 0) {
            for (i in 0 until include.length()) {
                try {
                    b.addAllowedApplication(include.getString(i))
                } catch (e: PackageManager.NameNotFoundException) {
                }
            }
        } else {
            // сам BypassHub — мимо VPN: его соединения (сервер AmneziaWG, обновления) не зацикливаются
            b.addDisallowedApplication(packageName)
            each("excludePackage") {
                try {
                    b.addDisallowedApplication(it)
                } catch (e: PackageManager.NameNotFoundException) {
                }
            }
        }
        if (Build.VERSION.SDK_INT >= 29) b.setMetered(false)
        b.setConfigureIntent(PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE))
        val pfd = b.establish() ?: throw Exception("Нет разрешения на VPN — откройте BypassHub и включите VPN ещё раз")
        tunPfd?.close()
        tunPfd = pfd
        return pfd.fd
    }

    override fun protect(fd: Int): Boolean = super.protect(fd)

    override fun interfacesJSON(): String {
        val cm = getSystemService(ConnectivityManager::class.java)!!
        val info = mutableMapOf<String, Triple<String, List<String>, Boolean>>()
        for (n in cm.allNetworks) {
            val lp = cm.getLinkProperties(n) ?: continue
            val name = lp.interfaceName ?: continue
            val caps = cm.getNetworkCapabilities(n)
            val type = when {
                caps == null -> "other"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"
                else -> "other"
            }
            val metered = caps != null && !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)
            info[name] = Triple(type, lp.dnsServers.mapNotNull { it.hostAddress }, metered)
        }
        val arr = JSONArray()
        try {
            for (ni in NetworkInterface.getNetworkInterfaces()) {
                val extra = info[ni.name]
                val addrs = JSONArray()
                for (ia in ni.interfaceAddresses) {
                    val host = ia.address?.hostAddress?.substringBefore("%") ?: continue
                    addrs.put("$host/${ia.networkPrefixLength}")
                }
                arr.put(JSONObject()
                    .put("name", ni.name)
                    .put("index", ni.index)
                    .put("mtu", try { ni.mtu } catch (e: Exception) { 1500 })
                    .put("addresses", addrs)
                    .put("type", extra?.first ?: "other")
                    .put("dns", JSONArray(extra?.second ?: emptyList<String>()))
                    .put("metered", extra?.third ?: false)
                    .put("up", try { ni.isUp } catch (e: Exception) { false }))
            }
        } catch (e: Exception) {
            LogBuffer.add("Сетевые интерфейсы: ${e.message}")
        }
        return arr.toString()
    }

    override fun writeLog(message: String) = LogBuffer.add(message)
}

/** Есть ли разрешение на VPN (null — есть). */
fun vpnPrepareIntent(context: Context): Intent? = VpnService.prepare(context)
