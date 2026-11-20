package live.pricechat.kiwivpn

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.ParcelFileDescriptor
import android.util.Log
import androidx.core.content.ContextCompat
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.wireguard.crypto.KeyPair
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
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.net.InetSocketAddress
import java.net.Socket

/** Shared helpers for the on-device routing tests. Evidence goes to logcat under the tag KiwiEvidence, so it survives the test app being uninstalled after the run. */
abstract class RoutingDeviceTestBase {
    protected val instrumentation = InstrumentationRegistry.getInstrumentation()
    protected val context: Context = instrumentation.targetContext
    protected val own: String = context.packageName
    protected val driver by lazy { GoTunnelDriver(context) }

    protected fun shell(command: String): String =
        ParcelFileDescriptor.AutoCloseInputStream(instrumentation.uiAutomation.executeShellCommand(command)).bufferedReader().use { it.readText() }

    protected fun evidence(name: String, text: String) {
        Log.i(TAG, "===== $name (Android ${Build.VERSION.RELEASE}, SDK ${Build.VERSION.SDK_INT}, ${Build.MODEL}) =====")
        text.lineSequence().forEach { line -> line.chunked(900).forEach { Log.i(TAG, it) } }
    }

    /**
     * A tunnel to 192.0.2.1 (reserved for documentation): nothing ever answers, so traffic routed into it goes nowhere. That makes routing visible: an app inside the VPN loses its connection, an app outside
     * it keeps its normal one. The tunnel's DNS server, 10.66.0.1, is reachable only through the tunnel.
     */
    protected fun config(prefs: RoutingPrefs): String {
        val peer = Peer(KeyPair().publicKey.toBase64(), "192.0.2.1", 51821, listOf("10.66.0.9/32"), listOf(TUNNEL_DNS), listOf("0.0.0.0/0", "::/0"), 25)
        val base = PeerRules.render(KeyPair().privateKey.toBase64(), peer)
        return RoutingPolicy.applyTo(base, RoutingPolicy.plan(prefs, own) { AppCatalog.isInstalled(context, it) })
    }

    /** The VPN network's line from Android's own report, or null when there's no VPN network. */
    protected fun vpnLine(): String? = shell("dumpsys connectivity").lineSequence()
        .firstOrNull { it.trimStart().startsWith("NetworkAgentInfo") && Regex("Transports: [A-Z_|]*VPN").containsMatchIn(it) }

    protected fun uidRanges(line: String): List<IntRange> {
        val text = Regex("Uids: <\\{([^}]*)\\}>").find(line)?.groupValues?.get(1) ?: return emptyList()
        return text.split(",").map { it.trim() }.filter { it.isNotEmpty() }.map { range ->
            val parts = range.substringBefore("/").split("-")
            parts.first().toInt()..parts.last().toInt()
        }
    }

    protected fun dnsOf(line: String): String = Regex("DnsAddresses: \\[([^]]*)]").find(line)?.groupValues?.get(1)?.trim() ?: ""

    protected fun uidOf(packageName: String) = context.packageManager.getApplicationInfo(packageName, 0).uid

    protected fun covers(ranges: List<IntRange>, packageName: String) = uidOf(packageName).let { uid -> ranges.any { uid in it } }

    protected fun waitForVpn(): String {
        repeat(60) {
            vpnLine()?.let { if (it.contains("Uids:") || it.contains("CONNECTED")) return it }
            Thread.sleep(250)
        }
        throw AssertionError("Android never reported a VPN network. Tunnel up: ${driver.isUp()}")
    }

    protected fun waitForNoVpn() {
        repeat(60) {
            if (vpnLine() == null) return
            Thread.sleep(250)
        }
        throw AssertionError("A VPN network stayed behind")
    }

    /** Android's shell (com.android.shell, UID 2000) sends one ping. Returns ping's output: "1 received" means the packet went out and came back. */
    protected fun shellPing(target: String): String = shell("ping -c 1 -W 3 $target")

    protected fun reached(pingOutput: String) = pingOutput.contains(" 1 received")

    /** The name resolved: ping prints "PING <name> (<address>)" only after DNS answered. */
    protected fun resolved(pingOutput: String) = Regex("PING \\S+ \\((\\d+\\.\\d+\\.\\d+\\.\\d+)\\)").containsMatchIn(pingOutput)

    /** A TCP connection from this test's own process, which runs as Kiwi VPN's UID. */
    protected fun kiwiCanConnect(): Boolean = try {
        Socket().use { it.connect(InetSocketAddress("1.1.1.1", 443), 3000); true }
    } catch (_: Exception) {
        false
    }

    /** A launchable app with its own UID, other than Kiwi VPN, to stand for "an app the person didn't pick". */
    protected fun anotherApp(): String = AppCatalog.load(context).map { it.packageName }
        .first { it != SHELL && uidOf(it) >= 10000 && uidOf(it) != uidOf(own) }

    companion object {
        const val TAG = "KiwiEvidence"
        const val SHELL = "com.android.shell"
        const val TUNNEL_DNS = "10.66.0.1"
        const val PING_IP = "1.1.1.1"
        const val PING_NAME = "dns.google"
        val NL: String = System.lineSeparator()
    }
}

/**
 * Real Android, the real WireGuard library and a real VPN interface: per-app allowlisting proven by traffic, not only by Android's bookkeeping. Android's shell stands in for a selected or unselected app,
 * because it's the one other UID a test can make send packets. Emulator results only: no real server, account or phone maker's changes are involved.
 */
@RunWith(AndroidJUnit4::class)
class SelectiveRoutingTest : RoutingDeviceTestBase() {
    @Before fun ready() {
        shell("appops set $own ACTIVATE_VPN allow")
        assertFalse("VPN consent should now be granted", driver.needsPermission())
        assumeTrue("These tests need the emulator to have internet first", reached(shellPing(PING_IP)))
        assertEquals("No always-on VPN may be set for these tests", "0", shell("settings get secure always_on_vpn_lockdown").trim().ifEmpty { "0" }.replace("null", "0"))
    }

    @After fun stop() {
        runCatching { driver.down() }
        Thread.sleep(1500)
    }

    @Test fun baseline_without_vpn_shell_and_kiwi_both_reach_the_internet() {
        val ping = shellPing(PING_IP)
        val dns = shellPing(PING_NAME)
        evidence("baseline_no_vpn", "vpnLine=${vpnLine()}\nping $PING_IP:\n$ping\nping $PING_NAME:\n$dns\nkiwiCanConnect=${kiwiCanConnect()}\nprivate_dns_mode=${shell("settings get global private_dns_mode").trim()}")
        assertTrue(reached(ping))
        assertTrue(resolved(dns))
        assertTrue(kiwiCanConnect())
    }

    @Test fun a_selected_app_sends_its_traffic_and_dns_into_the_tunnel() {
        val other = anotherApp()
        driver.up(config(RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf(SHELL))))
        val line = waitForVpn()
        val ranges = uidRanges(line)
        Thread.sleep(1000)
        val ping = shellPing(PING_IP)
        val dns = shellPing(PING_NAME)
        val kiwi = kiwiCanConnect()
        evidence(
            "only_selected_shell_selected",
            "selected=[$SHELL uid ${uidOf(SHELL)}] other=[$other uid ${uidOf(other)}] kiwi uid=${uidOf(own)}\nvpnLine=$line\nuidRanges=$ranges\ndns=${dnsOf(line)}\n" +
                "shell ping $PING_IP:\n$ping\nshell ping $PING_NAME:\n$dns\nkiwiCanConnect=$kiwi",
        )
        assertTrue("The selected app is on the VPN network: $ranges", covers(ranges, SHELL))
        assertTrue("Kiwi VPN itself stays on it: $ranges", covers(ranges, own))
        assertFalse("An unselected app isn't: $ranges", covers(ranges, other))
        assertTrue("The VPN network's DNS is the tunnel's: ${dnsOf(line)}", dnsOf(line).contains(TUNNEL_DNS))
        assertFalse("The selected app's packets went into the dead tunnel, not out the normal way:\n$ping", reached(ping))
        assertFalse("The selected app's DNS went into the tunnel too, so the name didn't resolve:\n$dns", resolved(dns))
        assertFalse("Kiwi's own traffic is in the tunnel", kiwi)
    }

    @Test fun an_unselected_app_keeps_its_normal_connection_and_dns() {
        val other = anotherApp()
        driver.up(config(RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf(other))))
        val line = waitForVpn()
        val ranges = uidRanges(line)
        Thread.sleep(1000)
        val ping = shellPing(PING_IP)
        val dns = shellPing(PING_NAME)
        val kiwi = kiwiCanConnect()
        evidence(
            "only_selected_shell_not_selected",
            "selected=[$other uid ${uidOf(other)}] unselected=[$SHELL uid ${uidOf(SHELL)}]\nvpnLine=$line\nuidRanges=$ranges\nshell ping $PING_IP:\n$ping\nshell ping $PING_NAME:\n$dns\nkiwiCanConnect=$kiwi",
        )
        assertFalse("Shell isn't on the VPN network: $ranges", covers(ranges, SHELL))
        assertTrue(covers(ranges, other))
        assertTrue("The unselected app still reaches the internet the normal way:\n$ping", reached(ping))
        assertTrue("and resolves names with the normal network's DNS:\n$dns", resolved(dns))
        assertFalse("Kiwi itself is still in the tunnel", kiwi)
    }

    @Test fun all_apps_puts_shell_in_the_tunnel_and_all_except_lets_it_out() {
        driver.up(config(RoutingPrefs()))
        val allLine = waitForVpn()
        Thread.sleep(1000)
        val allPing = shellPing(PING_IP)
        driver.up(config(RoutingPrefs(RoutingMode.ALL_EXCEPT, excluded = setOf(SHELL))))
        Thread.sleep(1500)
        val exceptLine = waitForVpn()
        val exceptPing = shellPing(PING_IP)
        evidence("all_apps_then_all_except_shell", "ALL vpnLine=$allLine\nALL shell ping:\n$allPing\nEXCEPT vpnLine=$exceptLine\nEXCEPT shell ping:\n$exceptPing")
        assertTrue(covers(uidRanges(allLine), SHELL))
        assertFalse("All Apps: shell's packets go into the tunnel", reached(allPing))
        assertFalse(covers(uidRanges(exceptLine), SHELL))
        assertTrue("All Except shell: shell uses the normal connection", reached(exceptPing))
    }

    @Test fun changing_the_selection_rebuilds_the_interface_and_traffic_follows() {
        val other = anotherApp()
        driver.up(config(RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf(SHELL))))
        val first = waitForVpn()
        Thread.sleep(1000)
        val before = shellPing(PING_IP)
        driver.up(config(RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf(other))))
        Thread.sleep(1500)
        val second = waitForVpn()
        val after = shellPing(PING_IP)
        driver.up(config(RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf(SHELL, other))))
        Thread.sleep(1500)
        val third = waitForVpn()
        val again = shellPing(PING_IP)
        evidence("selection_change_rebuild", "1 [shell] $first\nping:\n$before\n2 [$other] $second\nping:\n$after\n3 [shell,$other] $third\nping:\n$again")
        assertFalse(reached(before))
        assertTrue("Removed from the list: back on the normal connection", reached(after))
        assertFalse(covers(uidRanges(second), SHELL))
        assertTrue(covers(uidRanges(third), SHELL) && covers(uidRanges(third), other))
        assertFalse("Added again: in the tunnel", reached(again))
    }

    @Test fun reconnecting_ten_times_leaves_the_same_routing_each_time_and_nothing_after() {
        val prefs = RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf(SHELL))
        val log = StringBuilder()
        repeat(10) { round ->
            driver.up(config(prefs))
            val line = waitForVpn()
            assertTrue("Round $round", covers(uidRanges(line), SHELL))
            driver.down()
            waitForNoVpn()
            log.append("round $round ok: ${uidRanges(line)}\n")
        }
        val ping = shellPing(PING_IP)
        log.append("after last down, shell ping:\n$ping")
        evidence("reconnect_10_rounds", log.toString())
        assertTrue("With the VPN gone, the shell's normal connection is back", reached(ping))
    }

    @Test fun the_vpn_network_reports_ipv4_and_ipv6_default_routes_into_the_tunnel() {
        driver.up(config(RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf(SHELL))))
        val line = waitForVpn()
        val ping6 = shell("ping6 -c 1 -W 3 2001:4860:4860::8888")
        evidence("routes_and_ipv6", "vpnLine=$line\nshell ping6 (shell selected):\n$ping6\nunderlying network line=${shell("dumpsys connectivity").lineSequence().firstOrNull { it.trimStart().startsWith("NetworkAgentInfo") && Regex("Transports: (WIFI|ETHERNET|CELLULAR)").containsMatchIn(it) }}")
        assertTrue("IPv4 default route through the tunnel: $line", line.contains("0.0.0.0/0"))
        assertTrue("IPv6 default route through the tunnel, so IPv6 can't go around it: $line", line.contains("::/0"))
        assertFalse("No IPv6 escapes for a selected app", ping6.contains(" 1 received"))
    }

    /**
     * DEVICE-TEST-PLAN test 47 on this image: Android accepts the systemExempted foreground service type (1024) for a VPN app. The service stops itself straight away here, because the app's own
     * connection isn't Connected (the test drives the tunnel directly), so the proof is Android's event log: the start record carries the type, and nothing refused it.
     */
    @Test fun the_foreground_service_starts_as_system_exempted() {
        driver.up(config(RoutingPrefs()))
        waitForVpn()
        shell("logcat -b events -c")
        val before = live.pricechat.kiwivpn.log.Diag.recent().size
        ContextCompat.startForegroundService(context, Intent(context, TunnelService::class.java))
        Thread.sleep(6000) // past the time Android allows a service to enter the foreground
        val events = shell("logcat -b events -d").lineSequence().filter { it.contains("TunnelService") }.toList()
        val main = shell("logcat -d -t 400").lineSequence().filter { it.contains("TunnelService") || it.contains("ForegroundService", ignoreCase = true) }.toList()
        val diag = live.pricechat.kiwivpn.log.Diag.recent().drop(before)
        evidence("foreground_service", listOf("events:", events.joinToString(NL), "main log:", main.joinToString(NL), "app events: $diag").joinToString(NL))
        val start = events.firstOrNull { it.contains("am_foreground_service_start") }
        assertTrue("Android recorded the foreground start: $events", start != null)
        assertTrue("with type 1024, systemExempted: $start", start!!.trimEnd().trimEnd(']').endsWith(",1024"))
        assertFalse("The app didn't see a refusal: $diag", diag.any { it.contains("foreground_refused") || it.contains("foreground_start_refused") })
        assertFalse("Android didn't refuse it: $main", main.any { it.contains("ForegroundServiceStartNotAllowed") || it.contains("ForegroundServiceTypeException") || it.contains("InvalidForegroundServiceType") })
    }

    @Test fun the_picker_lists_only_launchable_apps() {
        val listed = AppCatalog.load(context).map { it.packageName }
        evidence("app_catalog", "listed ${listed.size} launchable apps: $listed\nshell listed=${SHELL in listed}")
        assertFalse("Kiwi doesn't list itself", own in listed)
        assertFalse("A component with no launcher icon (the shell) isn't listed, so it can't be picked in the app", SHELL in listed)
        assertTrue(listed.isNotEmpty())
    }
}

/**
 * Always-on VPN with "Block connections without VPN", set on the emulator only by tools (secure settings and a reboot): skipped unless they are set for this app. See test-evidence/2026-11-20/README.md.
 */
@RunWith(AndroidJUnit4::class)
class LockdownRoutingTest : RoutingDeviceTestBase() {
    @Before fun onlyWithLockdown() {
        assumeTrue("Always-on is set for this app", shell("settings get secure always_on_vpn_app").trim() == own)
        assumeTrue("Lockdown is on", shell("settings get secure always_on_vpn_lockdown").trim() == "1")
        shell("appops set $own ACTIVATE_VPN allow")
    }

    @After fun stop() {
        runCatching { driver.down() }
        Thread.sleep(1500)
    }

    @Test fun lockdown_with_only_selected_cuts_off_an_unselected_app() {
        val other = anotherApp()
        val noTunnel = shellPing(PING_IP)
        driver.up(config(RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf(other))))
        val line = waitForVpn()
        Thread.sleep(1500)
        val unselected = shellPing(PING_IP)
        val always = driver.alwaysOn()
        evidence(
            "lockdown_only_selected",
            "always_on_vpn_app=${shell("settings get secure always_on_vpn_app").trim()} lockdown=${shell("settings get secure always_on_vpn_lockdown").trim()} library alwaysOn/lockdown=$always\n" +
                "shell ping with no tunnel up:\n$noTunnel\nvpnLine=$line\nuidRanges=${uidRanges(line)}\nshell (unselected) ping with Only Selected [$other]:\n$unselected\n" +
                "dumpsys lockdown lines:\n" + shell("dumpsys connectivity").lineSequence().filter { it.contains("ockdown", ignoreCase = true) || it.contains("lways", ignoreCase = true) }.take(20).joinToString("\n"),
        )
        assertFalse("Lockdown and no tunnel: nothing gets out", reached(noTunnel))
        assertFalse("Shell isn't in the VPN's list", covers(uidRanges(line), SHELL))
        assertFalse("Lockdown cuts off apps outside the VPN, as Google warns:\n$unselected", reached(unselected))
    }

    @Test fun lockdown_with_all_except_cuts_off_the_excluded_app() {
        driver.up(config(RoutingPrefs(RoutingMode.ALL_EXCEPT, excluded = setOf(SHELL))))
        val line = waitForVpn()
        Thread.sleep(1500)
        val excluded = shellPing(PING_IP)
        evidence("lockdown_all_except", "vpnLine=$line\nshell (excluded) ping:\n$excluded")
        assertFalse(covers(uidRanges(line), SHELL))
        assertFalse("Excluded under lockdown: no connection at all", reached(excluded))
    }
}
