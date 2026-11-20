package live.pricechat.kiwivpn

import kotlinx.coroutines.test.runTest
import live.pricechat.kiwivpn.net.HttpKiwiApi
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The unified state the backend sends an Android app (backend/app/vpn/unified.py). The same fixture is checked against the real backend by backend/tests/test_android_contract.py, so a field the app
 * reads can't disappear from the server without one side failing.
 */
class UnifiedContractTest {
    private val fixture = javaClass.getResource("/contract/state_android.json")!!.readText()
    private val seen = mutableListOf<Request>()
    private val api = HttpKiwiApi(
        "https://pricechat.test",
        OkHttpClient.Builder().addInterceptor(Interceptor { chain ->
            seen += chain.request()
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(200).message("OK").body(fixture.toResponseBody("application/json".toMediaType())).build()
        }).build(),
    )

    @Test fun the_app_asks_for_its_platform_and_version_with_its_session() = runTest {
        api.unifiedState("kd.s", "0.2.0")
        val r = seen.single()
        assertEquals("/api/members/vpn/v1/state", r.url.encodedPath)
        assertEquals("android", r.url.queryParameter("platform"))
        assertEquals("0.2.0", r.url.queryParameter("version"))
        assertEquals("pc_session=kd.s", r.header("Cookie"))
    }

    @Test fun every_capability_the_app_reads_parses() = runTest {
        val s = api.unifiedState("kd.s", "0.2.0")
        for (key in listOf("passport_enabled", "referrals_enabled", "auto_call_enabled", "app_routing_enabled")) {
            assertEquals(key, true, s.capabilities[key])
        }
        assertEquals(false, s.capabilities["gaming_mode_enabled"])
    }

    @Test fun the_passport_parses_with_the_fields_the_account_screen_shows() = runTest {
        val p = api.unifiedState("kd.s", "0.2.0").passport
        assertNotNull(p)
        p!!
        assertEquals("androiduser", p.username)
        assertEquals("Android User", p.displayName)
        assertEquals(2500L, p.xp)
        assertEquals("Warrior", p.rank.current.name)
        assertEquals(3, p.rank.current.level)
        assertEquals("Next", p.rank.next!!.name)
        assertEquals(500L, p.rank.xpToNext)
        assertEquals(0.5f, p.rank.progress)
        assertEquals(84L, p.standing!!.position)
        assertEquals("First", p.badges.single().name)
        assertTrue(p.referral!!.link.startsWith("https://pricechat.live/"))
    }

    @Test fun a_null_display_name_or_missing_passport_never_breaks_parsing() = runTest {
        val bare = HttpKiwiApi(
            "https://pricechat.test",
            OkHttpClient.Builder().addInterceptor(Interceptor { chain ->
                Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(200).message("OK")
                    .body("""{"api_version":1,"capabilities":{"passport_enabled":false},"passport":{"username":"u","display_name":null},"missions":[]}""".toResponseBody("application/json".toMediaType())).build()
            }).build(),
        )
        val s = bare.unifiedState("kd.s", "0.2.0")
        assertEquals("", s.passport!!.displayName)
        assertEquals(false, s.capabilities["passport_enabled"])
    }
}
