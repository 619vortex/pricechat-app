package live.pricechat.kiwivpn.autocall

import android.app.Notification
import android.content.Context
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import live.pricechat.kiwivpn.KiwiApp

/**
 * Android hands this service every app's notifications once the person grants access. Everything from any other app stops at the first line of `facts`. For WhatsApp and Telegram it reads only
 * the fields in NotificationFacts: never EXTRA_TITLE, EXTRA_TEXT, people, pictures or actions. It holds no network code, and what it reads isn't saved. `sbn.key` is reduced to a hash in memory,
 * because a notification's tag can be anything its app chooses.
 *
 * Battery: nothing here polls, holds a wake lock or sets an alarm. Android only wakes it for a notification event. The manifest leaves chat conversations out of those events, since a call is never one,
 * and it asks Android to unbind it altogether while Auto-Call VPN is off.
 */
class CallListenerService : NotificationListenerService() {
    private val app get() = application as KiwiApp

    private fun facts(sbn: StatusBarNotification): NotificationFacts? {
        if (CallClassifier.appFor(sbn.packageName) == null) return null
        val n: Notification = sbn.notification ?: return null
        return NotificationFacts(
            packageName = sbn.packageName,
            category = n.category,
            ongoing = n.flags and Notification.FLAG_ONGOING_EVENT != 0,
            callType = n.extras?.getInt(EXTRA_CALL_TYPE, 0) ?: 0,
            template = n.extras?.getString(Notification.EXTRA_TEMPLATE),
            channelId = n.channelId,
        )
    }

    override fun onListenerConnected() {
        current = this
        if (!app.autoCall.isListening()) {
            requestUnbind()
            return
        }
        val active = try {
            activeNotifications.orEmpty().mapNotNull { sbn -> facts(sbn)?.let { sbn.key.hashCode() to it } }
        } catch (_: SecurityException) {
            emptyList()
        }
        app.autoCall.listenerConnected(active)
    }

    override fun onListenerDisconnected() {
        current = null
        if (!app.autoCall.isListening()) return
        app.autoCall.listenerLost()
        requestRebind(AutoCallController.listenerComponent(this))
    }

    override fun onNotificationPosted(sbn: StatusBarNotification, rankingMap: NotificationListenerService.RankingMap) {
        val f = facts(sbn) ?: return
        if (app.autoCall.isListening()) app.autoCall.posted(sbn.key.hashCode(), f)
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification, rankingMap: NotificationListenerService.RankingMap, reason: Int) {
        val f = facts(sbn) ?: return
        if (app.autoCall.isListening()) app.autoCall.removed(sbn.key.hashCode(), f, reason)
    }

    companion object {
        private const val EXTRA_CALL_TYPE = "android.callType" // Notification.EXTRA_CALL_TYPE, public from API 34

        @Volatile
        internal var current: CallListenerService? = null
            private set

        /** On: Android binds the listener again. Off: it's unbound and nothing wakes for notifications, though the access stays granted. Android refuses either if access isn't granted, which is fine: granting it binds the listener. */
        fun setActive(context: Context, on: Boolean) {
            try {
                if (on) requestRebind(AutoCallController.listenerComponent(context)) else current?.requestUnbind()
            } catch (_: SecurityException) { /* no access yet, or already unbound */ }
        }
    }
}
