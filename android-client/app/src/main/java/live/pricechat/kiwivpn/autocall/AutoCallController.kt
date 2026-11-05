package live.pricechat.kiwivpn.autocall

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioManager
import android.os.Build
import android.provider.Settings
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import live.pricechat.kiwivpn.MainActivity
import live.pricechat.kiwivpn.R
import live.pricechat.kiwivpn.log.Diag
import live.pricechat.kiwivpn.security.Vault
import live.pricechat.kiwivpn.vpn.ConnectionManager
import live.pricechat.kiwivpn.vpn.ConnectionState

/** One line for the on-screen list in Diagnostics, in memory only: what Android's public signals said, with no notification content. For checking on real phones what each app actually sends. */
data class SignalTrace(val app: String, val event: String, val category: String?, val ongoing: Boolean, val callType: Int, val template: String?, val channelId: String?, val classified: String)

/**
 * Joins AutoCallEngine to Android: the listener's events go in, and Connect, Disconnect and status messages come out. All on one thread, so events can't interleave. Nothing leaves the phone:
 * this class makes no network request of its own, and the only connection it causes is the tunnel itself.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class AutoCallController(
    private val context: Context,
    private val scope: CoroutineScope,
    private val vault: Vault,
    private val connection: ConnectionManager,
) {
    private val serial = Dispatchers.Default.limitedParallelism(1)
    private val engine = AutoCallEngine(owned = vault.read().autoCallOwned)
    private val _status = MutableStateFlow(AutoCallStatus.IDLE)
    val status: StateFlow<AutoCallStatus> = _status.asStateFlow()
    private val _traces = MutableStateFlow<List<SignalTrace>>(emptyList())
    val traces: StateFlow<List<SignalTrace>> = _traces.asStateFlow()
    private var timer: Job? = null

    init {
        createChannel()
        scope.launch(serial) {
            connection.state.collect { state ->
                when (state) {
                    is ConnectionState.Connected -> apply(engine.onConnected())
                    is ConnectionState.Failed -> apply(engine.onConnectFailed())
                    else -> Unit
                }
            }
        }
    }

    private fun tunnelOn() = connection.state.value.isActive

    private fun wants(app: CallApp): Boolean {
        val s = vault.read().settings
        return s.autoCallEnabled && s.autoCallConsented && when (app) {
            CallApp.WHATSAPP -> s.autoCallWhatsApp
            CallApp.TELEGRAM -> s.autoCallTelegram
        }
    }

    fun isListening() = vault.read().settings.let { it.autoCallEnabled && it.autoCallConsented }

    fun posted(key: Int, facts: NotificationFacts) {
        scope.launch(serial) {
            val signal = CallClassifier.classify(facts)
            trace(facts, "posted", signal)
            if (signal == null || !wants(signal.app)) return@launch
            Diag.event("autocall_signal", "phase" to signal.phase.name, "app" to signal.app.name)
            apply(engine.onPosted(key, signal, tunnelOn()))
        }
    }

    fun removed(key: Int, facts: NotificationFacts, reason: Int) {
        scope.launch(serial) {
            trace(facts, "removed, reason $reason", CallClassifier.classify(facts))
            apply(engine.onRemoved(key, CallClassifier.isConfirmedEnd(reason), System.currentTimeMillis()))
        }
    }

    fun listenerConnected(active: List<Pair<Int, NotificationFacts>>) {
        scope.launch(serial) {
            Diag.event("autocall_listener_connected")
            val calls = active.mapNotNull { (key, facts) -> CallClassifier.classify(facts)?.takeIf { wants(it.app) }?.let { key to it } }
            apply(engine.onListenerConnected(calls, tunnelOn()))
        }
    }

    fun listenerLost() {
        scope.launch(serial) {
            Diag.event("autocall_listener_lost")
            apply(engine.onListenerLost())
        }
    }

    fun userConnected() {
        scope.launch(serial) { engine.onUserConnected(); settle() }
    }

    fun userDisconnected() {
        scope.launch(serial) { engine.onUserDisconnected(); settle() }
    }

    fun disabled() {
        scope.launch(serial) { engine.onDisabled(); settle() }
    }

    private fun settle() {
        vault.update { it.copy(autoCallOwned = engine.owned) }
        _status.value = engine.status
        rearm()
    }

    private fun apply(effects: List<AutoCallEffect>) {
        for (effect in effects) {
            when (effect) {
                AutoCallEffect.Connect -> connectForCall()
                AutoCallEffect.Disconnect -> connection.disconnect()
                is AutoCallEffect.Status -> show(effect.status, effect.alert)
            }
        }
        settle()
    }

    private fun connectForCall() {
        val server = vault.read().lastServer
        if (server == null || vault.read().session == null || connection.needsPermission()) {
            // Android's VPN consent can only be shown from a screen, so it can't be asked for from here.
            Diag.event("autocall_cannot_connect", "needs_permission" to connection.needsPermission())
            apply(engine.onConnectFailed(needsApp = true))
            return
        }
        connection.connect(server)
    }

    /** A delay in this process: if the process dies during it, the saved `owned` flag makes the next start report "uncertain" and keep the VPN on, rather than lose track. */
    private fun rearm() {
        timer?.cancel()
        val due = engine.deadline ?: return
        timer = scope.launch(serial) {
            delay((due - System.currentTimeMillis()).coerceAtLeast(0))
            apply(engine.onTick(System.currentTimeMillis(), audioBusy()))
        }
    }

    /** Android's audio mode: readable with no permission, and the system's own sign that some app is in a call. */
    private fun audioBusy(): Boolean {
        val mode = context.getSystemService(AudioManager::class.java).mode
        return mode == AudioManager.MODE_IN_CALL || mode == AudioManager.MODE_IN_COMMUNICATION || mode == AudioManager.MODE_RINGTONE
    }

    private fun trace(facts: NotificationFacts, event: String, signal: CallSignal?) {
        val line = SignalTrace(CallClassifier.appFor(facts.packageName)?.label ?: "?", event, facts.category, facts.ongoing, facts.callType, facts.template?.substringAfterLast('.'), facts.channelId, signal?.phase?.name ?: "not a call")
        _traces.value = (_traces.value + line).takeLast(30)
    }

    private fun show(status: AutoCallStatus, alert: Boolean) {
        _status.value = status
        Diag.event("autocall_status", "status" to status.name)
        val manager = NotificationManagerCompat.from(context)
        if (status == AutoCallStatus.IDLE) {
            manager.cancel(NOTIFICATION_ID)
            return
        }
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
        val open = PendingIntent.getActivity(context, 2, Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP), PendingIntent.FLAG_IMMUTABLE)
        val note = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_stat_kiwi)
            .setContentTitle("Auto-Call VPN")
            .setContentText(status.message)
            .setStyle(NotificationCompat.BigTextStyle().bigText(status.message))
            .setContentIntent(open)
            .setAutoCancel(true)
            .setOnlyAlertOnce(!alert)
            .setPriority(if (alert) NotificationCompat.PRIORITY_DEFAULT else NotificationCompat.PRIORITY_LOW)
            .build()
        try {
            manager.notify(NOTIFICATION_ID, note)
        } catch (_: SecurityException) { /* permission withdrawn in that same moment */ }
    }

    private fun createChannel() {
        val channel = NotificationChannel(CHANNEL, context.getString(R.string.channel_autocall), NotificationManager.IMPORTANCE_DEFAULT)
        channel.setSound(null, null)
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    companion object {
        private const val CHANNEL = "autocall"
        private const val NOTIFICATION_ID = 8

        fun hasNotificationAccess(context: Context) = NotificationManagerCompat.getEnabledListenerPackages(context).contains(context.packageName)

        fun accessSettings() = Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)

        fun listenerComponent(context: Context) = ComponentName(context, CallListenerService::class.java)
    }
}
