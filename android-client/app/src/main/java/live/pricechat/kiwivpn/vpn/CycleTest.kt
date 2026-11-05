package live.pricechat.kiwivpn.vpn

import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeoutOrNull
import live.pricechat.kiwivpn.model.ServerRef

/**
 * For real-device testing: repeated connect, check, disconnect through the app's real connection code, alternating servers. Reachable only from debug builds. It counts a cycle only if a real handshake
 * happened, the exit address was the server's own, and the state came back to Disconnected. It can't test DNS leaks, IPv6 leaks, or anything outside this app: see docs/DEVICE-TEST-PLAN.md.
 */
class CycleTest(private val connection: ConnectionManager, private val limitMs: Long = 70_000) {
    suspend fun run(servers: List<ServerRef>, rounds: Int, report: (String) -> Unit) {
        val lines = mutableListOf<String>()
        var passed = 0
        for (round in 1..rounds) {
            val server = servers[(round - 1) % servers.size]
            val result = cycle(server)
            if (result == null) passed++
            lines += "Cycle $round ${server.name}: ${result ?: "ok"}"
            report((lines.takeLast(12) + "$passed of $round passed so far").joinToString("\n"))
        }
        report((lines + "RESULT: $passed of $rounds passed").joinToString("\n"))
    }

    /** null if it all worked, else what didn't. */
    private suspend fun cycle(server: ServerRef): String? {
        connection.connect(server)
        val on = withTimeoutOrNull(limitMs) { connection.state.first { it is ConnectionState.Connected || it is ConnectionState.Failed } }
        if (on !is ConnectionState.Connected) return "no connection: ${(on as? ConnectionState.Failed)?.message ?: "timed out"}"
        val checked = withTimeoutOrNull(20_000) { connection.state.first { it is ConnectionState.Connected && it.link.publicIp != null } }
        val matched = (checked as? ConnectionState.Connected)?.link?.ipMatchesServer == true
        connection.disconnect()
        val off = withTimeoutOrNull(limitMs) { connection.state.first { it is ConnectionState.Disconnected || it is ConnectionState.Failed } }
        return when {
            off !is ConnectionState.Disconnected -> "didn't disconnect"
            !matched -> "connected, but the exit address wasn't confirmed as the server's"
            else -> null
        }
    }
}
