package live.pricechat.kiwivpn

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.runTest
import live.pricechat.kiwivpn.auth.LoopbackListener
import live.pricechat.kiwivpn.auth.SignInAttempt
import live.pricechat.kiwivpn.auth.readCallback
import live.pricechat.kiwivpn.log.Diag
import live.pricechat.kiwivpn.model.StatusKind
import live.pricechat.kiwivpn.model.Plan
import live.pricechat.kiwivpn.model.Trial
import live.pricechat.kiwivpn.model.accountStatus
import live.pricechat.kiwivpn.model.suggestedServer
import live.pricechat.kiwivpn.net.ApiException
import live.pricechat.kiwivpn.net.HttpKiwiApi
import live.pricechat.kiwivpn.security.Sealer
import live.pricechat.kiwivpn.security.SavedDevice
import live.pricechat.kiwivpn.security.Vault
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
import java.io.File
import java.io.IOException
import java.net.InetAddress
import java.net.Socket
import java.nio.file.Files
import java.security.GeneralSecurityException

class SignInTest {
    @Test fun the_challenge_matches_the_rfc_7636_test_vector() {
        assertEquals("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM", SignInAttempt.challengeOf("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"))
    }

    @Test fun an_attempt_has_what_the_website_page_requires() {
        val a = SignInAttempt.create()
        assertEquals(43, a.verifier.length)
        assertEquals(43, a.challenge.length)
        assertTrue(a.state.length in 16..64)
        for (t in listOf(a.challenge, a.state)) assertTrue(t.all { it.isLetterOrDigit() || it == '-' || it == '_' })
        assertEquals(SignInAttempt.challengeOf(a.verifier), a.challenge)
        assertEquals("https://x/vpn/desktop?challenge=${a.challenge}&port=50123&state=${a.state}&client=android", a.pageUrl("https://x", 50123))
        assertFalse("The verifier stays in the app", a.pageUrl("https://x", 50123).contains(a.verifier))
    }

    @Test fun attempts_differ() {
        val (a, b) = SignInAttempt.create() to SignInAttempt.create()
        assertTrue(a.verifier != b.verifier && a.state != b.state)
    }

    @Test fun only_our_callback_with_our_state_gives_a_code() {
        val code = "c".repeat(43)
        assertEquals(code, readCallback("GET /callback?code=$code&state=abc HTTP/1.1", "abc"))
        assertEquals(code, readCallback("GET /callback?state=abc&code=$code HTTP/1.1", "abc"))
        for (bad in listOf("GET /callback?code=$code&state=other HTTP/1.1", "GET /callback?code=$code HTTP/1.1", "POST /callback?code=$code&state=abc HTTP/1.1", "GET /other?code=$code&state=abc HTTP/1.1", "GET /callback?code=short&state=abc HTTP/1.1", "", "GET")) {
            assertNull(bad, readCallback(bad, "abc"))
        }
    }

    @Test fun the_real_listener_refuses_the_wrong_request_and_takes_the_right_one() = runBlocking<Unit> {
        val code = "k".repeat(43)
        LoopbackListener().use { listener ->
            assertTrue(listener.port >= 1024)
            val waiting = async(Dispatchers.IO) { listener.awaitCode("mine", 10_000) }
            fun send(line: String): String = Socket(InetAddress.getByAddress(byteArrayOf(127, 0, 0, 1)), listener.port).use { s ->
                s.soTimeout = 10_000 // a listener that has stopped accepting fails this test rather than hanging it
                s.getOutputStream().write("$line\r\nHost: x\r\n\r\n".toByteArray())
                s.getInputStream().readBytes().toString(Charsets.UTF_8)
            }
            val refused = async(Dispatchers.IO) { send("GET /callback?code=wrongwrongwrongwrongwrong&state=theirs HTTP/1.1") }.await()
            val taken = async(Dispatchers.IO) { send("GET /callback?code=$code&state=mine HTTP/1.1") }.await()
            assertEquals(code, waiting.await())
            assertTrue(refused.startsWith("HTTP/1.1 400"))
            assertTrue(taken.startsWith("HTTP/1.1 200") && taken.contains("kiwivpn://signed-in"))
            assertFalse("The code must not be echoed into the page", taken.contains(code))
        }
    }

    @Test fun time_running_out_gives_null() = runBlocking<Unit> {
        LoopbackListener().use { assertNull(it.awaitCode("x", 50)) }
    }
}

class ApiTest {
    private val seen = mutableListOf<Request>()
    private fun bodyOf(r: Request) = Buffer().also { r.body?.writeTo(it) }.readUtf8()

    private fun api(status: Int = 200, body: String = "{}", thrown: IOException? = null) = HttpKiwiApi(
        "https://pricechat.test",
        OkHttpClient.Builder().addInterceptor(Interceptor { chain ->
            seen += chain.request()
            thrown?.let { throw it }
            Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(status).message("x").body(body.toResponseBody("application/json".toMediaType())).build()
        }).build(),
    )

    // A real reply's shape, from backend/app/vpn/service.py overview(), with its fields the app doesn't use left in.
    private val realAccount = """{"enabled":true,"test_mode":false,"payments":false,"eligible":true,"reason":null,"max_devices":3,
      "servers":[{"id":2,"name":"Germany","region":"Falkenstein, Germany","country_code":"DE","online":true,"load":0.01,"adblock":false,"recommended":false,"browser":false}],
      "devices":[{"id":5,"name":"Android Pixel (2)","server_id":2,"ip":"10.66.0.9","created_at":"2026-11-05T00:00:00","last_handshake":null,"rx_bytes":0,"tx_bytes":0,"paused_until":null}],
      "usage":{"month":"2026-11","used_bytes":0,"cap_bytes":0,"paused_until":null},"paid_until":null,"anonymous":false,"has_card":false,"billing":null,
      "plan":{"kind":"trial","until":"2026-12-05T00:00:00+00:00","days_left":12},"royal":false,"trial":{"available":false,"days":30}}"""

    @Test fun the_real_account_reply_parses_and_the_session_goes_only_as_a_cookie() = runTest {
        val a = api(body = realAccount).account("kd.secret")
        assertTrue(a.eligible)
        assertEquals(12L, a.plan!!.daysLeft)
        assertEquals("DE", a.servers[0].countryCode)
        assertEquals(2L, a.devices[0].serverId)
        val r = seen.single()
        assertEquals("https://pricechat.test/api/members/vpn", r.url.toString())
        assertEquals("pc_session=kd.secret", r.header("Cookie"))
        assertFalse(r.url.toString().contains("secret"))
    }

    @Test fun adding_a_device_sends_the_names_the_backend_requires_and_no_more() = runTest {
        val reply = """{"device":{"id":5,"name":"n","server_id":2},"peer":{"server_public_key":"$KEY_A","endpoint_host":"5.75.10.20","listen_port":51821,"addresses":["10.66.0.9/32"],"dns":["10.66.0.1"],"allowed_ips":["0.0.0.0/0","::/0"],"keepalive":25}}"""
        val d = api(body = reply).addDevice("t", "Android Pixel (2)", 2, KEY_A)
        assertEquals(51821, d.peer.listenPort)
        val sent = bodyOf(seen.single())
        assertEquals("""{"name":"Android Pixel (2)","server_id":2,"public_key":"$KEY_A"}""", sent)
    }

    @Test fun the_token_exchange_has_no_cookie_and_sends_code_and_verifier() = runTest {
        assertEquals("kd.abc", api(body = """{"token":"kd.abc","expires_in_days":90}""").exchangeCode("code", "verifier"))
        val r = seen.single()
        assertNull(r.header("Cookie"))
        assertEquals("""{"code":"code","verifier":"verifier"}""", bodyOf(r))
    }

    @Test fun the_backends_message_is_shown() = runTest {
        try {
            api(409, """{"detail":"You can have up to 3 devices. Delete one first."}""").addDevice("t", "n", 1, KEY_A)
        } catch (e: ApiException) {
            assertEquals(409, e.status)
            assertEquals("You can have up to 3 devices. Delete one first.", e.message)
            return@runTest
        }
        throw AssertionError("Should have thrown")
    }

    @Test fun error_shapes_that_arent_text_still_give_a_message() = runTest {
        for (body in listOf("""{"detail":[{"loc":["x"]}]}""", "<html>", "")) {
            try {
                api(500, body).account("t")
                throw AssertionError("Should have thrown")
            } catch (e: ApiException) {
                assertEquals("Price Chat answered 500.", e.message)
            }
        }
    }

    @Test fun no_answer_is_unreachable_and_status_401_is_signed_out() = runTest {
        try {
            api(thrown = IOException("down")).account("t")
            throw AssertionError("Should have thrown")
        } catch (e: ApiException) {
            assertTrue(e.unreachable)
        }
        try {
            api(401, """{"detail":"Sign in"}""").account("t")
            throw AssertionError("Should have thrown")
        } catch (e: ApiException) {
            assertTrue(e.signedOut)
        }
    }

    @Test fun a_garbled_success_is_an_error_not_a_crash() = runTest {
        try {
            api(body = "not json").account("t")
            throw AssertionError("Should have thrown")
        } catch (e: ApiException) {
            assertTrue(e.message.contains("can't read"))
        }
    }

    @Test fun the_shared_state_parses_with_passport_and_capabilities() = runTest {
        val reply = """{"api_version":1,"refresh_after_seconds":60,"capabilities":{"passport_enabled":true,"auto_call_enabled":true,"gaming_mode_enabled":false},"vpn":{"eligible":true},"missions":[],
          "passport":{"username":"kiwi","display_name":"Kiwi","xp":2500,"bonus_xp":0,"xp_suspended":false,
            "rank":{"current":{"level":3,"key":"warrior","name":"Warrior","min_xp":2000,"description":"x","art":{"a":1}},"next":{"level":4,"key":"k","name":"Next","min_xp":3000,"description":"","art":{}},"score":2500,"xp_to_next":500,"progress":0.5},
            "badges":[{"key":"b","name":"First","description":"d","awarded_at":"2026-01-01","awarded_by":"automatic"}],"standing":{"position":84,"board":"all"},
            "referral":{"code":"kiwi123456","link":"https://x/website/join/kiwi123456","joined":2,"qualified":1,"xp_earned":10,"reward_xp":10}}}"""
        val s = api(body = reply).unifiedState("t", "0.1.0-beta1")
        assertEquals(2500L, s.passport!!.xp)
        assertEquals("Warrior", s.passport.rank.current.name)
        assertEquals(500L, s.passport.rank.xpToNext)
        assertEquals(84L, s.passport.standing!!.position)
        assertEquals("kiwi123456", s.passport.referral!!.code)
        assertEquals(true, s.capabilities["auto_call_enabled"])
        val url = seen.single().url
        assertEquals("android", url.queryParameter("platform"))
        assertEquals("0.1.0-beta1", url.queryParameter("version"))
        assertEquals("/api/members/vpn/v1/state", url.encodedPath)
    }

    @Test fun only_tls_is_accepted() {
        assertThrows(IllegalArgumentException::class.java) { HttpKiwiApi("http://pricechat.live") }
    }
}

class AccountStatusTest {
    @Test fun an_active_trial_shows_its_remaining_time_and_offers_no_new_one() {
        val s = accountStatus(account().copy(plan = Plan("trial", null, 12), trial = Trial(false, 30)))
        assertEquals(StatusKind.TRIAL, s.kind)
        assertEquals("Free trial: 12 days left", s.label)
        assertTrue(s.canConnect)
        assertFalse(s.canStartTrial)
    }

    @Test fun signing_in_never_starts_a_trial_it_only_offers_when_the_server_does() {
        val s = accountStatus(account(eligible = false).copy(plan = Plan("not_activated"), trial = Trial(true, 30)))
        assertEquals(StatusKind.NEEDS_TRIAL, s.kind)
        assertTrue(s.canStartTrial)
        assertFalse(s.canConnect)
        val used = accountStatus(account(eligible = false).copy(plan = Plan("not_activated"), trial = Trial(false, 30)))
        assertFalse(used.canStartTrial)
    }

    @Test fun expired_closed_blocked_and_unavailable_cant_connect() {
        for ((kind, want) in listOf("expired" to StatusKind.EXPIRED, "closed" to StatusKind.CLOSED, "blocked" to StatusKind.BLOCKED)) {
            val s = accountStatus(account(eligible = false).copy(plan = Plan(kind)))
            assertEquals(want, s.kind)
            assertFalse(s.canConnect)
            assertFalse(s.canStartTrial)
        }
        assertEquals(StatusKind.UNAVAILABLE, accountStatus(null).kind)
    }

    @Test fun a_paid_plan_and_the_special_kinds_are_active() {
        assertEquals(StatusKind.PAID, accountStatus(account().copy(plan = Plan("paid", null, 3))).kind)
        for (k in listOf("owner", "royal", "free", "granted")) assertEquals(StatusKind.UNLIMITED, accountStatus(account().copy(plan = Plan(k))).kind)
    }

    @Test fun a_kind_this_version_doesnt_know_follows_the_servers_yes_or_no() {
        assertTrue(accountStatus(account(eligible = true).copy(plan = Plan("something_new"))).canConnect)
        assertFalse(accountStatus(account(eligible = false).copy(plan = Plan("something_new"))).canConnect)
    }

    @Test fun the_server_suggested_first_is_last_used_then_recommended_then_any_online() {
        val all = account().servers
        assertEquals(2L, suggestedServer(all, 2)!!.id)
        assertEquals(1L, suggestedServer(all, 3)!!.id)
        assertEquals(1L, suggestedServer(all, null)!!.id)
        assertEquals(2L, suggestedServer(all.filter { it.id != 1L }, null)!!.id)
        assertNull(suggestedServer(emptyList(), null))
    }
}

class VaultTest {
    private fun dir() = Files.createTempDirectory("vault").toFile()

    @Test fun whats_on_disk_is_encrypted() {
        val file = File(dir(), "v.bin")
        val v = Vault(file, MemorySealer())
        v.update { it.copy(session = "kd.secret", devices = mapOf(2L to SavedDevice(5, KEY_B))) }
        val raw = file.readBytes().toString(Charsets.ISO_8859_1)
        assertFalse(raw.contains("kd.secret"))
        assertFalse(raw.contains(KEY_B))
    }

    @Test fun it_survives_a_restart() {
        val file = File(dir(), "v.bin")
        val sealer = MemorySealer()
        Vault(file, sealer).update { it.copy(session = "kd.secret", devices = mapOf(2L to SavedDevice(5, KEY_B, peer()))) }
        val back = Vault(file, sealer).read()
        assertEquals("kd.secret", back.session)
        assertEquals(KEY_B, back.devices[2]!!.privateKey)
        assertEquals(peer(), back.devices[2]!!.peer)
    }

    @Test fun a_file_that_wont_open_means_starting_clean_not_crashing() {
        val file = File(dir(), "v.bin")
        val sealer = MemorySealer()
        Vault(file, sealer).update { it.copy(session = "kd.secret") }
        val lost = object : Sealer {
            override fun seal(plain: ByteArray) = plain
            override fun open(sealed: ByteArray): ByteArray = throw GeneralSecurityException("key gone")
        }
        assertNull(Vault(file, lost).read().session)
        assertFalse(file.exists())
    }

    @Test fun signing_out_wipes_the_secrets_and_keeps_the_settings() {
        val v = tempVault()
        v.update { it.copy(session = "kd.secret", devices = mapOf(2L to SavedDevice(5, KEY_B)), settings = it.settings.copy(autoConnect = true), autoCallOwned = true) }
        val after = v.forgetAccount()
        assertNull(after.session)
        assertTrue(after.devices.isEmpty())
        assertTrue(after.settings.autoConnect)
        assertFalse(after.autoCallOwned)
    }
}

class DiagTest {
    @Test fun secrets_cant_get_into_the_log() {
        for (secret in listOf(KEY_A, "kd.abcdef", "PrivateKey = $KEY_B", "5.75.10.20", "2001:db8::1", "x".repeat(41))) {
            assertEquals(secret, "[removed]", Diag.clean(secret))
        }
        assertEquals("trial", Diag.clean("trial"))
        assertEquals("12", Diag.clean(12))
    }

    @Test fun an_event_is_a_name_and_short_values() {
        assertEquals("connected server=2 token=[removed]", Diag.format("connected", arrayOf("server" to 2L, "token" to "kd.abc")))
    }
}
