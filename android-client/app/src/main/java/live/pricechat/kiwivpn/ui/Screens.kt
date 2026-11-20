package live.pricechat.kiwivpn.ui

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.delay
import live.pricechat.kiwivpn.AppViewModel
import live.pricechat.kiwivpn.BuildConfig
import live.pricechat.kiwivpn.UiState
import live.pricechat.kiwivpn.autocall.AutoCallController
import live.pricechat.kiwivpn.log.Diag
import live.pricechat.kiwivpn.model.Server
import live.pricechat.kiwivpn.model.accountStatus
import live.pricechat.kiwivpn.net.BASE_URL
import live.pricechat.kiwivpn.routing.AppCatalog
import live.pricechat.kiwivpn.routing.RoutingMode
import live.pricechat.kiwivpn.routing.RoutingPolicy
import live.pricechat.kiwivpn.vpn.ConnectionState
import java.util.Locale

@Composable
private fun Page(title: String, content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 18.dp)) {
        Text(title, style = MaterialTheme.typography.headlineSmall)
        Spacer(Modifier.height(14.dp))
        content()
    }
}

@Composable
private fun Card(content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxWidth().padding(bottom = 12.dp).clip(RoundedCornerShape(14.dp)).background(Kiwi.Forest).padding(14.dp), content = content)
}

@Composable
private fun Fact(name: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(name, color = Kiwi.Muted, style = MaterialTheme.typography.bodySmall)
        Spacer(Modifier.width(12.dp))
        Text(value, color = Kiwi.Text, style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
private fun Toggle(title: String, detail: String?, on: Boolean, enabled: Boolean = true, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium, color = if (enabled) Kiwi.Text else Kiwi.Muted)
            detail?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Kiwi.Muted) }
        }
        Spacer(Modifier.width(10.dp))
        Switch(on, onChange, enabled = enabled, colors = SwitchDefaults.colors(checkedTrackColor = Kiwi.Lime, checkedThumbColor = Kiwi.Ink))
    }
}

@Composable
private fun LinkRow(title: String, detail: String? = null, onClick: () -> Unit) {
    Column(Modifier.fillMaxWidth().clickable(onClick = onClick).padding(vertical = 10.dp)) {
        Text(title, style = MaterialTheme.typography.titleMedium, color = Kiwi.LimeBright)
        detail?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Kiwi.Muted) }
    }
}

private fun openIntentChooser(context: Context, text: String) {
    val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text)
    context.startActivity(Intent.createChooser(send, "Share your invite link"))
}

fun countryName(code: String): String = if (code.isBlank()) "" else Locale.Builder().setRegion(code.uppercase()).build().displayCountry

// --- Locations -------------------------------------------------------------------------------------------------------

@Composable
fun LocationsScreen(ui: UiState, state: ConnectionState, onSelect: (Server) -> Unit) {
    Page("Locations") {
        val servers = ui.account?.servers.orEmpty()
        if (servers.isEmpty()) Text(if (ui.checked) ui.error ?: "No servers are available right now." else "Loading servers", color = Kiwi.Soft)
        val current = when (state) {
            is ConnectionState.Connected -> state.server.id
            is ConnectionState.Reconnecting -> state.server.id
            is ConnectionState.Connecting -> state.target.id
            else -> ui.selected?.id
        }
        servers.forEach { s ->
            val here = s.id == current
            Row(
                Modifier.fillMaxWidth().padding(bottom = 10.dp).clip(RoundedCornerShape(14.dp)).background(if (here) Kiwi.Forest2 else Kiwi.Forest)
                    .clickable(enabled = s.online && !here) { onSelect(s) }.padding(14.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(flagOf(s.countryCode), style = MaterialTheme.typography.headlineSmall)
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text(countryName(s.countryCode).ifBlank { s.name }, style = MaterialTheme.typography.titleMedium)
                    Text(listOf(s.name, s.region).filter(String::isNotBlank).distinct().joinToString(" · "), style = MaterialTheme.typography.bodySmall, color = Kiwi.Soft)
                }
                Text(
                    when { !s.online -> "Offline"; here -> "Selected"; s.recommended -> "Recommended"; else -> "Online" },
                    style = MaterialTheme.typography.labelSmall,
                    color = if (s.online) Kiwi.LimeBright else Kiwi.Warn,
                )
            }
        }
        Text("Only what the server reports is shown. No speed or load figures are estimated.", style = MaterialTheme.typography.bodySmall, color = Kiwi.Muted)
    }
}

// --- Account ---------------------------------------------------------------------------------------------------------

@Composable
fun AccountScreen(model: AppViewModel, ui: UiState, state: ConnectionState, openUrl: (String) -> Unit, onDeleteAccount: () -> Unit) {
    val context = LocalContext.current
    val status = accountStatus(ui.account)
    var confirm by remember { mutableStateOf<Long?>(null) }
    val running = when (state) {
        is ConnectionState.Connected -> state.server.id
        is ConnectionState.Reconnecting -> state.server.id
        else -> null
    }
    Page("Account") {
        ui.passport?.let { p ->
            Card {
                Text(p.displayName.ifBlank { p.username }, style = MaterialTheme.typography.titleMedium)
                Text("Kiwi Passport: ${p.rank.current.name}, level ${p.rank.current.level}", color = Kiwi.LimeBright, style = MaterialTheme.typography.bodySmall)
                Spacer(Modifier.height(8.dp))
                LinearProgressIndicator(progress = { p.rank.progress.coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth(), color = Kiwi.Lime, trackColor = Kiwi.Forest3)
                Spacer(Modifier.height(6.dp))
                Fact("XP", "%,d".format(p.xp))
                p.rank.next?.let { Fact("To ${it.name}", "%,d points".format(p.rank.xpToNext)) }
                p.standing?.let { Fact("Leaderboard", "#${it.position}") }
                if (p.badges.isNotEmpty()) Fact("Achievements", p.badges.joinToString(", ") { it.name })
                p.referral?.let { invite ->
                    Fact("Friends invited", "${invite.joined} joined, ${invite.qualified} verified")
                    LinkRow("Share your invite link") {
                        openIntentChooser(context, invite.link)
                    }
                }
            }
        }
        Card {
            Text(ui.who ?: "Price Chat account", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(4.dp))
            Text(status.label, color = Kiwi.LimeBright)
            status.detail?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Kiwi.Soft) }
            if (status.canStartTrial) {
                Spacer(Modifier.height(10.dp))
                Button(onClick = model::startTrial) { Text("Start free trial") }
            }
            if (ui.account?.payments == false) {
                Spacer(Modifier.height(6.dp))
                Text("Payments aren't available yet. The plan will be \$9.99 for 30 days.", style = MaterialTheme.typography.bodySmall, color = Kiwi.Muted)
            }
        }
        ui.account?.let { account ->
            Card {
                Text("Devices (${account.devices.size} of ${account.maxDevices})", style = MaterialTheme.typography.titleMedium)
                Text("Each country a device uses counts as one.", style = MaterialTheme.typography.bodySmall, color = Kiwi.Muted)
                Spacer(Modifier.height(6.dp))
                account.devices.forEach { d ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(d.name, Modifier.weight(1f))
                        if (d.serverId != running) TextButton(onClick = { confirm = d.id }) { Text("Remove") }
                    }
                }
            }
        }
        OutlinedButton(onClick = model::signOut, modifier = Modifier.fillMaxWidth()) { Text("Sign out") }
        Spacer(Modifier.height(10.dp))
        LinkRow("Manage on pricechat.live") { openUrl("$BASE_URL/vpn") }
        LinkRow("Delete account", "Deletes your Price Chat account and everything tied to it", onClick = onDeleteAccount)
    }
    confirm?.let { id ->
        AlertDialog(
            onDismissRequest = { confirm = null },
            title = { Text("Remove this device?") },
            text = { Text("It stops working straight away. If it's another phone or PC, it will need setting up again.") },
            confirmButton = { TextButton(onClick = { model.removeDevice(id); confirm = null }) { Text("Remove") } },
            dismissButton = { TextButton(onClick = { confirm = null }) { Text("Keep") } },
        )
    }
}

// --- Settings --------------------------------------------------------------------------------------------------------

@Composable
fun SettingsScreen(
    model: AppViewModel,
    ui: UiState,
    openUrl: (String) -> Unit,
    openIntent: (Intent) -> Unit,
    onDiagnostics: () -> Unit,
    onAppRouting: () -> Unit,
    onChooseApps: () -> Unit,
    onDeleteAccount: () -> Unit,
    onSignOut: () -> Unit,
) {
    val s = ui.settings
    Page("Settings") {
        Card {
            Toggle("Connect when the app opens", "Connects to your last country each time you open Kiwi VPN.", s.autoConnect) { on -> model.changeSettings { it.copy(autoConnect = on) } }
            Toggle("Show the country in the notification", "Otherwise the notification only says you're protected.", s.notificationShowsCountry) { on -> model.changeSettings { it.copy(notificationShowsCountry = on) } }
        }
        Card {
            Text("Always-on VPN", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.height(4.dp))
            Text("Android can keep Kiwi VPN running from startup, and can block all traffic when the VPN isn't connected. Open VPN settings, press the gear beside Kiwi VPN, and turn on Always-on VPN and, if you want it, Block connections without VPN.", style = MaterialTheme.typography.bodySmall, color = Kiwi.Soft)
            Text("Those are Android's own controls. Kiwi VPN doesn't have a kill switch of its own, and this combination hasn't finished testing.", style = MaterialTheme.typography.bodySmall, color = Kiwi.Warn)
            LinkRow("Open VPN settings") { openIntent(Intent(Settings.ACTION_VPN_SETTINGS)) }
        }
        if (ui.capabilities["app_routing_enabled"] != false) {
            Card {
                LinkRow("App-Based VPN", RoutingPolicy.modeName(s.routing.mode) + if (s.routing.mode == RoutingMode.ALL_APPS) "" else ": " + RoutingPolicy.countText(s.routing), onClick = onAppRouting)
                LinkRow("Choose what uses Kiwi VPN", "Quick setup: all apps, or only apps you choose, such as WhatsApp, Telegram or Signal", onClick = onChooseApps)
            }
        }
        if (ui.capabilities["auto_call_enabled"] != false) AutoCallCard(model, ui, openIntent)
        Card {
            LinkRow("Connection diagnostics", onClick = onDiagnostics)
            LinkRow("Privacy policy") { openUrl("$BASE_URL/vpn/privacy") }
            LinkRow("Support") { openUrl("$BASE_URL/contact") }
            Fact("App version", BuildConfig.VERSION_NAME)
            Fact("Build", BuildConfig.GIT_COMMIT.take(12))
        }
        Card {
            Text("Account", style = MaterialTheme.typography.titleMedium)
            LinkRow("Delete account", "Deletes your Price Chat account, Kiwi VPN devices and trial, and erases this phone's copy", onClick = onDeleteAccount)
        }
        OutlinedButton(onClick = onSignOut, modifier = Modifier.fillMaxWidth()) { Text("Sign out") }
    }
}

@Composable
private fun AutoCallCard(model: AppViewModel, ui: UiState, openIntent: (Intent) -> Unit) {
    val context = LocalContext.current
    val s = ui.settings
    val status by model.autoCallStatus.collectAsStateWithLifecycle()
    var consent by remember { mutableStateOf(false) }
    // Re-read when the screen is shown again after a trip to Android's settings
    var access by remember { mutableStateOf(model.hasNotificationAccess()) }
    LaunchedEffect(Unit) {
        while (true) {
            access = model.hasNotificationAccess()
            delay(1500)
        }
    }
    fun installed(vararg names: String) = names.any {
        try { context.packageManager.getPackageInfo(it, 0); true } catch (_: PackageManager.NameNotFoundException) { false }
    }
    Card {
        Toggle("Auto-Call VPN", "Experimental. Connects when a WhatsApp or Telegram call is detected, and disconnects 15 seconds after it ends.", s.autoCallEnabled) { on ->
            if (on && !s.autoCallConsented) {
                consent = true
            } else {
                model.changeSettings { it.copy(autoCallEnabled = on) }
            }
        }
        if (s.autoCallEnabled) {
            if (!access) {
                Text("Kiwi VPN needs Notification access to notice calls.", color = Kiwi.Warn, style = MaterialTheme.typography.bodySmall)
                LinkRow("Open Notification access") { openIntent(AutoCallController.accessSettings()) }
                Text("If Android says it's a restricted setting: open the app's page in Settings, use the menu at the top right, choose Allow restricted settings, and try again. That applies to apps installed from an APK.", style = MaterialTheme.typography.bodySmall, color = Kiwi.Muted)
                LinkRow("Open this app's page in Settings") { openIntent(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${context.packageName}"))) }
            }
            Toggle("WhatsApp", if (installed("com.whatsapp", "com.whatsapp.w4b")) null else "Not installed on this phone", s.autoCallWhatsApp) { on -> model.changeSettings { it.copy(autoCallWhatsApp = on) } }
            Toggle("Telegram", if (installed("org.telegram.messenger", "org.telegram.messenger.web", "org.thunderdog.challegram")) null else "Not installed on this phone", s.autoCallTelegram) { on -> model.changeSettings { it.copy(autoCallTelegram = on) } }
            Fact("Status", status.message)
            RoutingPolicy.autoCallWarnings(s.routing, s.autoCallWhatsApp, s.autoCallTelegram) { AppCatalog.isInstalled(context, it) }.forEach {
                Text(it, style = MaterialTheme.typography.bodySmall, color = Kiwi.Warn)
            }
        }
        Spacer(Modifier.height(4.dp))
        Text("Not yet proven on real phones. It can only act after the call has started, so the start of a call can happen before the VPN is up, and turning a VPN on can interrupt a call. Don't rely on it for your safety.", style = MaterialTheme.typography.bodySmall, color = Kiwi.Warn)
    }
    if (consent) {
        AlertDialog(
            onDismissRequest = { consent = false },
            title = { Text("Let Kiwi VPN read notifications?") },
            text = {
                Text(
                    "To notice calls, Android has to give Kiwi VPN access to the notifications on this phone.\n\n" +
                        "It looks only at notifications from WhatsApp and Telegram, and only at whether they're a call. It doesn't read names, numbers or messages. Nothing about your notifications or calls is saved or leaves this phone.\n\n" +
                        "You can turn this off here, or withdraw access in Android's settings, at any time.",
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    consent = false
                    model.changeSettings { it.copy(autoCallEnabled = true, autoCallConsented = true) }
                    if (!model.hasNotificationAccess()) openIntent(AutoCallController.accessSettings())
                }) { Text("I agree") }
            },
            dismissButton = { TextButton(onClick = { consent = false }) { Text("Not now") } },
        )
    }
}

// --- Diagnostics -----------------------------------------------------------------------------------------------------

@Composable
fun DiagnosticsScreen(model: AppViewModel, ui: UiState, state: ConnectionState, onBack: () -> Unit) {
    val context = LocalContext.current
    val traces by model.autoCallTraces.collectAsStateWithLifecycle()
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) {
        while (true) {
            now = System.currentTimeMillis()
            delay(1000)
        }
    }
    val link = (state as? ConnectionState.Connected)?.link ?: (state as? ConnectionState.Reconnecting)?.link
    Page("Connection diagnostics") {
        TextButton(onClick = onBack) { Text("Back to Settings") }
        Card {
            Fact("Tunnel state", statusWord(state))
            Fact("App routing on the interface", link?.let { RoutingPolicy.modeName(it.routing.mode) + if (it.routing.chosenCount > 0) " (${it.routing.chosenCount})" else "" } ?: "None")
            Fact("Endpoint", link?.endpoint?.ifBlank { null } ?: "None")
            Fact("Last handshake", link?.lastHandshakeMs?.takeIf { it > 0 }?.let { "${((now - it) / 1000).coerceAtLeast(0)} s ago" } ?: "None")
            Fact("Received", bytes(link?.rxBytes))
            Fact("Sent", bytes(link?.txBytes))
            val exit = link?.publicIp
            Fact("Public IPv4", if (exit == null) "Not checked" else if (link.ipMatchesServer) "$exit (the server's)" else "$exit (not the server's)")
            (state as? ConnectionState.Failed)?.let { Fact("Error", it.message) }
        }
        Card {
            Text("DNS and always-on, as Android reports them", style = MaterialTheme.typography.titleMedium)
            val network = context.getSystemService(ConnectivityManager::class.java)
            val props = network.getLinkProperties(network.activeNetwork)
            Fact("Tunnel's DNS", link?.dns?.joinToString(", ")?.ifBlank { null } ?: "None")
            Fact("Active network's DNS", props?.dnsServers?.joinToString(", ") { it.hostAddress ?: "?" }?.ifBlank { null } ?: "None")
            if (Build.VERSION.SDK_INT >= 28) Fact("Android Private DNS", if (props?.isPrivateDnsActive == true) "On: it can use its own resolver" else "Off")
            val always = model.alwaysOn()
            Fact("Always-on VPN", always?.first?.let { if (it) "On" else "Off" } ?: "Not known")
            Fact("Block without VPN", always?.second?.let { if (it) "On" else "Off" } ?: "Not known")
            Text("This shows settings, not whether anything leaks. See the DNS and IPv6 tests in the test plan.", style = MaterialTheme.typography.bodySmall, color = Kiwi.Muted)
        }
        if (BuildConfig.DEBUG) {
            Card {
                Text("Reconnect test (debug builds)", style = MaterialTheme.typography.titleMedium)
                Text("Connects and disconnects 20 times, switching between countries. Takes several minutes.", style = MaterialTheme.typography.bodySmall, color = Kiwi.Muted)
                Button(onClick = { model.runCycleTest(20) }) { Text("Run 20 cycles") }
                ui.testReport?.let { Text(it, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall, color = Kiwi.Soft) }
            }
        }
        Card {
            Text("What Auto-Call VPN saw", style = MaterialTheme.typography.titleMedium)
            Text("In memory on this phone, for testing. Only Android's call signals: no names, numbers or messages.", style = MaterialTheme.typography.bodySmall, color = Kiwi.Muted)
            if (traces.isEmpty()) Text("Nothing yet.", color = Kiwi.Soft)
            traces.asReversed().forEach {
                Text("${it.app} ${it.event}: ${it.classified}\ncategory=${it.category} ongoing=${it.ongoing} type=${it.callType} style=${it.template} channel=${it.channelId}", fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall, color = Kiwi.Soft, modifier = Modifier.padding(vertical = 3.dp))
            }
        }
        Card {
            Text("Recent events", style = MaterialTheme.typography.titleMedium)
            Diag.recent().takeLast(40).asReversed().forEach { Text(it, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodySmall, color = Kiwi.Soft) }
        }
    }
}

private fun bytes(n: Long?): String = when {
    n == null -> "None"
    n < 1024 -> "$n B"
    n < 1024 * 1024 -> "${n / 1024} KB"
    else -> "%.1f MB".format(n / (1024.0 * 1024.0))
}
