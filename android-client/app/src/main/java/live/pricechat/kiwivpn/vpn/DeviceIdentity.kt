package live.pricechat.kiwivpn.vpn

import java.security.MessageDigest

/**
 * The name this phone gives its device on a server: "Android Pixel8 a1b2c3d4 (2)". It's how a reinstalled app finds the device whose key it has lost, so it can free that slot. So it must be the same after a
 * reinstall, and different from any other phone: the model alone wasn't, and two identical phones on one account could delete each other's devices.
 *
 * The tag comes from Android's ANDROID_ID, which is unique to this app's signing key on this phone, needs no permission, and survives a reinstall. It's hashed, so the ID itself never leaves the phone and the
 * backend sees only a short tag it can't reverse or match with anything else.
 */
object DeviceIdentity {
    private const val LIMIT = 32 // backend/app/vpn/service.py clean_name
    private const val PREFIX = "Android"
    private const val TAG_LENGTH = 8

    /** A value Android 2.2 shipped on many phones, and the all-zero one some builds report: neither tells phones apart. */
    fun isUsable(androidId: String?): Boolean =
        !androidId.isNullOrBlank() && androidId != "9774d56d682e549c" && androidId.any { it != '0' }

    fun tag(stableId: String): String =
        MessageDigest.getInstance("SHA-256").digest("kiwi-device-v1:$stableId".toByteArray()).joinToString("") { "%02x".format(it) }.take(TAG_LENGTH)

    fun name(model: String?, stableId: String, serverId: Long): String {
        val suffix = " ${tag(stableId)} ($serverId)"
        val room = (LIMIT - PREFIX.length - suffix.length - 1).coerceAtLeast(0)
        val shortModel = model.orEmpty().filter { it.code < 128 && (it.isLetterOrDigit() || it == '-') }.take(room)
        return if (shortModel.isEmpty()) "$PREFIX$suffix" else "$PREFIX $shortModel$suffix"
    }
}
