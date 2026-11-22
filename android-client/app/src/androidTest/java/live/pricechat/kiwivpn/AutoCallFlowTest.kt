package live.pricechat.kiwivpn

import android.content.Context
import android.os.ParcelFileDescriptor
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.wireguard.crypto.KeyPair
import live.pricechat.kiwivpn.autocall.AutoCallStatus
import live.pricechat.kiwivpn.autocall.CallListenerService
import live.pricechat.kiwivpn.model.Peer
import live.pricechat.kiwivpn.model.ServerRef
import live.pricechat.kiwivpn.security.SavedDevice
import live.pricechat.kiwivpn.vpn.ConnectionState
import live.pricechat.kiwivpn.vpn.PeerRules
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

object AutoCallTestData {
    val server = ServerRef(1, "Test", "FI")
    val peer: Peer by lazy { Peer(KeyPair().publicKey.toBase64(), "192.0.2.1", 51821, listOf("10.66.0.9/32"), listOf("10.66.0.1"), listOf("0.0.0.0/0", "::/0"), 25) }
    val privateKey: String by lazy { KeyPair().privateKey.toBase64() }
    fun config(): String = PeerRules.render(privateKey, peer)
}

/** Real notifications from the stand-in apps in testfixture, through Android to the real listener, up to the real VPN interface. See TestKiwiApp for what isn't real. */
@RunWith(AndroidJUnit4::class)
class AutoCallFlowTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context: Context = instrumentation.targetContext
    private val app get() = context.applicationContext as KiwiApp
    private val own = context.packageName
    private val whatsapp = "com.whatsapp"
    private val telegram = "org.telegram.messenger"
    private val other = "live.pricechat.fakecall.other"

    private fun shell(command: String): String =
        ParcelFileDescriptor.AutoCloseInputStream(instrumentation.uiAutomation.executeShellCommand(command)).bufferedReader().use { it.readText() }

    private fun fixture(pkg: String, kind: String, id: Int = 1) {
        shell("am broadcast -f 0x10000000 -n $pkg/live.pricechat.fakecall.CallReceiver --es kind $kind --ei id $id")
    }

    private fun vpnInterfaceUp() = shell("dumpsys connectivity").lineSequence().any { it.trimStart().startsWith("NetworkAgentInfo") && Regex("Transports: [A-Z_|]*VPN").containsMatchIn(it) }

    private fun waitFor(what: String, limitMs: Long = 20_000, check: () -> Boolean) {
        val until = System.currentTimeMillis() + limitMs
        while (System.currentTimeMillis() < until) {
            if (check()) return
            Thread.sleep(150)
        }
        throw AssertionError("$what. Status: ${app.autoCall.status.value}, state: ${app.connection.state.value::class.simpleName}, listening: ${CallListenerService.current != null}, traces: ${app.autoCall.traces.value.takeLast(4)}")
    }

    private fun connected() = app.connection.state.value is ConnectionState.Connected
    private fun disconnected() = app.connection.state.value is ConnectionState.Disconnected
    private fun status() = app.autoCall.status.value
    private fun settings(whatsApp: Boolean = true, telegramOn: Boolean = true, enabled: Boolean = true) = app.vault.update {
        it.copy(session = "kd.test", lastServer = AutoCallTestData.server, wanted = null, autoCallOwned = false,
            devices = mapOf(1L to SavedDevice(1, AutoCallTestData.privateKey, AutoCallTestData.peer)),
            settings = it.settings.copy(autoCallEnabled = enabled, autoCallConsented = true, autoCallWhatsApp = whatsApp, autoCallTelegram = telegramOn))
    }

    @Before fun setUp() {
        for (p in listOf(whatsapp, telegram, other)) {
            shell("pm grant $p android.permission.POST_NOTIFICATIONS")
            shell("appops set $p USE_FULL_SCREEN_INTENT allow")
            fixture(p, "endall")
        }
        shell("appops set $own ACTIVATE_VPN allow")
        shell("cmd notification allow_listener $own/live.pricechat.kiwivpn.autocall.CallListenerService")
        settings()
        CallListenerService.setActive(context, true)
        waitFor("The listener was never connected") { CallListenerService.current != null }
        app.userDisconnect()
        waitFor("Started with a tunnel up") { disconnected() && !vpnInterfaceUp() }
        Thread.sleep(300)
    }

    @After fun tearDown() {
        for (p in listOf(whatsapp, telegram, other)) fixture(p, "endall")
        shell("appops set $own ACTIVATE_VPN allow")
        Thread.sleep(400)
        app.userDisconnect()
        app.autoCall.disabled()
        waitFor("Left a tunnel up") { disconnected() }
    }

    @Test fun a_whatsapp_call_connects_then_disconnects_15_seconds_after_it_ends() {
        fixture(whatsapp, "ring")
        waitFor("The call didn't connect the VPN") { connected() }
        assertTrue("Android reports a VPN network", vpnInterfaceUp())
        waitFor("Status") { status() == AutoCallStatus.CONNECTED }
        assertTrue("Saved as started by Auto-Call", app.vault.read().autoCallOwned)
        fixture(whatsapp, "end")
        waitFor("The end wasn't noticed") { status() == AutoCallStatus.CALL_ENDED }
        Thread.sleep(10_000)
        assertTrue("Still on inside the 15 seconds", connected() && vpnInterfaceUp())
        waitFor("It didn't disconnect after the wait") { disconnected() && !vpnInterfaceUp() }
        assertEquals(AutoCallStatus.DISCONNECTED, status())
        assertFalse(app.vault.read().autoCallOwned)
    }

    @Test fun a_telegram_call_with_only_a_call_category_works_too() {
        fixture(telegram, "plain")
        waitFor("It didn't connect") { connected() }
        fixture(telegram, "end")
        waitFor("It didn't disconnect") { disconnected() && !vpnInterfaceUp() }
    }

    @Test fun an_ongoing_notification_on_a_call_channel_works_as_the_weak_guess() {
        fixture(telegram, "voip")
        waitFor("It didn't connect") { connected() }
        fixture(telegram, "end")
        waitFor("It didn't disconnect") { disconnected() }
    }

    @Test fun a_vpn_the_person_started_is_never_disconnected_by_a_call_ending() {
        app.userConnect(AutoCallTestData.server)
        waitFor("The person's connection didn't come up") { connected() }
        fixture(whatsapp, "ongoing")
        waitFor("The call wasn't seen") { status() == AutoCallStatus.ALREADY_ON }
        assertFalse(app.vault.read().autoCallOwned)
        fixture(whatsapp, "end")
        Thread.sleep(20_000)
        assertTrue("A connection made by hand must survive the call", connected() && vpnInterfaceUp())
    }

    @Test fun answering_by_replacing_ringing_with_an_ongoing_call_keeps_the_connection() {
        fixture(whatsapp, "ring", 1)
        waitFor("It didn't connect") { connected() }
        fixture(whatsapp, "ongoing", 2)
        Thread.sleep(300)
        fixture(whatsapp, "end", 1)
        Thread.sleep(18_000)
        assertTrue("The call is still going", connected())
        fixture(whatsapp, "end", 2)
        waitFor("It didn't disconnect after the real end") { disconnected() }
    }

    @Test fun it_stays_on_until_the_last_of_two_calls_ends() {
        fixture(whatsapp, "ongoing")
        waitFor("It didn't connect") { connected() }
        fixture(telegram, "ongoing")
        Thread.sleep(500)
        fixture(whatsapp, "end")
        Thread.sleep(18_000)
        assertTrue("The Telegram call is still going", connected())
        fixture(telegram, "end")
        waitFor("It didn't disconnect") { disconnected() }
    }

    @Test fun a_notification_removed_by_someone_else_stays_on_and_says_so() {
        fixture(whatsapp, "clearable")
        waitFor("It didn't connect") { connected() }
        val listener = CallListenerService.current!!
        listener.cancelNotification(listener.activeNotifications.first { it.packageName == whatsapp }.key)
        waitFor("It wasn't called uncertain") { status() == AutoCallStatus.UNCERTAIN }
        Thread.sleep(18_000)
        assertTrue("It can't prove the call is over", connected())
    }

    @Test fun the_persons_disconnect_during_a_call_isnt_undone() {
        fixture(whatsapp, "ongoing")
        waitFor("It didn't connect") { connected() }
        app.userDisconnect()
        waitFor("Disconnect") { disconnected() }
        fixture(whatsapp, "ongoing") // the same call updating itself
        Thread.sleep(3_000)
        assertTrue("Not reconnected during this call", disconnected())
    }

    @Test fun nothing_else_starts_it() {
        fixture(whatsapp, "note", 1)
        fixture(whatsapp, "missed", 2)
        fixture(other, "ring", 1)
        fixture(other, "ongoing", 2)
        waitFor("The ordinary notification didn't arrive") { app.autoCall.traces.value.any { it.classified == "not a call" } }
        Thread.sleep(2_500)
        assertTrue("Other apps, messages and missed calls connect nothing", disconnected() && !vpnInterfaceUp())
        assertEquals(AutoCallStatus.IDLE, status())
    }

    @Test fun chat_conversations_arent_even_delivered_to_the_listener() {
        fixture(whatsapp, "note", 1)
        waitFor("The control notification didn't arrive") { app.autoCall.traces.value.any { it.classified == "not a call" && it.category == "msg" } }
        val before = app.autoCall.traces.value.size
        fixture(whatsapp, "conversation", 2)
        waitFor("Android didn't post the conversation") { shell("dumpsys notification --noredact").contains("shortcut=chat") || shell("dumpsys notification --noredact").contains("isConversation=true") }
        Thread.sleep(1_500)
        assertEquals("A conversation woke the listener", before, app.autoCall.traces.value.size)
    }

    @Test fun switching_an_app_off_ignores_its_calls() {
        settings(whatsApp = false)
        fixture(whatsapp, "ring")
        Thread.sleep(2_500)
        assertTrue(disconnected())
        fixture(telegram, "ongoing")
        waitFor("The other app should still work") { connected() }
    }

    @Test fun turning_it_off_unbinds_the_listener_and_on_again_rebinds_it() {
        settings(enabled = false)
        CallListenerService.setActive(context, false)
        waitFor("Still bound while off", 10_000) { CallListenerService.current == null }
        fixture(whatsapp, "ring")
        Thread.sleep(2_500)
        assertTrue("Off means off", disconnected())
        assertTrue("Access stays granted", shell("cmd notification get_allowed_listeners 2>&1; settings get secure enabled_notification_listeners").contains(own))
        fixture(whatsapp, "end")
        settings(enabled = true)
        CallListenerService.setActive(context, true)
        waitFor("Didn't rebind", 10_000) { CallListenerService.current != null }
        Thread.sleep(500)
        fixture(whatsapp, "ring", 3)
        waitFor("Works again") { connected() }
    }

    @Test fun without_vpn_permission_it_asks_the_person_and_starts_nothing() {
        shell("appops set $own ACTIVATE_VPN deny")
        waitFor("The permission was still reported as granted", 5_000) { app.connection.needsPermission() }
        fixture(whatsapp, "ring")
        waitFor("It didn't say what's needed") { status() == AutoCallStatus.NEEDS_APP }
        assertTrue(disconnected() && !vpnInterfaceUp())
        assertFalse(app.vault.read().autoCallOwned)
    }

    @Test fun signed_out_it_starts_nothing() {
        app.vault.update { it.copy(session = null) }
        fixture(whatsapp, "ring")
        waitFor("It didn't say what's needed") { status() == AutoCallStatus.NEEDS_APP }
        assertTrue(disconnected())
    }

    @Test fun no_notification_content_is_kept() {
        fixture(whatsapp, "ring")
        waitFor("It didn't connect") { connected() }
        val saved = app.autoCall.traces.value.joinToString()
        assertFalse("Test person" in saved)
        assertFalse("Fixture" in saved)
    }
}
