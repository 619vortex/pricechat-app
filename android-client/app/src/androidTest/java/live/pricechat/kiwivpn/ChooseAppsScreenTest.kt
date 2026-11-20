package live.pricechat.kiwivpn

import android.graphics.Bitmap
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performImeAction
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.hasScrollAction
import androidx.compose.ui.test.hasContentDescription
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import live.pricechat.kiwivpn.routing.AppCatalog
import live.pricechat.kiwivpn.routing.RoutingMode
import live.pricechat.kiwivpn.routing.RoutingPrefs
import live.pricechat.kiwivpn.ui.ChooseAppsScreen
import live.pricechat.kiwivpn.ui.KiwiTheme
import org.junit.Assert.assertEquals
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/**
 * The real "Choose what uses Kiwi VPN" screen on the device, with the device's real installed apps and icons. Needs WhatsApp and Telegram installed (the kiwi36 emulator has stand-ins with those
 * package names). No account, server or VPN is involved. Screenshots go to the app's external files folder, under evidence/.
 */
@RunWith(AndroidJUnit4::class)
class ChooseAppsScreenTest {
    @get:Rule val compose = createComposeRule()
    private val context = InstrumentationRegistry.getInstrumentation().targetContext

    private fun shot(name: String) {
        compose.waitForIdle()
        val bitmap = InstrumentationRegistry.getInstrumentation().uiAutomation.takeScreenshot() ?: return
        val dir = File(context.getExternalFilesDir(null), "evidence").apply { mkdirs() }
        File(dir, "$name.png").outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
    }

    @Test fun only_apps_i_choose_with_quick_picks_search_and_save() {
        val installed = AppCatalog.load(context).map { it.packageName }
        assumeTrue("Needs WhatsApp and Telegram installed", "com.whatsapp" in installed && "org.telegram.messenger" in installed)
        var saved: RoutingPrefs? = null
        compose.setContent { KiwiTheme { ChooseAppsScreen(RoutingPrefs(), firstRun = true, connected = false, onSave = { saved = it }, onClose = null) } }

        compose.onNodeWithText("Choose what uses Kiwi VPN").assertExists()
        compose.waitUntil(10_000) { compose.onAllNodes(hasText("Use Kiwi VPN for all apps")).fetchSemanticsNodes().isNotEmpty() }
        shot("1-choices")

        compose.onNodeWithText("Only apps I choose").performClick()
        compose.onNodeWithText("Messaging apps on this phone").assertExists()
        // The installed messengers start switched on
        compose.onNodeWithText("Use Kiwi VPN for 2 apps").assertIsEnabled()
        shot("2-only-apps-suggested")

        compose.onNodeWithText("Clear").performClick()
        compose.onNodeWithText("Use Kiwi VPN for 0 apps").assertIsNotEnabled()
        compose.onNodeWithText("No apps are selected", substring = true).assertExists()

        // The Telegram quick pick
        compose.onNodeWithContentDescription("Telegram uses Kiwi VPN").performClick()
        compose.onNodeWithText("Use Kiwi VPN for 1 app").assertIsEnabled()

        // Search narrows the list; a row toggles its app
        compose.onNodeWithContentDescription("Search apps by name or package").performTextInput("whats")
        compose.onNodeWithContentDescription("Search apps by name or package").performImeAction() // closes the keyboard
        compose.onNode(hasScrollAction()).performScrollToNode(hasContentDescription("uses your normal connection", substring = true))
        shot("3-search-whats")
        // The stand-in app here is labelled "Call test fixture", so the row is found by its state, the only match left after the search. Clicked through its accessibility action, as TalkBack would.
        compose.onNodeWithContentDescription("uses your normal connection", substring = true).performSemanticsAction(SemanticsActions.OnClick)
        compose.onNodeWithText("Use Kiwi VPN for 2 apps").assertIsEnabled().performClick()
        compose.waitForIdle()

        assertEquals(RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf("org.telegram.messenger", "com.whatsapp")), saved)
    }

    @Test fun all_apps_saves_all_apps() {
        var saved: RoutingPrefs? = null
        compose.setContent { KiwiTheme { ChooseAppsScreen(RoutingPrefs(), firstRun = true, connected = false, onSave = { saved = it }, onClose = null) } }
        compose.waitUntil(10_000) { compose.onAllNodes(hasText("Use Kiwi VPN for all apps")).fetchSemanticsNodes().isNotEmpty() }
        compose.onNodeWithText("Use Kiwi VPN for all apps").performClick()
        compose.waitForIdle()
        assertEquals(RoutingMode.ALL_APPS, saved?.mode)
    }
}
