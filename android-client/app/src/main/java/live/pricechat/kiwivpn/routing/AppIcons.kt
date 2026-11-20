package live.pricechat.kiwivpn.routing

import android.content.Context
import android.content.pm.PackageManager
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.core.graphics.drawable.toBitmap
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** A small least-recently-used cache. Plain Kotlin, so its rules are tested without a phone. */
class LruMemo<K : Any, V : Any>(private val maxEntries: Int) {
    init {
        require(maxEntries > 0)
    }

    private val map = object : LinkedHashMap<K, V>(16, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<K, V>?): Boolean = size > maxEntries
    }

    @Synchronized fun get(key: K): V? = map[key]
    @Synchronized fun put(key: K, value: V) { map[key] = value }
    @Synchronized fun size(): Int = map.size
    @Synchronized fun keys(): List<K> = map.keys.toList()
}

/**
 * Real app icons for the app pickers. Each one is loaded with PackageManager off the main thread, drawn once at the size it's shown (so a 512 px adaptive icon becomes a small bitmap), and kept in memory.
 * At most 4 load at a time, so scrolling a long list never floods the phone. Icons are never written to disk or sent anywhere.
 */
object AppIcons {
    private const val MAX_ICONS = 160 // about 160 x 40 dp icons: under 2 MB at 3x density
    private val memo = LruMemo<String, ImageBitmap>(MAX_ICONS)

    @OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
    private val loader: CoroutineDispatcher = Dispatchers.IO.limitedParallelism(4)

    fun cached(packageName: String, px: Int): ImageBitmap? = memo.get(key(packageName, px))

    suspend fun load(context: Context, packageName: String, px: Int): ImageBitmap? {
        memo.get(key(packageName, px))?.let { return it }
        return withContext(loader) {
            try {
                val pm = context.packageManager
                val drawable = pm.getApplicationInfo(packageName, 0).loadIcon(pm)
                drawable.toBitmap(px, px).asImageBitmap().also { memo.put(key(packageName, px), it) }
            } catch (_: PackageManager.NameNotFoundException) {
                null
            } catch (_: RuntimeException) {
                null // a broken icon resource in another app must not break the list
            }
        }
    }

    private fun key(packageName: String, px: Int) = "$packageName@$px"
}
