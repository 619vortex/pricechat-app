"""Ticker input and display for the Telegram bot.

Input: every way members write a market is normalised to one internal `Symbol` — "BTC", "$BTC", "btcusdt", "$BTCUSDT",
"BTC/USDT", "BTC-USDT", "BTCUSDT.P", "BTC-PERP", names ("bitcoin", "gold") and DEX contract addresses — plus optional
qualifier words anywhere in the command: an exchange ("binance", "bybit", "okx", "hyperliquid"/"hl", "dex", "uniswap", ...),
a market type ("spot", "perp") and a chain ("eth", "base", "solana", ...). What the member wrote explicitly (exchange,
market type, quote currency) is kept; nothing explicit is ever replaced by a default.

Display: `cashtag()` makes the one spelling Price Chat publishes for a market — "$" + uppercase symbol, no spaces
("$BTCUSDT", "$BTC") — so the same string can be searched for in Telegram. The internal identifier (`Symbol`, venue ids
like "BTC-USDT-SWAP") stays separate from this display form. Telegram itself doesn't treat "$BTC" as a special entity, and
other bots may or may not parse it; it's simply a consistent, parseable spelling."""

import re
from dataclasses import dataclass, replace

QUOTES = ("FDUSD", "USDT", "USDC", "USD")  # longest first: "BTCUSDT" -> BTC + USDT, never BTCUSD + T
NAMES = {"BITCOIN": "BTC", "XBT": "BTC", "ETHEREUM": "ETH", "ETHER": "ETH", "SOLANA": "SOL", "HYPERLIQUID": "HYPE",
         "GOLD": "PAXG", "XAU": "PAXG", "DOGECOIN": "DOGE", "RIPPLE": "XRP", "CARDANO": "ADA", "CHAINLINK": "LINK",
         "AVALANCHE": "AVAX", "LITECOIN": "LTC", "POLKADOT": "DOT", "TRON": "TRX", "TONCOIN": "TON", "KASPA": "KAS",
         "SHIBA": "SHIB"}
VENUE_WORDS = {"binance": "binance", "bybit": "bybit", "okx": "okx", "okex": "okx", "hyperliquid": "hyperliquid", "hl": "hyperliquid",
               "dex": "dex", "onchain": "dex", "uniswap": "dex", "pancakeswap": "dex", "raydium": "dex", "aerodrome": "dex",
               "orca": "dex", "meteora": "dex", "sushiswap": "dex", "pumpswap": "dex"}
DEX_NAMES = {"uniswap": "uniswap", "pancakeswap": "pancakeswap", "raydium": "raydium", "aerodrome": "aerodrome", "orca": "orca",
             "meteora": "meteora", "sushiswap": "sushiswap", "pumpswap": "pumpswap"}
KIND_WORDS = {"spot": "spot", "perp": "perp", "perps": "perp", "perpetual": "perp", "futures": "perp", "future": "perp", "swap": "perp"}
CHAIN_WORDS = {"eth": "eth", "ethereum": "eth", "base": "base", "bsc": "bsc", "bnb": "bsc", "solana": "solana", "sol": "solana",
               "arbitrum": "arbitrum", "polygon": "polygon_pos", "avalanche": "avax", "ton": "ton"}  # GeckoTerminal network ids
EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
SOL_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
BASE_RE = re.compile(r"^[A-Z0-9]{1,20}$")


@dataclass(frozen=True)
class Symbol:
    """One market as the member asked for it. `quote`/`venue`/`kind` are None when they didn't say."""
    base: str
    quote: str | None = None
    venue: str | None = None  # binance | bybit | okx | hyperliquid | dex
    kind: str | None = None  # spot | perp
    address: str | None = None  # a DEX token contract (when given instead of a ticker)
    chain: str | None = None  # a DEX network hint
    dex: str | None = None  # a DEX name hint ("uniswap")

    @property
    def display(self) -> str:
        return cashtag(self.base, self.quote) if not self.address else short_address(self.address)


def cashtag(base: str, quote: str | None = None) -> str:
    """'$BTCUSDT' for a pair, '$BTC' for the asset: uppercase, no spaces, no separators."""
    clean = re.sub(r"[^A-Za-z0-9]", "", base).upper()
    return f"${clean}{re.sub(r'[^A-Za-z0-9]', '', quote).upper() if quote else ''}"


def short_address(a: str) -> str:
    return f"{a[:6]}…{a[-4:]}"


def is_address(word: str) -> bool:
    return bool(EVM_ADDRESS.match(word) or (SOL_ADDRESS.match(word) and any(c.isdigit() for c in word) and any(c.islower() for c in word)))


def parse_symbol(word: str) -> Symbol | None:
    """One word -> a Symbol, or None when it can't be a market. Case-insensitive; '$' optional."""
    raw = word.strip().strip(",;")
    if is_address(raw.lstrip("$")):
        return Symbol(base="", address=raw.lstrip("$"), venue="dex")
    w = raw.upper().lstrip("$")
    kind = None
    for suffix in (".P", "-PERP", "PERP", "-SWAP"):
        if w.endswith(suffix) and len(w) > len(suffix):
            w, kind = w[: -len(suffix)], "perp"
            break
    quote = None
    if "/" in w or "-" in w or "_" in w:
        parts = [p for p in re.split(r"[/\-_]", w) if p]
        if len(parts) != 2:
            return None
        w, quote = parts
        if quote not in QUOTES:
            return None
    else:
        for q in QUOTES:
            if w.endswith(q) and len(w) > len(q) and BASE_RE.match(w[: -len(q)]):
                w, quote = w[: -len(q)], q
                break
    w = NAMES.get(w, w)
    if not BASE_RE.match(w):
        return None
    return Symbol(base=w, quote=quote, kind=kind)


def qualifiers(words: list[str]) -> tuple[dict[str, str], list[str]]:
    """Pull exchange / market type / chain words out of a command. ({"venue":..,"kind":..,"chain":..,"dex":..}, the rest)."""
    found: dict[str, str] = {}
    rest = []
    lows = [w.lower().strip(",;") for w in words]
    # "eth", "solana", "base" are also coins: they mean a chain only in a DEX command (a DEX word or a contract address) where
    # another word names the token ("/chart pepe dex eth" = PEPE on Ethereum; "/chart eth dex" = the ETH token). Callers pass
    # the words left after timeframes and chart options are taken out.
    dexy = any(VENUE_WORDS.get(w) == "dex" for w in lows) or any(is_address(w.lstrip("$")) for w in words)
    for i, (raw, low) in enumerate(zip(words, lows)):
        others = [x for j, x in enumerate(lows) if j != i and x not in VENUE_WORDS and x not in KIND_WORDS]
        if low in VENUE_WORDS and "venue" not in found:
            found["venue"] = VENUE_WORDS[low]
            if low in DEX_NAMES:
                found["dex"] = DEX_NAMES[low]
        elif low in KIND_WORDS and "kind" not in found:
            found["kind"] = KIND_WORDS[low]
        elif dexy and low in CHAIN_WORDS and "chain" not in found and others:
            found["chain"] = CHAIN_WORDS[low]
        else:
            rest.append(raw)
    return found, rest


def with_qualifiers(sym: Symbol, q: dict[str, str]) -> Symbol:
    """The command's qualifier words on top of the symbol (the symbol's own explicit parts win: "BTCUSDT.P spot" is a contradiction)."""
    kind = sym.kind or q.get("kind")
    if sym.kind and q.get("kind") and sym.kind != q["kind"]:
        raise ValueError(f"{sym.display} is written as a perpetual, but the command also says {q['kind']}: pick one.")
    venue = sym.venue or q.get("venue")
    if venue == "dex" and kind == "perp":
        raise ValueError("DEX pools are spot markets: drop 'perp', or name an exchange.")
    return replace(sym, venue=venue, kind=kind, chain=q.get("chain") or sym.chain, dex=q.get("dex") or sym.dex)


def symbol_args(words: list[str]) -> tuple[Symbol | None, list[str]]:
    """A command's words -> (the market, the words left over). The first word that parses as a market is the market."""
    q, rest = qualifiers(words)
    for i, w in enumerate(rest):
        s = parse_symbol(w)
        if s is not None:
            return with_qualifiers(s, q), rest[:i] + rest[i + 1:]
    return None, rest
