package live.pricechat.kiwivpn

import live.pricechat.kiwivpn.model.Account
import live.pricechat.kiwivpn.model.Device
import live.pricechat.kiwivpn.model.Me
import live.pricechat.kiwivpn.model.NewDevice
import live.pricechat.kiwivpn.model.Peer
import live.pricechat.kiwivpn.model.Plan
import live.pricechat.kiwivpn.model.Server
import live.pricechat.kiwivpn.model.ServerRef
import live.pricechat.kiwivpn.model.UnifiedState
import live.pricechat.kiwivpn.net.ApiException
import live.pricechat.kiwivpn.net.KiwiApi
import live.pricechat.kiwivpn.net.UNREACHABLE
import live.pricechat.kiwivpn.security.Sealer
import live.pricechat.kiwivpn.security.Vault
import live.pricechat.kiwivpn.vpn.ConnectionMemory
import live.pricechat.kiwivpn.vpn.KeyMaker
import live.pricechat.kiwivpn.vpn.KeyPairText
import java.io.File
import java.nio.file.Files
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.spec.GCMParameterSpec

const val KEY_A = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
const val KEY_B = "ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8="

val GERMANY = ServerRef(2, "Germany", "DE")
val FINLAND = ServerRef(1, "Helsinki 1", "FI")

fun peer() = Peer(KEY_A, "5.75.10.20", 51821, listOf("10.66.0.9/32"), listOf("10.66.0.1"), listOf("0.0.0.0/0", "::/0"), 25)

fun account(eligible: Boolean = true, devices: List<Device> = emptyList(), maxDevices: Long = 3) = Account(
    enabled = true,
    eligible = eligible,
    reason = if (eligible) null else "Your free trial has ended.",
    maxDevices = maxDevices,
    servers = listOf(Server(1, "Helsinki 1", "Finland", "FI", online = true, recommended = true), Server(2, "Germany", "Falkenstein, Germany", "DE", online = true), Server(3, "Offline", "", "XX", online = false)),
    devices = devices,
    plan = Plan("trial", null, 12),
)

/** Seals with a real AES-GCM key held in memory, so the Vault's encryption really runs in these tests. */
class MemorySealer : Sealer {
    private val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
    override fun seal(plain: ByteArray): ByteArray {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, key)
        return c.iv + c.doFinal(plain)
    }
    override fun open(sealed: ByteArray): ByteArray {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, sealed, 0, 12))
        return c.doFinal(sealed, 12, sealed.size - 12)
    }
}

fun tempVault(sealer: Sealer = MemorySealer()) = Vault(File(Files.createTempDirectory("vault").toFile(), "vault.bin"), sealer)

class FakeKeys : KeyMaker {
    var made = 0
    override fun generate(): KeyPairText {
        made++
        return KeyPairText(KEY_B, KEY_A)
    }
}

class FakeMemory : ConnectionMemory {
    var value: ServerRef? = null
    override fun wanted() = value
    override fun setWanted(server: ServerRef?) { value = server }
}

/** The backend's rules that matter to the client: a device limit, 404s and scripted failures. */
class FakeApi(var current: Account = account()) : KiwiApi {
    val calls = mutableListOf<String>()
    var accountFails: ApiException? = null
    var failuresBeforeAnswer = 0
    var peerAnswer: Peer = peer()
    var addFails: ApiException? = null
    var trialAnswer: Account? = null
    private var nextId = 50L
    val unreachable get() = ApiException(0, UNREACHABLE)

    override suspend fun account(token: String): Account {
        calls += "account"
        if (failuresBeforeAnswer > 0) { failuresBeforeAnswer--; throw unreachable }
        accountFails?.let { throw it }
        return current
    }
    override suspend fun startTrial(token: String): Account { calls += "trial"; return trialAnswer ?: current }
    override suspend fun peer(token: String, deviceId: Long): Peer { calls += "peer $deviceId"; return peerAnswer }
    override suspend fun addDevice(token: String, name: String, serverId: Long, publicKey: String): NewDevice {
        calls += "add $serverId $publicKey"
        addFails?.let { throw it }
        if (current.devices.size >= current.maxDevices) throw ApiException(409, "You can have up to ${current.maxDevices} devices. Delete one first.")
        val d = Device(nextId++, name, serverId)
        current = current.copy(devices = current.devices + d)
        return NewDevice(d, peerAnswer)
    }
    override suspend fun deleteDevice(token: String, deviceId: Long) {
        calls += "delete $deviceId"
        current = current.copy(devices = current.devices.filter { it.id != deviceId })
    }
    override suspend fun me(token: String) = Me()
    override suspend fun unifiedState(token: String, version: String) = UnifiedState()
    override suspend fun exchangeCode(code: String, verifier: String) = "kd.token"
    override suspend fun refresh(token: String) = "kd.fresh"
    override suspend fun logout(token: String) { calls += "logout" }
    var deleteFails: ApiException? = null
    override suspend fun deleteAccount(token: String, confirmUsername: String) {
        calls += "delete-account $confirmUsername"
        deleteFails?.let { throw it }
    }
    override suspend fun myIp() = "5.75.10.20"
    override fun dropConnections() { calls += "drop" }
}
