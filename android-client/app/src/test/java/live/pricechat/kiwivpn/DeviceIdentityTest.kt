package live.pricechat.kiwivpn

import kotlinx.coroutines.test.runTest
import live.pricechat.kiwivpn.model.Device
import live.pricechat.kiwivpn.security.SavedDevice
import live.pricechat.kiwivpn.vpn.DeviceIdentity
import live.pricechat.kiwivpn.vpn.Provisioner
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DeviceIdentityTest {
    private val phoneA = "1a2b3c4d5e6f7081"
    private val phoneB = "8f7e6d5c4b3a2910"

    @Test fun two_identical_models_get_different_names() {
        assertNotEquals(DeviceIdentity.name("Pixel 8", phoneA, 2), DeviceIdentity.name("Pixel 8", phoneB, 2))
    }

    @Test fun the_same_phone_gets_the_same_name_every_time_so_a_reinstall_finds_its_device() {
        assertEquals(DeviceIdentity.name("Pixel 8", phoneA, 2), DeviceIdentity.name("Pixel 8", phoneA, 2))
    }

    @Test fun each_server_gets_its_own_name() {
        assertNotEquals(DeviceIdentity.name("Pixel 8", phoneA, 1), DeviceIdentity.name("Pixel 8", phoneA, 2))
    }

    @Test fun the_name_is_readable_and_holds_only_a_hash_of_the_id() {
        val name = DeviceIdentity.name("Pixel-8", phoneA, 2)
        assertTrue(name, Regex("^Android Pixel-8 [0-9a-f]{8} \\(2\\)$").matches(name))
        assertFalse("The ID itself must not leave the phone", name.contains(phoneA))
        assertFalse(name.contains(phoneA.take(8)))
    }

    @Test fun any_model_and_server_id_fits_the_backends_limit() {
        for (model in listOf("", null, "SM-S918B", "A".repeat(200), "Pixel 8 Pro", "Ünï çødé 名前", "   ")) {
            for (server in listOf(1L, 12345L, 9_999_999_999L)) { // database ids: ten digits is far beyond anything real
                val name = DeviceIdentity.name(model, phoneA, server)
                assertTrue("$name is ${name.length}", name.length <= 32)
                assertTrue(name, name.startsWith("Android"))
                assertEquals("clean_name collapses spaces, so there must be none to collapse", name, name.split(Regex("\\s+")).joinToString(" "))
                assertTrue(name, name.all { it.code < 128 })
            }
        }
    }

    @Test fun long_models_keep_the_tag_and_the_server_because_those_tell_devices_apart() {
        val name = DeviceIdentity.name("A".repeat(200), phoneA, 2)
        assertTrue(name.endsWith(" ${DeviceIdentity.tag(phoneA)} (2)"))
    }

    @Test fun ids_that_dont_tell_phones_apart_are_rejected() {
        for (bad in listOf(null, "", "  ", "9774d56d682e549c", "0000000000000000")) assertFalse("$bad", DeviceIdentity.isUsable(bad))
        assertTrue(DeviceIdentity.isUsable(phoneA))
    }

    // --- with the real device code ---

    private val vault = tempVault().also { v -> v.update { it.copy(session = "kd.token") } }
    private val api = FakeApi()
    private val mine = Provisioner(api, vault, FakeKeys(), { id -> DeviceIdentity.name("Pixel 8", phoneA, id) }, retryPauseMs = 1)

    @Test fun the_bug_another_identical_phones_device_is_never_deleted() = runTest {
        val other = DeviceIdentity.name("Pixel 8", phoneB, 2)
        api.current = account(devices = listOf(Device(70, other, 2)))
        mine.profile(2, null)
        assertFalse(api.calls.toString(), api.calls.contains("delete 70"))
        assertEquals(2, api.current.devices.size)
    }

    @Test fun this_phones_own_lost_device_is_still_cleared_after_a_reinstall() = runTest {
        api.current = account(devices = listOf(Device(71, DeviceIdentity.name("Pixel 8", phoneA, 2), 2)))
        mine.profile(2, null)
        assertTrue(api.calls.contains("delete 71"))
    }

    @Test fun a_device_from_an_older_beta_with_only_a_model_name_is_never_deleted_because_it_could_be_any_phone() = runTest {
        api.current = account(devices = listOf(Device(72, "Android Pixel8 (2)", 2)))
        mine.profile(2, null)
        assertFalse(api.calls.contains("delete 72"))
    }

    @Test fun an_app_updated_in_place_keeps_using_its_existing_device_whatever_its_name() = runTest {
        api.current = account(devices = listOf(Device(73, "Android Pixel8 (2)", 2)))
        vault.update { it.copy(devices = mapOf(2L to SavedDevice(73, KEY_B, peer()))) }
        mine.profile(2, null)
        assertFalse(api.calls.any { it.startsWith("add") || it.startsWith("delete") })
    }

    @Test fun the_fallback_id_survives_a_restart_and_signing_out() {
        val file = java.io.File(java.nio.file.Files.createTempDirectory("vault").toFile(), "v.bin")
        val sealer = MemorySealer()
        val first = live.pricechat.kiwivpn.security.Vault(file, sealer)
        assertNull(first.read().fallbackDeviceId)
        first.update { it.copy(fallbackDeviceId = "abc", session = "kd.x") }
        val back = live.pricechat.kiwivpn.security.Vault(file, sealer)
        assertEquals("abc", back.read().fallbackDeviceId)
        assertEquals("Otherwise signing out would orphan this phone's name", "abc", back.forgetAccount().fallbackDeviceId)
    }
}
