package live.pricechat.kiwivpn

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.ParcelFileDescriptor
import androidx.core.content.ContextCompat
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.wireguard.crypto.KeyPair
import live.pricechat.kiwivpn.log.Diag
import live.pricechat.kiwivpn.model.Peer
import live.pricechat.kiwivpn.routing.AppCatalog
import live.pricechat.kiwivpn.routing.RoutingMode
import live.pricechat.kiwivpn.routing.RoutingPolicy
import live.pricechat.kiwivpn.routing.RoutingPrefs
import live.pricechat.kiwivpn.vpn.GoTunnelDriver
import live.pricechat.kiwivpn.vpn.PeerRules
import live.pricechat.kiwivpn.vpn.TunnelService
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/**
 * Real Android, with the real WireGuard library and a real VPN interface. No server or account is involved: the peer is 192.0.2.1, an address reserved for documentation, so no handshake ever happens and no traffic
 * leaves. What it proves is what Android itself reports about the interface: which apps it covers. It can't show traffic reaching a real server, and it says nothing about real phones' makers' changes.
 */
@RunWith(AndroidJUnit4::class)
class RealVpnInterfaceTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context: Context = instrumentation.targetContext
    private val own = context.packageName
    private val driver by lazy { GoTunnelDriver(context) }

    private fun shell(command: String): String =
        ParcelFileDescriptor.AutoCloseInputStream(instrumentation.uiAutomation.executeShellCommand(command)).bufferedReader().use { it.readText() }

    @Before fun grantVpnConsent() {
        shell("appops set $own ACTIVATE_VPN allow")
        assertFalse("VPN consent should now be granted", driver.needsPermission())
    }

    @After fun stop() {
        driver.down()
        Thread.sleep(1500)
    }

    private fun config(prefs: RoutingPrefs): String {
        val server = KeyPair()
        val peer = Peer(server.publicKey.toBase64(), "192.0.2.1", 51821, listOf("10.66.0.9/32"), listOf("10.66.0.1"), listOf("0.0.0.0/0", "::/0"), 25)
        val base = PeerRules.render(KeyPair().privateKey.toBase64(), peer)
        return RoutingPolicy.applyTo(base, RoutingPolicy.plan(prefs, own) { AppCatalog.isInstalled(context, it) })
    }

    /** Android's own record of which UIDs the VPN network covers, or null if there's no VPN network. */
    private fun vpnUidRanges(): List<IntRange>? {
        val dump = shell("dumpsys connectivity")
        // Android lists a VPN on Wi-Fi as "Transports: WIFI|VPN"
        val line = dump.lineSequence().firstOrNull { it.trimStart().startsWith("NetworkAgentInfo") && Regex("Transports: [A-Z_|]*VPN").containsMatchIn(it) } ?: return null
        val text = Regex("Uids: <\\{([^}]*)\\}>").find(line)?.groupValues?.get(1) ?: return emptyList()
        return text.split(",").map { it.trim() }.filter { it.isNotEmpty() }.map { range ->
            val parts = range.substringBefore("/").split("-")
            parts.first().toInt()..parts.last().toInt()
        }
    }

    private fun covers(ranges: List<IntRange>, packageName: String) = context.packageManager.getApplicationInfo(packageName, 0).uid.let { uid -> ranges.any { uid in it } }

    /** Two other installed apps with their own UIDs. */
    private fun twoOtherApps(): Pair<String, String> {
        val apps = AppCatalog.load(context).map { it.packageName }.filter { context.packageManager.getApplicationInfo(it, 0).uid >= 10000 }.distinctBy { context.packageManager.getApplicationInfo(it, 0).uid }
        assertTrue("This image needs at least two launchable apps: $apps", apps.size >= 2)
        return apps[0] to apps[1]
    }

    private fun waitForRanges(): List<IntRange> {
        repeat(40) {
            vpnUidRanges()?.let { return it }
            Thread.sleep(250)
        }
        throw AssertionError("Android never reported a VPN network. Tunnel up: ${driver.isUp()}")
    }

    @Test fun all_apps_covers_this_app_and_others() {
        val (a, b) = twoOtherApps()
        driver.up(config(RoutingPrefs()))
        assertTrue(driver.isUp())
        val ranges = waitForRanges()
        assertTrue(covers(ranges, own))
        assertTrue(covers(ranges, a))
        assertTrue(covers(ranges, b))
    }

    @Test fun only_selected_covers_the_chosen_app_and_kiwi_and_nothing_else() {
        val (chosen, other) = twoOtherApps()
        driver.up(config(RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf(chosen))))
        val ranges = waitForRanges()
        assertTrue("The chosen app must use the VPN: $ranges", covers(ranges, chosen))
        assertTrue("Kiwi itself stays on the VPN: $ranges", covers(ranges, own))
        assertFalse("Any other app must not: $ranges", covers(ranges, other))
    }

    @Test fun all_except_covers_everything_but_the_excluded_app() {
        val (excluded, other) = twoOtherApps()
        driver.up(config(RoutingPrefs(RoutingMode.ALL_EXCEPT, excluded = setOf(excluded))))
        val ranges = waitForRanges()
        assertFalse("The excluded app must bypass: $ranges", covers(ranges, excluded))
        assertTrue(covers(ranges, other))
        assertTrue(covers(ranges, own))
    }

    @Test fun switching_mode_replaces_the_interface_with_the_new_lists() {
        val (a, b) = twoOtherApps()
        driver.up(config(RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf(a))))
        assertFalse(covers(waitForRanges(), b))
        driver.up(config(RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf(b))))
        Thread.sleep(1000)
        val ranges = waitForRanges()
        assertTrue("The new list is on the interface: $ranges", covers(ranges, b))
        assertFalse("The old list is gone: $ranges", covers(ranges, a))
    }

    @Test fun ten_rounds_of_up_and_down_with_no_pause_stay_up_and_end_clean() {
        repeat(10) { round ->
            driver.up(config(RoutingPrefs()))
            Thread.sleep(1500)
            assertTrue("Round $round: a new tunnel must survive the old service going away", driver.isUp())
            waitForRanges()
            driver.down()
            assertFalse(driver.isUp())
        }
        repeat(40) {
            if (vpnUidRanges() == null) return
            Thread.sleep(250)
        }
        throw AssertionError("A VPN network stayed behind after the last disconnect")
    }

    @Test fun no_handshake_is_reported_when_the_server_doesnt_answer() {
        driver.up(config(RoutingPrefs()))
        Thread.sleep(4000)
        assertEquals("Nothing answered, so nothing may claim a handshake", 0L, driver.stats()!!.lastHandshakeMs)
    }

    /** The open risk: Android's documentation lists VPN apps as allowed this foreground service type, and lint says otherwise. */
    @Test fun the_foreground_service_type_is_accepted_for_a_vpn_app() {
        driver.up(config(RoutingPrefs()))
        waitForRanges()
        val before = Diag.recent().size
        ContextCompat.startForegroundService(context, Intent(context, TunnelService::class.java))
        Thread.sleep(6000) // past the five seconds Android allows a service to enter the foreground
        val events = Diag.recent().drop(before.coerceAtMost(Diag.recent().size))
        assertFalse("Android ${Build.VERSION.SDK_INT} refused it: $events", events.any { it.contains("foreground_refused") || it.contains("foreground_start_refused") })
        assertTrue("The app is still alive", File("/proc/self").exists())
    }
}
