"""Kiwi Passport 2.0: the badge collection, rarity tiers, the Founding Kiwi limits and the community reputation score's rules.

Nothing here touches XP, ranks or rewards. Badges and the community score never have money value and never count towards the rank.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

FAMILIES: dict[str, dict[str, Any]] = {
    "founders": {"name": "Founders", "color": "#00e58f", "sort": 1},
    "community": {"name": "Community", "color": "#3fa9f5", "sort": 2},
    "predictor": {"name": "Predictor", "color": "#f5c542", "sort": 3},
    "streak": {"name": "Streak", "color": "#ff7a45", "sort": 4},
    "collector": {"name": "Collector", "color": "#e05ad6", "sort": 5},
    "recruiter": {"name": "Recruiter", "color": "#7fd6ff", "sort": 6},
    "verified": {"name": "Verified", "color": "#9aa7a0", "sort": 7},
    "honours": {"name": "Honours", "color": "#c7d0d6", "sort": 8},
}
TIERS = ("bronze", "silver", "gold", "mythic")

# Founding Kiwi: numbered, for the first FOUNDING_CAP eligible members or until FOUNDING_UNTIL, whichever comes first
FOUNDING_KEY = "founding_kiwi"
FOUNDING_CAP = 100
FOUNDING_UNTIL = datetime(2027, 1, 1, tzinfo=timezone.utc)

# The badges that already exist keep their key, name, rule and holders: they only join a collection (family, tier, title).
EXISTING: dict[str, dict[str, Any]] = {
    "early_kiwi": dict(family="founders", tier="bronze", title="Early Kiwi", sort=12),
    "first_100": dict(family="founders", tier="silver", title=None, sort=11),
    "conversation_starter": dict(family="community", tier="bronze", title="Conversation Starter", sort=20),
    "community_regular": dict(family="community", tier="silver", title="Telegram Regular", sort=21),
    "content_supporter": dict(family="community", tier="bronze", title="Content Supporter", sort=24),
    "first_friend": dict(family="recruiter", tier="bronze", title="Recruiter", sort=60),
    "kiwi_recruiter": dict(family="recruiter", tier="silver", title="Kiwi Recruiter", sort=61),
    "community_builder": dict(family="recruiter", tier="gold", title="Community Builder", sort=62),
    "kiwi_ambassador": dict(family="recruiter", tier="mythic", title="Kiwi Ambassador", sort=63),
    # Price Chat Predictions' own badges (app/predictions/rules.py, live since 28 Nov): kept as they are, now in the Predictor family
    "pred_first_call": dict(family="predictor", tier="bronze", title="Caller", sort=30),
    "pred_hat_trick": dict(family="predictor", tier="bronze", title="Hat-trick", sort=31),
    "pred_sharp_kiwi": dict(family="predictor", tier="silver", title="Sharp Kiwi", sort=32),
    "pred_against_crowd": dict(family="predictor", tier="silver", title="Contrarian", sort=33),
    "pred_regular": dict(family="predictor", tier="silver", title="Regular Caller", sort=34),
    "pred_perfect_week": dict(family="predictor", tier="gold", title="Perfect Week", sort=35),
    "pred_oracle_kiwi": dict(family="predictor", tier="mythic", title="Oracle Kiwi", sort=37),
    "telegram_og": dict(family="honours", tier="gold", title="Telegram OG", sort=80),
    "market_nerd": dict(family="honours", tier="gold", title="Market Nerd", sort=81),
    "collector": dict(family="honours", tier="gold", title="Collector", sort=82),
    "og": dict(family="honours", tier="mythic", title="OG", sort=83),
    "partner": dict(family="honours", tier="gold", title=None, sort=84),
}

# New badges (added only while PASSPORT2_MODE is on). Criteria kinds are in app/members/badges._meets.
NEW: list[dict[str, Any]] = [
    dict(key=FOUNDING_KEY, name="Founding Kiwi", kind="auto", family="founders", tier="mythic", title="Founding Kiwi", sort=10,
         description=f"One of the first {FOUNDING_CAP} Kiwi Passports, numbered in join order. Closes 1 January 2027.",
         criteria={"founding": True}),
    dict(key="pillar", name="Pillar of the Chat", kind="auto", family="community", tier="gold", title="Pillar of the Chat", sort=22,
         description="Took part in the Price Chat Telegram on 90 different days.",
         criteria={"event_type": "telegram.first_participation", "count": 90}),
    dict(key="helpful_kiwi", name="Helpful Kiwi", kind="auto", family="community", tier="silver", title="Helpful Kiwi", sort=23,
         description="25 replies in the Telegram that the community found helpful.",
         criteria={"event_type": "telegram.helpful_reply", "count": 25}),
    dict(key="sharp_caller", name="Sharp Caller", kind="auto", family="predictor", tier="gold", title="Sharp Caller", sort=36,
         description="Right on at least 60% of 30 or more decided Predictions calls.",
         criteria={"predictor": "accuracy", "min": 0.6, "decided": 30}),
    dict(key="seven_days", name="Seven Days", kind="auto", family="streak", tier="bronze", title="Daily Kiwi", sort=40,
         description="A 7-day Passport streak.", criteria={"best_streak": 7}),
    dict(key="thirty_days", name="Thirty Days", kind="auto", family="streak", tier="silver", title="Dedicated", sort=41,
         description="A 30-day Passport streak.", criteria={"best_streak": 30}),
    dict(key="hundred_club", name="Hundred Club", kind="auto", family="streak", tier="gold", title="Hundred Club", sort=42,
         description="A 100-day Passport streak.", criteria={"best_streak": 100}),
    dict(key="first_pull", name="First Pull", kind="auto", family="collector", tier="bronze", title="Pack Opener", sort=50,
         description="Opened a first pack in the Pack Giveaway.", criteria={"packs_opened": 1}),
    dict(key="binder_keeper", name="Binder Keeper", kind="auto", family="collector", tier="silver", title="Binder Keeper", sort=51,
         description="25 different cards in the Pack Giveaway binder.", criteria={"binder_cards": 25}),
    dict(key="binder_master", name="Binder Master", kind="auto", family="collector", tier="gold", title="Binder Master", sort=52,
         description="100 different cards in the Pack Giveaway binder.", criteria={"binder_cards": 100}),
    dict(key="telegram_verified", name="Telegram Verified", kind="auto", family="verified", tier="bronze", title=None, sort=70,
         description="Linked a Telegram account to the Passport.", criteria={"linked": "telegram"}),
    dict(key="x_verified", name="X Verified", kind="auto", family="verified", tier="bronze", title=None, sort=71,
         description="Linked an X account to the Passport.", criteria={"linked": "x"}),
    dict(key="wallet_verified", name="Wallet Verified", kind="auto", family="verified", tier="silver", title=None, sort=72,
         description="Proved a wallet with a signed message.", criteria={"event_type": "wallet.verified", "count": 1}),
]
NEW_KEYS = {b["key"] for b in NEW}


def rarity(holders: int, members: int, serial: int | None = None) -> dict[str, Any]:
    """The share of eligible members who hold a badge, and its rarity tier."""
    share = holders / members if members else 0.0
    if holders == 0:
        label = "Unclaimed"
    elif share >= 0.4:
        label = "Common"
    elif share >= 0.15:
        label = "Uncommon"
    elif share >= 0.05:
        label = "Rare"
    elif share >= 0.01:
        label = "Epic"
    else:
        label = "Legendary"
    return {"holders": holders, "members": members, "share": round(share, 4), "label": label}


# --- the community reputation score -----------------------------------------------------------------------------------------------
# Genuine participation only, over the last WINDOW_DAYS: taking part in the Telegram, helping others, content missions, friends who
# stayed, and Predictions calls (taking part, not being right). Never packs, purchases, deposits, trading, sign-ins or linking
# accounts. It is computed from the existing ledger every time (nothing stored), so it changes no XP balance, rank or reward.
WINDOW_DAYS = 90
SCORE: list[dict[str, Any]] = [
    dict(key="active_days", label="Days taking part in the Telegram", event="telegram.first_participation", points=10, day_cap=1),
    dict(key="discussion", label="Real conversation", event="telegram.discussion", points=1, day_cap=5),
    dict(key="helpful", label="Helpful replies", event="telegram.helpful_reply", points=8, day_cap=3),
    dict(key="missions", label="Community missions", event="mission.complete", points=4, day_cap=3),
    dict(key="friends", label="Friends who joined and stayed", event="referral.telegram", points=25, day_cap=None),
    dict(key="predictions", label="Days making a Predictions call", event=None, points=3, day_cap=1),
]
FLAG_PENALTY = 50  # a moderation flag a person upheld (status "resolved") in the window
LEVELS: list[tuple[int, str]] = [(0, "Newcomer"), (50, "Participant"), (200, "Regular"), (500, "Pillar"), (1000, "Legend")]


def level_for(score: int) -> dict[str, Any]:
    name, floor, nxt, nxt_name = LEVELS[0][1], 0, None, None
    for i, (at, n) in enumerate(LEVELS):
        if score >= at:
            name, floor = n, at
            nxt, nxt_name = (LEVELS[i + 1] if i + 1 < len(LEVELS) else (None, None))
    return {"name": name, "floor": floor, "next_at": nxt, "next_name": nxt_name}


# --- the public page ----------------------------------------------------------------------------------------------------------------
SECTIONS = ("community", "predictions", "streak", "platforms", "badges")
DEFAULT_SECTIONS = {k: True for k in SECTIONS}
FEATURED_SLOTS = 3
BIO_MAX = 80
PLATFORMS = ("telegram", "x", "google", "youtube")  # shown as a tick only: never a handle, id or address
