package live.pricechat.kiwivpn

import android.app.Application
import android.content.Context
import androidx.test.runner.AndroidJUnitRunner
import kotlinx.coroutines.Dispatchers
import live.pricechat.kiwivpn.model.ServerRef
import live.pricechat.kiwivpn.vpn.ConnectionManager
import live.pricechat.kiwivpn.vpn.ConnectionMemory
import live.pricechat.kiwivpn.vpn.Profile
import live.pricechat.kiwivpn.vpn.ProfileSource
import live.pricechat.kiwivpn.vpn.TunnelDriver

class KiwiTestRunner : AndroidJUnitRunner() {
    override fun newApplication(loader: ClassLoader, name: String, context: Context): Application =
        super.newApplication(loader, TestKiwiApp::class.java.name, context)
}

/**
 * The real app, the real listener, real Android notifications and the real WireGuard interface, with two stand-ins, because no server is involved: the connection details come from here, and the driver
 * reports the handshake a server would have answered with. So these tests can't show traffic reaching a server.
 */
class TestKiwiApp : KiwiApp() {
    override val connection: ConnectionManager by lazy {
        ConnectionManager(
            scope = scope,
            driver = object : TunnelDriver by driver {
                override fun stats() = driver.stats()?.copy(lastHandshakeMs = System.currentTimeMillis())
            },
            source = object : ProfileSource {
                override suspend fun profile(serverId: Long, keepServer: Long?) = Profile(AutoCallTestData.config(), "192.0.2.1", listOf("10.66.0.1"), fromCache = false)
            },
            memory = object : ConnectionMemory {
                override fun wanted() = vault.read().wanted
                override fun setWanted(server: ServerRef?) { vault.update { it.copy(wanted = server) } }
            },
            ip = { "203.0.113.9" },
            networkChanged = {},
            blocking = Dispatchers.IO,
        )
    }
}
