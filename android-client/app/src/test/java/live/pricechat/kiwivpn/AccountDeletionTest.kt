package live.pricechat.kiwivpn

import kotlinx.coroutines.test.runTest
import live.pricechat.kiwivpn.account.AccountDeletion
import live.pricechat.kiwivpn.account.DeletionResult
import live.pricechat.kiwivpn.model.ServerRef
import live.pricechat.kiwivpn.net.ACCOUNT_DELETION_WEB_URL
import live.pricechat.kiwivpn.net.ApiException
import live.pricechat.kiwivpn.net.HttpKiwiApi
import live.pricechat.kiwivpn.routing.RoutingMode
import live.pricechat.kiwivpn.routing.RoutingPrefs
import live.pricechat.kiwivpn.security.SavedDevice
import live.pricechat.kiwivpn.security.VaultData
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import okio.Buffer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class AccountDeletionTest {
    private val api = FakeApi()
    private val vault = tempVault()
    private var stopped = 0
    private val deletion = AccountDeletion(api, vault) { stopped++ }

    private fun signedIn() = vault.update {
        it.copy(
            session = "kd.session",
            devices = mapOf(2L to SavedDevice(51, KEY_B, peer())),
            lastServer = ServerRef(2, "Germany", "DE"),
            wanted = ServerRef(2, "Germany", "DE"),
            settings = it.settings.copy(routing = RoutingPrefs(RoutingMode.ONLY_SELECTED, selected = setOf("com.whatsapp")), routingOnboarded = true, autoCallEnabled = true),
        )
    }

    @Test fun confirming_needs_the_username_exactly_apart_from_outer_spaces() {
        assertTrue(deletion.canConfirm("egg_1", "egg_1"))
        assertTrue(deletion.canConfirm(" egg_1 ", "egg_1"))
        assertFalse("Case matters, as on the server", deletion.canConfirm("Egg_1", "egg_1"))
        assertFalse(deletion.canConfirm("egg", "egg_1"))
        assertFalse(deletion.canConfirm("", "egg_1"))
        assertFalse("Unknown username: never confirmable", deletion.canConfirm("", null))
    }

    @Test fun a_confirmed_deletion_calls_the_server_then_stops_the_tunnel_and_erases_the_phone() = runTest {
        signedIn()
        val result = deletion.delete(" egg_1", "egg_1")
        assertEquals(DeletionResult.Deleted, result)
        assertEquals(listOf("delete-account egg_1"), api.calls)
        assertEquals(1, stopped)
        assertEquals("Nothing the account left stays", VaultData(), vault.read())
    }

    @Test fun a_wrong_username_never_reaches_the_server_or_changes_the_phone() = runTest {
        val before = signedIn()
        val result = deletion.delete("someone_else", "egg_1")
        assertTrue(result is DeletionResult.Failed)
        assertTrue(api.calls.isEmpty())
        assertEquals(0, stopped)
        assertEquals(before, vault.read())
    }

    @Test fun the_server_refusing_leaves_everything_in_place_and_shows_its_reason() = runTest {
        val before = signedIn()
        api.deleteFails = ApiException(400, "Type your username exactly to confirm.")
        val result = deletion.delete("egg_1", "egg_1") as DeletionResult.Failed
        assertEquals("Type your username exactly to confirm.", result.message)
        assertFalse(result.signedOut)
        assertEquals(0, stopped)
        assertEquals(before, vault.read())
    }

    @Test fun no_connection_leaves_everything_in_place() = runTest {
        val before = signedIn()
        api.deleteFails = api.unreachable
        val result = deletion.delete("egg_1", "egg_1") as DeletionResult.Failed
        assertTrue(result.message.contains("nothing was deleted"))
        assertEquals(before, vault.read())
        assertEquals(0, stopped)
    }

    @Test fun an_ended_session_says_to_sign_in_again_and_deletes_nothing() = runTest {
        signedIn()
        api.deleteFails = ApiException(401, "Sign in first.")
        val result = deletion.delete("egg_1", "egg_1") as DeletionResult.Failed
        assertTrue(result.signedOut)
        assertEquals(0, stopped)
        assertEquals("kd.session", vault.read().session)
    }

    @Test fun signed_out_or_no_username_is_refused_before_any_call() = runTest {
        assertTrue((deletion.delete("egg_1", "egg_1") as DeletionResult.Failed).signedOut)
        signedIn()
        assertTrue(deletion.delete("egg_1", null) is DeletionResult.Failed)
        assertTrue(api.calls.isEmpty())
    }

    @Test fun the_disclosure_names_vpn_devices_trial_and_this_phone() {
        val text = AccountDeletion.WHAT_IS_DELETED.joinToString(" ")
        assertTrue(text.contains("device"))
        assertTrue(text.contains("trial"))
        assertTrue(text.contains("XP"))
        assertTrue(AccountDeletion.ON_THIS_PHONE.contains("erases"))
        assertEquals("https://pricechat.live/passport", ACCOUNT_DELETION_WEB_URL)
    }
}

/** The real HTTP client against a stand-in server: the exact request the backend's DELETE /members/me expects. */
class DeleteAccountHttpTest {
    private val seen = mutableListOf<Request>()
    private fun api(status: Int, body: String) = HttpKiwiApi(
        "https://pricechat.test",
        OkHttpClient.Builder().addInterceptor(Interceptor { chain ->
            seen += chain.request()
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(status).message("x").body(body.toResponseBody("application/json".toMediaType())).build()
        }).build(),
    )

    @Test fun it_sends_delete_with_the_username_as_json_and_the_session_as_a_cookie() = runTest {
        api(200, """{"deleted":true}""").deleteAccount("kd.secret", "egg_1")
        val r = seen.single()
        assertEquals("DELETE", r.method)
        assertEquals("https://pricechat.test/api/members/me", r.url.toString())
        assertEquals("pc_session=kd.secret", r.header("Cookie"))
        assertNull("No Origin header, so the backend's cross-site check doesn't apply", r.header("Origin"))
        assertEquals("""{"confirm_username":"egg_1"}""", Buffer().also { r.body!!.writeTo(it) }.readUtf8())
        assertTrue(r.body!!.contentType().toString().startsWith("application/json"))
    }

    @Test fun the_servers_refusal_comes_back_with_its_words() = runTest {
        val e = assertThrows(ApiException::class.java) {
            kotlinx.coroutines.runBlocking { api(400, """{"detail":"Type your username exactly to confirm."}""").deleteAccount("kd.s", "x") }
        }
        assertEquals(400, e.status)
        assertEquals("Type your username exactly to confirm.", e.message)
    }

    @Test fun an_answer_without_deleted_true_is_not_taken_as_success() = runTest {
        assertThrows(ApiException::class.java) { kotlinx.coroutines.runBlocking { api(200, """{"ok":true}""").deleteAccount("kd.s", "x") } }
    }
}
