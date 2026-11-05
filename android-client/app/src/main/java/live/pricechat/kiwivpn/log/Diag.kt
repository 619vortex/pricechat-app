package live.pricechat.kiwivpn.log

import android.util.Log
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * The app's only log: named events with short values, kept in memory for the Diagnostics screen and sent to logcat. There's no way to log free text, and every value passes `clean`.
 * Never given a key, a token, a config, an IP address, or anything from a notification.
 */
object Diag {
    private const val KEEP = 200
    private val lines = ArrayDeque<String>()
    private val time = SimpleDateFormat("HH:mm:ss", Locale.US)

    /** Anything that looks like a secret, or that's longer than a short value should be, never gets through. */
    fun clean(value: Any?): String {
        val text = value?.toString() ?: "null"
        if (text.length > 40) return "[removed]"
        if (text.startsWith("kd.") || Regex("[A-Za-z0-9+/_-]{32,}={0,2}").containsMatchIn(text)) return "[removed]"
        if (Regex("\\d{1,3}(\\.\\d{1,3}){3}").containsMatchIn(text) || text.contains(':')) return "[removed]"
        return text
    }

    fun format(name: String, values: Array<out Pair<String, Any?>>): String =
        name + values.joinToString("") { " ${it.first}=${clean(it.second)}" }

    fun event(name: String, vararg values: Pair<String, Any?>) {
        val line = format(name, values)
        synchronized(lines) {
            lines.addLast("${synchronized(time) { time.format(Date()) }} $line")
            while (lines.size > KEEP) lines.removeFirst()
        }
        Log.i("KiwiVPN", line)
    }

    fun recent(): List<String> = synchronized(lines) { lines.toList() }
}
