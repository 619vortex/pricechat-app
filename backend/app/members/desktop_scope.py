"""What a Kiwi VPN desktop app's session may reach: the VPN, who's signed in, and signing out. Nothing else, so a token stolen from a PC can't spend, trade or change an account.

A browser session can't start with this prefix: token_urlsafe's output is random and every other session is made without one, so a prefix can only come from the desktop sign-in.
"""

DESKTOP_TOKEN_PREFIX = "kd_"
_EXACT = {"/api/members/vpn", "/api/members/me", "/api/members/logout"}
_UNDER = "/api/members/vpn/"


def desktop_token_may(path: str) -> bool:
    clean = path.rstrip("/") or "/"
    return clean in _EXACT or clean.startswith(_UNDER)
