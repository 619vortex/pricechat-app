"""What a Kiwi VPN desktop app's session may reach: the VPN, who's signed in, and signing out. Nothing else, so a token stolen from a PC can't spend, trade or change an account.

A browser session can't start with this prefix: "." isn't in token_urlsafe's alphabet, so random tokens can never begin this way.
"""

DESKTOP_TOKEN_PREFIX = "kd."
_EXACT = {"/api/members/vpn", "/api/members/me", "/api/members/logout", "/api/members/desktop/refresh"}
_UNDER = "/api/members/vpn/"


def desktop_token_may(path: str) -> bool:
    clean = path.rstrip("/") or "/"
    return clean in _EXACT or clean.startswith(_UNDER)
