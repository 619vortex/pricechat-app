package live.pricechat.kiwivpn.ui

import android.content.Intent
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import live.pricechat.kiwivpn.AppViewModel
import live.pricechat.kiwivpn.R
import live.pricechat.kiwivpn.UiState
import live.pricechat.kiwivpn.routing.RoutingOnboarding

private enum class Tab(val label: String, val icon: Int) {
    HOME("Home", R.drawable.ic_nav_home),
    LOCATIONS("Locations", R.drawable.ic_nav_places),
    ACCOUNT("Account", R.drawable.ic_nav_account),
    SETTINGS("Settings", R.drawable.ic_nav_settings),
}

@Composable
fun KiwiRoot(model: AppViewModel, openBrowser: (String) -> Unit, openUrl: (String) -> Unit, openIntent: (Intent) -> Unit, connect: () -> Unit) {
    val ui by model.ui.collectAsStateWithLifecycle()
    Box(Modifier.fillMaxSize().background(Kiwi.Ground)) {
        if (!ui.signedIn) Welcome(ui, onSignIn = { model.signIn(openBrowser) }, onCancel = model::cancelSignIn)
        else SignedIn(model, ui, openUrl, openIntent, connect)
    }
}

@Composable
private fun SignedIn(model: AppViewModel, ui: UiState, openUrl: (String) -> Unit, openIntent: (Intent) -> Unit, connect: () -> Unit) {
    var tabName by rememberSaveable { mutableStateOf(Tab.HOME.name) }
    var diagnostics by rememberSaveable { mutableStateOf(false) }
    var appRouting by rememberSaveable { mutableStateOf(false) }
    var chooseApps by rememberSaveable { mutableStateOf(false) }
    var deleteAccount by rememberSaveable { mutableStateOf(false) }
    val tab = Tab.valueOf(tabName)
    val state by model.connection.collectAsStateWithLifecycle()

    // First sign-in on this phone: "Choose what uses Kiwi VPN", full screen, before anything else.
    if (RoutingOnboarding.shouldShow(ui.signedIn, ui.settings.routingOnboarded)) {
        Box(Modifier.fillMaxSize().safeDrawingPadding()) {
            ChooseAppsScreen(ui.settings.routing, firstRun = true, connected = state.isActive, onSave = model::finishRoutingOnboarding, onClose = null)
        }
        return
    }
    BackHandler(enabled = diagnostics || appRouting || chooseApps || deleteAccount || tab != Tab.HOME) {
        if (deleteAccount) {
            if (!ui.deleting) deleteAccount = false
        } else if (chooseApps) {
            chooseApps = false
        } else if (diagnostics) {
            diagnostics = false
        } else if (appRouting) {
            appRouting = false
        } else {
            tabName = Tab.HOME.name
        }
    }
    Scaffold(
        containerColor = Kiwi.Ground,
        bottomBar = {
            NavigationBar(containerColor = Kiwi.Forest) {
                Tab.entries.forEach { t ->
                    NavigationBarItem(
                        selected = t == tab && !diagnostics && !appRouting && !chooseApps && !deleteAccount,
                        onClick = { if (!ui.deleting) { tabName = t.name; diagnostics = false; appRouting = false; chooseApps = false; deleteAccount = false } },
                        icon = { Icon(painterResource(t.icon), contentDescription = null) },
                        label = { Text(t.label) },
                        colors = NavigationBarItemDefaults.colors(selectedIconColor = Kiwi.Ink, indicatorColor = Kiwi.Lime, selectedTextColor = Kiwi.LimeBright, unselectedIconColor = Kiwi.Muted, unselectedTextColor = Kiwi.Muted),
                    )
                }
            }
        },
    ) { padding ->
        Box(Modifier.padding(padding).fillMaxSize()) {
            when {
                deleteAccount -> DeleteAccountScreen(model, ui, openUrl, onBack = { deleteAccount = false })
                chooseApps -> ChooseAppsScreen(ui.settings.routing, firstRun = false, connected = state.isActive, onSave = { model.finishRoutingOnboarding(it); chooseApps = false }, onClose = { chooseApps = false })
                diagnostics -> DiagnosticsScreen(model, ui, state, onBack = { diagnostics = false })
                appRouting -> AppRoutingScreen(model, ui, state, onBack = { appRouting = false }, onChooseAgain = { appRouting = false; chooseApps = true })
                tab == Tab.HOME -> HomeScreen(ui, state, onConnect = connect, onDisconnect = model::disconnect, onStartTrial = model::startTrial, onLocations = { tabName = Tab.LOCATIONS.name })
                tab == Tab.LOCATIONS -> LocationsScreen(ui, state, onSelect = { model.select(it); tabName = Tab.HOME.name })
                tab == Tab.ACCOUNT -> AccountScreen(model, ui, state, openUrl, onDeleteAccount = { deleteAccount = true })
                else -> SettingsScreen(
                    model, ui, openUrl, openIntent,
                    onDiagnostics = { diagnostics = true }, onAppRouting = { appRouting = true }, onChooseApps = { chooseApps = true },
                    onDeleteAccount = { deleteAccount = true }, onSignOut = model::signOut,
                )
            }
        }
    }
}

@Composable
private fun Welcome(ui: UiState, onSignIn: () -> Unit, onCancel: () -> Unit) {
    Column(
        Modifier.fillMaxSize().safeDrawingPadding().padding(28.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Image(painterResource(R.drawable.kiwi), contentDescription = null, Modifier.size(132.dp))
        Spacer(Modifier.height(20.dp))
        Text("KIWI VPN", style = MaterialTheme.typography.displaySmall, color = Kiwi.LimeBright)
        Spacer(Modifier.height(8.dp))
        Text("Protects every app on your phone, or only the apps you choose. Sign in with your Price Chat account, choose a country and connect.", color = Kiwi.Soft, textAlign = TextAlign.Center)
        Spacer(Modifier.height(8.dp))
        BetaChip()
        Spacer(Modifier.height(28.dp))
        if (ui.signingIn) {
            CircularProgressIndicator(color = Kiwi.Lime)
            Spacer(Modifier.height(12.dp))
            Text("Finish signing in in your browser, then come back here.", color = Kiwi.Soft, textAlign = TextAlign.Center)
            TextButton(onClick = onCancel) { Text("Cancel") }
        } else {
            Button(onClick = onSignIn, modifier = Modifier.fillMaxWidth().height(52.dp)) { Text("Sign in") }
            Spacer(Modifier.height(10.dp))
            Text("Opens your browser. Kiwi VPN never sees your password.", style = MaterialTheme.typography.bodySmall, color = Kiwi.Muted, textAlign = TextAlign.Center)
        }
        ui.error?.let {
            Spacer(Modifier.height(14.dp))
            Text(it, color = Kiwi.Warn, textAlign = TextAlign.Center)
        }
        ui.notice?.let {
            Spacer(Modifier.height(14.dp))
            Text(it, color = Kiwi.Soft, textAlign = TextAlign.Center)
        }
    }
}
