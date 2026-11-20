package live.pricechat.kiwivpn.ui

import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import live.pricechat.kiwivpn.routing.AppCatalog
import live.pricechat.kiwivpn.routing.AppIcons
import live.pricechat.kiwivpn.routing.InstalledApp
import live.pricechat.kiwivpn.routing.QuickPick
import live.pricechat.kiwivpn.routing.QuickPicks

/** The apps a person can open, read off the main thread. null while loading. */
@Composable
fun rememberInstalledApps(): List<InstalledApp>? {
    val context = LocalContext.current
    val apps by produceState<List<InstalledApp>?>(null) { value = withContext(Dispatchers.IO) { AppCatalog.load(context) } }
    return apps
}

@Composable
fun AppSearchField(query: String, onChange: (String) -> Unit) {
    OutlinedTextField(
        query,
        onChange,
        Modifier.fillMaxWidth().semantics { contentDescription = "Search apps by name or package" },
        singleLine = true,
        label = { Text("Search apps") },
    )
}

/** One-press choices for the messaging apps on this phone. Nothing is shown for an app that isn't installed. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun QuickPickChips(picks: List<QuickPick>, selected: Set<String>, onChange: (Set<String>) -> Unit) {
    if (picks.isEmpty()) return
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        picks.forEach { pick ->
            val on = QuickPicks.isOn(pick, selected)
            FilterChip(
                selected = on,
                onClick = { onChange(QuickPicks.toggle(pick, selected)) },
                label = { Text(pick.label) },
                leadingIcon = { AppIcon(pick.packages.first(), 18) },
                modifier = Modifier.semantics {
                    contentDescription = "${pick.label} uses Kiwi VPN"
                    stateDescription = if (on) "On" else "Off"
                },
                colors = FilterChipDefaults.filterChipColors(selectedContainerColor = Kiwi.Lime, selectedLabelColor = Kiwi.Ink, labelColor = Kiwi.Text),
            )
        }
    }
}

/** The app's real icon, downsampled to the size shown and cached. A blank square until it loads, so rows don't jump. */
@Composable
fun AppIcon(packageName: String, sizeDp: Int) {
    val context = LocalContext.current
    val px = with(LocalDensity.current) { sizeDp.dp.roundToPx() }.coerceAtLeast(1)
    val icon by produceState<ImageBitmap?>(AppIcons.cached(packageName, px), packageName, px) {
        if (value == null) value = AppIcons.load(context, packageName, px)
    }
    Box(Modifier.size(sizeDp.dp)) {
        icon?.let { Image(it, contentDescription = null, Modifier.size(sizeDp.dp)) }
    }
}

/**
 * One app. The whole row is the switch, so it's a large target, and a screen reader hears "<app>, uses Kiwi VPN, on" as one item. The switch always means Use VPN.
 */
@Composable
fun AppRow(app: InstalledApp, usesVpn: Boolean, onChange: (Boolean) -> Unit) {
    Row(
        Modifier.fillMaxWidth().heightIn(min = 56.dp).clip(RoundedCornerShape(10.dp))
            .toggleable(value = usesVpn, role = Role.Switch, onValueChange = onChange)
            .semantics(mergeDescendants = true) {
                contentDescription = "${app.label}, ${if (usesVpn) "uses Kiwi VPN" else "uses your normal connection"}"
            }
            .padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        AppIcon(app.packageName, 40)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f).clearAndSetSemantics { }) {
            Text(app.label, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(app.packageName, style = MaterialTheme.typography.bodySmall, color = Kiwi.Muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(if (usesVpn) "Uses VPN" else "Normal connection", style = MaterialTheme.typography.labelSmall, color = if (usesVpn) Kiwi.LimeBright else Kiwi.Soft)
        }
        Spacer(Modifier.width(8.dp))
        Switch(
            usesVpn,
            onCheckedChange = null, // the row handles it
            modifier = Modifier.clearAndSetSemantics { },
            colors = SwitchDefaults.colors(checkedTrackColor = Kiwi.Lime, checkedThumbColor = Kiwi.Ink),
        )
    }
}

/** A notice block used on both routing screens. */
@Composable
fun Notice(text: String, warn: Boolean = false) {
    Text(
        text,
        style = MaterialTheme.typography.bodySmall,
        color = if (warn) Kiwi.Warn else Kiwi.Muted,
        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
    )
}
