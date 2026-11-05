package live.pricechat.kiwivpn.vpn

import live.pricechat.kiwivpn.model.Peer
import java.util.Base64

class BadPeer(message: String) : Exception(message)

/**
 * Checks every field the server sends before it can reach the tunnel, and rejects anything unexpected. A wrong answer, whatever the reason, must not quietly send traffic outside the tunnel:
 * so the routes must be exactly every IPv4 and every IPv6 address, and there must be a DNS server, or Android would keep using the network's own.
 */
object PeerRules {
    private val FULL_TUNNEL = setOf("0.0.0.0/0", "::/0")
    private val LABEL = Regex("^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")

    fun check(peer: Peer) {
        checkKey(peer.serverPublicKey)
        checkEndpoint(peer.endpointHost)
        if (peer.listenPort !in 1..65535) throw BadPeer("port")
        checkAddresses(peer.addresses)
        if (peer.dns.isEmpty() || peer.dns.size > 4) throw BadPeer("dns count")
        peer.dns.forEach { if (!isIpv4(it) && !isIpv6(it)) throw BadPeer("dns") }
        if (peer.allowedIps.toSet() != FULL_TUNNEL) throw BadPeer("routes")
        if (peer.keepalive !in 0..65535) throw BadPeer("keepalive")
    }

    fun checkKey(value: String) {
        if (value.length != 44) throw BadPeer("key length")
        val raw = try { Base64.getDecoder().decode(value) } catch (_: IllegalArgumentException) { throw BadPeer("key encoding") }
        if (raw.size != 32 || raw.all { it == 0.toByte() }) throw BadPeer("key")
    }

    private fun checkEndpoint(host: String) {
        if (host.isEmpty() || host.length > 253) throw BadPeer("endpoint")
        if (isIpv4(host)) {
            if (isInternalIpv4(host)) throw BadPeer("endpoint internal")
            return
        }
        if (isIpv6(host)) {
            if (host == "::1" || host == "::") throw BadPeer("endpoint internal")
            return
        }
        // Every label a hostname, and not all digits: "1.2.3" or "999.1.1.1" isn't an address or a name
        val labels = host.split('.')
        if (labels.size < 2 || labels.any { !LABEL.matches(it) } || labels.last().all(Char::isDigit)) throw BadPeer("endpoint")
    }

    private fun checkAddresses(addresses: List<String>) {
        if (addresses.isEmpty() || addresses.size > 2) throw BadPeer("addresses")
        var v4 = 0
        var v6 = 0
        for (a in addresses) {
            val at = a.indexOf('/')
            if (at <= 0) throw BadPeer("address")
            val ip = a.substring(0, at)
            val mask = a.substring(at + 1)
            when {
                isIpv4(ip) && mask == "32" && isTunnelIpv4(ip) -> v4++
                isIpv6(ip) && mask == "128" -> v6++
                else -> throw BadPeer("address")
            }
        }
        if (v4 != 1 || v6 > 1) throw BadPeer("addresses")
    }

    fun isIpv4(value: String): Boolean {
        val parts = value.split('.')
        return parts.size == 4 && parts.all { p -> p.length in 1..3 && p.all(Char::isDigit) && p.toInt() <= 255 && (p.length == 1 || p[0] != '0') }
    }

    private fun octets(ip: String) = ip.split('.').map(String::toInt)

    /** The private ranges a WireGuard pool lives in. */
    private fun isTunnelIpv4(ip: String): Boolean {
        val (a, b) = octets(ip)
        return a == 10 || (a == 172 && b in 16..31) || (a == 192 && b == 168) || (a == 100 && b in 64..127)
    }

    private fun isInternalIpv4(ip: String): Boolean {
        val (a, b) = octets(ip)
        return a == 0 || a == 127 || a >= 224 || (a == 169 && b == 254) || isTunnelIpv4(ip)
    }

    fun isIpv6(value: String): Boolean {
        if (value.length > 39 || !value.contains(':') || value.any { !(it.isDigit() || it in 'a'..'f' || it in 'A'..'F' || it == ':') }) return false
        if (value.contains(":::")) return false
        val double = value.indexOf("::")
        if (double != value.lastIndexOf("::")) return false
        val groups = value.split("::").flatMap { it.split(':') }.filter { it.isNotEmpty() }
        if (groups.any { it.length > 4 }) return false
        if (double >= 0) return groups.size <= 7
        return value.split(':').size == 8 && value.split(':').all { it.isNotEmpty() }
    }

    /** wg-quick text, for the library's own parser. It holds the private key: it's never logged, stored or sent anywhere. */
    fun render(privateKey: String, peer: Peer): String = buildString {
        append("[Interface]\nPrivateKey = ").append(privateKey).append('\n')
        append("Address = ").append(peer.addresses.joinToString(", ")).append('\n')
        append("DNS = ").append(peer.dns.joinToString(", ")).append('\n')
        val host = if (isIpv6(peer.endpointHost)) "[${peer.endpointHost}]" else peer.endpointHost
        append("\n[Peer]\nPublicKey = ").append(peer.serverPublicKey).append('\n')
        append("Endpoint = ").append(host).append(':').append(peer.listenPort).append('\n')
        append("AllowedIPs = ").append(peer.allowedIps.joinToString(", ")).append('\n')
        if (peer.keepalive > 0) append("PersistentKeepalive = ").append(peer.keepalive).append('\n')
    }
}
