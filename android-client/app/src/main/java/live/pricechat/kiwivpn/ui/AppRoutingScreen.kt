package live.pricechat.kiwivpn.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.RadioButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import live.pricechat.kiwivpn.AppViewModel
import live.pricechat.kiwivpn.UiState
import live.pricechat.kiwivpn.routing.AppCatalog
import live.pricechat.kiwivpn.routing.QuickPicks
import live.pricechat.kiwivpn.routing.RoutingMode
import live.pricechat.kiwivpn.routing.RoutingPolicy
import live.pricechat.kiwivpn.vpn.ConnectionState

private data class ModeChoice(val mode: RoutingMode, val detail: String)

private val MODES = listOf(
    ModeChoice(RoutingMode.ALL_APPS, "Every app uses Kiwi VPN. Nothing to choose."),
    ModeChoice(RoutingMode.ONLY_SELECTED, "Only the apps you switch on use Kiwi VPN. All others use your normal connection."),
    ModeChoice(RoutingMode.ALL_EXCEPT, "Every app uses Kiwi VPN, except the ones you switch off."),
)

/** What's really on the interface now, read from the connection, never from what was last pressed. */
private fun activeLine(state: ConnectionState): String = when (state) {
    is ConnectionState.Connected -> "Active now: ${RoutingPolicy.modeName(state.link.routing.mode)}. ${RoutingPolicy.describe(state.link.routing)}"
    is ConnectionState.Reconnecting -> "Active now: ${RoutingPolicy.modeName(state.link.routing.mode)}, while the server isn't answering."
    is ConnectionState.Connecting -> "Connecting. The mode shows as active once it has been applied."
    is ConnectionState.Disconnecting -> "Disconnecting."
    is ConnectionState.Disconnected -> "Not connected. Your saved choice applies the next time you connect."
    is ConnectionState.Failed -> "Not connected: ${state.message}"
}

@Composable
fun AppRoutingScreen(model: AppViewModel, ui: UiState, state: ConnectionState, onBack: () -> Unit, onChooseAgain: () -> Unit) {
    val context = LocalContext.current
    val saved = ui.settings.routing
    var draft by remember(saved) { mutableStateOf(saved) }
    var query by remember { mutableStateOf("") }
    var confirm by remember { mutableStateOf(false) }
    val apps = rememberInstalledApps()
    val picks = remember(apps) { QuickPicks.available(apps.orEmpty().map { it.packageName }) }
    val shown = remember(apps, query) { RoutingPolicy.search(apps.orEmpty(), query) }
    val changed = draft != saved
    val problem = RoutingPolicy.saveProblem(draft)
    val choosing = draft.mode != RoutingMode.ALL_APPS
    val warnings = remember(draft, ui.settings) {
        if (!ui.settings.autoCallEnabled) emptyList()
        else RoutingPolicy.autoCallWarnings(draft, ui.settings.autoCallWhatsApp, ui.settings.autoCallTelegram) { AppCatalog.isInstalled(context, it) }
    }

    Column(Modifier.fillMaxSize()) {
        LazyColumn(Modifier.weight(1f).padding(horizontal = 20.dp)) {
            item {
                Spacer(Modifier.height(18.dp))
                Text("App-Based VPN", style = MaterialTheme.typography.headlineSmall)
                Row {
                    TextButton(onClick = onBack) { Text("Back to Settings") }
                    TextButton(onClick = onChooseAgain) { Text("Quick setup") }
                }
                Text(activeLine(state), style = MaterialTheme.typography.bodySmall, color = Kiwi.Soft)
                Spacer(Modifier.height(12.dp))
            }
            items(MODES, key = { it.mode.name }) { choice ->
                val picked = draft.mode == choice.mode
                Row(
                    Modifier.fillMaxWidth().padding(bottom = 8.dp).clip(RoundedCornerShape(14.dp)).background(if (picked) Kiwi.Forest2 else Kiwi.Forest)
                        .selectable(selected = picked, role = Role.RadioButton, onClick = { draft = draft.copy(mode = choice.mode) }).padding(12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    RadioButton(selected = picked, onClick = null, colors = RadioButtonDefaults.colors(selectedColor = Kiwi.Lime))
                    Spacer(Modifier.width(10.dp))
                    Column {
                        Text(RoutingPolicy.modeName(choice.mode), style = MaterialTheme.typography.titleMedium)
                        Text(choice.detail, style = MaterialTheme.typography.bodySmall, color = Kiwi.Soft)
                    }
                }
            }
            item {
                warnings.forEach { Text(it, style = MaterialTheme.typography.bodySmall, color = Kiwi.Warn, modifier = Modifier.padding(vertical = 3.dp)) }
                if (choosing) {
                    Text(
                        "Apps that aren't using the VPN have no protection from it: their traffic and DNS use your normal connection. If Android's \"Block connections without VPN\" is on, Android may cut them off from the internet instead.",
                        style = MaterialTheme.typography.bodySmall,
                        color = Kiwi.Muted,
                        modifier = Modifier.padding(vertical = 6.dp),
                    )
                    Text("Kiwi VPN itself always stays on the VPN, so it can check your connection.", style = MaterialTheme.typography.bodySmall, color = Kiwi.Muted)
                    Spacer(Modifier.height(8.dp))
                    if (draft.mode == RoutingMode.ONLY_SELECTED && picks.isNotEmpty()) {
                        Text("Messaging apps on this phone", style = MaterialTheme.typography.titleSmall, color = Kiwi.LimeBright)
                        QuickPickChips(picks, draft.selected) { draft = draft.copy(selected = it) }
                        Spacer(Modifier.height(6.dp))
                    }
                    AppSearchField(query) { query = it }
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.SpaceBetween) {
                        Text(RoutingPolicy.countText(draft), style = MaterialTheme.typography.labelSmall, color = Kiwi.LimeBright)
                        Row {
                            TextButton(onClick = { draft = draft.withAllListed(shown.map { it.packageName }) }) { Text(if (draft.mode == RoutingMode.ONLY_SELECTED) "Select all" else "Bypass all") }
                            TextButton(onClick = { draft = draft.withListCleared() }) { Text("Clear selection") }
                        }
                    }
                    Text("Each switch means Use VPN: on uses Kiwi VPN, off uses your normal connection.", style = MaterialTheme.typography.bodySmall, color = Kiwi.Soft)
                    Spacer(Modifier.height(6.dp))
                    if (apps == null) Text("Loading your apps", color = Kiwi.Soft)
                    if (apps?.isEmpty() == true) Text("No apps were found.", color = Kiwi.Warn)
                }
            }
            if (choosing) {
                items(shown, key = { it.packageName }) { app ->
                    AppRow(app, draft.usesVpn(app.packageName)) { use -> draft = draft.withUsesVpn(app.packageName, use) }
                }
            }
            item { Spacer(Modifier.height(10.dp)) }
        }
        Column(Modifier.fillMaxWidth().background(Kiwi.Forest).padding(horizontal = 20.dp, vertical = 10.dp)) {
            problem?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Kiwi.Warn, modifier = Modifier.padding(bottom = 6.dp)) }
            Button(
                onClick = { if (state.isActive) confirm = true else model.saveRouting(draft) },
                enabled = changed && problem == null,
                modifier = Modifier.fillMaxWidth().height(48.dp),
            ) { Text(if (changed) "Save settings" else "Saved") }
        }
    }
    if (confirm) {
        AlertDialog(
            onDismissRequest = { confirm = false },
            title = { Text("Reconnect to apply this?") },
            text = { Text("The VPN reconnects to change which apps use it. For a few seconds, traffic may stop or go over your normal connection.") },
            confirmButton = {
                TextButton(onClick = {
                    confirm = false
                    model.saveRouting(draft)
                }) { Text("Save and reconnect") }
            },
            dismissButton = { TextButton(onClick = { confirm = false }) { Text("Not now") } },
        )
    }
}
