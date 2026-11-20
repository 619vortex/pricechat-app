package live.pricechat.kiwivpn.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.RadioButtonDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import live.pricechat.kiwivpn.routing.QuickPicks
import live.pricechat.kiwivpn.routing.RoutingMode
import live.pricechat.kiwivpn.routing.RoutingOnboarding
import live.pricechat.kiwivpn.routing.RoutingPolicy
import live.pricechat.kiwivpn.routing.RoutingPrefs

/**
 * "Choose what uses Kiwi VPN": shown once after the first sign-in, and from Settings at any time. Two choices: All apps, or Only apps I choose. It saves into the same App-Based VPN setting that
 * Settings edits, so the two screens can never disagree. All Apps Except Selected is left to Settings.
 */
@Composable
fun ChooseAppsScreen(saved: RoutingPrefs, firstRun: Boolean, connected: Boolean, onSave: (RoutingPrefs) -> Unit, onClose: (() -> Unit)?) {
    val apps = rememberInstalledApps()
    var draft by remember(saved) { mutableStateOf(saved) }
    var seeded by rememberSaveable { mutableStateOf(false) }
    var query by rememberSaveable { mutableStateOf("") }
    // Start "Only apps I choose" with the messaging apps on this phone switched on, once the list is known, unless something is already chosen.
    LaunchedEffect(apps) {
        val list = apps ?: return@LaunchedEffect
        if (!seeded) {
            draft = RoutingOnboarding.startingDraft(draft, list.map { it.packageName })
            seeded = true
        }
    }
    val onlyChosen = draft.mode == RoutingMode.ONLY_SELECTED
    val picks = remember(apps) { QuickPicks.available(apps.orEmpty().map { it.packageName }) }
    val shown = remember(apps, query) { RoutingPolicy.search(apps.orEmpty(), query) }
    val problem = if (onlyChosen) RoutingPolicy.saveProblem(draft) else null

    // Its own surface: on first run it is shown outside the Scaffold, where text would otherwise default to black on the dark ground.
    Surface(Modifier.fillMaxSize(), color = Kiwi.Ground, contentColor = Kiwi.Text) { Column(Modifier.fillMaxSize()) {
        LazyColumn(Modifier.weight(1f).padding(horizontal = 20.dp)) {
            item {
                Spacer(Modifier.height(18.dp))
                Text("Choose what uses Kiwi VPN", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.semantics { heading() })
                Spacer(Modifier.height(6.dp))
                Text(
                    if (firstRun) "You can change this any time in Settings > App-Based VPN." else "This is the same choice as Settings > App-Based VPN.",
                    style = MaterialTheme.typography.bodySmall,
                    color = Kiwi.Soft,
                )
                onClose?.let { TextButton(onClick = it) { Text("Back") } }
                Spacer(Modifier.height(10.dp))
                Choice(
                    title = "All apps",
                    detail = "Every app on this phone uses Kiwi VPN.",
                    picked = !onlyChosen,
                    badge = null,
                ) { draft = RoutingOnboarding.choose(draft, onlyChosen = false) }
                Choice(
                    title = "Only apps I choose",
                    detail = "Only the apps you pick use Kiwi VPN, for example WhatsApp, Telegram or Signal. Every other app uses your normal internet connection.",
                    picked = onlyChosen,
                    badge = if (picks.isNotEmpty()) "Suggested for messaging" else null,
                ) { draft = RoutingOnboarding.choose(draft, onlyChosen = true) }
            }
            if (onlyChosen) {
                item {
                    Notice("Apps you don't pick get no protection from Kiwi VPN: their traffic and DNS go over your normal connection, as if Kiwi VPN were off.", warn = true)
                    Notice("If Android's \"Block connections without VPN\" is on for Kiwi VPN, Android may cut apps you didn't pick off from the internet instead.", warn = true)
                    Notice("Kiwi VPN itself always uses the VPN, so it can check your connection. It isn't listed.")
                    if (picks.isNotEmpty()) {
                        Spacer(Modifier.height(8.dp))
                        Text("Messaging apps on this phone", style = MaterialTheme.typography.titleSmall, color = Kiwi.LimeBright)
                        Spacer(Modifier.height(4.dp))
                        QuickPickChips(picks, draft.selected) { draft = draft.copy(selected = it) }
                    }
                    Spacer(Modifier.height(10.dp))
                    AppSearchField(query) { query = it }
                    Row(Modifier.fillMaxWidth().padding(top = 6.dp), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Text(RoutingPolicy.countText(draft), style = MaterialTheme.typography.labelLarge, color = Kiwi.LimeBright)
                        if (draft.selected.isNotEmpty()) TextButton(onClick = { draft = draft.withListCleared() }) { Text("Clear") }
                    }
                    when {
                        apps == null -> Text("Loading your apps", color = Kiwi.Soft)
                        apps.isEmpty() -> Text("No apps were found.", color = Kiwi.Warn)
                        shown.isEmpty() -> Text("No app matches \"$query\".", color = Kiwi.Soft)
                    }
                }
                items(shown, key = { it.packageName }) { app ->
                    AppRow(app, draft.usesVpn(app.packageName)) { use -> draft = draft.withUsesVpn(app.packageName, use) }
                }
            }
            item { Spacer(Modifier.height(12.dp)) }
        }
        Column(Modifier.fillMaxWidth().background(Kiwi.Forest).padding(horizontal = 20.dp, vertical = 10.dp)) {
            problem?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = Kiwi.Warn, modifier = Modifier.padding(bottom = 6.dp)) }
            if (connected) Text("Saving reconnects the VPN to apply this. For a few seconds traffic may stop or use your normal connection.", style = MaterialTheme.typography.bodySmall, color = Kiwi.Soft, modifier = Modifier.padding(bottom = 6.dp))
            Button(
                onClick = { onSave(draft) },
                enabled = problem == null && apps != null,
                modifier = Modifier.fillMaxWidth().height(48.dp),
            ) { Text(RoutingOnboarding.saveLabel(draft)) }
        }
    } }
}

@Composable
private fun Choice(title: String, detail: String, picked: Boolean, badge: String?, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(bottom = 10.dp).clip(RoundedCornerShape(14.dp))
            .background(if (picked) Kiwi.Forest2 else Kiwi.Forest)
            .border(if (picked) 2.dp else 1.dp, if (picked) Kiwi.Lime else Kiwi.Line, RoundedCornerShape(14.dp))
            .selectable(selected = picked, role = Role.RadioButton, onClick = onClick)
            .padding(14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(selected = picked, onClick = null, colors = RadioButtonDefaults.colors(selectedColor = Kiwi.Lime))
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            badge?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = Kiwi.LimeBright) }
            Text(detail, style = MaterialTheme.typography.bodySmall, color = Kiwi.Soft)
        }
    }
}
