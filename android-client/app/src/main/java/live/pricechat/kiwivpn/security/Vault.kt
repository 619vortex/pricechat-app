package live.pricechat.kiwivpn.security

import kotlinx.serialization.Serializable
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import live.pricechat.kiwivpn.model.Peer
import live.pricechat.kiwivpn.model.ServerRef
import live.pricechat.kiwivpn.routing.RoutingPrefs
import java.io.File
import java.security.GeneralSecurityException

/** Encrypts and decrypts the app's one saved file. KeystoreSealer is the real one. */
interface Sealer {
    fun seal(plain: ByteArray): ByteArray
    fun open(sealed: ByteArray): ByteArray
}

@Serializable
data class SavedDevice(val deviceId: Long, val privateKey: String, val peer: Peer? = null)

@Serializable
data class AppSettings(
    val autoConnect: Boolean = false,
    val notificationShowsCountry: Boolean = true,
    val autoCallEnabled: Boolean = false,
    val autoCallWhatsApp: Boolean = false,
    val autoCallTelegram: Boolean = false,
    val autoCallConsented: Boolean = false,
    /** Which apps use the VPN. On this phone only: never sent to Price Chat. */
    val routing: RoutingPrefs = RoutingPrefs(),
)

@Serializable
data class VaultData(
    val session: String? = null,
    val refreshedAt: Long? = null,
    val devices: Map<Long, SavedDevice> = emptyMap(),
    val lastServer: ServerRef? = null,
    /** The tunnel the person last asked for and hasn't ended: how a restarted process knows a connection was lost. */
    val wanted: ServerRef? = null,
    /** Auto-Call VPN started the running connection, so it may end it. Nothing about any call is ever saved: this is the only Auto-Call state on disk. */
    val autoCallOwned: Boolean = false,
    val settings: AppSettings = AppSettings(),
)

/**
 * Everything the app keeps, in one encrypted file: the session, the devices' private keys, and settings. Excluded from backups by the manifest.
 * If it can't be opened, as after a restore or a reset of the Keystore, it's treated as empty: the person signs in again and the phone makes new keys.
 */
class Vault(private val file: File, private val sealer: Sealer) {
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = false; coerceInputValues = true }
    private val lock = Any()
    private var cached: VaultData? = null

    fun read(): VaultData = synchronized(lock) {
        cached ?: load().also { cached = it }
    }

    fun update(change: (VaultData) -> VaultData): VaultData = synchronized(lock) {
        val next = change(read())
        write(next)
        cached = next
        next
    }

    /** Signed out: the keys and session go, and the settings stay. */
    fun forgetAccount(): VaultData = update { VaultData(settings = it.settings, autoCallOwned = false) }

    private fun load(): VaultData {
        if (!file.exists()) return VaultData()
        return try {
            json.decodeFromString(VaultData.serializer(), sealer.open(file.readBytes()).toString(Charsets.UTF_8))
        } catch (_: GeneralSecurityException) {
            file.delete(); VaultData()
        } catch (_: SerializationException) {
            file.delete(); VaultData()
        } catch (_: IllegalArgumentException) {
            file.delete(); VaultData()
        }
    }

    /** All or nothing: a crash mid-write leaves the old file. */
    private fun write(data: VaultData) {
        val temp = File(file.parentFile, file.name + ".tmp")
        temp.writeBytes(sealer.seal(json.encodeToString(VaultData.serializer(), data).toByteArray(Charsets.UTF_8)))
        if (!temp.renameTo(file)) {
            file.delete()
            check(temp.renameTo(file)) { "Couldn't save the app's data." }
        }
    }
}
