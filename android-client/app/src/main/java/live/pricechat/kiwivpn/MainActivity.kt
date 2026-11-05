package live.pricechat.kiwivpn

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.net.VpnService
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.browser.customtabs.CustomTabsIntent
import androidx.core.content.ContextCompat
import live.pricechat.kiwivpn.ui.KiwiRoot
import live.pricechat.kiwivpn.ui.KiwiTheme

class MainActivity : ComponentActivity() {
    private val model: AppViewModel by viewModels()

    private val vpnConsent = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        if (result.resultCode == RESULT_OK) model.connect()
    }
    private val notifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) { /* the VPN works either way; only the notification depends on it */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            KiwiTheme {
                KiwiRoot(model = model, openBrowser = ::openBrowser, openUrl = ::openUrl, openIntent = ::openIntent, connect = ::connectWithConsent)
            }
        }
    }

    override fun onStart() {
        super.onStart()
        model.load() // the account is refreshed whenever the app comes to the front
    }

    /** Android's own consent screen, then connect. Connect isn't pressed for the person if they decline. */
    private fun connectWithConsent() {
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            notifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        val consent = VpnService.prepare(this)
        if (consent != null) vpnConsent.launch(consent) else model.connect()
    }

    /** The phone's real browser in a Custom Tab, never a WebView: the app can't see what's typed there, and Google's sign-in allows it. */
    private fun openBrowser(url: String) {
        try {
            CustomTabsIntent.Builder().setShowTitle(true).build().launchUrl(this, Uri.parse(url))
        } catch (_: ActivityNotFoundException) {
            openUrl(url)
        }
    }

    private fun openUrl(url: String) = openIntent(Intent(Intent.ACTION_VIEW, Uri.parse(url)))

    private fun openIntent(intent: Intent) {
        try {
            startActivity(intent)
        } catch (_: ActivityNotFoundException) { /* no app for it: nothing useful to do */ }
    }
}
