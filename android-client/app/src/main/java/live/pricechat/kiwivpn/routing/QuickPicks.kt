package live.pricechat.kiwivpn.routing

/**
 * The messaging apps offered as one-press choices in "Choose what uses Kiwi VPN". A pick is shown only when at least one of its packages is installed, and pressing it selects or clears every installed
 * package it stands for (WhatsApp and WhatsApp Business are separate apps; so are the Telegram builds). Plain Kotlin, so it's tested without a phone.
 */
data class QuickPick(val label: String, val packages: List<String>)

object QuickPicks {
    val ALL: List<QuickPick> = listOf(
        QuickPick("WhatsApp", listOf("com.whatsapp")),
        QuickPick("WhatsApp Business", listOf("com.whatsapp.w4b")),
        QuickPick("Telegram", listOf("org.telegram.messenger", "org.telegram.messenger.web", "org.thunderdog.challegram")),
        QuickPick("Signal", listOf("org.thoughtcrime.securesms")),
    )

    /** The picks whose apps are on this phone, each narrowed to the packages that really are installed. */
    fun available(installed: Collection<String>): List<QuickPick> {
        val here = installed.toSet()
        return ALL.mapNotNull { pick -> pick.packages.filter { it in here }.takeIf { it.isNotEmpty() }?.let { pick.copy(packages = it) } }
    }

    /** On only when every installed package it stands for is selected. */
    fun isOn(pick: QuickPick, selected: Set<String>): Boolean = pick.packages.isNotEmpty() && pick.packages.all { it in selected }

    /** Pressing a pick: all of its packages in, or all out. Other selections are untouched. */
    fun toggle(pick: QuickPick, selected: Set<String>): Set<String> = if (isOn(pick, selected)) selected - pick.packages.toSet() else selected + pick.packages

    /** What "Only apps I choose" starts with when nothing has been chosen yet: the messaging apps on this phone. The person sees them switched on and can change them before saving. */
    fun suggestedSelection(installed: Collection<String>): Set<String> = available(installed).flatMap { it.packages }.toSet()
}

/** When the first-run choice is shown. It's a setting on the phone, kept across signing out, so it appears once per install, and Settings can open it again at any time. */
object RoutingOnboarding {
    fun shouldShow(signedIn: Boolean, onboarded: Boolean): Boolean = signedIn && !onboarded

    /**
     * The draft the screen starts from. A saved Only Selected list is kept as it is. Otherwise, choosing "Only apps I choose" starts with the installed messaging apps switched on, and the All Apps Except list
     * (edited only in Settings) is left alone.
     */
    fun startingDraft(saved: RoutingPrefs, installed: Collection<String>): RoutingPrefs =
        if (saved.selected.isNotEmpty()) saved else saved.copy(selected = QuickPicks.suggestedSelection(installed))

    /** The Save button's words, so the person sees exactly what pressing it does. */
    fun saveLabel(draft: RoutingPrefs): String = when (draft.mode) {
        RoutingMode.ONLY_SELECTED -> "Use Kiwi VPN for " + if (draft.selected.size == 1) "1 app" else "${draft.selected.size} apps"
        else -> "Use Kiwi VPN for all apps"
    }

    /** The two choices on the first-run screen; All Apps Except stays in Settings. Saving All Apps keeps the lists, so switching back later loses nothing. */
    fun choose(draft: RoutingPrefs, onlyChosen: Boolean): RoutingPrefs = draft.copy(mode = if (onlyChosen) RoutingMode.ONLY_SELECTED else RoutingMode.ALL_APPS)
}
