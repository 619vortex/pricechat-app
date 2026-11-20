package live.pricechat.kiwivpn.autocall

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import live.pricechat.kiwivpn.KiwiApp

/**
 * Android hands this service every app's notifications once the person grants access. Everything from any other app stops at the first line of `facts`. For WhatsApp and Telegram it reads only
 * the fields in NotificationFacts: never EXTRA_TITLE, EXTRA_TEXT, people, pictures or actions. It holds no network code, and what it reads isn't saved. `sbn.key` is reduced to a hash in memory,
 * because a notification's tag can be anything its app chooses.
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
        val active = try {
            activeNotifications.orEmpty().mapNotNull { sbn -> facts(sbn)?.let { sbn.key.hashCode() to it } }
        } catch (_: SecurityException) {
            emptyList()
        }
        if (app.autoCall.isListening()) app.autoCall.listenerConnected(active)
    }

    override fun onListenerDisconnected() {
        if (app.autoCall.isListening()) app.autoCall.listenerLost()
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

    private companion object {
        const val EXTRA_CALL_TYPE = "android.callType" // Notification.EXTRA_CALL_TYPE, public from API 34
    }
}
