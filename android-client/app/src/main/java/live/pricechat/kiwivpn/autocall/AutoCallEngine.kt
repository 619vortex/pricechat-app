package live.pricechat.kiwivpn.autocall

enum class AutoCallStatus(val message: String) {
    IDLE("Waiting for a call"),
    CALL_DETECTED("Call detected"),
    CONNECTING("Connecting VPN"),
    CONNECTED("VPN connected"),
    ALREADY_ON("Call detected. VPN was already on, and stays on."),
    CALL_ENDED("Call ended. VPN disconnects in 15 seconds."),
    AUDIO_BUSY("A call still seems to be in progress. VPN stays on."),
    UNCERTAIN("Couldn't confirm the call ended. VPN stays on until you disconnect it."),
    CONNECT_FAILED("Couldn't connect the VPN for this call."),
    NEEDS_APP("Open Kiwi VPN to allow this connection."),
    DISCONNECTED("Call over. VPN disconnected."),
}

sealed interface AutoCallEffect {
    data object Connect : AutoCallEffect
    data object Disconnect : AutoCallEffect
    data class Status(val status: AutoCallStatus, val alert: Boolean = false) : AutoCallEffect
}

data class TrackedCall(val key: Int, val app: CallApp, val phase: CallPhase)

/**
 * The whole of Auto-Call VPN's logic, with no Android in it. Its rules:
 *  - It only ever disconnects a VPN it started itself (`owned`), so a connection the person made by hand survives the call.
 *  - It only disconnects after a confirmed end, then 15 seconds, then only if no other call is tracked and Android's audio mode doesn't show a call.
 *  - Anything uncertain keeps the VPN on and tells the person. It's cleared only by the person acting, because nothing here can prove an uncertain call is over.
 *  - The same call seen twice, or a new key while the VPN is already on, starts nothing.
 *  - If the person disconnects during a call, it isn't reconnected until that call is over.
 *
 * Keys are hashes held in memory. Only `owned` is ever saved. Calls run one at a time on the controller's single thread.
 */
class AutoCallEngine(owned: Boolean = false, private val graceMs: Long = 15_000) {
    var owned: Boolean = owned
        private set
    var status: AutoCallStatus = AutoCallStatus.IDLE
        private set
    var uncertain: Boolean = false
        private set
    /** When the waiting after a call ends, or null. The controller calls onTick then. */
    var deadline: Long? = null
        private set

    private val calls = LinkedHashMap<Int, TrackedCall>()
    private var heldOff = false

    val activeCalls: List<TrackedCall> get() = calls.values.toList()

    fun onPosted(key: Int, signal: CallSignal, tunnelOn: Boolean): List<AutoCallEffect> {
        val isNew = key !in calls
        val wasIdle = calls.isEmpty()
        calls[key] = TrackedCall(key, signal.app, signal.phase)
        deadline = null
        if (!isNew) return emptyList()
        return startFor(wasIdle, tunnelOn)
    }

    private fun startFor(wasIdle: Boolean, tunnelOn: Boolean): List<AutoCallEffect> {
        if (heldOff) return emptyList()
        if (tunnelOn) {
            // Already on: if this engine started it, it's still this engine's to end. If the person did, it never will be.
            return if (owned) listOf(status(AutoCallStatus.CONNECTED)) else if (wasIdle) listOf(status(AutoCallStatus.ALREADY_ON)) else emptyList()
        }
        owned = true
        return listOf(status(AutoCallStatus.CALL_DETECTED, alert = true), status(AutoCallStatus.CONNECTING), AutoCallEffect.Connect)
    }

    fun onRemoved(key: Int, confirmedEnd: Boolean, now: Long): List<AutoCallEffect> {
        if (calls.remove(key) == null) return emptyList()
        if (!confirmedEnd) uncertain = true
        if (calls.isNotEmpty()) return emptyList()
        heldOff = false
        if (!owned) {
            if (status != AutoCallStatus.IDLE) status = AutoCallStatus.IDLE
            uncertain = false
            return listOf(AutoCallEffect.Status(AutoCallStatus.IDLE))
        }
        if (uncertain) return listOf(status(AutoCallStatus.UNCERTAIN, alert = true))
        deadline = now + graceMs
        return listOf(status(AutoCallStatus.CALL_ENDED))
    }

    /** The waiting is over. `audioBusy`: Android's audio mode shows a call, from any app. It can't say whose, so it only ever delays a disconnect. */
    fun onTick(now: Long, audioBusy: Boolean): List<AutoCallEffect> {
        val due = deadline ?: return emptyList()
        if (now < due) return emptyList()
        deadline = null
        if (calls.isNotEmpty() || !owned) return emptyList()
        if (uncertain) return listOf(status(AutoCallStatus.UNCERTAIN, alert = true))
        if (audioBusy) {
            deadline = now + graceMs
            return listOf(status(AutoCallStatus.AUDIO_BUSY))
        }
        owned = false
        return listOf(AutoCallEffect.Disconnect, status(AutoCallStatus.DISCONNECTED))
    }

    /** Android has (re)connected this listener and listed what's showing. `wasOwnedBeforeRestart`-style recovery: the saved `owned` flag survived, the list of calls didn't. */
    fun onListenerConnected(active: List<Pair<Int, CallSignal>>, tunnelOn: Boolean): List<AutoCallEffect> {
        val known = calls.keys.toSet()
        val seen = active.map { it.first }.toSet()
        val lost = known - seen
        lost.forEach { calls.remove(it) }
        active.forEach { (key, signal) -> calls[key] = TrackedCall(key, signal.app, signal.phase) }
        if (calls.isNotEmpty()) {
            deadline = null
            if (!tunnelOn) return startFor(wasIdle = known.isEmpty(), tunnelOn = false)
            return if (owned) listOf(status(AutoCallStatus.CONNECTED)) else listOf(status(AutoCallStatus.ALREADY_ON))
        }
        // A VPN this engine started, with no call to show for it and no confirmed end: a crash, or the listener was away during the end of the call.
        if (owned && tunnelOn && deadline == null) {
            uncertain = true
            return listOf(status(AutoCallStatus.UNCERTAIN, alert = true))
        }
        if (owned && !tunnelOn) owned = false
        return emptyList()
    }

    /** Android has cut the listener off. Nothing more can be known until it returns, so nothing may be ended. */
    fun onListenerLost(): List<AutoCallEffect> {
        if (!owned || (calls.isEmpty() && deadline == null)) return emptyList()
        deadline = null
        uncertain = true
        return listOf(status(AutoCallStatus.UNCERTAIN, alert = true))
    }

    fun onConnected(): List<AutoCallEffect> = if (owned && status == AutoCallStatus.CONNECTING) listOf(status(AutoCallStatus.CONNECTED, alert = true)) else emptyList()

    /** The connection this engine asked for didn't happen. No retry for this call: a loop of failing tunnels during a call would do more harm than good. */
    fun onConnectFailed(needsApp: Boolean = false): List<AutoCallEffect> {
        if (!owned) return emptyList()
        owned = false
        heldOff = calls.isNotEmpty()
        return listOf(status(if (needsApp) AutoCallStatus.NEEDS_APP else AutoCallStatus.CONNECT_FAILED, alert = true))
    }

    /** The person pressed Connect: the connection is theirs from now on. */
    fun onUserConnected() {
        owned = false
        uncertain = false
        deadline = null
        status = AutoCallStatus.IDLE
    }

    /** The person pressed Disconnect: respected, and not undone for the rest of this call. */
    fun onUserDisconnected() {
        owned = false
        uncertain = false
        deadline = null
        heldOff = calls.isNotEmpty()
        status = AutoCallStatus.IDLE
    }

    /** Auto-Call turned off, or an app deselected. The VPN is left as it is, and becomes the person's. */
    fun onDisabled() {
        calls.clear()
        owned = false
        uncertain = false
        deadline = null
        heldOff = false
        status = AutoCallStatus.IDLE
    }

    private fun status(s: AutoCallStatus, alert: Boolean = false): AutoCallEffect {
        status = s
        return AutoCallEffect.Status(s, alert)
    }
}
