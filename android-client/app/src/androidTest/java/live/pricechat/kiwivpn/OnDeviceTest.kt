package live.pricechat.kiwivpn

import androidx.test.ext.junit.runners.AndroidJUnit4
import com.wireguard.config.Config
import com.wireguard.crypto.Key
import com.wireguard.crypto.KeyPair
import live.pricechat.kiwivpn.model.Peer
import live.pricechat.kiwivpn.routing.RoutePlan
import live.pricechat.kiwivpn.routing.RoutingMode
import live.pricechat.kiwivpn.routing.RoutingPolicy
import live.pricechat.kiwivpn.security.KeystoreSealer
import live.pricechat.kiwivpn.vpn.PeerRules
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test
import org.junit.runner.RunWith
import java.io.ByteArrayInputStream

/** Runs on a real phone or emulator: `gradle connectedDebugAndroidTest`. These need Android itself: the real Keystore, and the real WireGuard library. They create no tunnel and touch no server. */
@RunWith(AndroidJUnit4::class)
class OnDeviceTest {
    @Test fun the_keystore_seals_and_opens() {
        val sealer = KeystoreSealer("kiwi_test_key")
        val plain = "kd.secret".toByteArray()
        val sealed = sealer.seal(plain)
        assertFalse(String(sealed, Charsets.ISO_8859_1).contains("kd.secret"))
        assertArrayEquals(plain, sealer.open(sealed))
    }

    @Test fun the_libraries_keys_and_parser_accept_what_the_app_renders() {
        val pair = KeyPair()
        val other = KeyPair()
        assertEquals(44, pair.privateKey.toBase64().length)
        assertEquals(pair.publicKey.toBase64(), KeyPair(Key.fromBase64(pair.privateKey.toBase64())).publicKey.toBase64())
        val peer = Peer(other.publicKey.toBase64(), "5.75.10.20", 51821, listOf("10.66.0.9/32"), listOf("10.66.0.1"), listOf("0.0.0.0/0", "::/0"), 25)
        PeerRules.check(peer)
        val config = Config.parse(ByteArrayInputStream(PeerRules.render(pair.privateKey.toBase64(), peer).toByteArray()))
        assertEquals(1, config.peers.size)
        assertEquals(2, config.peers[0].allowedIps.size)
        assertEquals(25, config.peers[0].persistentKeepalive.get())
        assertEquals(1, config.`interface`.dnsServers.size)
    }

    /** The real library's own parser reads what the app writes: this is what GoBackend turns into addAllowedApplication and addDisallowedApplication. */
    @Test fun the_library_reads_each_routing_mode_from_the_config() {
        val pair = KeyPair()
        val peer = Peer(KeyPair().publicKey.toBase64(), "5.75.10.20", 51821, listOf("10.66.0.9/32"), listOf("10.66.0.1"), listOf("0.0.0.0/0", "::/0"), 25)
        val base = PeerRules.render(pair.privateKey.toBase64(), peer)
        fun parse(plan: RoutePlan) = Config.parse(ByteArrayInputStream(RoutingPolicy.applyTo(base, plan).toByteArray())).`interface`

        val all = parse(RoutePlan.ALL)
        assertEquals(emptySet<String>(), all.includedApplications)
        assertEquals(emptySet<String>(), all.excludedApplications)

        val only = parse(RoutePlan(RoutingMode.ONLY_SELECTED, listOf("com.whatsapp", "live.pricechat.kiwivpn"), 1))
        assertEquals(setOf("com.whatsapp", "live.pricechat.kiwivpn"), only.includedApplications)
        assertEquals(emptySet<String>(), only.excludedApplications)

        val except = parse(RoutePlan(RoutingMode.ALL_EXCEPT, listOf("com.bank"), 1))
        assertEquals(setOf("com.bank"), except.excludedApplications)
        assertEquals(emptySet<String>(), except.includedApplications)
    }
}
