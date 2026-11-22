package live.pricechat.fakecall

import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Person
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ShortcutInfo
import android.content.pm.ShortcutManager
import android.graphics.drawable.Icon

class EmptyActivity : Activity()

/** Posts one notification of the shape named in `kind`, from this package. Driven over adb: am broadcast -n <package>/live.pricechat.fakecall.CallReceiver --es kind ring --ei id 1 */
class CallReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val kind = intent.getStringExtra("kind") ?: return // a tap or a full-screen launch of the pending intent below
        val id = intent.getIntExtra("id", 1)
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannels(listOf(
            NotificationChannel("incoming", "Incoming", NotificationManager.IMPORTANCE_HIGH),
            NotificationChannel("voip_ongoing", "Current", NotificationManager.IMPORTANCE_LOW),
            NotificationChannel("messages", "Messages", NotificationManager.IMPORTANCE_HIGH),
        ))
        val pending = PendingIntent.getBroadcast(context, 0, Intent(context, CallReceiver::class.java), PendingIntent.FLAG_IMMUTABLE)
        val person = Person.Builder().setName("Test person").setImportant(true).build()
        fun on(channel: String) = Notification.Builder(context, channel).setSmallIcon(android.R.drawable.sym_action_call).setContentTitle("Fixture").setContentText(kind)
        val note: Notification? = when (kind) {
            "ring" -> on("incoming").setStyle(Notification.CallStyle.forIncomingCall(person, pending, pending)).setCategory(Notification.CATEGORY_CALL).setFullScreenIntent(pending, true).build()
            "ongoing" -> on("incoming").setStyle(Notification.CallStyle.forOngoingCall(person, pending)).setCategory(Notification.CATEGORY_CALL).setOngoing(true).setFullScreenIntent(pending, true).build()
            "plain" -> on("incoming").setCategory(Notification.CATEGORY_CALL).setOngoing(true).build() // a call category, with no CallStyle
            "voip" -> on("voip_ongoing").setOngoing(true).build() // no call category: only the channel's name hints at a call
            "clearable" -> on("incoming").setCategory(Notification.CATEGORY_CALL).build() // a call a person can swipe away
            "missed" -> on("messages").setCategory("missed_call").build()
            "note" -> on("messages").setCategory(Notification.CATEGORY_MESSAGE).build()
            "conversation" -> {
                context.getSystemService(ShortcutManager::class.java).pushDynamicShortcut(
                    ShortcutInfo.Builder(context, "chat").setShortLabel("Chat").setLongLived(true).setPerson(person)
                        .setIcon(Icon.createWithResource(context, android.R.drawable.sym_action_chat))
                        .setIntent(Intent(context, EmptyActivity::class.java).setAction(Intent.ACTION_MAIN)).build(),
                )
                on("messages").setShortcutId("chat").setCategory(Notification.CATEGORY_MESSAGE)
                    .setStyle(Notification.MessagingStyle(person).addMessage("A chat message", System.currentTimeMillis(), person)).build()
            }
            else -> null
        }
        when {
            note != null -> manager.notify(id, note)
            kind == "end" -> manager.cancel(id)
            kind == "endall" -> manager.cancelAll()
        }
    }
}
