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

/** A server as the connection code needs it, and as it's remembered between runs. */
@Serializable
data class ServerRef(val id: Long, val name: String, val countryCode: String = "")

fun Server.ref() = ServerRef(id, name, countryCode)
