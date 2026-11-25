"""Number formatting for Findings. Every figure a post may show is formatted HERE, once, and stored with the Finding: the writer never
formats a number, and the figures gate compares the final text against exactly these strings."""

from __future__ import annotations

from datetime import date

MINUS = "−"  # the typographic minus used in posts; the figures gate treats it like "-"
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _dp(x: float) -> int:
    a = abs(x)
    return 0 if a >= 100 else 1


def pct(x: float, *, signed: bool = True, dp: int | None = None) -> str:
    """x is a percentage (e.g. -27.23 -> '−27.2%')."""
    d = _dp(x) if dp is None else dp
    s = f"{abs(x):,.{d}f}%"
    if not signed:
        return s
    if round(abs(x), d) == 0:
        return f"{0:.{d}f}%"
    return ("+" if x > 0 else MINUS) + s


def pts(x: float, dp: int = 1) -> str:
    return f"{abs(x):,.{dp}f}"


def num(x: float, dp: int = 0) -> str:
    return f"{x:,.{dp}f}"


def money(x: float) -> str:
    a = abs(x)
    if a >= 1e9:
        s = f"${a / 1e9:,.2f}B"
    elif a >= 1e6:
        s = f"${a / 1e6:,.1f}M"
    elif a >= 1000:
        s = f"${a:,.0f}"
    elif a >= 1:
        s = f"${a:,.2f}"
    else:
        s = f"${a:.4g}"
    return (MINUS if x < 0 else "") + s


def ratio(x: float) -> str:
    return f"{x:.4f}" if x < 1 else f"{x:,.2f}"


def mult(x: float) -> str:
    return f"{x:.1f}x"


def day(d: date) -> str:
    return f"{MON[d.month - 1]} {d.day}, {d.year}"


def month_year(d: date) -> str:
    return f"{MONTHS[d.month - 1]} {d.year}"


def month(d: date) -> str:
    return MONTHS[d.month - 1]


def year(d: date | int) -> str:
    return str(d if isinstance(d, int) else d.year)


def raw(x: float, dp: int = 4) -> str:
    """Full-precision figure for calculation text (not for posts)."""
    return f"{x:,.{dp}f}"
