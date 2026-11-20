package live.pricechat.kiwivpn.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.unit.dp
import live.pricechat.kiwivpn.AppViewModel
import live.pricechat.kiwivpn.UiState
import live.pricechat.kiwivpn.account.AccountDeletion
import live.pricechat.kiwivpn.net.ACCOUNT_DELETION_WEB_URL

/** Settings or Account > Delete account. Explains what goes, asks for the username typed exactly, and calls the server. The phone is erased only after the server confirms. */
@Composable
fun DeleteAccountScreen(model: AppViewModel, ui: UiState, openUrl: (String) -> Unit, onBack: () -> Unit) {
    var typed by rememberSaveable { mutableStateOf("") }
    val username = ui.username
    val ready = model.canConfirmDeletion(typed) && !ui.deleting
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 18.dp)) {
        Text("Delete your account", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.semantics { heading() })
        TextButton(onClick = onBack, enabled = !ui.deleting) { Text("Back") }
        Text("This deletes your Price Chat account for good. It can't be undone.", color = Kiwi.Warn, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(10.dp))
        Section("What's deleted", AccountDeletion.WHAT_IS_DELETED)
        Section("What's kept", AccountDeletion.WHAT_IS_KEPT)
        Text(AccountDeletion.ON_THIS_PHONE, style = MaterialTheme.typography.bodySmall, color = Kiwi.Soft)
        Spacer(Modifier.height(14.dp))
        if (username == null) {
            Text("Your username hasn't loaded yet. Check your connection; this screen updates when it does.", color = Kiwi.Soft, style = MaterialTheme.typography.bodySmall)
        } else {
            Text("To confirm, type your username: $username", style = MaterialTheme.typography.bodyMedium)
            Spacer(Modifier.height(6.dp))
            OutlinedTextField(
                typed,
                { typed = it; model.clearDeleteError() },
                Modifier.fillMaxWidth(),
                singleLine = true,
                enabled = !ui.deleting,
                label = { Text("Username") },
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.None, autoCorrectEnabled = false, imeAction = ImeAction.Done),
            )
        }
        ui.deleteError?.let {
            Spacer(Modifier.height(8.dp))
            Text(it, color = Kiwi.Warn)
        }
        Spacer(Modifier.height(12.dp))
        Button(
            onClick = { model.deleteAccount(typed) },
            enabled = ready,
            colors = ButtonDefaults.buttonColors(containerColor = Kiwi.Warn, contentColor = Kiwi.Ink),
            modifier = Modifier.fillMaxWidth().height(48.dp),
        ) {
            if (ui.deleting) CircularProgressIndicator(Modifier.height(20.dp), color = Kiwi.Ink, strokeWidth = 2.dp) else Text("Delete my account")
        }
        Spacer(Modifier.height(16.dp))
        Text("You can also delete it on the website: open pricechat.live/vpn/delete-account and sign in.", style = MaterialTheme.typography.bodySmall, color = Kiwi.Muted)
        TextButton(onClick = { openUrl(ACCOUNT_DELETION_WEB_URL) }) { Text("Open pricechat.live/vpn/delete-account") }
    }
}

@Composable
private fun Section(title: String, lines: List<String>) {
    Column(Modifier.fillMaxWidth().padding(bottom = 10.dp).clip(RoundedCornerShape(14.dp)).background(Kiwi.Forest).padding(14.dp)) {
        Text(title, style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.height(4.dp))
        lines.forEach { Text("• $it", style = MaterialTheme.typography.bodySmall, color = Kiwi.Soft, modifier = Modifier.padding(vertical = 2.dp)) }
    }
}
