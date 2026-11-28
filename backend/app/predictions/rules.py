"""Price Chat Predictions: the fixed rules. Times, XP, the Predictor progression, achievements, leaderboard points and minimums.

Owner decisions (2026-11-27): Bitcoin only for the first two weeks; daily call opens 13:30 UTC and locks 20:00 UTC, settling on the
Hyperliquid daily close (00:00 UTC); weekly call opens Monday 13:30 UTC, locks Wednesday 23:59 UTC, settles on Sunday's daily close;
the split stays hidden until a call locks; unlinked Telegram members play at once; Predictor progression and achievements are separate
and prediction XP never unlocks a monetary or trading reward.

Why prediction XP can't reach a money reward: it is written to the shared ledger (reputation_events) under its own category,
CATEGORY = "predictor". members/xp.recalc only adds "reputation" (xp_total, which makes the Kiwi rank score) and "premium"; every
leaderboard and the rank rewards (rewards.py: Knight's trading reward, Royal's VIP) read the rank score or "reputation" rows only.
tests/test_predictions.py holds that line.
"""

from __future__ import annotations

from datetime import time

CATEGORY = "predictor"
SOURCE = "predictions"
ASSETS = ("BTC",)  # Bitcoin only to start (owner, 2026-11-27)

DAILY_OPEN = time(13, 30)
DAILY_LOCK = time(20, 0)
WEEKLY_OPEN = time(13, 30)  # Monday
WEEKLY_LOCK_WEEKDAY, WEEKLY_LOCK = 2, time(23, 59)  # Wednesday 23:59 UTC
REMIND_BEFORE_LOCK_MIN = 60
SECOND_READ_AFTER_MIN = 10  # settlement reads the closed candle twice, this far apart, and settles only if both match
HOLD_AFTER_HOURS = 6  # no matching reads by then: the call is held for the owner
RANGE_STEP = 0.04  # weekly ranges: under -4%, -4..0, 0..+4, over +4% around the reference close
RANGE_ROUND = 500.0

# XP (ledger category "predictor"): (event_type, label, xp, daily_cap)
XP_RULES: list[dict] = [
    dict(event_type="predictions.daily_play", label="Made the daily call", xp=2, daily_cap=2),
    dict(event_type="predictions.daily_right", label="Daily call right", xp=8, daily_cap=8),
    dict(event_type="predictions.weekly_play", label="Made the weekly call", xp=5, daily_cap=5),
    dict(event_type="predictions.weekly_right", label="Weekly call right", xp=30, daily_cap=30),  # 4 ranges: ×1.5 of 20
    dict(event_type="predictions.contrarian", label="Right against the crowd", xp=4, daily_cap=8),
    dict(event_type="predictions.first_call", label="First call", xp=5, daily_cap=5),
    dict(event_type="predictions.streak_3", label="3 right in a row", xp=10, daily_cap=10),
    dict(event_type="predictions.streak_5", label="5 right in a row", xp=25, daily_cap=25),
    dict(event_type="predictions.streak_10", label="10 right in a row", xp=60, daily_cap=60),
    dict(event_type="predictions.perfect_week", label="Perfect week (7 of 7 daily calls right)", xp=40, daily_cap=40),
    dict(event_type="predictions.regular_30", label="Called 30 days running", xp=30, daily_cap=30),
]
XP = {r["event_type"]: r["xp"] for r in XP_RULES}
MIN_MEMBER_AGE_HOURS = 24  # a Passport must be a day old before prediction XP is written (pending until then)
CONTRARIAN_SHARE = 0.35  # right while under 35% of callers agreed
STREAKS = ((3, "predictions.streak_3"), (5, "predictions.streak_5"), (10, "predictions.streak_10"))
PENDING_DAYS = 90  # unlinked Telegram XP waits this long for a Passport link

# The Predictor progression: separate from the Kiwi ranks, by prediction XP
LEVELS: list[tuple[int, str]] = [(0, "Hatchling"), (50, "Spotter"), (150, "Caller"), (400, "Sharp Caller"), (900, "Market Reader"),
                                 (1800, "Strategist"), (3500, "Oracle"), (6000, "Grand Oracle")]

# Achievements (badges table, auto: awarded when the matching ledger event exists)
BADGES: list[dict] = [
    dict(key="pred_first_call", name="First Call", description="Made a first Price Chat Predictions call.",
         criteria={"event_type": "predictions.first_call", "count": 1}),
    dict(key="pred_hat_trick", name="Hat-trick", description="3 calls right in a row.", criteria={"event_type": "predictions.streak_3", "count": 1}),
    dict(key="pred_sharp_kiwi", name="Sharp Kiwi", description="5 calls right in a row.", criteria={"event_type": "predictions.streak_5", "count": 1}),
    dict(key="pred_oracle_kiwi", name="Oracle Kiwi", description="10 calls right in a row.", criteria={"event_type": "predictions.streak_10", "count": 1}),
    dict(key="pred_perfect_week", name="Perfect Week", description="7 of 7 daily calls right in one week.",
         criteria={"event_type": "predictions.perfect_week", "count": 1}),
    dict(key="pred_against_crowd", name="Against the Crowd", description="Right when under 35% of callers agreed.",
         criteria={"event_type": "predictions.contrarian", "count": 1}),
    dict(key="pred_regular", name="Regular", description="Made a call 30 days running.", criteria={"event_type": "predictions.regular_30", "count": 1}),
]

# Leaderboard points (not XP: making calls doesn't outrank making good ones)
POINTS = {"daily_right": 10, "weekly_right": 38, "contrarian": 5}
MIN_CALLS = {"week": 3, "month": 10, "all": 25}  # settled calls needed to appear on a board
MIN_FOR_ACCURACY = 10  # accuracy is shown only from this many settled calls

TAPS_PER_MINUTE = 10
GROUP_CHECK_HOURS = 24  # a voter's group membership is re-checked this often
TALLY_EDIT_SECONDS = 60  # the group post's count is edited at most this often
GROUP_REPLY_TTL = 60  # a command reply in the group deletes itself after this


def level_for(xp: int) -> dict:
    cur = LEVELS[0]
    nxt = None
    for i, (need, name) in enumerate(LEVELS):
        if xp >= need:
            cur = (need, name)
            nxt = LEVELS[i + 1] if i + 1 < len(LEVELS) else None
    return {"level": LEVELS.index(cur) + 1, "name": cur[1], "xp": xp, "floor": cur[0], "next_at": nxt[0] if nxt else None,
            "next_name": nxt[1] if nxt else None}
