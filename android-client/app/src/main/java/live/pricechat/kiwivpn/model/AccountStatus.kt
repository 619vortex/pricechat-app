package live.pricechat.kiwivpn.model

/** What the app shows and allows for an account. The server decides everything: this only reads its answer, and never starts or assumes a trial. */
enum class StatusKind { TRIAL, PAID, UNLIMITED, TESTER, NEEDS_TRIAL, EXPIRED, CLOSED, BLOCKED, UNAVAILABLE }

data class AccountStatus(val kind: StatusKind, val label: String, val detail: String?, val canConnect: Boolean, val canStartTrial: Boolean)

private fun days(n: Long?): String = when (n) {
    null -> "time left unknown"
    0L -> "less than a day left"
    1L -> "1 day left"
    else -> "$n days left"
}

fun accountStatus(account: Account?): AccountStatus {
    if (account == null) return AccountStatus(StatusKind.UNAVAILABLE, "Account unavailable", "Couldn't reach Price Chat. Check your connection.", canConnect = false, canStartTrial = false)
    val offer = account.trial?.available == true
    val plan = account.plan
    return when (plan?.kind) {
        "trial" -> AccountStatus(StatusKind.TRIAL, "Free trial: ${days(plan.daysLeft)}", null, account.eligible, false)
        "paid" -> AccountStatus(StatusKind.PAID, "Subscription: ${days(plan.daysLeft)}", null, account.eligible, false)
        "tester" -> AccountStatus(StatusKind.TESTER, "Tester access: ${days(plan.daysLeft)}", null, account.eligible, false)
        "owner", "royal", "free", "granted" -> AccountStatus(StatusKind.UNLIMITED, "Active", null, account.eligible, false)
        "not_activated" ->
            if (offer) AccountStatus(StatusKind.NEEDS_TRIAL, "Free ${account.trial.days}-day trial available", "One per account.", false, true)
            else AccountStatus(StatusKind.EXPIRED, "No active plan", account.reason, false, false)
        "expired" -> AccountStatus(StatusKind.EXPIRED, "Plan ended", account.reason, false, false)
        "closed" -> AccountStatus(StatusKind.CLOSED, "Kiwi VPN is unavailable", account.reason, false, false)
        "blocked" -> AccountStatus(StatusKind.BLOCKED, "This account can't use Kiwi VPN", account.reason, false, false)
        // A kind this version doesn't know: follow the server's own yes or no, and say what it said.
        else -> AccountStatus(if (account.eligible) StatusKind.UNLIMITED else StatusKind.UNAVAILABLE, if (account.eligible) "Active" else "Not available", account.reason, account.eligible, offer)
    }
}

/** The last server used if it's still online, else the backend's recommendation, else any online one. The same rule as the Windows app. */
fun suggestedServer(servers: List<Server>, last: Long?): Server? =
    servers.firstOrNull { it.id == last && it.online } ?: servers.firstOrNull { it.online && it.recommended } ?: servers.firstOrNull { it.online }
