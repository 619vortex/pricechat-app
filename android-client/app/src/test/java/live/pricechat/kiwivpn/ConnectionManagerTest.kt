package live.pricechat.kiwivpn

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.cancelChildren
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.currentTime
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import live.pricechat.kiwivpn.model.ServerRef
import live.pricechat.kiwivpn.vpn.ConnectionManager
import live.pricechat.kiwivpn.vpn.ConnectionState
import live.pricechat.kiwivpn.vpn.Profile
import live.pricechat.kiwivpn.vpn.ProfileError
import live.pricechat.kiwivpn.vpn.ProfileSource
import live.pricechat.kiwivpn.vpn.Timings
import live.pricechat.kiwivpn.vpn.TunnelDriver
import live.pricechat.kiwivpn.vpn.TunnelFailure
import live.pricechat.kiwivpn.vpn.TunnelStats
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private class FakeDriver(private val clock: () -> Long) : TunnelDriver {
    var running = false
    var handshakeAt = 0L
    var answers = true
    var ups = 0
    var downs = 0
    var maxRunning = 0
    var upFails: String? = null
    var hangOnDown = false
    private var onDown: () -> Unit = {}
    override fun needsPermission() = false
    override fun up(config: String) {
        upFails?.let { throw TunnelFailure(it) }
        ups++
        running = true
        maxRunning = maxOf(maxRunning, 1)
        handshakeAt = 0
    }
    override fun down() {
        downs++
        running = false
    }
    override fun isUp() = running
    override fun stats(): TunnelStats? {
        if (!running) return null
        if (answers) handshakeAt = clock()
        return TunnelStats(handshakeAt, 100, 200)
    }
    override fun setOnDown(listener: () -> Unit) { onDown = listener }
    fun killedFromOutside() {
        running = false
        onDown()
    }
}

private class FakeSource(var fail: ProfileError? = null) : ProfileSource {
    val asked = mutableListOf<Pair<Long, Long?>>()
    var limitOnce = false
    override suspend fun profile(serverId: Long, keepServer: Long?): Profile {
        asked += serverId to keepServer
        if (limitOnce) { limitOnce = false; throw ProfileError.DeviceLimit(tooMany = false) }
        fail?.let { throw it }
        return Profile("config", "5.75.10.20", listOf("10.66.0.1"), fromCache = false)
    }
}

@OptIn(ExperimentalCoroutinesApi::class)
class ConnectionManagerTest {
    private class Rig(val test: TestScope) {
        val dispatcher = StandardTestDispatcher(test.testScheduler)
        val driver = FakeDriver { test.currentTime + 1 }
        val source = FakeSource()
        val memory = FakeMemory()
        var drops = 0
        val manager = ConnectionManager(
            scope = TestScope(dispatcher),
            driver = driver,
            source = source,
            memory = memory,
            ip = { "5.75.10.20" },
            networkChanged = { drops++ },
            blocking = dispatcher,
            now = { test.currentTime + 1 },
            timings = Timings(),
        )
        val state get() = manager.state.value
        fun settle() { test.advanceTimeBy(2_000); test.runCurrent() }
    }

    private fun scenario(body: suspend TestScope.(Rig) -> Unit) = runTest {
        val rig = Rig(this)
        try {
            body(rig)
        } finally {
            rig.manager.disconnect()
            testScheduler.advanceTimeBy(1_000)
            testScheduler.runCurrent()
            coroutineContext.cancelChildren()
        }
    }

    @Test fun connected_needs_a_real_handshake_not_just_a_started_tunnel() = scenario { r ->
        r.driver.answers = false
        r.manager.connect(GERMANY)
        r.settle()
        assertTrue(r.driver.running)
        assertTrue("A running tunnel with no handshake must still read Connecting", r.state is ConnectionState.Connecting)
        r.driver.answers = true
        r.settle()
        assertTrue(r.state is ConnectionState.Connected)
    }

    @Test fun a_server_that_never_answers_fails_by_itself_and_cleans_up() = scenario { r ->
        r.driver.answers = false
        r.manager.connect(GERMANY)
        advanceTimeBy(46_000)
        runCurrent()
        assertTrue(r.state is ConnectionState.Failed)
        assertFalse("The tunnel must be gone", r.driver.running)
        assertNull(r.memory.value)
    }

    @Test fun twenty_connect_disconnect_cycles_leave_nothing_behind() = scenario { r ->
        repeat(20) { round ->
            r.manager.connect(if (round % 2 == 0) GERMANY else FINLAND)
            r.settle()
            assertTrue("cycle $round", r.state is ConnectionState.Connected)
            assertEquals(1, r.driver.maxRunning)
            r.manager.disconnect()
            r.settle()
            assertEquals("cycle $round", ConnectionState.Disconnected(), r.state)
            assertFalse(r.driver.running)
            assertNull(r.memory.value)
        }
        assertEquals(20, r.driver.ups)
        assertEquals(20, r.driver.downs)
    }

    @Test fun pressing_connect_twice_starts_one_tunnel() = scenario { r ->
        r.manager.connect(GERMANY)
        r.manager.connect(GERMANY)
        r.settle()
        r.manager.connect(GERMANY)
        r.settle()
        assertEquals(1, r.driver.ups)
        assertEquals(1, r.source.asked.size)
    }

    @Test fun disconnect_during_connecting_ends_up_off_with_no_tunnel() = scenario { r ->
        r.driver.answers = false
        r.manager.connect(GERMANY)
        r.settle()
        r.manager.disconnect()
        r.settle()
        assertEquals(ConnectionState.Disconnected(), r.state)
        assertFalse(r.driver.running)
    }

    @Test fun switching_asks_for_the_new_server_before_touching_the_old_tunnel() = scenario { r ->
        r.manager.connect(GERMANY)
        r.settle()
        r.manager.connect(FINLAND)
        r.settle()
        assertEquals(FINLAND.id to GERMANY.id, r.source.asked.last())
        assertEquals(FINLAND, (r.state as ConnectionState.Connected).server)
        assertEquals(FINLAND, r.memory.value)
    }

    @Test fun a_failed_switch_keeps_the_working_connection() = scenario { r ->
        r.manager.connect(GERMANY)
        r.settle()
        r.source.fail = ProfileError.Other("That server is offline right now.")
        r.manager.connect(FINLAND)
        r.settle()
        val s = r.state as ConnectionState.Connected
        assertEquals(GERMANY, s.server)
        assertTrue(s.notice!!.contains("offline"))
        assertTrue(r.driver.running)
        assertEquals(GERMANY, r.memory.value)
    }

    @Test fun reconnecting_after_a_failure_works() = scenario { r ->
        r.source.fail = ProfileError.Other("nope")
        r.manager.connect(GERMANY)
        r.settle()
        assertEquals("nope", (r.state as ConnectionState.Failed).message)
        r.source.fail = null
        r.manager.connect(GERMANY)
        r.settle()
        assertTrue(r.state is ConnectionState.Connected)
    }

    @Test fun a_tunnel_start_failure_is_shown_and_the_next_try_works() = scenario { r ->
        r.driver.upFails = "Android refused to create the VPN."
        r.manager.connect(GERMANY)
        r.settle()
        assertEquals("Android refused to create the VPN.", (r.state as ConnectionState.Failed).message)
        r.driver.upFails = null
        r.manager.connect(GERMANY)
        r.settle()
        assertTrue(r.state is ConnectionState.Connected)
    }

    @Test fun losing_the_network_reads_reconnecting_and_recovers_by_itself() = scenario { r ->
        r.manager.connect(GERMANY)
        r.settle()
        r.driver.answers = false
        advanceTimeBy(200_000)
        runCurrent()
        assertTrue("Stale handshake must not read as Protected", r.state is ConnectionState.Reconnecting)
        assertTrue("The tunnel stays up so routes keep pointing into it", r.driver.running)
        r.driver.answers = true
        advanceTimeBy(6_000)
        runCurrent()
        assertTrue(r.state is ConnectionState.Connected)
    }

    @Test fun android_stopping_the_tunnel_reads_as_failed_not_as_an_idle_button() = scenario { r ->
        r.manager.connect(GERMANY)
        r.settle()
        r.driver.killedFromOutside()
        r.settle()
        assertTrue((r.state as ConnectionState.Failed).message.contains("Android stopped"))
        r.manager.connect(GERMANY)
        r.settle()
        assertTrue(r.state is ConnectionState.Connected)
    }

    @Test fun a_new_process_with_a_wanted_tunnel_reports_it_lost() = scenario { r ->
        r.memory.value = GERMANY
        r.manager.reconcile()
        assertEquals(GERMANY, (r.state as ConnectionState.Failed).target)
        assertNull(r.memory.value)
    }

    @Test fun a_new_process_with_nothing_wanted_is_simply_off() = scenario { r ->
        r.manager.reconcile()
        assertEquals(ConnectionState.Disconnected(), r.state)
    }

    @Test fun the_exit_address_is_verified_against_the_server() = scenario { r ->
        r.manager.connect(GERMANY)
        r.settle()
        val link = (r.state as ConnectionState.Connected).link
        assertEquals("5.75.10.20", link.publicIp)
        assertTrue(link.ipMatchesServer)
    }

    @Test fun http_connections_are_dropped_whenever_the_tunnel_changes() = scenario { r ->
        r.manager.connect(GERMANY)
        r.settle()
        assertEquals(1, r.drops)
        r.manager.disconnect()
        r.settle()
        assertEquals(2, r.drops)
    }

    @Test fun device_limit_while_switching_stops_the_old_tunnel_then_retries_with_nothing_kept() = scenario { r ->
        r.manager.connect(GERMANY)
        r.settle()
        val downsBefore = r.driver.downs
        r.source.limitOnce = true
        r.manager.connect(FINLAND)
        r.settle()
        assertEquals("First try keeps the running device", FINLAND.id to GERMANY.id, r.source.asked[r.source.asked.size - 2])
        assertEquals("Second try, with the tunnel down, keeps nothing", FINLAND.id to null, r.source.asked.last())
        assertTrue(r.driver.downs > downsBefore)
        assertEquals(FINLAND, (r.state as ConnectionState.Connected).server)
    }
}
