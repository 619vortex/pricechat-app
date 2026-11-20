package live.pricechat.kiwivpn.autocall

enum class CallApp(val label: String) { WHATSAPP("WhatsApp"), TELEGRAM("Telegram") }

enum class CallPhase { RINGING, ACTIVE, UNKNOWN }

/**
 * The only facts Auto-Call VPN reads from a notification, and only for an allow-listed app. No title, text, person, picture or action is ever read, so there's no caller name or number to leak or log.
 * The Android-facing code in CallListenerService fills this in. Everything below it is plain Kotlin, so it can be tested without a phone.
 */
data class NotificationFacts(
    val packageName: String,
    val category: String?,
    val ongoing: Boolean,
    /** Notification.EXTRA_CALL_TYPE: 1 incoming, 2 ongoing, 3 screening, or 0 if not set. Public from API 34, and written by CallStyle from API 31. */
    val callType: Int,
    /** Notification.EXTRA_TEMPLATE, such as "android.app.Notification$CallStyle". */
    val template: String?,
    val channelId: String?,
)

data class CallSignal(val app: CallApp, val phase: CallPhase)

object CallClassifier {
    private val PACKAGES = mapOf(
        "com.whatsapp" to CallApp.WHATSAPP,
        "com.whatsapp.w4b" to CallApp.WHATSAPP,
        "org.telegram.messenger" to CallApp.TELEGRAM,
        "org.telegram.messenger.web" to CallApp.TELEGRAM,
        "org.thunderdog.challegram" to CallApp.TELEGRAM,
    )

    fun appFor(packageName: String): CallApp? = PACKAGES[packageName]

    /**
     * Is this notification a call, and in what phase? Neither app documents its notifications, so this reads Android's own public signals in order of strength:
     *  1. A CallStyle type: the app itself said incoming or ongoing.
     *  2. CATEGORY_CALL: Android's documented category for calls.
     *  3. A weak guess, for apps that set neither: an ongoing notification on a channel named for calls. Marked UNKNOWN, so it can never produce a confident "active".
     * What each app really sends has to be seen on real phones: Settings > Diagnostics shows these same facts, with no notification content, for that purpose.
     */
    fun classify(facts: NotificationFacts): CallSignal? {
        val app = appFor(facts.packageName) ?: return null
        if (facts.category == "missed_call") return null
        when (facts.callType) {
            1, 3 -> return CallSignal(app, CallPhase.RINGING)
            2 -> return CallSignal(app, CallPhase.ACTIVE)
        }
        val callStyle = facts.template?.endsWith("\$CallStyle") == true
        if (facts.category == "call" || callStyle) {
            return CallSignal(app, if (facts.ongoing) CallPhase.UNKNOWN else CallPhase.RINGING)
        }
        if (facts.ongoing && looksLikeCallChannel(facts.channelId)) return CallSignal(app, CallPhase.UNKNOWN)
        return null
    }

    private fun looksLikeCallChannel(channel: String?): Boolean {
        val name = channel?.lowercase() ?: return false
        return "voip" in name || "call" in name
    }

    // Values of NotificationListenerService.REASON_APP_CANCEL and REASON_APP_CANCEL_ALL.
    private const val REASON_APP_CANCEL = 8
    private const val REASON_APP_CANCEL_ALL = 9

    /** Only the calling app itself removing its notification counts as the call being over. A person swiping it away, or anything else Android does, says nothing about the call. */
    fun isConfirmedEnd(reason: Int): Boolean = reason == REASON_APP_CANCEL || reason == REASON_APP_CANCEL_ALL
}
