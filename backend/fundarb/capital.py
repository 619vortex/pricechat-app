"""Capital model: how much collateral a hedged pair ties up per $1 of notional on each leg.

A market is CROSS-eligible when it is neither onlyIsolated nor marginMode noCross / strictIsolated; otherwise it is an
ISOLATED bucket. Cross margin only works inside one margin account, and on Hyperliquid every perp dex is its own
clearinghouse with its own collateral token (verified 2026-10-24: clearinghouseState for one address returns different
account values for dex "" and dex "xyz"). So the bucket key is venue + dex + collateral, and a pair is cross-margined only
when both legs are cross-eligible in the SAME bucket. Two USDC dexes are still two accounts; a USDH dex never nets with a
USDC one; a cross-venue pair (Hyperliquid vs Lighter) is always two accounts.

Capital per $1 of notional per leg, at leverage L and buffer b (idle collateral as a share of initial margin):
  isolated / separate accounts: each leg posts its own margin and its own buffer
      2 x (1/L) x (1 + b)          = 2.5 / L at b = 0.25 (the "2.5x" capital tax: two legs x 1.25)
  cross, same account:          both legs' initial margin, one shared buffer (the hedge's P&L nets inside the account)
      2 x (1/L) + b x (1/L)        = 2.25 / L at b = 0.25
Leverage here only prices the capital: positions are sized from the risk budget, never from max leverage."""

from __future__ import annotations

from dataclasses import dataclass

ISOLATED_MODES = {"noCross", "strictIsolated"}


def market_bucket(only_isolated: bool, margin_mode: str, collateral: str, venue: str = "hyperliquid", dex: str = "") -> str:
    """"cross:<venue>:<dex>:<collateral>" or "isolated:<venue>:<dex>:<collateral>" ("native" for the main dex)."""
    kind = "isolated" if only_isolated or margin_mode in ISOLATED_MODES else "cross"
    return f"{kind}:{venue}:{dex or 'native'}:{collateral}"


def is_cross(bucket: str) -> bool:
    return bucket.startswith("cross:")


@dataclass(frozen=True)
class PairCapital:
    mode: str  # "cross" | "isolated"
    reason: str
    capital_per_notional: float  # collateral per $1 of notional on each leg


def pair_capital(bucket_a: str, bucket_b: str, leverage: float, buffer: float) -> PairCapital:
    if leverage <= 0:
        raise ValueError("leverage must be positive")
    if is_cross(bucket_a) and bucket_a == bucket_b:
        return PairCapital("cross", f"both legs cross-margined in one account ({bucket_a.split(':', 1)[1]})", (2 + buffer) / leverage)
    a, b = bucket_a.split(":"), bucket_b.split(":")
    if not (is_cross(bucket_a) and is_cross(bucket_b)):
        why = "an isolated-only / noCross leg"
    elif a[1:3] != b[1:3]:
        why = "separate margin accounts (different venue or dex)"
    else:
        why = "different collateral tokens"
    return PairCapital("isolated", why, 2 * (1 + buffer) / leverage)


def spot_perp_capital(leverage: float, buffer: float) -> PairCapital:
    """Long spot (no leverage: fully paid) + short perp (its own margin and buffer): 1 + (1 + b) / L per $1 of notional."""
    if leverage <= 0:
        raise ValueError("leverage must be positive")
    return PairCapital("spot-perp", "spot leg fully paid, perp leg margined", 1 + (1 + buffer) / leverage)


def capital_apr(net_apr: float, cap: PairCapital) -> float:
    """APR on the capital the pair ties up: net APR on notional / capital per $1 of notional."""
    return net_apr / cap.capital_per_notional
