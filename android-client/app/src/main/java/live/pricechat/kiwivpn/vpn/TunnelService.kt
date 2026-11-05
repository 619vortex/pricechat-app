package live.pricechat.kiwivpn.vpn

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import live.pricechat.kiwivpn.KiwiApp
import live.pricechat.kiwivpn.MainActivity
import live.pricechat.kiwivpn.R
import live.pricechat.kiwivpn.log.Diag
import live.pricechat.kiwivpn.routing.RoutePlan
import live.pricechat.kiwivpn.routing.RoutingMode

/**
 * The persistent notification, and what keeps the process running with the screen off. The tunnel itself lives in the library's own VpnService. Started only once the person has given VPN permission,
 * which is the condition for this foreground service type: Android's documentation lists "VPN apps" as allowed to use systemExempted.
 */
class TunnelService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var watching = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val app = application as KiwiApp
        try {
            val type = if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SYSTEM_EXEMPTED else 0
            ServiceCompat.startForeground(this, NOTIFICATION_ID, build(app, app.connection.state.value), type)
        } catch (e: Exception) {
            // The VPN keeps working without this: Android holds a VPN's own service at high priority. Only the notification and the extra protection from being killed are lost.
            Diag.event("foreground_refused", "type" to e.javaClass.simpleName)
            stopSelf()
            return START_NOT_STICKY
        }
        if (intent?.action == ACTION_DISCONNECT) app.userDisconnect()
        if (!watching) {
            watching = true
            scope.launch {
                app.connection.state.map { summary(app, it) }.distinctUntilChanged().collect {
                    val state = app.connection.state.value
                    if (state is ConnectionState.Disconnected || state is ConnectionState.Failed) {
                        ServiceCompat.stopForeground(this@TunnelService, ServiceCompat.STOP_FOREGROUND_REMOVE)
                        stopSelf()
                    } else {
                        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, build(app, state))
                    }
                }
            }
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private fun summary(app: KiwiApp, state: ConnectionState): String {
        val where = { server: live.pricechat.kiwivpn.model.ServerRef? ->
            if (app.vault.read().settings.notificationShowsCountry && server != null) ": ${server.name}" else ""
        }
        return when (state) {
            is ConnectionState.Connected -> "Protected" + where(state.server) + routed(state.link.routing)
            is ConnectionState.Connecting -> "Connecting" + where(state.target)
            is ConnectionState.Reconnecting -> "Reconnecting: the server isn't answering"
            is ConnectionState.Disconnecting -> "Disconnecting"
            is ConnectionState.Disconnected -> "Unprotected"
            is ConnectionState.Failed -> "Connection failed"
        }
    }

    /** "Protected" alone would overclaim when only some apps use the VPN. */
    private fun routed(plan: RoutePlan): String = when {
        plan.mode == RoutingMode.ONLY_SELECTED -> " (only ${plan.chosenCount} selected)"
        plan.mode == RoutingMode.ALL_EXCEPT && plan.chosenCount > 0 -> " (except ${plan.chosenCount} apps)"
        else -> ""
    }

    private fun build(app: KiwiApp, state: ConnectionState): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_IMMUTABLE)
        val stop = PendingIntent.getService(this, 1, Intent(this, TunnelService::class.java).setAction(ACTION_DISCONNECT), PendingIntent.FLAG_IMMUTABLE)
        val builder = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_kiwi)
            .setContentTitle("Kiwi VPN")
            .setContentText(summary(app, state))
            .setContentIntent(open)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .addAction(0, "Disconnect", stop)
        if (state is ConnectionState.Connected) builder.setWhen(state.link.sinceMs).setUsesChronometer(true).setShowWhen(true)
        return builder.build()
    }

    companion object {
        private const val CHANNEL = "connection"
        private const val NOTIFICATION_ID = 7
        private const val ACTION_DISCONNECT = "live.pricechat.kiwivpn.DISCONNECT"

        fun createChannel(context: Context) {
            val channel = NotificationChannel(CHANNEL, context.getString(R.string.channel_connection), NotificationManager.IMPORTANCE_LOW)
            channel.setShowBadge(false)
            context.getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }

        /** Failure here isn't failure of the VPN: see onStartCommand. */
        fun start(context: Context) {
            try {
                ContextCompat.startForegroundService(context, Intent(context, TunnelService::class.java))
            } catch (e: Exception) {
                Diag.event("foreground_start_refused", "type" to e.javaClass.simpleName)
            }
        }
    }
}
