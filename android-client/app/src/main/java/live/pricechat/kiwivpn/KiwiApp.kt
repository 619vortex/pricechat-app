package live.pricechat.kiwivpn

import android.app.Application
import android.os.Build
import com.wireguard.android.backend.GoBackend
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.distinctUntilChangedBy
import kotlinx.coroutines.launch
import live.pricechat.kiwivpn.autocall.AutoCallController
import live.pricechat.kiwivpn.log.Diag
import live.pricechat.kiwivpn.model.ServerRef
import live.pricechat.kiwivpn.net.HttpKiwiApi
import live.pricechat.kiwivpn.security.KeystoreSealer
import live.pricechat.kiwivpn.security.Vault
import live.pricechat.kiwivpn.vpn.ConnectionManager
import live.pricechat.kiwivpn.vpn.ConnectionMemory
import live.pricechat.kiwivpn.vpn.ConnectionState
import live.pricechat.kiwivpn.vpn.GoTunnelDriver
import live.pricechat.kiwivpn.vpn.Provisioner
import live.pricechat.kiwivpn.vpn.TunnelService
import live.pricechat.kiwivpn.vpn.WireGuardKeyMaker
import java.io.File

/** Holds the app's single instances. The connection outlives every screen, so it lives here rather than in a ViewModel. */
class KiwiApp : Application() {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    val vault by lazy { Vault(File(noBackupFilesDir, "vault.bin"), KeystoreSealer()) }
    val api by lazy { HttpKiwiApi(userAgent = "KiwiVPN-Android/${BuildConfig.VERSION_NAME}") }
    val driver by lazy { GoTunnelDriver(this) }

    val connection: ConnectionManager by lazy {
        ConnectionManager(
            scope = scope,
            driver = driver,
            source = Provisioner(api, vault, WireGuardKeyMaker(), ::deviceName),
            memory = object : ConnectionMemory {
                override fun wanted() = vault.read().wanted
                override fun setWanted(server: ServerRef?) { vault.update { it.copy(wanted = server) } }
            },
            ip = { api.myIp() },
            networkChanged = { api.dropConnections() },
            blocking = Dispatchers.IO,
        )
    }

    val autoCall: AutoCallController by lazy { AutoCallController(this, scope, vault, connection) }

    override fun onCreate() {
        super.onCreate()
        TunnelService.createChannel(this)
        GoBackend.setAlwaysOnCallback { alwaysOnStarted() }
        connection.reconcile()
        autoCall // listens to the connection from the start
        scope.launch {
            connection.state.distinctUntilChangedBy { it::class }.collect { state ->
                if (state is ConnectionState.Connecting && !connection.needsPermission()) TunnelService.start(this@KiwiApp)
            }
        }
    }

    /** The system started the VPN for "Always-on VPN": at boot, or after the app was stopped. Uses saved details if the network is blocked until the tunnel is up. */
    private fun alwaysOnStarted() {
        Diag.event("always_on_started")
        val server = vault.read().lastServer ?: return
        connection.connect(server)
    }

    fun userConnect(server: ServerRef) {
        autoCall.userConnected()
        vault.update { it.copy(lastServer = server) }
        connection.connect(server)
    }

    fun userDisconnect() {
        autoCall.userDisconnected()
        connection.disconnect()
    }

    /** Up to the backend's 32 characters. It's how a reinstalled app finds the device whose key it no longer has. A model name is all it says about the phone. */
    fun deviceName(serverId: Long): String {
        val model = Build.MODEL.orEmpty().filter { it.isLetterOrDigit() && it.code < 128 || it == '-' }.take(16)
        return "Android ${model.ifEmpty { "phone" }} ($serverId)"
    }
}
