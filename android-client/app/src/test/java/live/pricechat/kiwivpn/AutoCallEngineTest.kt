package live.pricechat.kiwivpn

import live.pricechat.kiwivpn.autocall.AutoCallEffect
import live.pricechat.kiwivpn.autocall.AutoCallEngine
import live.pricechat.kiwivpn.autocall.AutoCallStatus
import live.pricechat.kiwivpn.autocall.CallApp
import live.pricechat.kiwivpn.autocall.CallClassifier
import live.pricechat.kiwivpn.autocall.CallPhase
import live.pricechat.kiwivpn.autocall.CallSignal
import live.pricechat.kiwivpn.autocall.NotificationFacts
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AutoCallEngineTest {
    private val ringing = CallSignal(CallApp.WHATSAPP, CallPhase.RINGING)
    private val active = CallSignal(CallApp.WHATSAPP, CallPhase.ACTIVE)
    private val telegram = CallSignal(CallApp.TELEGRAM, CallPhase.ACTIVE)
    private fun List<AutoCallEffect>.connects() = count { it == AutoCallEffect.Connect }
    private fun List<AutoCallEffect>.disconnects() = count { it == AutoCallEffect.Disconnect }

    @Test fun a_call_connects_and_shows_each_status_in_order() {
        val e = AutoCallEngine()
        val out = e.onPosted(1, ringing, tunnelOn = false)
        assertEquals(1, out.connects())
        assertEquals(listOf(AutoCallStatus.CALL_DETECTED, AutoCallStatus.CONNECTING), out.filterIsInstance<AutoCallEffect.Status>().map { it.status })
        assertTrue(e.owned)
        e.onConnected()
        assertEquals(AutoCallStatus.CONNECTED, e.status)
    }

    @Test fun duplicate_notifications_start_nothing_more() {
        val e = AutoCallEngine()
        e.onPosted(1, ringing, false)
        repeat(5) { assertEquals(0, e.onPosted(1, ringing, tunnelOn = true).connects()) }
        assertEquals(0, e.onPosted(1, active, tunnelOn = false).connects())
    }

    @Test fun a_confirmed_end_waits_15_seconds_then_disconnects() {
        val e = AutoCallEngine()
        e.onPosted(1, active, false)
        e.onConnected()
        e.onRemoved(1, confirmedEnd = true, now = 1_000)
        assertEquals(16_000L, e.deadline)
        assertEquals(0, e.onTick(15_999, false).disconnects())
        assertEquals(1, e.onTick(16_000, false).disconnects())
        assertFalse(e.owned)
    }

    @Test fun a_vpn_the_person_started_is_never_disconnected() {
        val e = AutoCallEngine()
        val out = e.onPosted(1, active, tunnelOn = true)
        assertEquals(0, out.connects())
        assertEquals(AutoCallStatus.ALREADY_ON, e.status)
        val end = e.onRemoved(1, true, 1_000)
        assertEquals(0, end.disconnects())
        assertNull(e.deadline)
        assertFalse(e.owned)
    }

    @Test fun never_disconnects_while_another_call_is_active() {
        val e = AutoCallEngine()
        e.onPosted(1, active, false)
        e.onPosted(2, telegram, tunnelOn = true)
        e.onRemoved(1, true, 1_000)
        assertNull("Another call is still going", e.deadline)
        assertEquals(0, e.onTick(99_000, false).disconnects())
        e.onRemoved(2, true, 100_000)
        assertEquals(115_000L, e.deadline)
    }

    @Test fun a_new_call_during_the_wait_cancels_the_disconnect() {
        val e = AutoCallEngine()
        e.onPosted(1, active, false)
        e.onRemoved(1, true, 1_000)
        val out = e.onPosted(2, telegram, tunnelOn = true)
        assertEquals(0, out.connects())
        assertNull(e.deadline)
        assertEquals(0, e.onTick(99_000, false).disconnects())
        assertTrue(e.owned)
    }

    @Test fun ringing_turning_into_active_under_a_new_key_keeps_the_connection() {
        val e = AutoCallEngine()
        e.onPosted(1, ringing, false)
        e.onRemoved(1, true, 1_000)
        e.onPosted(2, active, tunnelOn = true)
        e.onRemoved(2, true, 60_000)
        assertEquals(1, e.onTick(75_000, false).disconnects())
    }

    @Test fun missed_and_rejected_calls_end_the_same_confirmed_way() {
        val e = AutoCallEngine()
        e.onPosted(1, ringing, false)
        e.onRemoved(1, confirmedEnd = true, now = 5_000) // the app removed its ringing notification
        assertEquals(AutoCallStatus.CALL_ENDED, e.status)
        assertEquals(1, e.onTick(20_000, false).disconnects())
    }

    @Test fun a_swiped_notification_is_uncertain_and_the_vpn_stays_on() {
        val e = AutoCallEngine()
        e.onPosted(1, active, false)
        val out = e.onRemoved(1, confirmedEnd = false, now = 1_000)
        assertEquals(0, out.disconnects())
        assertEquals(AutoCallStatus.UNCERTAIN, e.status)
        assertNull(e.deadline)
        assertEquals(0, e.onTick(99_000, false).disconnects())
        assertTrue(e.owned)
    }

    @Test fun losing_the_listener_during_a_call_is_uncertain() {
        val e = AutoCallEngine()
        e.onPosted(1, active, false)
        assertEquals(0, e.onListenerLost().disconnects())
        assertEquals(AutoCallStatus.UNCERTAIN, e.status)
    }

    @Test fun losing_the_listener_during_the_wait_cancels_the_disconnect() {
        val e = AutoCallEngine()
        e.onPosted(1, active, false)
        e.onRemoved(1, true, 1_000)
        e.onListenerLost()
        assertNull(e.deadline)
        assertEquals(AutoCallStatus.UNCERTAIN, e.status)
    }

    @Test fun a_restart_with_a_saved_vpn_and_no_call_keeps_the_vpn_and_says_so() {
        val e = AutoCallEngine(owned = true)
        val out = e.onListenerConnected(emptyList(), tunnelOn = true)
        assertEquals(0, out.disconnects())
        assertEquals(AutoCallStatus.UNCERTAIN, e.status)
        assertTrue(e.owned)
    }

    @Test fun a_restart_mid_call_with_the_tunnel_gone_connects_once() {
        val e = AutoCallEngine(owned = true)
        val out = e.onListenerConnected(listOf(1 to active), tunnelOn = false)
        assertEquals(1, out.connects())
        assertEquals(0, e.onPosted(1, active, tunnelOn = true).connects())
    }

    @Test fun a_restart_with_no_vpn_and_no_call_clears_ownership() {
        val e = AutoCallEngine(owned = true)
        e.onListenerConnected(emptyList(), tunnelOn = false)
        assertFalse(e.owned)
    }

    @Test fun android_showing_a_call_in_audio_mode_holds_the_disconnect_back() {
        val e = AutoCallEngine()
        e.onPosted(1, active, false)
        e.onRemoved(1, true, 1_000)
        val out = e.onTick(16_000, audioBusy = true)
        assertEquals(0, out.disconnects())
        assertEquals(AutoCallStatus.AUDIO_BUSY, e.status)
        assertEquals(31_000L, e.deadline)
        assertEquals(1, e.onTick(31_000, audioBusy = false).disconnects())
    }

    @Test fun the_persons_disconnect_stands_for_the_rest_of_the_call() {
        val e = AutoCallEngine()
        e.onPosted(1, active, false)
        e.onUserDisconnected()
        assertFalse(e.owned)
        assertEquals(0, e.onPosted(1, active, tunnelOn = false).connects())
        assertEquals("Not even a changed key", 0, e.onPosted(2, active, tunnelOn = false).connects())
        e.onRemoved(1, true, 1_000)
        e.onRemoved(2, true, 1_000)
        assertEquals("A later call works again", 1, e.onPosted(3, active, tunnelOn = false).connects())
    }

    @Test fun the_person_connecting_during_a_call_makes_the_vpn_theirs() {
        val e = AutoCallEngine()
        e.onPosted(1, active, false)
        e.onUserConnected()
        assertEquals(0, e.onRemoved(1, true, 1_000).disconnects())
        assertNull(e.deadline)
    }

    @Test fun a_failed_connection_isnt_retried_during_that_call() {
        val e = AutoCallEngine()
        e.onPosted(1, active, false)
        e.onConnectFailed()
        assertEquals(AutoCallStatus.CONNECT_FAILED, e.status)
        assertFalse(e.owned)
        assertEquals(0, e.onPosted(2, active, tunnelOn = false).connects())
    }

    @Test fun a_vpn_it_no_longer_owns_isnt_disconnected_when_the_wait_ends() {
        val e = AutoCallEngine()
        e.onPosted(1, active, false)
        e.onRemoved(1, confirmedEnd = true, now = 1_000)
        assertEquals(16_000L, e.deadline)
        e.onConnectFailed()
        assertFalse(e.owned)
        assertEquals(0, e.onTick(16_000, false).disconnects())
    }

    @Test fun turning_it_off_leaves_the_vpn_alone() {
        val e = AutoCallEngine()
        e.onPosted(1, active, false)
        e.onDisabled()
        assertFalse(e.owned)
        assertEquals(0, e.onRemoved(1, true, 1_000).size)
    }

    // --- reading Android's signals ---

    private fun facts(pkg: String = "com.whatsapp", category: String? = null, ongoing: Boolean = false, type: Int = 0, template: String? = null, channel: String? = null) = NotificationFacts(pkg, category, ongoing, type, template, channel)

    @Test fun other_apps_are_never_calls_whatever_they_claim() {
        assertNull(CallClassifier.classify(facts(pkg = "com.example.chat", category = "call", ongoing = true, type = 2)))
    }

    @Test fun callstyle_type_gives_the_phase() {
        assertEquals(CallPhase.RINGING, CallClassifier.classify(facts(type = 1))!!.phase)
        assertEquals(CallPhase.ACTIVE, CallClassifier.classify(facts(type = 2))!!.phase)
        assertEquals(CallPhase.RINGING, CallClassifier.classify(facts(type = 3))!!.phase)
    }

    @Test fun category_call_without_a_type_never_claims_active() {
        assertEquals(CallPhase.RINGING, CallClassifier.classify(facts(category = "call"))!!.phase)
        assertEquals(CallPhase.UNKNOWN, CallClassifier.classify(facts(category = "call", ongoing = true))!!.phase)
    }

    @Test fun a_missed_call_or_a_message_is_not_a_call() {
        assertNull(CallClassifier.classify(facts(category = "missed_call", ongoing = false)))
        assertNull(CallClassifier.classify(facts(category = "msg")))
        assertNull(CallClassifier.classify(facts(channel = "voip_notification", ongoing = false)))
    }

    @Test fun an_ongoing_call_channel_is_only_a_weak_guess() {
        assertEquals(CallPhase.UNKNOWN, CallClassifier.classify(facts(pkg = "org.telegram.messenger", channel = "voip_ongoing", ongoing = true))!!.phase)
    }

    @Test fun only_the_app_cancelling_counts_as_a_confirmed_end() {
        assertTrue(CallClassifier.isConfirmedEnd(8))
        assertTrue(CallClassifier.isConfirmedEnd(9))
        for (reason in listOf(1, 2, 3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20)) assertFalse("reason $reason", CallClassifier.isConfirmedEnd(reason))
    }
}
