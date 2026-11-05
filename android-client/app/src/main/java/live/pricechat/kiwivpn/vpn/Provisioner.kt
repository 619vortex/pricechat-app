package live.pricechat.kiwivpn.vpn

import kotlinx.coroutines.delay
import live.pricechat.kiwivpn.model.Account
import live.pricechat.kiwivpn.model.Peer
import live.pricechat.kiwivpn.net.ApiException
import live.pricechat.kiwivpn.net.KiwiApi
import live.pricechat.kiwivpn.security.SavedDevice
import live.pricechat.kiwivpn.security.Vault

/** A tunnel ready to bring up. `config` holds the private key: see PeerRules.render. */
class Profile(val config: String, val endpoint: String, val dns: List<String>, val fromCache: Boolean)

sealed class ProfileError(message: String) : Exception(message) {
    class SignedOut : ProfileError("Please sign in again.")
    class NotEntitled(reason: String?) : ProfileError(reason ?: "Your account doesn't include Kiwi VPN right now.")
    class DeviceLimit(val tooMany: Boolean) : ProfileError(if (tooMany) "Too many new devices. Please wait a few minutes and try again." else "You've used all your devices. Remove one under Account, or at pricechat.live/vpn, then try again.")
    class Other(message: String) : ProfileError(message)
    class BadAnswer : ProfileError("The server sent connection details the app can't trust, so it didn't connect. Please try again later.")
}

class KeyPairText(val privateKey: String, val publicKey: String)

interface KeyMaker {
    fun generate(): KeyPairText
}

interface ProfileSource {
    /** `keepServer`: the server the running tunnel uses, whose device must not be removed to make room. */
    suspend fun profile(serverId: Long, keepServer: Long?): Profile
}

/**
 * From "choose a server" to a config, reusing this phone's device on that server, or making a key and registering one. The private key is made here and only its public half is ever sent.
 *
 * Why a device stays after switching away: the backend allows 5 new devices, then one every 10 minutes (rate_limit.py, vpn_device), so a fresh device per switch would soon fail. Why it's sometimes
 * removed: each server needs its own device and an account has 3 by default, so a PC and a phone on two countries each would need 4. Only when the account is full does this phone give up its own
 * device on another server. It never touches a device it doesn't hold the key for, except one with its own name whose key is gone, as after a reinstall.
 */
class Provisioner(
    private val api: KiwiApi,
    private val vault: Vault,
    private val keys: KeyMaker,
    private val deviceName: (Long) -> String,
    private val retryPauseMs: Long = 1500,
) : ProfileSource {

    override suspend fun profile(serverId: Long, keepServer: Long?): Profile {
        val token = vault.read().session ?: throw ProfileError.SignedOut()
        val account = try {
            withRetry { api.account(token) }
        } catch (e: ApiException) {
            when {
                e.unreachable -> return cached(serverId) ?: throw ProfileError.Other(e.message)
                e.signedOut -> { vault.update { it.copy(session = null) }; throw ProfileError.SignedOut() }
                else -> throw ProfileError.Other(e.message)
            }
        }
        if (!account.eligible) throw ProfileError.NotEntitled(account.reason)
        val server = account.servers.firstOrNull { it.id == serverId } ?: throw ProfileError.Other("That server is no longer available. Please choose another.")
        if (!server.online) throw ProfileError.Other("That server is offline right now. Please choose another.")

        val saved = vault.read().devices[serverId]
        if (saved != null) {
            if (account.devices.any { it.id == saved.deviceId }) {
                val peer = try {
                    withRetry { api.peer(token, saved.deviceId) }
                } catch (e: ApiException) {
                    if (e.unreachable) return cached(serverId) ?: throw ProfileError.Other(e.message)
                    if (e.signedOut) { vault.update { it.copy(session = null) }; throw ProfileError.SignedOut() }
                    throw ProfileError.Other(e.message)
                }
                return build(serverId, saved.deviceId, saved.privateKey, peer)
            }
            forget(serverId) // removed on the website or from another device: this key no longer works
        }
        return register(token, account, serverId, keepServer)
    }

    private suspend fun register(token: String, account: Account, serverId: Long, keepServer: Long?): Profile {
        val name = deviceName(serverId)
        try {
            // A device with this phone's name whose key is gone can never connect again, so it shouldn't hold a slot.
            account.devices.firstOrNull { it.serverId == serverId && it.name == name }?.let { api.deleteDevice(token, it.id) }
            val pair = keys.generate()
            val added = try {
                api.addDevice(token, name, serverId, pair.publicKey)
            } catch (e: ApiException) {
                if (e.status != 409 || !makeRoom(token, serverId, keepServer)) throw e
                api.addDevice(token, name, serverId, pair.publicKey)
            }
            return build(serverId, added.device.id, pair.privateKey, added.peer)
        } catch (e: ApiException) {
            throw when (e.status) {
                401 -> { vault.update { it.copy(session = null) }; ProfileError.SignedOut() }
                409 -> ProfileError.DeviceLimit(tooMany = false)
                429 -> ProfileError.DeviceLimit(tooMany = true)
                else -> ProfileError.Other(e.message)
            }
        }
    }

    /** Gives up this phone's own device on another server. True if one went. */
    private suspend fun makeRoom(token: String, serverId: Long, keepServer: Long?): Boolean {
        val spare = vault.read().devices.entries.firstOrNull { it.key != serverId && it.key != keepServer } ?: return false
        api.deleteDevice(token, spare.value.deviceId)
        forget(spare.key)
        return true
    }

    private fun build(serverId: Long, deviceId: Long, privateKey: String, peer: Peer): Profile {
        try {
            PeerRules.check(peer)
        } catch (_: BadPeer) {
            throw ProfileError.BadAnswer()
        }
        vault.update { it.copy(devices = it.devices + (serverId to SavedDevice(deviceId, privateKey, peer))) }
        return Profile(PeerRules.render(privateKey, peer), peer.endpointHost, peer.dns, fromCache = false)
    }

    /**
     * Price Chat can't be reached: use the details saved the last time. This is what lets a phone reconnect when "Block connections without VPN" is on, since nothing can reach the internet until the tunnel is up.
     * It can't skip a check: the server only accepts this key while the account is entitled to it, and otherwise the handshake never completes.
     */
    private fun cached(serverId: Long): Profile? {
        val saved = vault.read().devices[serverId] ?: return null
        val peer = saved.peer ?: return null
        try { PeerRules.check(peer) } catch (_: BadPeer) { return null }
        return Profile(PeerRules.render(saved.privateKey, peer), peer.endpointHost, peer.dns, fromCache = true)
    }

    private fun forget(serverId: Long) {
        vault.update { it.copy(devices = it.devices - serverId) }
    }

    /** A request that never got an answer is tried again, since the network takes a moment to settle after the tunnel changes. Any answer from Price Chat stands. */
    private suspend fun <T> withRetry(call: suspend () -> T): T {
        repeat(2) {
            try {
                return call()
            } catch (e: ApiException) {
                if (!e.unreachable) throw e
                api.dropConnections()
                delay(retryPauseMs)
            }
        }
        return call()
    }
}
