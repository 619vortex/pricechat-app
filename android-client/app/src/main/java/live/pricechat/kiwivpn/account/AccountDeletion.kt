package live.pricechat.kiwivpn.account

import live.pricechat.kiwivpn.net.ApiException
import live.pricechat.kiwivpn.net.KiwiApi
import live.pricechat.kiwivpn.net.UNREACHABLE
import live.pricechat.kiwivpn.security.Vault

sealed interface DeletionResult {
    data object Deleted : DeletionResult

    /** signedOut: the session had already ended, so nothing was deleted and the person must sign in again first. */
    data class Failed(val message: String, val signedOut: Boolean = false) : DeletionResult
}

/**
 * In-app account deletion (Google Play requires it for apps with accounts). The server deletes the account; only after it confirms does this phone stop the tunnel and erase everything the app kept.
 * If the server refuses or can't be reached, nothing on the phone changes, so a failed attempt never leaves a half-deleted state. Plain Kotlin, tested with a fake API.
 */
class AccountDeletion(private val api: KiwiApi, private val vault: Vault, private val stopEverything: suspend () -> Unit) {
    /** What's deleted, as the screen lists it: read from backend/app/members/service.py delete_member and the database's cascades. */
    companion object {
        val WHAT_IS_DELETED = listOf(
            "Your Price Chat account (Kiwi Passport): username, display name, profile and sign-in methods (email, Google, X, Telegram)",
            "Your XP, rank history, badges, missions and referral code",
            "Your Kiwi VPN account: plan, free trial and any time left on it, which ends straight away",
            "Every Kiwi VPN device on this account, on every phone and PC. They stop working; servers drop them at their next check",
            "Browser extension logins and support reports you sent",
            "Every signed-in session, including this app",
        )
        val WHAT_IS_KEPT = listOf(
            "One-way codes showing a free trial or a one-time reward was used, so a new account with the same sign-in can't claim it again. They hold no name or address",
            "Records of any payments, with the link to your account removed",
            "A log line that an account was deleted, with no name",
        )
        const val ON_THIS_PHONE = "On this phone, Kiwi VPN disconnects and erases its saved session, VPN keys and settings."
    }

    /** Exactly what the server checks: the username, typed as it is. Spaces at either end are ignored. */
    fun canConfirm(typed: String, username: String?): Boolean = !username.isNullOrEmpty() && typed.trim() == username

    suspend fun delete(typed: String, username: String?): DeletionResult {
        if (username.isNullOrEmpty()) return DeletionResult.Failed("Your username hasn't loaded yet. Check your connection and try again.")
        if (!canConfirm(typed, username)) return DeletionResult.Failed("Type your username exactly as shown: $username")
        val token = vault.read().session ?: return DeletionResult.Failed("You're signed out. Sign in again to delete your account.", signedOut = true)
        try {
            api.deleteAccount(token, typed.trim())
        } catch (e: ApiException) {
            return when {
                e.signedOut -> DeletionResult.Failed("Your session has ended, so nothing was deleted. Sign in again, then delete your account.", signedOut = true)
                e.unreachable && e.message == UNREACHABLE -> DeletionResult.Failed("Couldn't reach Price Chat, so nothing was deleted. Check your connection and try again.")
                else -> DeletionResult.Failed(e.message)
            }
        }
        stopEverything()
        vault.eraseEverything()
        return DeletionResult.Deleted
    }
}
