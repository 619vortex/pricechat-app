package live.pricechat.kiwivpn

import live.pricechat.kiwivpn.routing.InstalledApp
import live.pricechat.kiwivpn.routing.RoutePlan
import live.pricechat.kiwivpn.routing.RoutingError
import live.pricechat.kiwivpn.routing.RoutingMode
import live.pricechat.kiwivpn.routing.RoutingPolicy
import live.pricechat.kiwivpn.routing.RoutingPrefs
import live.pricechat.kiwivpn.vpn.PeerRules
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class AppRoutingTest {
    private val own = "live.pricechat.kiwivpn"
    private val everythingInstalled: (String) -> Boolean = { true }
    private val only = RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf("com.whatsapp", "org.telegram.messenger"))
    private val except = RoutingPrefs(RoutingMode.ALL_EXCEPT, excluded = setOf("com.bank", "ae.gov.app"))

    @Test fun all_apps_has_no_list_and_is_the_default() {
        assertEquals(RoutingMode.ALL_APPS, RoutingPrefs().mode)
        assertEquals(RoutePlan.ALL, RoutingPolicy.plan(RoutingPrefs(selected = setOf("com.a"), excluded = setOf("com.b")), own, everythingInstalled))
    }

    @Test fun only_selected_lists_exactly_those_apps_plus_kiwi_itself() {
        val p = RoutingPolicy.plan(only, own, everythingInstalled)
        assertEquals(RoutingMode.ONLY_SELECTED, p.mode)
        assertEquals(setOf("com.whatsapp", "org.telegram.messenger", own), p.apps.toSet())
        assertEquals("The count is the person's apps, not Kiwi", 2, p.chosenCount)
    }

    @Test fun all_except_lists_exactly_those_and_can_never_exclude_kiwi() {
        val p = RoutingPolicy.plan(except.copy(excluded = except.excluded + own), own, everythingInstalled)
        assertEquals(setOf("com.bank", "ae.gov.app"), p.apps.toSet())
        assertFalse(own in p.apps)
    }

    @Test fun an_empty_only_selected_is_refused_because_android_would_route_every_app() {
        val e = assertThrows(RoutingError::class.java) { RoutingPolicy.plan(RoutingPrefs(RoutingMode.ONLY_SELECTED), own, everythingInstalled) }
        assertTrue(e.message!!.contains("No apps are selected"))
        assertNotNull(RoutingPolicy.saveProblem(RoutingPrefs(RoutingMode.ONLY_SELECTED)))
    }

    @Test fun only_kiwi_in_the_list_counts_as_empty() {
        assertThrows(RoutingError::class.java) { RoutingPolicy.plan(RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf(own)), own, everythingInstalled) }
    }

    @Test fun every_selected_app_gone_is_refused_rather_than_routing_everything() {
        assertThrows(RoutingError::class.java) { RoutingPolicy.plan(only, own) { false } }
    }

    @Test fun an_empty_all_except_is_allowed_and_means_all_apps() {
        val p = RoutingPolicy.plan(RoutingPrefs(RoutingMode.ALL_EXCEPT), own, everythingInstalled)
        assertTrue(p.apps.isEmpty())
        assertNull(RoutingPolicy.saveProblem(RoutingPrefs(RoutingMode.ALL_EXCEPT)))
        assertEquals("All apps use Kiwi VPN", RoutingPolicy.describe(p))
    }

    @Test fun uninstalled_apps_are_left_out_because_android_throws_on_them() {
        val p = RoutingPolicy.plan(only, own) { it == "com.whatsapp" }
        assertEquals(listOf("com.whatsapp", own), p.apps)
        assertEquals(1, p.chosenCount)
    }

    private fun planWith(prefs: RoutingPrefs) = RoutingPolicy.plan(prefs, own, everythingInstalled)

    @Test fun malformed_package_names_never_reach_the_config() {
        val hostile = listOf("com.a, com.b", "com.a\nPostUp = evil", "nodots", "", "1com.a", "com..a", "com.a b", "com.a=b", "a".repeat(300) + ".b")
        for (bad in hostile) assertFalse(bad, RoutingPolicy.isValidPackage(bad))
        assertEquals(listOf("com.ok", own), planWith(RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = hostile.toSet() + "com.ok")).apps)
        assertEquals(listOf("com.ok"), planWith(RoutingPrefs(RoutingMode.ALL_EXCEPT, excluded = hostile.toSet() + "com.ok")).apps)
    }

    @Test fun the_config_never_holds_both_lists_and_they_go_in_the_interface_section() {
        val base = PeerRules.render(KEY_B, peer())
        assertEquals(base, RoutingPolicy.applyTo(base, RoutePlan.ALL))
        assertEquals(base, RoutingPolicy.applyTo(base, planWith(RoutingPrefs(RoutingMode.ALL_EXCEPT))))
        val both = RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf("com.a"), excluded = setOf("com.b"))
        val a = RoutingPolicy.applyTo(base, planWith(both))
        assertTrue(a.contains("IncludedApplications = com.a, $own\n"))
        assertFalse(a.contains("ExcludedApplications"))
        assertFalse("A list from the other mode must not leak in", a.contains("com.b"))
        val b = RoutingPolicy.applyTo(base, planWith(both.copy(mode = RoutingMode.ALL_EXCEPT)))
        assertTrue(b.contains("ExcludedApplications = com.b\n"))
        assertFalse(b.contains("IncludedApplications"))
        assertFalse(b.contains("com.a"))
        for (text in listOf(a, b)) {
            assertTrue(text.indexOf("Applications") in 1 until text.indexOf("[Peer]"))
            assertTrue("Everything else is untouched", text.contains("PrivateKey = $KEY_B") && text.contains("AllowedIPs = 0.0.0.0/0, ::/0"))
        }
    }

    @Test fun the_switch_always_means_use_vpn_in_both_modes() {
        val m2 = RoutingPrefs(RoutingMode.ONLY_SELECTED)
        assertFalse(m2.usesVpn("com.chrome"))
        assertTrue(m2.withUsesVpn("com.chrome", true).usesVpn("com.chrome"))
        assertEquals(setOf("com.chrome"), m2.withUsesVpn("com.chrome", true).selected)
        val m3 = RoutingPrefs(RoutingMode.ALL_EXCEPT)
        assertTrue(m3.usesVpn("com.chrome"))
        assertFalse(m3.withUsesVpn("com.chrome", false).usesVpn("com.chrome"))
        assertEquals(setOf("com.chrome"), m3.withUsesVpn("com.chrome", false).excluded)
        assertTrue(RoutingPrefs().usesVpn("anything"))
    }

    @Test fun switching_mode_keeps_both_lists() {
        val p = RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf("com.a"), excluded = setOf("com.b"))
        for (m in RoutingMode.entries) {
            val moved = p.copy(mode = m)
            assertEquals(setOf("com.a"), moved.selected)
            assertEquals(setOf("com.b"), moved.excluded)
        }
    }

    @Test fun select_all_and_clear_touch_only_the_current_modes_list() {
        val p = RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf("com.a"), excluded = setOf("com.b"))
        assertEquals(setOf("com.a", "com.x", "com.y"), p.withAllListed(listOf("com.x", "com.y")).selected)
        assertEquals(setOf("com.b"), p.withAllListed(listOf("com.x")).excluded)
        assertTrue(p.withListCleared().selected.isEmpty())
        assertEquals(setOf("com.b"), p.withListCleared().excluded)
        val q = p.copy(mode = RoutingMode.ALL_EXCEPT)
        assertTrue(q.withListCleared().excluded.isEmpty())
        assertEquals(setOf("com.a"), q.withListCleared().selected)
        assertEquals(p.copy(mode = RoutingMode.ALL_APPS), p.copy(mode = RoutingMode.ALL_APPS).withListCleared())
    }

    @Test fun the_count_says_what_it_means() {
        assertEquals("2 apps will use the VPN", RoutingPolicy.countText(only))
        assertEquals("2 apps will bypass the VPN", RoutingPolicy.countText(except))
        assertEquals("1 app will use the VPN", RoutingPolicy.countText(RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf("com.a"))))
    }

    @Test fun the_description_never_claims_every_app_when_it_isnt_true() {
        assertTrue(RoutingPolicy.describe(planWith(only)).startsWith("Only 2 apps"))
        assertTrue(RoutingPolicy.describe(planWith(except)).contains("except 2 apps"))
    }

    @Test fun search_finds_by_name_or_package_ignoring_case() {
        val apps = listOf(InstalledApp("WhatsApp", "com.whatsapp"), InstalledApp("Chrome", "com.android.chrome"), InstalledApp("My Bank", "ae.bank.app"))
        assertEquals(listOf("WhatsApp"), RoutingPolicy.search(apps, "whats").map { it.label })
        assertEquals(listOf("My Bank"), RoutingPolicy.search(apps, "AE.BANK").map { it.label })
        assertEquals(3, RoutingPolicy.search(apps, "  ").size)
        assertTrue(RoutingPolicy.search(apps, "zzz").isEmpty())
    }

    @Test fun auto_call_warns_when_only_selected_leaves_out_a_calling_app() {
        val w = RoutingPolicy.autoCallWarnings(RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf("com.chrome")), whatsApp = true, telegram = true, everythingInstalled)
        assertEquals(2, w.size)
        assertTrue(w[0].startsWith("WhatsApp isn't selected"))
        assertTrue(w[1].startsWith("Telegram isn't selected"))
    }

    @Test fun auto_call_warns_when_a_calling_app_bypasses() {
        val w = RoutingPolicy.autoCallWarnings(RoutingPrefs(RoutingMode.ALL_EXCEPT, excluded = setOf("com.whatsapp")), whatsApp = true, telegram = true, everythingInstalled)
        assertEquals(1, w.size)
        assertTrue(w[0].startsWith("WhatsApp bypasses"))
    }

    @Test fun auto_call_stays_quiet_when_nothing_is_wrong() {
        val installed: (String) -> Boolean = { it == "com.whatsapp" }
        assertTrue(RoutingPolicy.autoCallWarnings(RoutingPrefs(), true, true, everythingInstalled).isEmpty())
        assertTrue(RoutingPolicy.autoCallWarnings(only, true, true, everythingInstalled).isEmpty())
        assertTrue("Telegram isn't on this phone", RoutingPolicy.autoCallWarnings(RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf("com.whatsapp")), true, true, installed).isEmpty())
        assertTrue("Not chosen for Auto-Call", RoutingPolicy.autoCallWarnings(RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf("com.chrome")), false, false, everythingInstalled).isEmpty())
    }

    @Test fun choices_survive_a_restart_in_the_encrypted_file_and_arent_readable_on_disk() {
        val dir = java.nio.file.Files.createTempDirectory("vault").toFile()
        val file = java.io.File(dir, "v.bin")
        val sealer = MemorySealer()
        live.pricechat.kiwivpn.security.Vault(file, sealer).update { it.copy(settings = it.settings.copy(routing = except)) }
        assertEquals(except, live.pricechat.kiwivpn.security.Vault(file, sealer).read().settings.routing)
        assertFalse(file.readBytes().toString(Charsets.ISO_8859_1).contains("com.bank"))
    }

    @Test fun signing_out_keeps_the_choices() {
        val v = tempVault()
        v.update { it.copy(session = "kd.x", settings = it.settings.copy(routing = only)) }
        assertEquals(only, v.forgetAccount().settings.routing)
    }

    @Test fun a_file_from_the_previous_version_opens_as_all_apps_and_keeps_everything_else() {
        val file = java.io.File(java.nio.file.Files.createTempDirectory("vault").toFile(), "v.bin")
        val sealer = MemorySealer()
        file.writeBytes(sealer.seal("""{"session":"kd.old","settings":{"autoConnect":true}}""".toByteArray()))
        val back = live.pricechat.kiwivpn.security.Vault(file, sealer).read()
        assertEquals(RoutingPrefs(), back.settings.routing)
        assertEquals("kd.old", back.session)
        assertTrue(back.settings.autoConnect)
    }
}
