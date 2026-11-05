package live.pricechat.kiwivpn.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// The backend's answers, exactly as docs/API-CONTRACT.md records them from backend/app/vpn/service.py. Unknown fields are ignored, so a new field on the server can't break the app.

@Serializable
data class Server(
    val id: Long,
    val name: String,
    val region: String = "",
    @SerialName("country_code") val countryCode: String = "",
    val online: Boolean,
    val recommended: Boolean = false,
)

@Serializable
data class Device(
    val id: Long,
    val name: String,
    @SerialName("server_id") val serverId: Long,
    @SerialName("last_handshake") val lastHandshake: String? = null,
)

@Serializable
data class Plan(
    val kind: String = "",
    val until: String? = null,
    @SerialName("days_left") val daysLeft: Long? = null,
)

@Serializable
data class Trial(val available: Boolean = false, val days: Long = 0)

@Serializable
data class Account(
    val enabled: Boolean = false,
    val eligible: Boolean,
    val reason: String? = null,
    val payments: Boolean = false,
    @SerialName("max_devices") val maxDevices: Long = 0,
    val servers: List<Server> = emptyList(),
    val devices: List<Device> = emptyList(),
    val plan: Plan? = null,
    val trial: Trial? = null,
)

@Serializable
data class Peer(
    @SerialName("server_public_key") val serverPublicKey: String,
    @SerialName("endpoint_host") val endpointHost: String,
    @SerialName("listen_port") val listenPort: Int,
    val addresses: List<String>,
    val dns: List<String> = emptyList(),
    @SerialName("allowed_ips") val allowedIps: List<String>,
    val keepalive: Int = 0,
)

@Serializable
data class NewDevice(val device: Device, val peer: Peer)

@Serializable
data class MemberInfo(
    val username: String? = null,
    @SerialName("display_name") val displayName: String? = null,
)

@Serializable
data class Me(val member: MemberInfo? = null)

// GET /members/vpn/v1/state: backend/app/vpn/unified.py. Not on production until it's approved and deployed, so everything here is optional to the app.

@Serializable
data class RankInfo(val level: Int = 1, val key: String = "", val name: String = "")

@Serializable
data class RankProgress(
    val current: RankInfo = RankInfo(),
    val next: RankInfo? = null,
    val score: Long = 0,
    @SerialName("xp_to_next") val xpToNext: Long = 0,
    val progress: Float = 0f,
)

@Serializable
data class Badge(val key: String, val name: String, val description: String = "")

@Serializable
data class Standing(val position: Long)

@Serializable
data class ReferralInfo(val code: String, val link: String, val joined: Long = 0, val qualified: Long = 0)

@Serializable
data class Passport(
    val username: String = "",
    @SerialName("display_name") val displayName: String = "",
    val xp: Long = 0,
    val rank: RankProgress = RankProgress(),
    val badges: List<Badge> = emptyList(),
    val standing: Standing? = null,
    val referral: ReferralInfo? = null,
)

@Serializable
data class UnifiedState(
    val capabilities: Map<String, Boolean> = emptyMap(),
    val passport: Passport? = null,
)

/** A server as the connection code needs it, and as it's remembered between runs. */
@Serializable
data class ServerRef(val id: Long, val name: String, val countryCode: String = "")

fun Server.ref() = ServerRef(id, name, countryCode)
