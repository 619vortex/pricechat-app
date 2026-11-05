package live.pricechat.kiwivpn.vpn

import android.content.Context
import android.net.VpnService
import android.os.Build
import android.os.SystemClock
import com.wireguard.android.backend.BackendException
import com.wireguard.android.backend.GoBackend
import com.wireguard.android.backend.Tunnel
import com.wireguard.config.BadConfigException
import com.wireguard.config.Config
import com.wireguard.crypto.KeyPair
import live.pricechat.kiwivpn.log.Diag
import java.io.ByteArrayInputStream
import java.io.IOException

/** The official WireGuard library. All WireGuard cryptography happens in it: this file only tells it what to do. */
class GoTunnelDriver(private val context: Context) : TunnelDriver {
    private val backend: GoBackend by lazy { GoBackend(context.applicationContext) }
    @Volatile private var onDown: () -> Unit = {}
    private var lastDownAt = 0L

    private val tunnel = object : Tunnel {
        override fun getName(): String = "kiwi"
        override fun onStateChange(newState: Tunnel.State) {
            if (newState == Tunnel.State.DOWN) onDown()
        }
    }

    override fun setOnDown(listener: () -> Unit) {
        onDown = listener
    }

    override fun needsPermission(): Boolean = VpnService.prepare(context) != null

    @Synchronized
    override fun up(config: String) {
        val parsed = try {
            Config.parse(ByteArrayInputStream(config.toByteArray(Charsets.UTF_8)))
        } catch (_: BadConfigException) {
            throw TunnelFailure("The connection details couldn't be used.")
        } catch (_: IOException) {
            throw TunnelFailure("The connection details couldn't be used.")
        }
        try {
            // The library's DOWN calls stopSelf on its VpnService, and Android destroys that service a moment later. If a new tunnel were started on the dying service, its onDestroy would take the new tunnel
            // down too. So the old tunnel goes first, and the service gets a moment to finish. This is the same class of bug as Windows 0.1.0's reconnect: if it still happens, it reads as a failure, not as Connected.
            if (backend.getState(tunnel) == Tunnel.State.UP) down()
            val waited = SystemClock.elapsedRealtime() - lastDownAt
            if (waited < SERVICE_SETTLE_MS) Thread.sleep(SERVICE_SETTLE_MS - waited)
            backend.setState(tunnel, Tunnel.State.UP, parsed)
        } catch (e: TunnelFailure) {
            throw e
        } catch (e: BackendException) {
            Diag.event("backend_error", "reason" to e.reason.name)
            throw TunnelFailure(friendly(e.reason))
        } catch (e: UnsatisfiedLinkError) {
            Diag.event("native_library_missing")
            throw TunnelFailure("This phone's processor isn't supported.")
        } catch (e: Exception) {
            // Mostly Android refusing to start the VPN service from the background.
            Diag.event("start_error", "type" to e.javaClass.simpleName)
            throw TunnelFailure("Android wouldn't start the VPN right now. Open Kiwi VPN and press Connect.")
        }
    }

    @Synchronized
    override fun down() {
        try {
            backend.setState(tunnel, Tunnel.State.DOWN, null)
            lastDownAt = SystemClock.elapsedRealtime()
        } catch (e: Exception) {
            Diag.event("stop_error", "type" to e.javaClass.simpleName)
            throw TunnelFailure("Couldn't stop the tunnel. Try again, or turn the VPN off in Settings.")
        }
    }

    @Synchronized
    override fun isUp(): Boolean = backend.getState(tunnel) == Tunnel.State.UP

    @Synchronized
    override fun stats(): TunnelStats? {
        if (backend.getState(tunnel) != Tunnel.State.UP) return null
        val all = backend.getStatistics(tunnel)
        val key = all.peers().firstOrNull() ?: return null
        val peer = all.peer(key) ?: return null
        return TunnelStats(peer.latestHandshakeEpochMillis(), all.totalRx(), all.totalTx())
    }

    /** null when Android hasn't said, as when no tunnel is running. */
    @Synchronized
    fun alwaysOn(): Pair<Boolean, Boolean>? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return null
        return try {
            backend.isAlwaysOn() to backend.isLockdownEnabled()
        } catch (_: Exception) {
            null
        }
    }

    private fun friendly(reason: BackendException.Reason): String = when (reason) {
        BackendException.Reason.VPN_NOT_AUTHORIZED -> "Kiwi VPN needs your permission to create a VPN. Open the app and press Connect."
        BackendException.Reason.UNABLE_TO_START_VPN -> "Android didn't start the VPN service. Please try again."
        BackendException.Reason.TUN_CREATION_ERROR -> "Android refused to create the VPN. Another VPN app may be set as always-on."
        BackendException.Reason.DNS_RESOLUTION_FAILURE -> "Couldn't find the server. Check your connection."
        else -> "Couldn't start the tunnel."
    }
}

private const val SERVICE_SETTLE_MS = 1200L

class WireGuardKeyMaker : KeyMaker {
    override fun generate(): KeyPairText {
        val pair = KeyPair() // SecureRandom, inside the library
        return KeyPairText(pair.privateKey.toBase64(), pair.publicKey.toBase64())
    }
}
