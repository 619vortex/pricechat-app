package live.pricechat.kiwivpn.vpn

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import live.pricechat.kiwivpn.log.Diag
import live.pricechat.kiwivpn.model.ServerRef

data class TunnelStats(val lastHandshakeMs: Long, val rxBytes: Long, val txBytes: Long)

/** What the connection code needs from WireGuard. GoTunnelDriver is the real one. Calls can block, and throw TunnelFailure with a message fit to show. */
interface TunnelDriver {
    fun needsPermission(): Boolean
    fun up(config: String)
    fun down()
    fun isUp(): Boolean
    fun stats(): TunnelStats?
    fun setOnDown(listener: () -> Unit)
}

class TunnelFailure(message: String) : Exception(message)

/** The states the rest of the app sees. Connected is only ever reached by a real handshake. */
sealed interface ConnectionState {
    data class Disconnected(val notice: String? = null) : ConnectionState
    data class Connecting(val target: ServerRef, val from: ServerRef? = null, val step: Step = Step.PREPARING) : ConnectionState {
        enum class Step { PREPARING, STARTING, WAITING_FOR_SERVER }
    }
    data class Connected(val server: ServerRef, val link: Link, val notice: String? = null) : ConnectionState
    /** The tunnel is up and the server has stopped answering, as when the network drops. WireGuard keeps trying and every route still points into the tunnel, so traffic waits or fails rather than going around it. */
    data class Reconnecting(val server: ServerRef, val link: Link) : ConnectionState
    data class Disconnecting(val server: ServerRef?) : ConnectionState
    data class Failed(val message: String, val target: ServerRef?) : ConnectionState

    val isActive: Boolean get() = this is Connecting || this is Connected || this is Reconnecting
}

data class Link(
    val sinceMs: Long,
    val endpoint: String,
    val dns: List<String>,
    val lastHandshakeMs: Long,
    val rxBytes: Long,
    val txBytes: Long,
    val publicIp: String? = null,
    val ipMatchesServer: Boolean = false,
)

/** What's remembered about the connection across a restart of the process. */
interface ConnectionMemory {
    fun wanted(): ServerRef?
    fun setWanted(server: ServerRef?)
}

data class Timings(
    val connectLimitMs: Long = 45_000,
    val pollMs: Long = 500,
    val monitorMs: Long = 5_000,
    /** WireGuard rekeys every 2 minutes with keepalives on, and refuses a session after 3. Older than this, the server has stopped answering. */
    val staleMs: Long = 195_000,
    val stopLimitMs: Long = 15_000,
    val ipRetryMs: Long = 2_000,
)

/**
 * The one place the tunnel is started or stopped: Disconnected -> Connecting -> Connected -> Disconnecting -> Disconnected, with Reconnecting and Failed.
 * One job runs at a time, and each new command waits for the old job to finish, so two tunnels or two attempts can't overlap. Windows 0.1.0 taught two things that apply here too: a tunnel that dies must
 * read as failed rather than as an idle Connect button, and nothing may wait without a limit.
 */
class ConnectionManager(
    private val scope: CoroutineScope,
    private val driver: TunnelDriver,
    private val source: ProfileSource,
    private val memory: ConnectionMemory,
    private val ip: suspend () -> String,
    private val networkChanged: () -> Unit,
    private val blocking: CoroutineDispatcher,
    private val now: () -> Long = System::currentTimeMillis,
    private val timings: Timings = Timings(),
) {
    private val _state = MutableStateFlow<ConnectionState>(ConnectionState.Disconnected())
    val state: StateFlow<ConnectionState> = _state.asStateFlow()

    private val guard = Any()
    private var job: Job? = null
    /** So a failed switch shows the running connection's real details. */
    @Volatile private var lastLink: Link? = null

    /** While this code itself is taking the tunnel down, the library's DOWN callback isn't news. */
    @Volatile private var changing = false

    init {
        driver.setOnDown(::tunnelWentDown)
    }

    fun needsPermission(): Boolean = driver.needsPermission()

    /** Connect, or switch if a tunnel is already running. A repeat of what's already happening does nothing. */
    fun connect(server: ServerRef) {
        synchronized(guard) {
            when (val s = _state.value) {
                is ConnectionState.Connecting -> if (s.target.id == server.id) return
                is ConnectionState.Connected -> if (s.server.id == server.id) return
                is ConnectionState.Reconnecting -> if (s.server.id == server.id) return
                else -> Unit
            }
            val previous = activeServer()
            val old = job
            // Set here, before any coroutine runs, so a second press in the same moment sees it
            _state.value = ConnectionState.Connecting(server, previous)
            Diag.event("connect_requested", "server" to server.id, "switching" to (previous != null))
            job = scope.launch {
                old?.cancelAndJoin()
                run(server, previous)
            }
        }
    }

    fun disconnect() {
        synchronized(guard) {
            val s = _state.value
            if (s is ConnectionState.Disconnecting) return
            if (s is ConnectionState.Disconnected && !driver.isUp()) {
                memory.setWanted(null)
                return
            }
            val server = activeServer() ?: (s as? ConnectionState.Connecting)?.target
            val old = job
            _state.value = ConnectionState.Disconnecting(server)
            Diag.event("disconnect_requested")
            job = scope.launch {
                old?.cancelAndJoin()
                withContext(NonCancellable) {
                    memory.setWanted(null)
                    _state.value = try {
                        stopTunnel()
                        ConnectionState.Disconnected()
                    } catch (e: TunnelFailure) {
                        ConnectionState.Failed(e.message ?: "Couldn't disconnect.", server)
                    }
                    Diag.event("disconnected")
                }
            }
        }
    }

    /** At startup: what's really running decides what's shown. Our tunnel lives in this process, so a new process with a wanted tunnel means that tunnel was lost. */
    fun reconcile() {
        synchronized(guard) {
            if (_state.value !is ConnectionState.Disconnected) return
            val wanted = memory.wanted() ?: return
            if (driver.isUp()) {
                // Can only happen if this object was remade while the process lived on: watch what's there.
                job = scope.launch { watch(wanted, Link(now(), "", emptyList(), 0, 0, 0)) }
                return
            }
            memory.setWanted(null)
            _state.value = ConnectionState.Failed("The connection ended while the app wasn't running. Traffic is no longer going through Kiwi VPN.", wanted)
            Diag.event("found_lost_tunnel")
        }
    }

    private fun activeServer(): ServerRef? = when (val s = _state.value) {
        is ConnectionState.Connected -> s.server
        is ConnectionState.Reconnecting -> s.server
        is ConnectionState.Connecting -> if (driver.isUp()) s.from else null
        else -> null
    }

    private suspend fun run(server: ServerRef, previous: ServerRef?) {
        var running = previous
        memory.setWanted(server)
        val profile = try {
            // Fetched before the old tunnel goes. With "Block connections without VPN" nothing could reach Price Chat afterwards, and a failure here leaves the person protected rather than not.
            try {
                source.profile(server.id, running?.id)
            } catch (e: ProfileError.DeviceLimit) {
                if (e.tooMany || running == null) throw e
                // The account is full and the only device to give up is the one in use. The tunnel has to go first, because removing its device would cut off this very request.
                withContext(NonCancellable) { stopTunnel() }
                running = null
                source.profile(server.id, null)
            }
        } catch (e: ProfileError) {
            Diag.event("profile_failed", "kind" to (e::class.simpleName ?: "?"))
            giveUp(e.message ?: "Couldn't get the connection details.", server, running)
            return
        }
        _state.value = ConnectionState.Connecting(server, running, ConnectionState.Connecting.Step.STARTING)
        try {
            changing = true
            bounded(timings.stopLimitMs, "Android didn't start the tunnel.") { driver.up(profile.config) }
        } catch (e: TunnelFailure) {
            Diag.event("tunnel_start_failed")
            withContext(NonCancellable) { silentStop() }
            memory.setWanted(null)
            _state.value = ConnectionState.Failed(e.message ?: "Couldn't start the tunnel.", server)
            return
        } finally {
            changing = false
        }
        networkChanged()
        _state.value = ConnectionState.Connecting(server, null, ConnectionState.Connecting.Step.WAITING_FOR_SERVER)

        val first = try {
            withTimeoutOrNull(timings.connectLimitMs) {
                var stats = readStats()
                while (stats == null || stats.lastHandshakeMs <= 0) {
                    delay(timings.pollMs)
                    stats = readStats()
                }
                stats
            }
        } catch (e: CancellationException) {
            // Cancelled by Disconnect or by a newer Connect, which take over from here.
            throw e
        }
        if (first == null) {
            Diag.event("handshake_timeout")
            withContext(NonCancellable) { silentStop() }
            memory.setWanted(null)
            _state.value = ConnectionState.Failed(if (profile.fromCache) "The server didn't answer. Check your connection and try again." else "The server didn't answer in time. Check your connection, or try another country.", server)
            return
        }
        Diag.event("connected", "server" to server.id, "from_cache" to profile.fromCache)
        watch(server, Link(now(), profile.endpoint, profile.dns, first.lastHandshakeMs, first.rxBytes, first.txBytes))
    }

    /** Stays on the old tunnel if there is one, since a failed switch shouldn't cost the working connection. */
    private fun giveUp(message: String, server: ServerRef, running: ServerRef?) {
        if (running != null && driver.isUp()) {
            memory.setWanted(running)
            val stats = driver.stats()
            val link = (lastLink ?: Link(now(), "", emptyList(), 0, 0, 0)).copy(lastHandshakeMs = stats?.lastHandshakeMs ?: 0, rxBytes = stats?.rxBytes ?: 0, txBytes = stats?.txBytes ?: 0)
            _state.value = ConnectionState.Connected(running, link, notice = "Couldn't switch: $message")
            job = scope.launch { watch(running, link, notice = "Couldn't switch: $message") }
        } else {
            memory.setWanted(null)
            _state.value = ConnectionState.Failed(message, server)
        }
    }

    private suspend fun watch(server: ServerRef, start: Link, notice: String? = null) = coroutineScope {
        var link = start
        lastLink = start
        _state.value = ConnectionState.Connected(server, link, notice)
        val ipCheck = launch { checkIp(server) }
        while (isActive) {
            delay(timings.monitorMs)
            val stats = readStats() ?: continue
            link = (currentLink() ?: link).copy(lastHandshakeMs = stats.lastHandshakeMs, rxBytes = stats.rxBytes, txBytes = stats.txBytes)
            lastLink = link
            val stale = now() - stats.lastHandshakeMs > timings.staleMs
            val before = _state.value
            when {
                before !is ConnectionState.Connected && before !is ConnectionState.Reconnecting -> Unit
                stale -> {
                    if (before is ConnectionState.Connected) Diag.event("server_stopped_answering")
                    _state.value = ConnectionState.Reconnecting(server, link.copy(publicIp = null, ipMatchesServer = false))
                }
                else -> {
                    if (before is ConnectionState.Reconnecting) {
                        Diag.event("server_answering_again")
                        link = link.copy(sinceMs = link.sinceMs)
                        _state.value = ConnectionState.Connected(server, link)
                        if (ipCheck.isCompleted) launch { checkIp(server) }
                    } else {
                        _state.value = ConnectionState.Connected(server, link, (before as ConnectionState.Connected).notice)
                    }
                }
            }
        }
    }

    private fun currentLink(): Link? = when (val s = _state.value) {
        is ConnectionState.Connected -> s.link
        is ConnectionState.Reconnecting -> s.link
        else -> null
    }

    /** The address Price Chat sees, asked through the tunnel. Shown only as verified when it's the server's own. */
    private suspend fun checkIp(server: ServerRef) {
        repeat(3) {
            try {
                val address = ip()
                _state.update { s ->
                    if (s is ConnectionState.Connected && s.server.id == server.id) {
                        s.copy(link = s.link.copy(publicIp = address, ipMatchesServer = address == s.link.endpoint))
                    } else s
                }
                Diag.event("exit_ip_checked", "matches_server" to ((_state.value as? ConnectionState.Connected)?.link?.ipMatchesServer == true))
                return
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                delay(timings.ipRetryMs)
            }
        }
    }

    private suspend fun readStats(): TunnelStats? = withContext(blocking) { driver.stats() }

    private suspend fun stopTunnel() {
        changing = true
        try {
            bounded(timings.stopLimitMs, "Android didn't stop the tunnel. Try again, or turn the VPN off in Settings.") { driver.down() }
        } finally {
            changing = false
            networkChanged()
        }
    }

    private suspend fun silentStop() {
        try { stopTunnel() } catch (_: TunnelFailure) { Diag.event("stop_failed") }
    }

    /** A blocking call that can't hang the state machine: past the limit the state moves on with an error, whatever the call is doing. */
    private suspend fun bounded(limitMs: Long, onTimeout: String, call: () -> Unit) {
        val work = scope.async(blocking) { runCatching(call) }
        val result = withTimeoutOrNull(limitMs) { work.await() } ?: throw TunnelFailure(onTimeout)
        result.exceptionOrNull()?.let { e ->
            throw e as? TunnelFailure ?: TunnelFailure("Couldn't change the tunnel.")
        }
    }

    /** The library says the tunnel is gone, and this code didn't ask for that: Android took it, or another VPN started. */
    private fun tunnelWentDown() {
        if (changing) return
        synchronized(guard) {
            val s = _state.value
            if (!s.isActive) return
            val server = when (s) {
                is ConnectionState.Connected -> s.server
                is ConnectionState.Reconnecting -> s.server
                is ConnectionState.Connecting -> s.target
                else -> null
            }
            job?.cancel()
            memory.setWanted(null)
            _state.value = ConnectionState.Failed("Android stopped the connection, or another VPN started. Traffic is no longer going through Kiwi VPN.", server)
            Diag.event("tunnel_stopped_from_outside")
        }
    }
}
