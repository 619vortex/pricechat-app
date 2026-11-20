package live.pricechat.kiwivpn.net

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.DeserializationStrategy
import kotlinx.serialization.Serializable
import kotlinx.serialization.SerializationException
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.serializer
import live.pricechat.kiwivpn.model.Account
import live.pricechat.kiwivpn.model.Me
import live.pricechat.kiwivpn.model.NewDevice
import live.pricechat.kiwivpn.model.Peer
import live.pricechat.kiwivpn.model.UnifiedState
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

const val BASE_URL = "https://pricechat.live"
const val UNREACHABLE = "Couldn't reach Price Chat. Check your connection and try again."

/** status 0: the request never got an answer. */
class ApiException(val status: Int, override val message: String) : Exception(message) {
    val signedOut get() = status == 401
    val unreachable get() = status == 0
}

/** Everything the app asks Price Chat. Every call and field here is one the backend really has: docs/API-CONTRACT.md. */
interface KiwiApi {
    suspend fun account(token: String): Account
    suspend fun startTrial(token: String): Account
    suspend fun peer(token: String, deviceId: Long): Peer
    suspend fun addDevice(token: String, name: String, serverId: Long, publicKey: String): NewDevice
    suspend fun deleteDevice(token: String, deviceId: Long)
    suspend fun me(token: String): Me
    /** Passport and capabilities. Answers 404 until the shared API is deployed, which the app treats as "not available yet". */
    suspend fun unifiedState(token: String, version: String): UnifiedState
    suspend fun exchangeCode(code: String, verifier: String): String
    suspend fun refresh(token: String): String
    suspend fun logout(token: String)
    suspend fun myIp(): String
    fun dropConnections()
}

@Serializable private data class TokenReply(val token: String)
@Serializable private data class IpReply(val ip: String)
@Serializable private data class TokenInput(val code: String, val verifier: String)
@Serializable private data class DeviceInput(val name: String, val server_id: Long, val public_key: String)

/** Never logs a request, a header or a body. Redirects are refused, so the session cookie can't be sent anywhere but BASE_URL. */
class HttpKiwiApi(base: String = BASE_URL, client: OkHttpClient? = null, private val userAgent: String = "KiwiVPN-Android") : KiwiApi {
    private val base = base.trimEnd('/')
    private val json = Json { ignoreUnknownKeys = true; coerceInputValues = true }
    private val mediaJson = "application/json".toMediaType()
    private val http: OkHttpClient = client ?: OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .callTimeout(35, TimeUnit.SECONDS)
        .followRedirects(false)
        .followSslRedirects(false)
        .build()

    init {
        require(this.base.startsWith("https://")) { "Price Chat is only ever reached over TLS." }
    }

    private fun request(path: String, token: String?): Request.Builder {
        val b = Request.Builder().url("$base/api$path").header("Accept", "application/json").header("User-Agent", userAgent)
        if (token != null) b.header("Cookie", "pc_session=$token")
        return b
    }

    private fun detailOf(body: String, status: Int): String {
        val fallback = "Price Chat answered $status."
        return try {
            val detail = (json.parseToJsonElement(body) as? JsonObject)?.get("detail") as? JsonPrimitive
            if (detail != null && detail.isString) detail.content else fallback
        } catch (_: SerializationException) {
            fallback
        }
    }

    private suspend fun send(request: Request): String = withContext(Dispatchers.IO) {
        try {
            http.newCall(request).execute().use { reply ->
                val text = reply.body.string()
                if (!reply.isSuccessful) throw ApiException(reply.code, detailOf(text, reply.code))
                text
            }
        } catch (e: IOException) {
            throw ApiException(0, UNREACHABLE)
        }
    }

    private fun <T> parse(strategy: DeserializationStrategy<T>, text: String): T = try {
        json.decodeFromString(strategy, text)
    } catch (e: SerializationException) {
        throw ApiException(0, "Price Chat gave an answer the app can't read.")
    } catch (e: IllegalArgumentException) {
        throw ApiException(0, "Price Chat gave an answer the app can't read.")
    }

    private inline fun <reified T> bodyOf(value: T): RequestBody = json.encodeToString(value).toRequestBody(mediaJson)
    private val empty: RequestBody get() = ByteArray(0).toRequestBody(null)

    override suspend fun account(token: String): Account = parse(serializer(), send(request("/members/vpn", token).get().build()))
    override suspend fun startTrial(token: String): Account = parse(serializer(), send(request("/members/vpn/trial", token).post(empty).build()))
    override suspend fun peer(token: String, deviceId: Long): Peer = parse(serializer(), send(request("/members/vpn/devices/$deviceId/peer", token).get().build()))

    override suspend fun addDevice(token: String, name: String, serverId: Long, publicKey: String): NewDevice =
        parse(serializer(), send(request("/members/vpn/devices", token).post(bodyOf(DeviceInput(name, serverId, publicKey))).build()))

    override suspend fun deleteDevice(token: String, deviceId: Long) {
        send(request("/members/vpn/devices/$deviceId", token).delete().build())
    }

    override suspend fun me(token: String): Me = parse(serializer(), send(request("/members/me", token).get().build()))

    override suspend fun unifiedState(token: String, version: String): UnifiedState {
        val url = "$base/api/members/vpn/v1/state".toHttpUrl().newBuilder().addQueryParameter("platform", "android").addQueryParameter("version", version).build()
        return parse(serializer(), send(request("/members/vpn/v1/state", token).url(url).get().build()))
    }

    override suspend fun exchangeCode(code: String, verifier: String): String =
        parse<TokenReply>(serializer(), send(request("/members/desktop/token", null).post(bodyOf(TokenInput(code, verifier))).build())).token

    override suspend fun refresh(token: String): String = parse<TokenReply>(serializer(), send(request("/members/desktop/refresh", token).post(empty).build())).token

    override suspend fun logout(token: String) {
        send(request("/members/logout", token).post(empty).build())
    }

    override suspend fun myIp(): String = parse<IpReply>(serializer(), send(request("/members/vpn/ip", null).get().build())).ip

    /** Sockets opened before the routes changed are dead or go the wrong way. Dropped every time the tunnel comes up or goes down, so the next request doesn't waste its time on one. */
    override fun dropConnections() {
        http.connectionPool.evictAll()
    }
}
