"""Community Intelligence: what counts, what's refused, and what a tip can earn.

Members earn community reputation (the Passport 2.0 community score) and Researcher badges only: never XP, money or any reward with
money value. Nothing a member sends is ever published automatically: an editor decides every tip, and a story built on one goes out
only through the desk's normal gates when the owner publishes it.
"""

from __future__ import annotations

from datetime import timedelta

KINDS = ("news", "research", "anomaly", "discovery")
KIND_LABEL = {"news": "News", "research": "Research", "anomaly": "Market anomaly", "discovery": "Discovery"}
STATUSES = ("checking", "review", "needs_info", "accepted", "merged", "duplicate", "rejected", "withdrawn")
OPEN = ("checking", "review", "needs_info")

TITLE_MAX, NOTE_MAX, APPEAL_MAX, EDITOR_NOTE_MAX = 160, 600, 400, 400

# --- who may send, and how often (anti-flood; sending alone never earns anything) -------------------------------------------------
MIN_PASSPORT_AGE = timedelta(days=3)  # a brand-new account can't send tips (throwaway accounts)
PER_DAY = {"Newcomer": 2, "Participant": 3, "Regular": 5, "Pillar": 5, "Legend": 5}  # by community level
MIN_GAP = timedelta(seconds=60)
SPAM_LOCKOUT = 3  # upheld spam / misleading rejections in the last 90 days: no more tips until one is overturned on appeal
DUP_WINDOW = timedelta(days=30)  # the same link or forwarded post counts as a duplicate within this window
SIMILAR_WINDOW = timedelta(days=7)
SIMILAR_AT = 0.72
STORY_WINDOW = timedelta(hours=72)
APPEAL_WINDOW = timedelta(days=14)

# --- reputation (community score; never XP) --------------------------------------------------------------------------------------
POINTS = {"accepted": 15, "research": 25, "published": 40, "spam": -50}
ACCEPTED_PER_DAY = 3  # accepted tips that count per member per decision day (the rest are still accepted, just not scored)
SPAM_REASONS = {"promotional", "misleading"}

REASONS: dict[str, str] = {
    "not_enough": "Not enough to act on yet",
    "no_source": "No source we could check",
    "covered": "Already covered",
    "not_relevant": "Not market-relevant",
    "unverified": "Couldn't be independently verified",
    "promotional": "Promotional or a pump",
    "misleading": "Misleading",
}

# --- sources (classified from the link's address; the member's link is never fetched by the server) -----------------------------
SHORTENERS = {"bit.ly", "t.co", "tinyurl.com", "goo.gl", "ow.ly", "buff.ly", "is.gd", "rb.gy", "cutt.ly", "shorturl.at", "lnkd.in", "trib.al"}
OFFICIAL_SUFFIXES = (".gov", ".gov.uk", ".europa.eu", ".gc.ca", ".gov.au", ".go.jp", ".gouv.fr", ".bund.de")
OFFICIAL_HOSTS = {"sec.gov", "federalreserve.gov", "ecb.europa.eu", "bankofengland.co.uk", "boj.or.jp", "imf.org", "worldbank.org", "bis.org",
                  "cftc.gov", "fca.org.uk", "esma.europa.eu", "snb.ch", "rba.gov.au", "bankofcanada.ca", "opec.org", "iea.org"}
OUTLETS = {"reuters.com": "Reuters", "bloomberg.com": "Bloomberg", "ft.com": "Financial Times", "wsj.com": "Wall Street Journal",
           "apnews.com": "AP", "cnbc.com": "CNBC", "bbc.co.uk": "BBC", "bbc.com": "BBC", "nytimes.com": "New York Times",
           "theguardian.com": "The Guardian", "economist.com": "The Economist", "marketwatch.com": "MarketWatch", "barrons.com": "Barron's",
           "axios.com": "Axios", "politico.com": "Politico", "aljazeera.com": "Al Jazeera", "nikkei.com": "Nikkei", "asia.nikkei.com": "Nikkei",
           "coindesk.com": "CoinDesk", "theblock.co": "The Block", "decrypt.co": "Decrypt", "cointelegraph.com": "Cointelegraph",
           "finance.yahoo.com": "Yahoo Finance", "investing.com": "Investing.com", "forbes.com": "Forbes", "fortune.com": "Fortune",
           "businessinsider.com": "Business Insider", "semafor.com": "Semafor", "theinformation.com": "The Information", "dlnews.com": "DL News"}
SOCIAL = {"x.com", "twitter.com", "t.me", "reddit.com", "youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com",
          "discord.com", "discord.gg", "threads.net", "farcaster.xyz", "warpcast.com", "medium.com", "substack.com"}
MARKET_DATA = {"hyperliquid.xyz", "app.hyperliquid.xyz", "coinglass.com", "defillama.com", "tradingview.com", "coingecko.com",
               "coinmarketcap.com", "glassnode.com", "dune.com", "etherscan.io", "solscan.io", "arkhamintelligence.com", "intel.arkm.com",
               "tokenterminal.com", "artemis.xyz", "messari.io", "kaiko.com", "fred.stlouisfed.org", "cmegroup.com"}
ANOMALY_WORDS = ("funding", "liquidat", "open interest", " oi ", "basis", "spread", "depeg", "de-peg", "volume spike", "outflow", "inflow",
                 "whale", "premium", "discount", "skew", "short squeeze", "unusual")
PUMP_WORDS = ("100x", "1000x", "10x", "moon", "to the moon", "guaranteed", "get in now", "don't miss", "last chance", "next 100x", "gem",
              "presale", "pre-sale", "airdrop now", "ape in", "send it")

# --- Researcher badges (Passport 2.0 collection, family "researcher") -------------------------------------------------------------
BADGES = [
    dict(key="ci_first_lead", name="First Lead", kind="auto", family="researcher", tier="bronze", title="Researcher", sort=90,
         description="A first tip accepted by the Price Chat desk.", criteria={"ci": "accepted", "min": 1}),
    dict(key="ci_sharp_eye", name="Sharp Eye", kind="auto", family="researcher", tier="silver", title="Sharp Eye", sort=91,
         description="10 tips accepted by the desk.", criteria={"ci": "accepted", "min": 10}),
    dict(key="ci_first_spotted", name="First Spotted", kind="auto", family="researcher", tier="gold", title="First Spotted", sort=92,
         description="A published story you tipped before any outlet in the Terminal had it.", criteria={"ci": "first_spotted", "min": 1}),
    dict(key="ci_anomaly_hunter", name="Anomaly Hunter", kind="auto", family="researcher", tier="silver", title="Anomaly Hunter", sort=93,
         description="3 market anomalies confirmed against live data.", criteria={"ci": "anomalies", "min": 3}),
    dict(key="ci_primary_source", name="Primary Source", kind="auto", family="researcher", tier="silver", title="Primary Source", sort=94,
         description="5 accepted tips citing an official filing or statement.", criteria={"ci": "official", "min": 5}),
    dict(key="ci_analyst", name="Analyst", kind="auto", family="researcher", tier="gold", title="Analyst", sort=95,
         description="3 research pieces accepted, their data checked by an editor.", criteria={"ci": "research", "min": 3}),
    dict(key="ci_fact_checker", name="Fact Checker", kind="auto", family="researcher", tier="bronze", title="Fact Checker", sort=96,
         description="A tip that showed a circulating claim was false, confirmed by an editor.", criteria={"ci": "fact_checks", "min": 1}),
    dict(key="ci_researcher_month", name="Researcher of the Month", kind="auto", family="researcher", tier="mythic", title="Researcher of the Month",
         sort=97, description="Top of the monthly Researcher board. Numbered: one a month.", criteria={"ci_month": True}),
]
BADGE_KEYS = {b["key"] for b in BADGES}
MONTH_MIN_POINTS = 40  # a Researcher of the Month needs at least one published story's worth of points
