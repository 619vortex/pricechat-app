package live.pricechat.kiwivpn

import kotlinx.coroutines.test.runTest
import live.pricechat.kiwivpn.model.Device
import live.pricechat.kiwivpn.net.ApiException
import live.pricechat.kiwivpn.security.SavedDevice
import live.pricechat.kiwivpn.vpn.BadPeer
import live.pricechat.kiwivpn.vpn.PeerRules
import live.pricechat.kiwivpn.vpn.ProfileError
import live.pricechat.kiwivpn.vpn.Provisioner
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class ProvisionerTest {
    private val vault = tempVault().also { v -> v.update { it.copy(session = "kd.token") } }
    private val keys = FakeKeys()
    private val api = FakeApi()
    private val p = Provisioner(api, vault, keys, { id -> "Android Pixel ($id)" }, retryPauseMs = 1)

    @Test fun a_new_device_sends_only_the_public_key_and_keeps_the_private_one_here() = runTest {
        val profile = p.profile(2, null)
        assertTrue(api.calls.contains("add 2 $KEY_A"))
        assertFalse("The private key must never be sent", api.calls.any { KEY_B in it })
        assertTrue(profile.config.contains("PrivateKey = $KEY_B"))
        assertEquals(KEY_B, vault.read().devices[2]!!.privateKey)
    }

    @Test fun reconnecting_reuses_the_device_and_makes_no_new_key() = runTest {
        p.profile(2, null)
        repeat(10) { p.profile(2, null) }
        assertEquals(1, keys.made)
        assertEquals(1, api.calls.count { it.startsWith("add") })
    }

    @Test fun switching_back_and_forth_doesnt_use_up_the_new_device_limit() = runTest {
        repeat(10) { p.profile(if (it % 2 == 0) 2 else 1, null) }
        assertEquals(2, api.calls.count { it.startsWith("add") })
    }

    @Test fun a_device_removed_elsewhere_is_replaced() = runTest {
        vault.update { it.copy(devices = mapOf(2L to SavedDevice(999, KEY_B, peer()))) }
        p.profile(2, null)
        assertEquals(1, keys.made)
        assertEquals(50L, vault.read().devices[2]!!.deviceId)
    }

    @Test fun a_stale_device_with_this_phones_name_is_cleared_after_a_reinstall() = runTest {
        api.current = account(devices = listOf(Device(7, "Android Pixel (2)", 2)))
        p.profile(2, null)
        assertTrue(api.calls.contains("delete 7"))
    }

    @Test fun a_full_account_gives_up_this_phones_other_device_and_nothing_else() = runTest {
        p.profile(1, null)
        val own = vault.read().devices[1]!!.deviceId
        api.current = api.current.copy(devices = api.current.devices + listOf(Device(90, "My PC (1)", 1), Device(91, "My PC (2)", 2)))
        p.profile(2, null)
        assertTrue(api.calls.contains("delete $own"))
        assertFalse(api.calls.contains("delete 90"))
        assertFalse(api.calls.contains("delete 91"))
        assertNull(vault.read().devices[1])
    }

    @Test fun a_full_account_never_gives_up_the_device_in_use() = runTest {
        p.profile(1, null)
        api.current = api.current.copy(devices = api.current.devices + listOf(Device(90, "My PC (1)", 1), Device(91, "My PC (2)", 2)))
        try {
            p.profile(2, keepServer = 1)
            fail("Should report the limit")
        } catch (e: ProfileError.DeviceLimit) {
            assertFalse(e.tooMany)
        }
        assertNotNull(vault.read().devices[1])
    }

    @Test fun someone_elses_devices_alone_mean_the_limit_is_reported() = runTest {
        api.current = account(devices = listOf(Device(90, "PC 1", 1), Device(91, "PC 2", 2), Device(92, "Laptop", 1)))
        try {
            p.profile(2, null)
            fail("Should report the limit")
        } catch (e: ProfileError.DeviceLimit) {
            assertTrue(e.message!!.contains("used all your devices"))
        }
        assertFalse(api.calls.any { it.startsWith("delete") })
    }

    @Test fun too_many_new_devices_says_so() = runTest {
        api.addFails = ApiException(429, "Too many new devices.")
        try {
            p.profile(2, null)
            fail("Should report the rate limit")
        } catch (e: ProfileError.DeviceLimit) {
            assertTrue(e.tooMany)
        }
    }

    @Test fun an_ended_plan_stops_it_with_the_servers_reason() = runTest {
        api.current = account(eligible = false)
        try {
            p.profile(2, null)
            fail("Should refuse")
        } catch (e: ProfileError.NotEntitled) {
            assertEquals("Your free trial has ended.", e.message)
        }
    }

    @Test fun an_expired_session_clears_the_token_and_keeps_the_keys() = runTest {
        p.profile(2, null)
        api.accountFails = ApiException(401, "Sign in")
        try {
            p.profile(2, null)
            fail("Should say signed out")
        } catch (_: ProfileError.SignedOut) { }
        assertNull(vault.read().session)
        assertNotNull(vault.read().devices[2])
    }

    @Test fun not_signed_in_goes_nowhere() = runTest {
        vault.update { it.copy(session = null) }
        try {
            p.profile(2, null)
            fail("Should say signed out")
        } catch (_: ProfileError.SignedOut) { }
        assertTrue(api.calls.isEmpty())
    }

    @Test fun an_offline_server_or_an_unknown_one_is_refused() = runTest {
        try { p.profile(3, null); fail("offline") } catch (e: ProfileError.Other) { assertTrue(e.message!!.contains("offline")) }
        try { p.profile(99, null); fail("unknown") } catch (e: ProfileError.Other) { assertTrue(e.message!!.contains("no longer available")) }
    }

    @Test fun a_network_gap_just_after_disconnect_is_waited_out() = runTest {
        api.failuresBeforeAnswer = 2
        assertFalse(p.profile(2, null).fromCache)
        assertTrue("Stale sockets must be dropped", api.calls.contains("drop"))
    }

    @Test fun with_no_network_it_uses_the_saved_details_as_block_without_vpn_requires() = runTest {
        p.profile(2, null)
        api.accountFails = api.unreachable
        val profile = p.profile(2, null)
        assertTrue(profile.fromCache)
        assertTrue(profile.config.contains(KEY_B))
    }

    @Test fun with_no_network_and_nothing_saved_it_says_so() = runTest {
        api.accountFails = api.unreachable
        try {
            p.profile(2, null)
            fail("Nothing to use")
        } catch (e: ProfileError.Other) {
            assertTrue(e.message!!.contains("Couldn't reach"))
        }
    }

    @Test fun an_untrustworthy_answer_from_the_server_never_reaches_the_tunnel() = runTest {
        api.peerAnswer = peer().copy(allowedIps = listOf("10.0.0.0/8"))
        try {
            p.profile(2, null)
            fail("Should refuse")
        } catch (_: ProfileError.BadAnswer) { }
    }
}

class PeerRulesTest {
    private fun rejected(block: () -> Unit) { assertThrows(BadPeer::class.java) { block() } }

    @Test fun the_real_answer_shape_passes() {
        PeerRules.check(peer())
        PeerRules.check(peer().copy(addresses = listOf("10.66.0.9/32", "fd00::9/128")))
        PeerRules.check(peer().copy(endpointHost = "de.pricechat.example"))
    }

    @Test fun routes_must_be_exactly_the_full_tunnel() {
        rejected { PeerRules.check(peer().copy(allowedIps = listOf("0.0.0.0/0"))) } // IPv6 would go around the tunnel
        rejected { PeerRules.check(peer().copy(allowedIps = listOf("::/0"))) }
        rejected { PeerRules.check(peer().copy(allowedIps = listOf("0.0.0.0/1", "128.0.0.0/1", "::/0"))) }
        rejected { PeerRules.check(peer().copy(allowedIps = emptyList())) }
    }

    @Test fun no_dns_is_refused_because_android_would_keep_the_networks_own() {
        rejected { PeerRules.check(peer().copy(dns = emptyList())) }
        rejected { PeerRules.check(peer().copy(dns = listOf("dns.example.com"))) }
        rejected { PeerRules.check(peer().copy(dns = listOf("1.1.1.1\nPostUp = evil"))) }
    }

    @Test fun bad_keys_are_refused() {
        rejected { PeerRules.check(peer().copy(serverPublicKey = "short")) }
        rejected { PeerRules.check(peer().copy(serverPublicKey = "A".repeat(43) + "=")) } // all zeros
        rejected { PeerRules.check(peer().copy(serverPublicKey = "!".repeat(44))) }
        rejected { PeerRules.checkKey("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=") }
    }

    @Test fun endpoints_that_could_inject_or_stay_on_the_phone_are_refused() {
        for (bad in listOf("", "127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.1.1", "0.0.0.0", "::1", "evil\nPostUp=x", "host:1", "a b.example", "999.1.1.1", "1.2.3", "localhost")) {
            rejected { PeerRules.check(peer().copy(endpointHost = bad)) }
        }
    }

    @Test fun ports_and_addresses_are_bounded() {
        rejected { PeerRules.check(peer().copy(listenPort = 0)) }
        rejected { PeerRules.check(peer().copy(listenPort = 70000)) }
        rejected { PeerRules.check(peer().copy(addresses = emptyList())) }
        rejected { PeerRules.check(peer().copy(addresses = listOf("10.66.0.9/24"))) }
        rejected { PeerRules.check(peer().copy(addresses = listOf("8.8.8.8/32"))) }
        rejected { PeerRules.check(peer().copy(addresses = listOf("fd00::9/128"))) }
        rejected { PeerRules.check(peer().copy(keepalive = -1)) }
    }

    @Test fun rendering_is_what_wireguard_expects() {
        val text = PeerRules.render(KEY_B, peer())
        assertTrue(text.startsWith("[Interface]\nPrivateKey = $KEY_B\nAddress = 10.66.0.9/32\nDNS = 10.66.0.1\n"))
        assertTrue(text.contains("[Peer]\nPublicKey = $KEY_A\nEndpoint = 5.75.10.20:51821\nAllowedIPs = 0.0.0.0/0, ::/0\nPersistentKeepalive = 25\n"))
        assertTrue(PeerRules.render(KEY_B, peer().copy(endpointHost = "2001:db8::1")).contains("Endpoint = [2001:db8::1]:51821"))
    }
}
