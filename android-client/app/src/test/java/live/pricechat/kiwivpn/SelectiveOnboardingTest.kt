package live.pricechat.kiwivpn

import live.pricechat.kiwivpn.routing.InstalledApp
import live.pricechat.kiwivpn.routing.LruMemo
import live.pricechat.kiwivpn.routing.QuickPicks
import live.pricechat.kiwivpn.routing.RoutingError
import live.pricechat.kiwivpn.routing.RoutingMode
import live.pricechat.kiwivpn.routing.RoutingOnboarding
import live.pricechat.kiwivpn.routing.RoutingPolicy
import live.pricechat.kiwivpn.routing.RoutingPrefs
import live.pricechat.kiwivpn.security.AppSettings
import live.pricechat.kiwivpn.security.Vault
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files

class SelectiveOnboardingTest {
    private val own = "live.pricechat.kiwivpn"
    private val phone = listOf(
        InstalledApp("Chrome", "com.android.chrome"),
        InstalledApp("Signal", "org.thoughtcrime.securesms"),
        InstalledApp("Telegram", "org.telegram.messenger"),
        InstalledApp("Telegram X", "org.thunderdog.challegram"),
        InstalledApp("WhatsApp", "com.whatsapp"),
        InstalledApp("YouTube", "com.google.android.youtube"),
    )
    private val installed = phone.map { it.packageName }

    // --- search ---

    @Test fun search_matches_label_or_package_ignoring_case_and_spaces() {
        assertEquals(listOf("org.telegram.messenger", "org.thunderdog.challegram"), RoutingPolicy.search(phone, "  TELEGRAM ").map { it.packageName })
        assertEquals(listOf("org.thoughtcrime.securesms"), RoutingPolicy.search(phone, "securesms").map { it.packageName })
        assertEquals(listOf("com.whatsapp"), RoutingPolicy.search(phone, "whats").map { it.packageName })
    }

    @Test fun an_empty_search_shows_everything_and_a_miss_shows_nothing() {
        assertEquals(phone, RoutingPolicy.search(phone, ""))
        assertEquals(phone, RoutingPolicy.search(phone, "   "))
        assertTrue(RoutingPolicy.search(phone, "zzz-not-an-app").isEmpty())
    }

    @Test fun searching_never_changes_what_is_selected() {
        val prefs = RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf("com.whatsapp", "com.android.chrome"))
        RoutingPolicy.search(phone, "tele")
        assertEquals(setOf("com.whatsapp", "com.android.chrome"), prefs.selected)
        // "Select all" acts only on what the search shows
        val shown = RoutingPolicy.search(phone, "tele").map { it.packageName }
        assertEquals(setOf("com.whatsapp", "com.android.chrome", "org.telegram.messenger", "org.thunderdog.challegram"), prefs.withAllListed(shown).selected)
    }

    // --- quick picks ---

    @Test fun quick_picks_show_only_installed_messaging_apps_in_a_fixed_order() {
        val picks = QuickPicks.available(installed)
        assertEquals(listOf("WhatsApp", "Telegram", "Signal"), picks.map { it.label })
        assertEquals("Only the Telegram builds that are installed", listOf("org.telegram.messenger", "org.thunderdog.challegram"), picks.first { it.label == "Telegram" }.packages)
        assertTrue("No WhatsApp Business here", picks.none { it.label == "WhatsApp Business" })
        assertTrue(QuickPicks.available(listOf("com.android.chrome")).isEmpty())
    }

    @Test fun the_quick_pick_packages_are_the_ones_the_owner_named() {
        val all = QuickPicks.ALL.flatMap { it.packages }.toSet()
        assertEquals(setOf("com.whatsapp", "com.whatsapp.w4b", "org.telegram.messenger", "org.telegram.messenger.web", "org.thunderdog.challegram", "org.thoughtcrime.securesms"), all)
        assertTrue(all.all(RoutingPolicy::isValidPackage))
    }

    @Test fun pressing_a_quick_pick_adds_or_removes_all_its_installed_packages_and_nothing_else() {
        val telegram = QuickPicks.available(installed).first { it.label == "Telegram" }
        val start = setOf("com.android.chrome")
        val on = QuickPicks.toggle(telegram, start)
        assertEquals(setOf("com.android.chrome", "org.telegram.messenger", "org.thunderdog.challegram"), on)
        assertTrue(QuickPicks.isOn(telegram, on))
        val off = QuickPicks.toggle(telegram, on)
        assertEquals(start, off)
        assertFalse(QuickPicks.isOn(telegram, off))
    }

    @Test fun a_pick_with_only_some_of_its_apps_selected_reads_off_and_pressing_it_selects_the_rest() {
        val telegram = QuickPicks.available(installed).first { it.label == "Telegram" }
        val partly = setOf("org.telegram.messenger")
        assertFalse(QuickPicks.isOn(telegram, partly))
        assertEquals(setOf("org.telegram.messenger", "org.thunderdog.challegram"), QuickPicks.toggle(telegram, partly))
    }

    @Test fun only_messaging_apps_picked_route_exactly_those_and_kiwi() {
        var prefs = RoutingOnboarding.choose(RoutingPrefs(), onlyChosen = true)
        prefs = prefs.copy(selected = QuickPicks.toggle(QuickPicks.available(installed).first { it.label == "WhatsApp" }, prefs.selected))
        val plan = RoutingPolicy.plan(prefs, own) { it in installed }
        assertEquals(RoutingMode.ONLY_SELECTED, plan.mode)
        assertEquals(listOf("com.whatsapp", own), plan.apps)
        assertEquals(1, plan.chosenCount)
    }

    // --- onboarding ---

    @Test fun onboarding_shows_once_after_sign_in_and_never_when_signed_out() {
        assertFalse(RoutingOnboarding.shouldShow(signedIn = false, onboarded = false))
        assertTrue(RoutingOnboarding.shouldShow(signedIn = true, onboarded = false))
        assertFalse(RoutingOnboarding.shouldShow(signedIn = true, onboarded = true))
    }

    @Test fun only_apps_i_choose_starts_with_the_installed_messengers_switched_on() {
        val draft = RoutingOnboarding.startingDraft(RoutingPrefs(), installed)
        assertEquals(RoutingMode.ALL_APPS, draft.mode)
        assertEquals(setOf("com.whatsapp", "org.telegram.messenger", "org.thunderdog.challegram", "org.thoughtcrime.securesms"), draft.selected)
        assertEquals("Nothing to suggest on a phone with no messengers", emptySet<String>(), RoutingOnboarding.startingDraft(RoutingPrefs(), listOf("com.android.chrome")).selected)
    }

    @Test fun a_list_already_chosen_is_kept_as_it_is() {
        val saved = RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf("com.android.chrome"), excluded = setOf("com.bank"))
        assertEquals(saved, RoutingOnboarding.startingDraft(saved, installed))
    }

    @Test fun choosing_all_apps_keeps_the_lists_for_later_and_routes_everything() {
        val draft = RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf("com.whatsapp"), excluded = setOf("com.bank"))
        val all = RoutingOnboarding.choose(draft, onlyChosen = false)
        assertEquals(RoutingMode.ALL_APPS, all.mode)
        assertEquals(setOf("com.whatsapp"), all.selected)
        assertEquals(setOf("com.bank"), all.excluded)
        assertNull(RoutingPolicy.saveProblem(all))
        assertEquals("Use Kiwi VPN for all apps", RoutingOnboarding.saveLabel(all))
    }

    @Test fun save_is_refused_with_nothing_chosen_and_the_label_counts_the_apps() {
        val none = RoutingOnboarding.choose(RoutingPrefs(), onlyChosen = true)
        assertNotNull(RoutingPolicy.saveProblem(none))
        assertThrows(RoutingError::class.java) { RoutingPolicy.plan(none, own) { true } }
        assertEquals("Use Kiwi VPN for 0 apps", RoutingOnboarding.saveLabel(none))
        assertEquals("Use Kiwi VPN for 1 app", RoutingOnboarding.saveLabel(none.copy(selected = setOf("com.whatsapp"))))
        assertEquals("Use Kiwi VPN for 2 apps", RoutingOnboarding.saveLabel(none.copy(selected = setOf("com.whatsapp", "org.telegram.messenger"))))
    }

    // --- persistence: the onboarding and Settings share one stored setting ---

    @Test fun the_choice_and_the_onboarded_flag_survive_a_restart_and_sign_out() {
        val sealer = MemorySealer()
        val dir = Files.createTempDirectory("vault").toFile()
        val first = Vault(File(dir, "vault.bin"), sealer)
        val chosen = RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf("org.telegram.messenger"))
        first.update { it.copy(session = "kd.t", settings = it.settings.copy(routing = chosen, routingOnboarded = true)) }

        val reopened = Vault(File(dir, "vault.bin"), sealer) // a new process reads the file
        assertEquals(chosen, reopened.read().settings.routing)
        assertTrue(reopened.read().settings.routingOnboarded)

        reopened.forgetAccount()
        assertNull(reopened.read().session)
        assertEquals("Signing out keeps the choice", chosen, reopened.read().settings.routing)
        assertFalse("and doesn't ask again", RoutingOnboarding.shouldShow(true, reopened.read().settings.routingOnboarded))
    }

    @Test fun settings_edits_what_the_onboarding_saved() {
        val vault = tempVault()
        vault.update { it.copy(settings = it.settings.copy(routing = RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf("com.whatsapp")), routingOnboarded = true)) }
        // Settings > App-Based VPN turns Signal on as well
        vault.update { it.copy(settings = it.settings.copy(routing = it.settings.routing.withUsesVpn("org.thoughtcrime.securesms", true))) }
        assertEquals(setOf("com.whatsapp", "org.thoughtcrime.securesms"), vault.read().settings.routing.selected)
    }

    @Test fun an_older_saved_file_without_the_flag_reads_as_not_onboarded() {
        assertFalse(AppSettings().routingOnboarded)
        val vault = tempVault()
        vault.update { it.copy(settings = AppSettings(autoConnect = true)) }
        assertFalse(vault.read().settings.routingOnboarded)
    }

    // --- icon cache ---

    @Test fun the_icon_cache_keeps_the_most_recently_used_and_drops_the_oldest() {
        val memo = LruMemo<String, Int>(3)
        memo.put("a", 1); memo.put("b", 2); memo.put("c", 3)
        assertEquals(1, memo.get("a")) // a is now the most recent
        memo.put("d", 4)
        assertNull("b was least recently used", memo.get("b"))
        assertEquals(listOf("c", "a", "d"), memo.keys())
        assertEquals(3, memo.size())
    }
}
