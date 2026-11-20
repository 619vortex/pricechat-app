package live.pricechat.kiwivpn

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import live.pricechat.kiwivpn.account.DeletionResult
import live.pricechat.kiwivpn.auth.LoopbackListener
import live.pricechat.kiwivpn.auth.SignInAttempt
import live.pricechat.kiwivpn.autocall.AutoCallController
import live.pricechat.kiwivpn.log.Diag
import live.pricechat.kiwivpn.model.Account
import live.pricechat.kiwivpn.model.Passport
import live.pricechat.kiwivpn.model.Server
import live.pricechat.kiwivpn.model.ref
import live.pricechat.kiwivpn.model.suggestedServer
import live.pricechat.kiwivpn.net.ApiException
import live.pricechat.kiwivpn.net.BASE_URL
import live.pricechat.kiwivpn.routing.RoutingPrefs
import live.pricechat.kiwivpn.security.AppSettings
import live.pricechat.kiwivpn.vpn.ConnectionState
import live.pricechat.kiwivpn.vpn.TunnelDriver

data class UiState(
    val signedIn: Boolean = false,
    val signingIn: Boolean = false,
    val who: String? = null,
    /** The Price Chat username, exactly as the server has it: deleting the account asks for it to be typed. */
    val username: String? = null,
    val account: Account? = null,
    val selected: Server? = null,
    val checked: Boolean = false,
    val error: String? = null,
    val settings: AppSettings = AppSettings(),
    val testReport: String? = null,
    val passport: Passport? = null,
    /** Empty until the shared API answers. A feature is hidden only if the server explicitly says no. */
    val capabilities: Map<String, Boolean> = emptyMap(),
    val deleting: Boolean = false,
    val deleteError: String? = null,
    /** Shown on the sign-in screen after the account was deleted. */
    val notice: String? = null,
)

private const val SIGN_IN_MS = 5 * 60 * 1000L
private const val REFRESH_EVERY_MS = 7 * 24 * 3600 * 1000L
private const val ACCOUNT_EVERY_MS = 60_000L

class AppViewModel(application: Application) : AndroidViewModel(application) {
    private val app = application as KiwiApp
    private val _ui = MutableStateFlow(
        UiState(
            signedIn = app.vault.read().session != null,
            settings = app.vault.read().settings,
            // So Connect works before the account has loaded, or when it can't load, as with "Block connections without VPN" on
            selected = app.vault.read().lastServer?.let { Server(it.id, it.name, "", it.countryCode, online = true) },
        ),
    )
    val ui: StateFlow<UiState> = _ui.asStateFlow()
    val connection = app.connection.state
    val autoCallStatus = app.autoCall.status
    val autoCallTraces = app.autoCall.traces
    private var signIn: Job? = null
    private var autoConnected = false

    init {
        viewModelScope.launch {
            while (isActive) {
                if (_ui.value.signedIn) load()
                delay(ACCOUNT_EVERY_MS)
            }
        }
    }

    /** A failed check keeps the last good answer: a brief network gap, as while the tunnel changes, mustn't take away the countries and Connect. Windows 0.1.0 had exactly this bug. A real sign-out still applies. */
    fun load() {
        viewModelScope.launch {
            val token = app.vault.read().session ?: return@launch
            try {
                refreshIfDue(token)
                val current = app.vault.read().session ?: return@launch
                val account = app.api.account(current)
                val who = runCatching { app.api.me(current).member }.getOrNull()
                // Optional: production doesn't have this route until it's approved. Any failure keeps what was last shown, and never affects the VPN.
                val shared = runCatching { app.api.unifiedState(current, BuildConfig.VERSION_NAME) }.getOrNull()
                if (shared != null) _ui.update { it.copy(passport = shared.passport ?: it.passport, capabilities = shared.capabilities) }
                _ui.update { s ->
                    val keep = s.selected?.let { old -> account.servers.firstOrNull { it.id == old.id && it.online } }
                    s.copy(account = account, who = who?.displayName ?: who?.username ?: s.who, username = who?.username ?: shared?.passport?.username?.ifBlank { null } ?: s.username, selected = keep ?: suggestedServer(account.servers, app.vault.read().lastServer?.id), checked = true, error = null)
                }
                maybeAutoConnect()
            } catch (e: ApiException) {
                if (e.signedOut) {
                    Diag.event("session_expired")
                    app.vault.update { it.copy(session = null) }
                    _ui.update { it.copy(signedIn = false, account = null, who = null, checked = true, error = "Your session ended. Please sign in again.") }
                } else {
                    _ui.update { it.copy(checked = true, error = if (it.account == null) e.message else it.error) }
                }
            }
        }
    }

    /** About weekly, so regular use never reaches the 90-day limit and a copied token soon stops working. Failing changes nothing: the current session still works. */
    private suspend fun refreshIfDue(token: String) {
        val now = System.currentTimeMillis()
        val last = app.vault.read().refreshedAt
        if (last == null) {
            app.vault.update { it.copy(refreshedAt = now) }
        } else if (now - last >= REFRESH_EVERY_MS) {
            try {
                val fresh = app.api.refresh(token)
                app.vault.update { it.copy(session = fresh, refreshedAt = now) }
            } catch (e: ApiException) {
                if (e.signedOut) throw e
            }
        }
    }

    private fun maybeAutoConnect() {
        val s = _ui.value
        val server = s.selected
        if (autoConnected || !s.settings.autoConnect || s.account?.eligible != true || server == null) return
        autoConnected = true
        if (app.connection.state.value is ConnectionState.Disconnected && !app.connection.needsPermission()) app.userConnect(server.ref())
    }

    /** Opens the browser through `open`. The result comes back on the loopback listener, and only the code with this attempt's state is taken. */
    fun signIn(open: (String) -> Unit) {
        signIn?.cancel()
        signIn = viewModelScope.launch {
            _ui.update { it.copy(signingIn = true, error = null, notice = null) }
            val attempt = SignInAttempt.create()
            try {
                LoopbackListener().use { listener ->
                    open(attempt.pageUrl(BASE_URL, listener.port))
                    val code = listener.awaitCode(attempt.state, SIGN_IN_MS)
                    if (code == null) {
                        _ui.update { it.copy(signingIn = false, error = "Signing in took too long. Please try again.") }
                        return@launch
                    }
                    val token = app.api.exchangeCode(code, attempt.verifier)
                    // A different account from the last one on this phone gets none of its keys.
                    app.vault.update { it.copy(session = token, refreshedAt = System.currentTimeMillis()) }
                    Diag.event("signed_in")
                    _ui.update { it.copy(signingIn = false, signedIn = true, error = null) }
                    load()
                }
            } catch (e: ApiException) {
                _ui.update { it.copy(signingIn = false, error = e.message) }
            } catch (e: java.io.IOException) {
                _ui.update { it.copy(signingIn = false, error = "Couldn't start signing in. Please try again.") }
            }
        }
    }

    fun cancelSignIn() {
        signIn?.cancel()
        _ui.update { it.copy(signingIn = false) }
    }

    /** Only ever on the person's own press. The server allows one per account, and says so if it's been used. */
    fun startTrial() {
        viewModelScope.launch {
            val token = app.vault.read().session ?: return@launch
            try {
                val account = app.api.startTrial(token)
                _ui.update { it.copy(account = account, selected = it.selected ?: suggestedServer(account.servers, null), error = null) }
            } catch (e: ApiException) {
                _ui.update { it.copy(error = e.message) }
            }
        }
    }

    fun select(server: Server) {
        _ui.update { it.copy(selected = server) }
        app.vault.update { it.copy(lastServer = server.ref()) }
        if (app.connection.state.value.isActive) app.userConnect(server.ref())
    }

    fun needsVpnPermission() = app.connection.needsPermission()

    fun connect() {
        val server = _ui.value.selected ?: return
        app.userConnect(server.ref())
    }

    fun disconnect() = app.userDisconnect()

    fun removeDevice(id: Long) {
        viewModelScope.launch {
            val token = app.vault.read().session ?: return@launch
            try {
                app.api.deleteDevice(token, id)
                app.vault.update { v -> v.copy(devices = v.devices.filterValues { it.deviceId != id }) }
                load()
            } catch (e: ApiException) {
                _ui.update { it.copy(error = e.message) }
            }
        }
    }

    /** Tunnel down, this phone's devices removed so they don't hold slots, session ended on the server, and keys wiped here. If the network is down the keys still go, and the slots free up the next time this phone signs in. */
    fun signOut() {
        viewModelScope.launch {
            app.userDisconnect()
            app.autoCall.disabled()
            val v = app.vault.read()
            val token = v.session
            if (token != null) {
                v.devices.values.forEach { runCatching { app.api.deleteDevice(token, it.deviceId) } }
                runCatching { app.api.logout(token) }
            }
            app.vault.forgetAccount()
            Diag.event("signed_out")
            _ui.update { UiState(settings = it.settings) }
        }
    }

    fun changeSettings(change: (AppSettings) -> AppSettings) {
        val before = app.vault.read().settings
        val after = change(before)
        app.vault.update { it.copy(settings = after) }
        _ui.update { it.copy(settings = after) }
        if (before.autoCallEnabled && !after.autoCallEnabled) app.autoCall.disabled()
    }

    /** "Choose what uses Kiwi VPN" answered: saved into the same setting Settings edits, and not asked again on this phone. */
    fun finishRoutingOnboarding(prefs: RoutingPrefs) {
        saveRouting(prefs)
        changeSettings { it.copy(routingOnboarded = true) }
    }

    /** Deletes the Price Chat account on the server, then erases this phone's copy of everything. Nothing local changes unless the server confirms. */
    fun deleteAccount(typed: String) {
        if (_ui.value.deleting) return
        viewModelScope.launch {
            _ui.update { it.copy(deleting = true, deleteError = null) }
            when (val result = app.accountDeletion.delete(typed, _ui.value.username)) {
                is DeletionResult.Deleted -> {
                    Diag.event("account_deleted")
                    _ui.update { UiState(notice = "Your Price Chat account was deleted, and Kiwi VPN erased what it kept on this phone.") }
                }
                is DeletionResult.Failed -> {
                    if (result.signedOut) {
                        app.vault.forgetAccount()
                        _ui.update { UiState(settings = it.settings, error = result.message) }
                    } else {
                        _ui.update { it.copy(deleting = false, deleteError = result.message) }
                    }
                }
            }
        }
    }

    fun clearDeleteError() = _ui.update { it.copy(deleteError = null) }

    fun canConfirmDeletion(typed: String): Boolean = app.accountDeletion.canConfirm(typed, _ui.value.username)

    /** Saved first, so the choice survives whatever happens next. If a VPN is running, it's rebuilt with the new lists. If not, they apply on the next connection. */
    fun saveRouting(prefs: RoutingPrefs) {
        changeSettings { it.copy(routing = prefs) }
        if (app.connection.state.value.isActive) app.connection.applyRouting()
    }

    fun hasNotificationAccess() = AutoCallController.hasNotificationAccess(getApplication())

    fun alwaysOn(): Pair<Boolean, Boolean>? = app.driver.alwaysOn()

    /** Debug builds only: the real connection code, the real server, N times. Its report holds no secrets, and is for pasting into a test report. */
    fun runCycleTest(rounds: Int) {
        val servers = _ui.value.account?.servers?.filter { it.online }?.map { it.ref() }.orEmpty()
        if (servers.isEmpty()) {
            _ui.update { it.copy(testReport = "No servers online.") }
            return
        }
        viewModelScope.launch {
            val tester = live.pricechat.kiwivpn.vpn.CycleTest(app.connection)
            tester.run(servers, rounds) { line -> _ui.update { it.copy(testReport = line) } }
        }
    }
}
