"""Default ranks, XP rules, badges and entitlements. Seeded once; after that the dashboard owns the values.

Rank art is layered on the ONE canonical Kiwi (frontend/public/brand/kiwi.webp): a fixed `treatment` the website
knows how to render (scale, gold, aura...) plus an optional `overlay` image drawn to the Kiwi's 1000x844 frame.
Until real overlay art exists, `placeholder` marks the accessory slot. Nothing ever redraws the Kiwi itself.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.members import Badge, Entitlement, Rank, XPRule

RANKS: list[dict[str, Any]] = [
    {"level": 1, "key": "egg", "name": "The Egg", "min_xp": 0, "description": "Every Kiwi starts here.",
     "art": {"treatment": "egg", "overlay": None, "placeholder": "🥚", "slot": "frame"}},
    {"level": 2, "key": "chick", "name": "Kiwi Chick", "min_xp": 50, "description": "Hatched and finding your feet.",
     "art": {"treatment": "chick", "overlay": None, "placeholder": "🐣", "slot": None}},
    {"level": 3, "key": "kiwi", "name": "Kiwi", "min_xp": 150, "description": "A regular in the chat.",
     "art": {"treatment": "none", "overlay": None, "placeholder": None, "slot": None}},
    {"level": 4, "key": "scout", "name": "Scout Kiwi", "min_xp": 400, "description": "Out exploring the markets.",
     "art": {"treatment": "none", "overlay": None, "placeholder": "🎒", "slot": "back"}},
    {"level": 5, "key": "warrior", "name": "Warrior Kiwi", "min_xp": 900, "description": "Shows up when it counts.",
     "art": {"treatment": "none", "overlay": None, "placeholder": "⚔️", "slot": "hand"}},
    {"level": 6, "key": "knight", "name": "Knight Kiwi", "min_xp": 1800, "description": "Trusted by the community.",
     "art": {"treatment": "none", "overlay": None, "placeholder": "🛡️", "slot": "body"}},
    {"level": 7, "key": "royal", "name": "Royal Kiwi", "min_xp": 3500, "description": "Part of the inner circle.",
     "art": {"treatment": "none", "overlay": None, "placeholder": "👑", "slot": "head"}},
    {"level": 8, "key": "golden", "name": "Golden Kiwi", "min_xp": 6000, "description": "Rare, and earned.",
     "art": {"treatment": "gold", "overlay": None, "placeholder": None, "slot": None}},
    {"level": 9, "key": "legend", "name": "Kiwi Legend", "min_xp": 10000, "description": "Part of Price Chat history.",
     "art": {"treatment": "aura", "overlay": None, "placeholder": None, "slot": None}},
    {"level": 10, "key": "mythic", "name": "Mythic Kiwi", "min_xp": 16000, "description": "The rarest Kiwi of all.",
     "art": {"treatment": "mythic", "overlay": None, "placeholder": None, "slot": None}},
]

X_POLICY_NOTE = ("X's developer policy doesn't allow rewards (points or XP) for actions on X: posts, replies, likes or "
                 "reposts. You can still reply and repost from Price Chat; it just doesn't earn XP.")
X_ENGAGEMENT_RULES = ("x.mention_pricechat", "x.reply_pricechat", "x.like_pricechat", "x.repost_pricechat")

RULES: list[dict[str, Any]] = [
    # --- website: Price Chat's own events, the easiest to verify -----------------------------------------
    dict(event_type="website.account_created", label="Joined Price Chat", xp=20, max_per_member=1, verification_source="website",
         description="Once, when the account is created."),
    dict(event_type="website.profile_completed", label="Chose a username", xp=15, max_per_member=1, verification_source="website",
         description="Once, the first time a member picks their own username."),
    dict(event_type="watch.combo", label="Watch combo bonus", xp=0, daily_cap=60, verification_source="watch",
         description="Watch & Earn: the day's 2nd paid video pays double, the 3rd and later double +5 (on top of their normal XP)."),
    dict(event_type="earn.joined_hyperliquid", label="Joined Hyperliquid", xp=25, max_per_member=1, verification_source="earn",
         description="Once: you opened Hyperliquid with the Price Chat referral link from Earn."),
    dict(event_type="earn.first_trade", label="Your first bot signal", xp=50, max_per_member=1, verification_source="earn",
         description="Once: you joined, skipped or automated your first trading-bot signal on Earn."),
    dict(event_type="earn.joined_group", label="Joined the Price Chat bot group", xp=50, max_per_member=1, verification_source="earn",
         description="Once: Telegram confirms your linked account is in t.me/botpricechat."),
    dict(event_type="earn.bot_connected", label="Connected the trading bot", xp=100, max_per_member=1, verification_source="earn",
         description="Once: Hyperliquid shows an API wallet approved on your verified wallet (what /connect in the bot sets up)."),
    dict(event_type="deposit.tier", label="Hyperliquid deposit tier reached", xp=0, verification_source="wallet",
         description="Once per tier: $10, $100, $1,000 and $10,000 deposited into your own Hyperliquid account (read from "
                     "Hyperliquid's deposit records, never your balance)."),
    dict(event_type="wallet.verified", label="Connected a wallet: 100+ XP", xp=0, max_per_member=1, verification_source="wallet",
         description="Once per Passport and once per wallet: signing a message with your own Ethereum wallet pays at least 100 XP, or enough "
                     "to reach your next Kiwi rank if that's more. Signing is free and moves no funds."),
    # --- Pokémon Portfolio: modest, once each (adding and deleting cards can never farm XP) -------------------------
    dict(event_type="portfolio.first_card", label="Started a Pokémon Portfolio", xp=15, max_per_member=1, verification_source="website",
         description="Once, when the first card is added to your Pokémon Portfolio."),
    dict(event_type="portfolio.first_scan", label="Scanned a Pokémon card", xp=10, max_per_member=1, verification_source="website",
         description="Once, the first time a scanned card is added to your portfolio."),
    dict(event_type="portfolio.public", label="Shared your Pokémon Portfolio", xp=10, max_per_member=1, verification_source="website",
         description="Once, the first time you make your portfolio public."),
    dict(event_type="pack.open", label="Opened a Giveaway pack", xp=5, daily_cap=100, verification_source="packs",
         description="+5 XP per free virtual pack opened in the Pack Giveaway, up to 100 XP a day (20 packs). Just for fun: no money, no real cards."),
    dict(event_type="streak.day", label="Daily streak", xp=0, daily_cap=500, verification_source="streak",
         description="Once per UTC day, on your first completed task. Grows each day in a row: +5 on day 1, +2 more each day (max +25), "
                     "plus milestone bonuses on days 3, 7, 14, 30, 60 and 100. Missing a day starts again from day 1."),
    dict(event_type="website.login", label="Daily visit", xp=2, daily_cap=2, verification_source="website",
         description="Once per UTC day while signed in. Refreshing pays nothing."),
    dict(event_type="website.content_view", label="Opened a Price Chat video", xp=1, daily_cap=5, cooldown_seconds=20,
         verification_source="website",
         description="Once per video, at most 5 a day. Counts opening it from the website; YouTube does not tell us who watched."),
    dict(event_type="website.x_post_read", label="Read a Price Chat post", xp=1, daily_cap=10, verification_source="website",
         description="Reading one of Price Chat's X posts in your Kiwi Passport (on screen for a few seconds). Once per post, "
                     "at most 10 a day. Likes, reposts and replies on X never earn XP: X doesn't allow rewards for them."),
    dict(event_type="website.streak", label="7-day streak", xp=10, cooldown_seconds=6 * 86400, verification_source="website",
         description="Visited on 7 days in a row. At most once a week."),
    # --- linking an account (once per external account, ever) --------------------------------------------
    dict(event_type="identity.telegram_linked", label="Connected Telegram", xp=20, max_per_member=1, verification_source="telegram",
         description="Once per Telegram account, even after unlinking or deleting."),
    dict(event_type="identity.x_linked", label="Connected X", xp=20, max_per_member=1, verification_source="x",
         description="Once per X account, even after unlinking or deleting."),
    dict(event_type="identity.google_linked", label="Connected Google", xp=20, max_per_member=1, verification_source="google",
         description="Once per Google account, even after unlinking or deleting."),
    # --- telegram (authorised Price Chat chats only, linked accounts only) --------------------------------
    dict(event_type="telegram.message", label="Telegram message", xp=1, daily_cap=50, cooldown_seconds=5, verification_source="tgchat",
         description="+1 XP for every message you send in the Price Chat Telegram, up to 50 XP a day. Different messages only "
                     "(repeats, bot commands and forwards don't count), at least 5 seconds apart."),
    dict(event_type="telegram.active_day", label="Active community day", xp=2, daily_cap=2, verification_source="telegram",
         description="A day with 3 or more meaningful messages in an authorised Price Chat chat."),
    dict(event_type="telegram.first_participation", label="Took part in the Price Chat Telegram today", xp=3, daily_cap=3,
         verification_source="telegram",
         description="Your first meaningful message of the day in an authorised Price Chat chat (15+ characters, your own words, "
                     "not a forward, command or repeat). \"gm\", \"lol\" or a single emoji don't count."),
    dict(event_type="telegram.discussion", label="Kept the Telegram discussion going", xp=1, daily_cap=5, verification_source="telegram",
         description="+1 for each further meaningful message the same day, up to the daily cap. Repeats never count."),
    dict(event_type="telegram.helpful_reply", label="Helpful reply (moderator)", xp=2, daily_cap=2, verification_source="moderator",
         description="A moderator marks a Telegram reply as genuinely helpful."),
    dict(event_type="telegram.message_quality", label="Message quality", xp=0, enabled=False, verification_source="telegram",
         availability="unavailable", availability_note="Would need an AI to judge quality. XP is never given because an AI "
                                                        "thinks someone is supportive, so this stays off."),
    dict(event_type="telegram.reaction_received", label="Reactions received", xp=0, enabled=False, verification_source="telegram",
         availability="unavailable", availability_note="The read-only bot does not receive reaction counts for group messages."),
    # --- x (from posts the X integration already reads; linked accounts only) ----------------------------
    dict(event_type="x.mention_pricechat", label="Mentioned Price Chat on X", xp=3, daily_cap=6, min_member_age_hours=24,
         enabled=False, availability="unavailable", availability_note=X_POLICY_NOTE, verification_source="x", description="A post by your linked X account that mentions @pricechat and says something "
                                              "(15+ characters besides mentions and links)."),
    dict(event_type="x.reply_pricechat", label="Replied to Price Chat on X", xp=3, daily_cap=6, min_member_age_hours=24,
         enabled=False, availability="unavailable", availability_note=X_POLICY_NOTE, verification_source="x", description="A real reply (15+ characters) from your linked X account to a Price Chat post."),
    dict(event_type="x.like_pricechat", label="Liked a Price Chat post", xp=1, daily_cap=3, enabled=False, verification_source="x",
         availability="unavailable", availability_note=X_POLICY_NOTE),
    dict(event_type="x.repost_pricechat", label="Reposted Price Chat", xp=3, daily_cap=6, enabled=False, verification_source="x",
         availability="unavailable", availability_note=X_POLICY_NOTE,
         description="A repost of a Price Chat mission post by your linked X account, confirmed with X's 'reposted by' list."),
    # --- google / youtube ---------------------------------------------------------------------------------
    dict(event_type="google.youtube_comment", label="Commented on a Price Chat video", xp=2, daily_cap=4, enabled=False,
         verification_source="google", availability="unavailable",
         availability_note="Possible later: needs the member's YouTube channel linked and comment sync on Price Chat videos."),
    dict(event_type="google.youtube_subscribed", label="Subscribed on YouTube", xp=0, enabled=False, verification_source="google",
         availability="unavailable", availability_note="Subscriptions are private by default; we cannot verify them for a member."),
    dict(event_type="google.youtube_watch", label="Watched a video", xp=0, enabled=False, verification_source="google",
         availability="unavailable", availability_note="YouTube never tells a channel who watched. This will not be built."),
    # --- invites (members/referrals.py): paid once the invited member has done something real ---------------
    dict(event_type="referral.qualified", label="Invited a friend", xp=25, verification_source="referral",
         description="When someone you invited creates their Kiwi Passport and then completes a first verified action (by default: "
                     "connecting X or Telegram, or taking part in the Price Chat Telegram). Opening your link earns nothing; "
                     "inviting yourself never counts; each person counts once."),
    dict(event_type="referral.welcome", label="Welcome bonus", xp=10, max_per_member=1, verification_source="referral",
         description="Once, for joining through a friend's invite and completing your first verified action."),
    # --- Invite your crew (members/crew.py): a friend who joins the Price Chat Telegram through your bot invite --------
    dict(event_type="referral.telegram", label="Friend joined the Price Chat Telegram", xp=100, verification_source="referral",
         description="When a friend opens your personal Telegram invite, joins the Price Chat Telegram as a NEW member and is still "
                     "there after the check period. Sharing or copying the link earns nothing; each Telegram account counts once, ever; "
                     "yourself never counts."),
    dict(event_type="referral.crew_bonus", label="Crew bonus: 5 friends joined", xp=500, max_per_member=1, verification_source="referral",
         description="Once, when 5 friends you invited have joined the Price Chat Telegram and been verified."),
    # --- community / staff --------------------------------------------------------------------------------
    dict(event_type="mission.complete", label="Mission completed", xp=0, daily_cap=30, verification_source="mission",
         description="A Price Chat mission whose completion Price Chat verified. The mission sets the XP; at most 30 a day."),
    dict(event_type="community.event", label="Took part in a Price Chat event", xp=25, daily_cap=50, verification_source="moderator",
         description="Awarded by a moderator for a real event (AMA, live, community call)."),
    dict(event_type="manual.award", label="Manual adjustment", xp=0, verification_source="admin",
         description="An audited adjustment by Price Chat staff, always with a reason."),
    # --- future, premium: architecture only, cannot be switched on yet -------------------------------------
    dict(event_type="purchase.future", label="Partner purchase", xp=0, enabled=False, verification_source="partner",
         availability="future", category="premium", availability_note="Not active. Premium XP never counts towards rank."),
    dict(event_type="subscription.future", label="Price Chat subscription", xp=0, enabled=False, verification_source="partner",
         availability="future", category="premium", availability_note="Not active. Premium XP never counts towards rank."),
    dict(event_type="referral.future", label="Referral", xp=0, enabled=False, verification_source="website",
         availability="future", category="premium", availability_note="Not active."),
]

BADGES: list[dict[str, Any]] = [
    # Invite your crew milestones (verified Telegram friends, counted from the ledger)
    dict(key="first_friend", name="First Friend", description="A friend you invited joined the Price Chat Telegram.", kind="auto",
         criteria={"event_type": "referral.telegram", "count": 1}),
    dict(key="kiwi_recruiter", name="Kiwi Recruiter", description="3 friends you invited joined the Price Chat Telegram.", kind="auto",
         criteria={"event_type": "referral.telegram", "count": 3}),
    dict(key="community_builder", name="Community Builder", description="5 friends you invited joined the Price Chat Telegram.", kind="auto",
         criteria={"event_type": "referral.telegram", "count": 5}),
    dict(key="kiwi_ambassador", name="Kiwi Ambassador", description="10 friends you invited joined the Price Chat Telegram.", kind="auto",
         criteria={"event_type": "referral.telegram", "count": 10}),
    dict(key="early_kiwi", name="Early Kiwi", description="Founding community member.", kind="auto",
         criteria={"joined_before": "2027-01-01T00:00:00+00:00"}),
    dict(key="first_100", name="First 100", description="One of the first 100 Price Chat members.", kind="auto",
         criteria={"member_number_max": 100}),
    dict(key="conversation_starter", name="Conversation Starter", description="10 days taking part in the Price Chat Telegram.",
         kind="auto", criteria={"event_type": "telegram.first_participation", "count": 10}),
    dict(key="community_regular", name="Community Regular", description="30 days taking part in the Price Chat Telegram.",
         kind="auto", criteria={"event_type": "telegram.first_participation", "count": 30}),
    dict(key="content_supporter", name="Content Supporter", description="Completed 10 Price Chat content missions.",
         kind="auto", criteria={"event_type": "mission.complete", "count": 10}),
    dict(key="telegram_og", name="Telegram OG", description="Part of the Price Chat Telegram from the early days.", kind="manual"),
    dict(key="market_nerd", name="Market Nerd", description="Known for great market discussion.", kind="manual"),
    dict(key="collector", name="Collector", description="Part of the collectibles community.", kind="manual"),
    dict(key="og", name="OG", description="Special recognition from the Price Chat team.", kind="manual"),
    dict(key="partner", name="Partner", description="Supported Price Chat through a partner (future).", kind="partner", enabled=False),
]

ENTITLEMENTS: list[dict[str, Any]] = [
    dict(key="profile_cosmetic", name="Profile cosmetic", required_rank=3,
         description="A special finish on your Kiwi Passport."),
    dict(key="community_badge", name="Community badge", required_rank=5,
         description="A community badge shown next to your name."),
    dict(key="special_content", name="Special content", required_rank=7,
         description="Access to special Price Chat content (coming later)."),
    dict(key="vip_access", name="VIP access", required_rank=8,
         description="Golden Kiwi perks: VIP Telegram, early content, events. Not active yet; nothing is created automatically."),
    dict(key="kiwi_vpn", name="Kiwi VPN", required_rank=None,
         description="A private WireGuard VPN (up to 3 devices). Not unlocked by rank."),
]


def ensure_catalog(db: Session) -> None:
    """Insert any missing defaults. Existing rows (edited in the dashboard) are never overwritten."""
    changed = False
    have = set(db.scalars(select(Rank.key)))
    for r in RANKS:
        if r["key"] not in have:
            db.add(Rank(**r))
            changed = True
    have = set(db.scalars(select(XPRule.event_type)))
    for r in RULES:
        if r["event_type"] not in have:
            db.add(XPRule(**r))
            changed = True
    # policy lock (every start): X's developer policy forbids rewards for actions on X, so these never pay
    for rule in db.scalars(select(XPRule).where(XPRule.event_type.in_(X_ENGAGEMENT_RULES))):
        if rule.enabled or rule.availability != "unavailable" or rule.availability_note != X_POLICY_NOTE:
            rule.enabled, rule.availability, rule.availability_note = False, "unavailable", X_POLICY_NOTE
            changed = True
    have = set(db.scalars(select(Badge.key)))
    for b in BADGES:
        if b["key"] not in have:
            db.add(Badge(**b))
            changed = True
    have = set(db.scalars(select(Entitlement.key)))
    for e in ENTITLEMENTS:
        if e["key"] not in have:
            db.add(Entitlement(**e))
            changed = True
    if changed:
        db.commit()
