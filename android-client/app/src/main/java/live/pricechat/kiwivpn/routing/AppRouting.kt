package live.pricechat.kiwivpn.routing

import kotlinx.serialization.Serializable

/**
 * App-Based VPN: which apps use the tunnel. The person chooses, and no mode is forced. All of this is plain Kotlin so it can be tested without a phone.
 *
 * The choices stay on this phone, in the encrypted Vault. Nothing here is ever sent to Price Chat or written to the log.
 */
@Serializable
enum class RoutingMode { ALL_APPS, ONLY_SELECTED, ALL_EXCEPT }

/** One list per mode, so switching mode never loses the other mode's choices. */
@Serializable
data class RoutingPrefs(
    val mode: RoutingMode = RoutingMode.ALL_APPS,
    val selected: Set<String> = emptySet(),
    val excluded: Set<String> = emptySet(),
) {
    /** The app's switch always means "Use VPN", in both modes, so its meaning never reverses. */
    fun usesVpn(packageName: String): Boolean = when (mode) {
        RoutingMode.ALL_APPS -> true
        RoutingMode.ONLY_SELECTED -> packageName in selected
        RoutingMode.ALL_EXCEPT -> packageName !in excluded
    }

    fun withUsesVpn(packageName: String, use: Boolean): RoutingPrefs = when (mode) {
        RoutingMode.ALL_APPS -> this
        RoutingMode.ONLY_SELECTED -> copy(selected = if (use) selected + packageName else selected - packageName)
        RoutingMode.ALL_EXCEPT -> copy(excluded = if (use) excluded - packageName else excluded + packageName)
    }

    /** "Select all": the listed apps go into this mode's list. */
    fun withAllListed(packages: Collection<String>): RoutingPrefs = when (mode) {
        RoutingMode.ALL_APPS -> this
        RoutingMode.ONLY_SELECTED -> copy(selected = selected + packages)
        RoutingMode.ALL_EXCEPT -> copy(excluded = excluded + packages)
    }

    /** "Clear selection": this mode's list only. */
    fun withListCleared(): RoutingPrefs = when (mode) {
        RoutingMode.ALL_APPS -> this
        RoutingMode.ONLY_SELECTED -> copy(selected = emptySet())
        RoutingMode.ALL_EXCEPT -> copy(excluded = emptySet())
    }

    val listSize: Int get() = when (mode) {
        RoutingMode.ALL_APPS -> 0
        RoutingMode.ONLY_SELECTED -> selected.size
        RoutingMode.ALL_EXCEPT -> excluded.size
    }
}

/** What's actually handed to the VPN interface. `apps` is the allowed list for ONLY_SELECTED, the disallowed list for ALL_EXCEPT, and empty for ALL_APPS. */
data class RoutePlan(val mode: RoutingMode, val apps: List<String>, val chosenCount: Int) {
    companion object {
        val ALL = RoutePlan(RoutingMode.ALL_APPS, emptyList(), 0)
    }
}

class RoutingError(message: String) : Exception(message)

data class InstalledApp(val label: String, val packageName: String)

object RoutingPolicy {
    private val PACKAGE = Regex("^[a-zA-Z][a-zA-Z0-9_]*(\\.[a-zA-Z][a-zA-Z0-9_]*)+$")
    const val NOTHING_SELECTED = "No apps are selected, so nothing would use the VPN. Choose at least one app, or switch to All Apps."

    fun isValidPackage(name: String): Boolean = name.length <= 255 && PACKAGE.matches(name)

    /**
     * Turns the saved choice into what the VPN interface gets, checking every package first, since Android throws on one that isn't installed.
     *
     * Kiwi VPN itself always stays in the tunnel. Android applies these lists to this app too, so otherwise its exit-address check would go outside the tunnel and report the wrong answer.
     *
     * ONLY_SELECTED with nothing left is refused rather than built: **Android treats an empty allowed list as "every app uses the VPN"**, the opposite of what was asked for, and keeping just Kiwi
     * in the list would show a VPN key icon on a connection that protects nothing.
     */
    fun plan(prefs: RoutingPrefs, ownPackage: String, isInstalled: (String) -> Boolean): RoutePlan {
        fun usable(names: Set<String>) = names.filter { it != ownPackage && isValidPackage(it) && isInstalled(it) }.sorted()
        return when (prefs.mode) {
            RoutingMode.ALL_APPS -> RoutePlan.ALL
            RoutingMode.ONLY_SELECTED -> {
                val chosen = usable(prefs.selected)
                if (chosen.isEmpty()) throw RoutingError(NOTHING_SELECTED)
                RoutePlan(RoutingMode.ONLY_SELECTED, chosen + ownPackage, chosen.size)
            }
            RoutingMode.ALL_EXCEPT -> usable(prefs.excluded).let { RoutePlan(RoutingMode.ALL_EXCEPT, it, it.size) }
        }
    }

    /** Only one of the two lines ever exists, so the two lists can't be mixed. Android refuses a builder with both. */
    private fun interfaceLine(plan: RoutePlan): String = when {
        plan.apps.isEmpty() -> ""
        plan.mode == RoutingMode.ONLY_SELECTED -> "IncludedApplications = ${plan.apps.joinToString(", ")}\n"
        plan.mode == RoutingMode.ALL_EXCEPT -> "ExcludedApplications = ${plan.apps.joinToString(", ")}\n"
        else -> ""
    }

    /** Puts the plan into the [Interface] section of a config. The library's parser reads these lines, and GoBackend calls addAllowedApplication or addDisallowedApplication with them. */
    fun applyTo(config: String, plan: RoutePlan): String {
        val line = interfaceLine(plan)
        if (line.isEmpty()) return config
        val at = config.indexOf("\n[Peer]")
        require(at > 0) { "Not a config" }
        return config.substring(0, at) + "\n" + line.trimEnd('\n') + config.substring(at)
    }

    /** Why this can't be saved as it stands, or null. */
    fun saveProblem(prefs: RoutingPrefs): String? = if (prefs.mode == RoutingMode.ONLY_SELECTED && prefs.selected.isEmpty()) NOTHING_SELECTED else null

    fun modeName(mode: RoutingMode): String = when (mode) {
        RoutingMode.ALL_APPS -> "All Apps"
        RoutingMode.ONLY_SELECTED -> "Only Selected Apps"
        RoutingMode.ALL_EXCEPT -> "All Apps Except Selected"
    }

    private fun apps(n: Int) = if (n == 1) "1 app" else "$n apps"

    /** What the plan means, as the app says it. */
    fun describe(plan: RoutePlan): String = when (plan.mode) {
        RoutingMode.ALL_APPS -> "All apps use Kiwi VPN"
        RoutingMode.ONLY_SELECTED -> "Only ${apps(plan.chosenCount)} you selected use Kiwi VPN. Other apps use your normal connection."
        RoutingMode.ALL_EXCEPT -> if (plan.chosenCount == 0) "All apps use Kiwi VPN" else "All apps use Kiwi VPN except ${apps(plan.chosenCount)} you chose, which use your normal connection."
    }

    fun countText(prefs: RoutingPrefs): String = when (prefs.mode) {
        RoutingMode.ALL_APPS -> "All apps use the VPN"
        RoutingMode.ONLY_SELECTED -> "${apps(prefs.selected.size)} will use the VPN"
        RoutingMode.ALL_EXCEPT -> "${apps(prefs.excluded.size)} will bypass the VPN"
    }

    /** The ones that can't be filtered out silently: Auto-Call would bring the VPN up, and the call itself wouldn't use it. */
    fun autoCallWarnings(prefs: RoutingPrefs, whatsApp: Boolean, telegram: Boolean, installed: (String) -> Boolean): List<String> {
        fun warn(on: Boolean, label: String, packages: List<String>): String? {
            val here = packages.filter(installed)
            if (!on || here.isEmpty()) return null
            return when {
                prefs.mode == RoutingMode.ONLY_SELECTED && here.none { it in prefs.selected } -> "$label isn't selected, so its calls won't use the VPN even when Auto-Call VPN connects it."
                prefs.mode == RoutingMode.ALL_EXCEPT && here.any { it in prefs.excluded } -> "$label bypasses the VPN, so its calls won't use the VPN even when Auto-Call VPN connects it."
                else -> null
            }
        }
        return listOfNotNull(
            warn(whatsApp, "WhatsApp", listOf("com.whatsapp", "com.whatsapp.w4b")),
            warn(telegram, "Telegram", listOf("org.telegram.messenger", "org.telegram.messenger.web", "org.thunderdog.challegram")),
        )
    }

    /** Search by name or package. */
    fun search(apps: List<InstalledApp>, query: String): List<InstalledApp> {
        val q = query.trim()
        return if (q.isEmpty()) apps else apps.filter { it.label.contains(q, ignoreCase = true) || it.packageName.contains(q, ignoreCase = true) }
    }
}
