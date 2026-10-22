"""Brand Manager defaults: the content strategy a brand starts with, so the owner can just press START AUTO."""

from typing import Any

# The pipeline, in order. `where` = who runs it: the backend (light work) or an AI worker (LLM, voice, video).
STAGES: tuple[tuple[str, str, str], ...] = (
    ("discover", "Discover topic", "backend"),
    ("research", "Research", "backend"),
    ("script", "Write script", "worker"),
    ("voice", "Generate voice", "worker"),
    ("visuals", "Create visuals", "worker"),
    ("assemble", "Assemble video", "worker"),
    ("subtitles", "Add subtitles", "worker"),
    ("quality", "Quality check", "backend"),
    ("schedule", "Schedule", "backend"),
    ("publish", "Publish", "backend"),
    ("analytics", "Collect analytics", "backend"),
    ("improve", "Improve next videos", "backend"),
)
STAGE_KEYS = tuple(s[0] for s in STAGES)

CATEGORIES: dict[str, dict[str, str]] = {
    "crypto_news": {"label": "Breaking crypto news", "template": "breaking_news"},
    "crypto_market": {"label": "Bitcoin and altcoin market updates", "template": "price_alert"},
    "crypto_derivatives": {"label": "Crypto derivatives: big moves, funding, open interest (Hyperliquid)", "template": "price_alert"},
    "pokemon_market": {"label": "Pokémon card market stories", "template": "pokemon_spotlight"},
    "pokemon_rare": {"label": "Rare collectible discoveries", "template": "pokemon_spotlight"},
    "facts": {"label": "Interesting crypto and finance facts", "template": "top_facts"},
    "pricechat_highlights": {"label": "Highlights from Price Chat content", "template": "youtube_highlight"},
    "finance_trends": {"label": "Finance trends: the words many outlets suddenly use (trend scan)", "template": "breaking_news"},
}

TEMPLATES: dict[str, dict[str, Any]] = {
    "breaking_news": {"label": "A · Breaking News", "accent": "#ff3b5c", "banner": "BREAKING", "scenes": ["hook", "facts", "source", "outro"]},
    "price_alert": {"label": "B · Crypto Price Alert", "accent": "#00e58f", "banner": "PRICE ALERT", "scenes": ["hook", "chart", "facts", "outro"]},
    "pokemon_spotlight": {"label": "C · Pokémon Card Spotlight", "accent": "#e6c46e", "banner": "CARD SPOTLIGHT", "scenes": ["hook", "card", "facts", "outro"]},
    "top_facts": {"label": "D · Top 5 Facts", "accent": "#7c5cff", "banner": "TOP 5 FACTS", "scenes": ["hook", "list", "outro"]},
    "youtube_highlight": {"label": "E · YouTube Highlight", "accent": "#ff0033", "banner": "FROM OUR CHANNEL", "scenes": ["hook", "thumbnail", "facts", "outro"]},
}

STRATEGY: dict[str, Any] = {
    # content
    "videos_per_day": 3,
    "min_seconds": 20,
    "max_seconds": 40,
    "language": "English",
    "voice": "auto",  # auto = the most natural engine the worker has (kokoro → openai → piper → system)
    "voice_name": "am_michael",  # the Kokoro narrator (VOICES)
    "voice_speed": 1.1,  # 0.8-1.4: short videos are watched fast
    "categories": list(CATEGORIES),
    "category_weights": {"crypto_news": 1.0, "crypto_market": 1.0, "crypto_derivatives": 0.9, "pokemon_market": 1.0, "pokemon_rare": 0.8, "facts": 0.7,
                         "pricechat_highlights": 0.6, "finance_trends": 0.9},
    "templates": {k: {"accent": v["accent"], "banner": v["banner"]} for k, v in TEMPLATES.items()},
    # built per video by app/brand_manager/hashtags.py: always → this story's own (coin, card, grade) → the writer's → category
    "hashtags": {"always": ["#pricechat"], "per_category": {
        "crypto_news": ["#cryptonews", "#crypto", "#bitcoin"], "crypto_market": ["#crypto", "#cryptotrading", "#altcoins"],
        "crypto_derivatives": ["#crypto", "#cryptotrading", "#hyperliquid"],
        "pokemon_market": ["#pokemoncards", "#pokemontcg", "#pokemon"], "pokemon_rare": ["#pokemoncards", "#vintagepokemon", "#pokemontcg"],
        "facts": ["#crypto", "#cryptofacts", "#bitcoin"], "pricechat_highlights": ["#crypto", "#cryptonews"],
        "finance_trends": ["#finance", "#marketnews", "#investing"]}, "max": 5},
    # schedule (local times in `timezone`)
    "timezone": "Asia/Dubai",
    "post_times": ["10:00", "15:00", "20:00"],
    # publishing
    "publish_mode": "drafts",  # drafts: TikTok inbox upload, the creator posts from the app | direct: needs approval of each post
    "approval_required": False,  # True: every video waits in Content Studio for Approve before it is sent anywhere
    "tiktok_audited": False,  # TikTok audited this app: public direct posts allowed. Until then direct posts are private only
    "content_disclosure": "your_brand",  # none | your_brand | branded_content (TikTok's commercial content disclosure)
    "ai_disclosure": True,  # label AI-generated content in the description
    # branding: TikTok's sharing guidelines forbid apps from superimposing brand logos / watermarks on shared content
    "watermark": False,
    "outro_cta": True,  # a spoken + on-screen call to action in the last seconds (content, not a watermark)
    # AI
    "llm": "auto",  # auto | ollama | anthropic | template
    "ollama_model": "gemma4:31b",  # chosen by worker/model_bakeoff.py (2026-10-19): fewest drafts, no invented claims
    "quality": "standard",  # draft | standard | high
    "monthly_cost_limit_usd": 20.0,
    # safety
    "risk_level": "low",  # low: market facts only, no price talk beyond reported numbers | medium
    "banned_topics": ["price prediction", "financial advice", "pump", "presale", "airdrop scam", "gambling", "leverage trading tips"],
    "require_sources": True,
    "duplicate_days": 14,
    "music": "",  # https URL of a background track the owner holds a licence for ("" = none)
    # Content Studio v2/v4 (worker/pricechat_worker/creative.py, editorial.py): "auto" = chosen from the story
    "creative": {"engine": "editorial", "production": "auto", "narration": "ai", "style": "auto", "tone": "auto", "narrator": "auto", "intensity": "auto", "music": "auto",
                 "captions": "auto", "seconds": None, "pronunciations": {}},
}

# What Content Studio's creative selectors offer (labels; the worker turns each into voices, looks and timing).
CREATIVE_OPTIONS: dict[str, dict[str, str]] = {
    "engine": {"editorial": "Editorial: real footage (v4)", "motion": "Motion graphics (v2)", "classic": "Classic slides (v1)"},
    # v4 production styles (worker/pricechat_worker/editorial.py STYLES): each edits differently
    "production": {"auto": "Auto (from the story)", "real_news": "Real news", "automotive": "Automotive", "luxury": "Luxury",
                   "collectibles": "Collectibles", "tech_demo": "Tech demo", "meme": "Funny / meme"},
    "narration": {"ai": "AI narrator", "upload": "Uploaded human narration", "none": "No narration (on-screen text)"},
    # the hook writer: "old" = script v4 alone; "new" = Kiwi hook layer (worker/pricechat_worker/kiwi.py) with code-checked rules
    "writer": {"old": "Standard writer", "new": "Kiwi hook writer (A/B)"},
    "style": {"auto": "Auto (from the story)", "pokemon": "Pokémon card sale", "crypto": "Crypto news", "trading": "Trading / price move",
              "viral": "Viral money story", "community": "Community highlight"},
    "tone": {"auto": "Auto", "breaking": "Breaking news", "funny": "Funny / sarcastic", "dramatic": "Dramatic", "educational": "Educational",
             "collectible": "Collectible discovery"},
    "narrator": {"auto": "Auto", "natural": "Natural (warm, US)", "steady": "Steady (calm, US)", "anchor": "News anchor (crisp, US)", "documentary": "Documentary (calm, British)",
                 "hype": "Fast and punchy (US)", "wry": "Dry and funny (US)", "warm": "Warm and friendly (US)", "bright": "Bright and upbeat (US)"},
    "intensity": {"auto": "Auto", "calm": "Calm", "normal": "Normal", "high": "High energy"},
    "music": {"auto": "Auto", "documentary": "Calm documentary pad", "viral": "Driving beat", "playful": "Playful plucks",
              "market": "Minimal market pulse", "none": "No music"},
    "captions": {"auto": "Auto", "pop": "Pop", "karaoke": "Karaoke", "boxed": "Boxed word", "clean": "Clean"},
    "preset": {"": "None", "documentary": "Premium collectible documentary", "viral": "Fast-paced viral TikTok", "funny": "Funny Price Chat commentary"},
    "visual": {"price_reveal": "Price reveal", "card_entrance": "Card entrance (3D)", "card_detail": "Card close-up + grade",
               "evidence": "Sale record", "chart": "Verified price chart", "headline": "Headline", "stat": "Big number",
               "punchline": "Kinetic text", "list": "Countdown item", "coin": "Coin + live price (3D)", "photo": "Real photo (licensed)"},
}

# Phrases a finance script must never contain (checked case-insensitively by app/brand_manager/safety.py).
FORBIDDEN_CLAIMS = (
    "guaranteed", "risk-free", "risk free", "can't lose", "cannot lose", "will moon", "to the moon", "100x", "1000x", "get rich",
    "financial advice", "buy now", "sell now", "not too late", "next bitcoin", "will hit", "will reach", "price prediction",
    "price target", "easy money", "passive income guaranteed", "double your",
)

# A hand-checked fact bank (each fact with its source) for the "facts" category: nothing here is generated.
FACTS: tuple[dict[str, str], ...] = (
    {"fact": "Bitcoin's genesis block was mined on January 3, 2009.", "source": "Bitcoin blockchain, block 0", "url": "https://mempool.space/block/0"},
    {"fact": "On May 22, 2010, Laszlo Hanyecz paid 10,000 BTC for two pizzas: the first known purchase of goods with Bitcoin.",
     "source": "BitcoinTalk forum, 2010", "url": "https://bitcointalk.org/index.php?topic=137.0"},
    {"fact": "Bitcoin's supply is capped at 21 million coins by its protocol rules.", "source": "Bitcoin whitepaper and Bitcoin Core",
     "url": "https://bitcoin.org/bitcoin.pdf"},
    {"fact": "The Bitcoin block reward halves every 210,000 blocks, roughly every four years.", "source": "Bitcoin Core source code",
     "url": "https://github.com/bitcoin/bitcoin"},
    {"fact": "The smallest unit of Bitcoin, 0.00000001 BTC, is called a satoshi.", "source": "Bitcoin.org glossary", "url": "https://bitcoin.org/en/vocabulary"},
    {"fact": "Ethereum switched from proof-of-work to proof-of-stake in The Merge on September 15, 2022.", "source": "ethereum.org",
     "url": "https://ethereum.org/en/roadmap/merge/"},
    {"fact": "The Bitcoin whitepaper was published on October 31, 2008, under the name Satoshi Nakamoto.", "source": "bitcoin.org",
     "url": "https://bitcoin.org/bitcoin.pdf"},
    {"fact": "The first Bitcoin transaction sent 10 BTC from Satoshi Nakamoto to Hal Finney in block 170, on January 12, 2009.",
     "source": "Bitcoin blockchain, block 170", "url": "https://mempool.space/block/170"},
    {"fact": "The first Pokémon Trading Card Game set, the Japanese Expansion Pack (Base Set), came out in October 1996.",
     "source": "The Pokémon Company", "url": "https://www.pokemon.com/"},
    {"fact": "Pokémon cards were first released in English by Wizards of the Coast in January 1999.", "source": "The Pokémon Company",
     "url": "https://www.pokemon.com/"},
    {"fact": "PSA grades trading cards on a 1 to 10 scale; a PSA 10 is 'Gem Mint'.", "source": "PSA grading standards",
     "url": "https://www.psacard.com/gradingstandards"},
    {"fact": "Bitcoin's genesis block carries a newspaper headline: 'The Times 03/Jan/2009 Chancellor on brink of second bailout for banks'.",
     "source": "Bitcoin blockchain, block 0", "url": "https://mempool.space/block/0"},
    {"fact": "Bitcoin aims for one new block about every 10 minutes, and its mining difficulty adjusts every 2,016 blocks.",
     "source": "Bitcoin Core source code", "url": "https://github.com/bitcoin/bitcoin"},
    {"fact": "The first Bitcoin halving, on November 28, 2012, cut the block reward from 50 to 25 bitcoin.", "source": "Bitcoin blockchain, block 210,000",
     "url": "https://mempool.space/block/210000"},
    {"fact": "Since the April 2024 halving, each new Bitcoin block pays a reward of 3.125 bitcoin.", "source": "Bitcoin blockchain, block 840,000",
     "url": "https://mempool.space/block/840000"},
    {"fact": "The Ethereum network went live on July 30, 2015.", "source": "ethereum.org", "url": "https://ethereum.org/en/history/"},
    {"fact": "The smallest unit of ether is called a wei; one ether is 10 to the power of 18 wei.", "source": "ethereum.org",
     "url": "https://ethereum.org/en/developers/docs/intro-to-ether/"},
    {"fact": "Dogecoin was created in December 2013 by Billy Markus and Jackson Palmer, starting as a joke based on the Doge meme.",
     "source": "Dogecoin Foundation", "url": "https://dogecoin.com/"},
    {"fact": "On September 7, 2021, El Salvador became the first country to make Bitcoin legal tender.", "source": "Reuters, 2021",
     "url": "https://www.reuters.com/"},
    {"fact": "On January 10, 2024, the US SEC approved the first spot Bitcoin exchange-traded funds.", "source": "U.S. Securities and Exchange Commission",
     "url": "https://www.sec.gov/"},
    {"fact": "The original English Pokémon Base Set has 102 cards.", "source": "The Pokémon Company", "url": "https://www.pokemon.com/"},
    {"fact": "Pikachu Illustrator cards were never sold in packs: they were prizes for a 1998 illustration contest run by CoroCoro Comic in Japan.",
     "source": "The Pokémon Company / CoroCoro Comic", "url": "https://www.pokemon.com/"},
)

CRYPTO_FEEDS: tuple[tuple[str, str], ...] = (
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
)
# Finance + more crypto outlets for the trend scan only (headline words are counted; headlines are shown linked, never copied
# into a script beyond the attributed headline itself)
FINANCE_FEEDS: tuple[tuple[str, str], ...] = (
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse"),
    ("Financial Times", "https://www.ft.com/markets?format=rss"),
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
)
TREND_FEEDS: tuple[tuple[str, str], ...] = CRYPTO_FEEDS + (("The Block", "https://www.theblock.co/rss.xml"),) + FINANCE_FEEDS
COINS: tuple[tuple[str, str, str], ...] = (
    ("bitcoin", "Bitcoin", "BTC"), ("ethereum", "Ethereum", "ETH"), ("solana", "Solana", "SOL"), ("ripple", "XRP", "XRP"),
    ("binancecoin", "BNB", "BNB"), ("dogecoin", "Dogecoin", "DOGE"), ("cardano", "Cardano", "ADA"), ("toncoin", "Toncoin", "TON"),
)


# Kokoro narrators the worker can use (strategy.voice_name), best first.
VOICES: dict[str, str] = {
    "am_michael": "Michael · US male, warm news voice", "am_adam": "Adam · US male, deep", "am_fenrir": "Fenrir · US male, energetic",
    "am_puck": "Puck · US male, upbeat", "am_eric": "Eric · US male, calm", "af_heart": "Heart · US female, warm", "af_bella": "Bella · US female, lively",
    "af_nicole": "Nicole · US female, soft", "af_sarah": "Sarah · US female, clear", "bm_george": "George · UK male, classic narrator",
    "bm_fable": "Fable · UK male, storyteller", "bf_emma": "Emma · UK female, crisp",
}
# The first defaults, replaced as a whole when a brand still has them saved (they put #crypto on Pokémon videos and
# never used the category tags).
_OLD_ALWAYS = ["#crypto", "#pricechat"]
_OLD_PER_CATEGORY = {"crypto_news": ["#cryptonews", "#bitcoin"], "crypto_market": ["#bitcoin", "#altcoins"],
                     "pokemon_market": ["#pokemon", "#pokemoncards"], "pokemon_rare": ["#pokemon", "#vintagepokemon"],
                     "facts": ["#cryptofacts"], "pricechat_highlights": ["#crypto"]}


_CATEGORIES_BEFORE = {"v1": ("crypto_news", "crypto_market", "pokemon_market", "pokemon_rare", "facts", "pricechat_highlights"),
                      "v2": ("crypto_news", "crypto_market", "crypto_derivatives", "pokemon_market", "pokemon_rare", "facts", "pricechat_highlights")}


def merged(overrides: dict[str, Any] | None) -> dict[str, Any]:
    """The brand's strategy: the defaults with the brand's own choices on top (one level of nested dicts merged)."""
    out = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v) for k, v in STRATEGY.items()}
    for k, v in (overrides or {}).items():
        if k not in STRATEGY:
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    # a brand that saved the category list before a new category existed gets it too (only when it had chosen ALL of them)
    if any(set(out["categories"]) == set(before) for before in _CATEGORIES_BEFORE.values()):
        out["categories"] = list(dict.fromkeys(list(out["categories"]) + list(CATEGORIES)))
    tags = out["hashtags"]
    if tags.get("always") == _OLD_ALWAYS:
        tags["always"] = list(STRATEGY["hashtags"]["always"])
    if tags.get("per_category") == _OLD_PER_CATEGORY:
        tags["per_category"] = {k: list(v) for k, v in STRATEGY["hashtags"]["per_category"].items()}
    return out
