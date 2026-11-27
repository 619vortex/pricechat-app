"""2026-11-25: every market move checked against Hyperliquid's trade.xyz market printed "trade.xyz" in its "Market data:" line, which the X
link check reads as a web address: X refused the post (Nasdaq -4.5%, event 1322; silver, 1286) and Telegram's package failed its own-links
check. The printed source line must never carry a domain-like name."""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.newsdesk import checks, livedata

VERIFIED = "Hyperliquid trade.xyz SP500 6,978.20 vs Yahoo 6,979.64: 0.02% apart (tolerance 0.60%)"


def _move(session: str = "intraday") -> SimpleNamespace:
    return SimpleNamespace(as_of=datetime(2026, 11, 25, 14, 44, tzinfo=timezone.utc), session=session)


def test_data_label_names_hyperliquid_without_the_domain() -> None:
    label = livedata.data_label(_move(), VERIFIED)
    assert label == "Market data: Yahoo Finance, matched against Hyperliquid, 14:44 UTC, intraday (US market open)."
    assert not checks.URLISH.search(label)


def test_crypto_and_single_source_labels_unchanged() -> None:
    assert livedata.data_label(_move("close"), "Hyperliquid 1,623.80 vs Yahoo 1,622.76: 0.06% apart") == \
        "Market data: Yahoo Finance, matched against Hyperliquid, 14:44 UTC, at the close."
    assert livedata.data_label(_move(""), "one price source (Yahoo Finance)") == "Market data: Yahoo Finance, 14:44 UTC."


def test_x_post_with_the_data_line_passes_the_link_check() -> None:
    text = "US STOCKS TUMBLE: NASDAQ −4.5% TO 22,675.10, S&P 500 −3.1% TO 6,979.64 TODAY\n\n" + livedata.data_label(_move(), VERIFIED)
    r = checks.check_post(text, source_text=text, source_names=[])
    assert all(c["passed"] for c in r.checks if c["check"] == "no_link"), r.checks
