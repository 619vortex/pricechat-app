package live.pricechat.kiwivpn.ui

import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp

/** The asset holding the notices the open-source licences require in the app's binary distribution (Apache-2.0, MIT, BSD). */
const val LICENCES_ASSET = "licences.txt"

/** Settings > Open-source licences: the bundled notices, read from the APK, nothing fetched. */
@Composable
fun LicencesDialog(onDismiss: () -> Unit) {
    val context = LocalContext.current
    val text = remember {
        runCatching { context.assets.open(LICENCES_ASSET).bufferedReader().use { it.readText() } }
            .getOrDefault("The licence notices couldn't be read.")
    }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Open-source licences") },
        text = { Text(text, style = MaterialTheme.typography.bodySmall, modifier = Modifier.heightIn(max = 480.dp).verticalScroll(rememberScrollState())) },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } },
    )
}
