package live.pricechat.kiwivpn.auth

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import kotlinx.coroutines.currentCoroutineContext
import java.io.ByteArrayOutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.net.URLDecoder
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64

/**
 * Signing in through the phone's browser, so the app never sees a password: RFC 8252 with PKCE (RFC 7636). The same flow as Windows (backend/app/api/routes/desktop.py), which needs no backend change:
 * a random verifier stays here, the page gets its hash, and the page hands a one-time code to 127.0.0.1 only. A code alone is useless, and works once. The token never appears in a URL.
 */
class SignInAttempt private constructor(val verifier: String, val challenge: String, val state: String) {
    fun pageUrl(base: String, port: Int): String = "$base/vpn/desktop?challenge=$challenge&port=$port&state=$state&client=android"

    companion object {
        private val encoder = Base64.getUrlEncoder().withoutPadding()

        fun challengeOf(verifier: String): String = encoder.encodeToString(MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray(Charsets.US_ASCII)))

        fun create(random: SecureRandom = SecureRandom()): SignInAttempt {
            fun text(size: Int) = ByteArray(size).also(random::nextBytes).let(encoder::encodeToString)
            val verifier = text(32)
            return SignInAttempt(verifier, challengeOf(verifier), text(24))
        }
    }
}

/** The code from the browser's request line, only if it's our callback, carrying our state. Anything else gives null. */
fun readCallback(requestLine: String, state: String): String? {
    val parts = requestLine.trim().split(Regex("\\s+"))
    if (parts.size < 2 || parts[0] != "GET") return null
    val query = parts[1].removePrefix("/callback?").takeIf { it != parts[1] } ?: return null
    val values = query.split('&').mapNotNull { pair ->
        val at = pair.indexOf('=')
        if (at <= 0) return@mapNotNull null
        val value = try { URLDecoder.decode(pair.substring(at + 1), "UTF-8") } catch (_: IllegalArgumentException) { return@mapNotNull null }
        pair.substring(0, at) to value
    }.toMap()
    if (values["state"] != state) return null
    return values["code"]?.takeIf { it.length in 20..100 }
}

private const val BACK = "kiwivpn://signed-in"
private val PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Kiwi VPN</title>
<body style="background:#070b09;color:#f6f8f4;font:18px system-ui;display:grid;place-items:center;height:90vh;text-align:center">
<div><p>You're signed in.</p><p><a href="$BACK" style="color:#bef264">Return to Kiwi VPN</a></p></div><script>location.href="$BACK"</script>""".trimIndent()

/** Listens on the loopback address only, on a port Android picks. Other requests are refused and the wait goes on. */
class LoopbackListener : AutoCloseable {
    private val socket = ServerSocket(0, 8, InetAddress.getByAddress(byteArrayOf(127, 0, 0, 1))).apply { soTimeout = 300 }
    val port: Int get() = socket.localPort

    /** The code, or null if the time ran out. Cancelling the coroutine stops the wait within a moment. */
    suspend fun awaitCode(state: String, limitMs: Long): String? = withContext(Dispatchers.IO) {
        val deadline = System.currentTimeMillis() + limitMs
        while (System.currentTimeMillis() < deadline) {
            currentCoroutineContext().ensureActive()
            val client = try { socket.accept() } catch (_: SocketTimeoutException) { continue }
            client.use {
                it.soTimeout = 5000
                val code = firstLine(it)?.let { line -> readCallback(line, state) }
                answer(it, code != null)
                if (code != null) return@withContext code
            }
        }
        null
    }

    private fun firstLine(client: Socket): String? = try {
        val data = ByteArrayOutputStream()
        val input = client.getInputStream()
        val chunk = ByteArray(1024)
        while (data.size() < 8192) {
            val got = input.read(chunk)
            if (got <= 0) break
            data.write(chunk, 0, got)
            if (data.toString("ISO-8859-1").contains("\r\n")) break
        }
        data.toString("ISO-8859-1").lineSequence().firstOrNull()
    } catch (_: java.io.IOException) {
        null
    }

    private fun answer(client: Socket, ok: Boolean) {
        val body = if (ok) PAGE else "This link isn't valid."
        val bytes = body.toByteArray(Charsets.UTF_8)
        val head = "HTTP/1.1 ${if (ok) "200 OK" else "400 Bad Request"}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: ${bytes.size}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"
        try {
            client.getOutputStream().run { write(head.toByteArray(Charsets.US_ASCII)); write(bytes); flush() }
        } catch (_: java.io.IOException) { /* the browser went away: nothing to do */ }
    }

    override fun close() = socket.close()
}
