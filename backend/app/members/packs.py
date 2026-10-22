"""Pokémon Pack Giveaway (just for fun): signed-in members open free virtual packs of real catalogue cards.

NOT real money and NOT real cards: nothing is bought, sold, won, shipped or withdrawn; pulls live only in the member's
virtual binder. Original vintage sets only (1999-2003). Opening is free and unlimited for now (a short cooldown protects
the server); XP is paid for the first XP_PACKS_PER_DAY packs a day ("pack.open", capped) and opening counts as the day's
task for the streak. Every Kiwi Passport level reached also earns one boosted Level-up pack (guaranteed holo, 30% chase).
Print runs, like the real thing: every pack is first rolled as a 1st Edition, Shadowless or Unlimited pack (1st Edition
and Shadowless are rare, PRINT_ODDS) and all five cards come from that print. In a 1st Edition / Shadowless pack the holo
is the chase ("ultra"); otherwise the chase is a Shining, Secret or reverse-foil rare.
Values: the Pokémon Portfolio's ungraded estimate; a rarer print is never shown below the commoner print of the same card
(when its own estimate is missing or lower, it shows "at least" that price: value_floor).
Each pack shows the real vintage booster wrapper and cards turn over from the real card back, both images from Scrydex
(the card-data provider the site already uses for card images); the official set logo (TCGdex) is the fallback art. Draws use the OS random
generator, server-side.
"""

import secrets
import threading
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import xp as ledger
from app.models.members import Member, MemberBinderCard, MemberPackOpen
from app.models.pokemon import PokemonCard

FREE_PER_DAY = None  # unlimited for now (requirements to be defined later)
XP_PACKS_PER_DAY = 20  # packs per day that pay XP: 20 x 5 XP = the "pack.open" rule's 100 XP daily cap
COOLDOWN = timedelta(seconds=2)
CARDS_PER_PACK = 5

PACKS: list[dict[str, Any]] = [
    {"key": "base", "image": "https://images.scrydex.com/pokemon/base1-s1u/large", "logo": "https://assets.tcgdex.net/en/base/base1/logo.png", "name": "Base Set", "set": "Base Set", "year": 1999, "tagline": "Where it all began", "theme": ["#ff7b00", "#d62828", "#ffd60a"], "mascot": "Charizard"},
    {"key": "jungle", "image": "https://images.scrydex.com/pokemon/base2-s1u/large", "logo": "https://assets.tcgdex.net/en/base/base2/logo.png", "name": "Jungle", "set": "Jungle", "year": 1999, "tagline": "Into the wild", "theme": ["#2d6a4f", "#52b788", "#f4a261"], "mascot": "Snorlax"},
    {"key": "fossil", "image": "https://images.scrydex.com/pokemon/base3-s1u/large", "logo": "https://assets.tcgdex.net/en/base/base3/logo.png", "name": "Fossil", "set": "Fossil", "year": 1999, "tagline": "Ancient power", "theme": ["#6f4518", "#e9c46a", "#8d99ae"], "mascot": "Dragonite"},
    {"key": "rocket", "image": "https://images.scrydex.com/pokemon/base5-s1u/large", "logo": "https://assets.tcgdex.net/en/base/base5/logo.png", "name": "Team Rocket", "set": "Team Rocket", "year": 2000, "tagline": "Prepare for trouble", "theme": ["#111111", "#c1121f", "#6a040f"], "mascot": "Dark Charizard"},
    {"key": "heroes", "image": "https://images.scrydex.com/pokemon/gym1-s1u/large", "logo": "https://assets.tcgdex.net/en/gym/gym1/logo.png", "name": "Gym Heroes", "set": "Gym Heroes", "year": 2000, "tagline": "Kanto's Gym Leaders", "theme": ["#1d3557", "#f1c40f", "#e63946"], "mascot": "Lt. Surge's Electabuzz"},
    {"key": "challenge", "image": "https://images.scrydex.com/pokemon/gym2-s1u/large", "logo": "https://assets.tcgdex.net/en/gym/gym2/logo.png", "name": "Gym Challenge", "set": "Gym Challenge", "year": 2000, "tagline": "Blaine's fire", "theme": ["#9d0208", "#f48c06", "#370617"], "mascot": "Blaine's Charizard"},
    {"key": "genesis", "image": "https://images.scrydex.com/pokemon/neo1-s1u/large", "logo": "https://assets.tcgdex.net/en/neo/neo1/logo.png", "name": "Neo Genesis", "set": "Neo Genesis", "year": 2000, "tagline": "A new generation", "theme": ["#0353a4", "#b9d6f2", "#ffd166"], "mascot": "Lugia"},
    {"key": "discovery", "image": "https://images.scrydex.com/pokemon/neo2-s1u/large", "logo": "https://assets.tcgdex.net/en/neo/neo2/logo.png", "name": "Neo Discovery", "set": "Neo Discovery", "year": 2001, "tagline": "Shadows of Johto", "theme": ["#240046", "#7b2cbf", "#ffd166"], "mascot": "Umbreon"},
    {"key": "revelation", "image": "https://images.scrydex.com/pokemon/neo3-s1u/large", "logo": "https://assets.tcgdex.net/en/neo/neo3/logo.png", "name": "Neo Revelation", "set": "Neo Revelation", "year": 2001, "tagline": "Legends awaken", "theme": ["#005f73", "#94d2bd", "#ee9b00"], "mascot": "Shining Gyarados"},
    {"key": "destiny", "image": "https://images.scrydex.com/pokemon/neo4-s1u/large", "logo": "https://assets.tcgdex.net/en/neo/neo4/logo.png", "name": "Neo Destiny", "set": "Neo Destiny", "year": 2002, "tagline": "Chase the Shining Pokémon", "theme": ["#14213d", "#fca311", "#e5e5e5"], "mascot": "Shining Charizard"},
    {"key": "legendary", "image": "https://images.scrydex.com/pokemon/base6-s1/large", "logo": "https://assets.tcgdex.net/en/lc/lc/logo.png", "name": "Legendary Collection", "set": "Legendary Collection", "year": 2002, "tagline": "Fireworks foil", "theme": ["#3c096c", "#ff006e", "#ffbe0b"], "mascot": "Charizard"},
    {"key": "skyridge", "image": "https://images.scrydex.com/pokemon/ecard3-s1/large", "logo": "https://assets.tcgdex.net/en/ecard/ecard3/logo.png", "name": "Skyridge", "set": "Skyridge", "year": 2003, "tagline": "The last of the e-series", "theme": ["#0077b6", "#90e0ef", "#ffb703"], "mascot": "Charizard"},
]
PACK = {p["key"]: p for p in PACKS}
# The real card back, from Scrydex (the card-image provider the site already uses).
CARD_BACK = "https://images.scrydex.com/pokemon/base6-s1u/large"

TIERS = ["common", "uncommon", "rare", "holo", "ultra"]
UNLIMITED = "Unlimited"
RARE_PRINTS = ("1st Edition", "Shadowless")
# the chance a pack is a rare print run (the rest are Unlimited); sets with one print ignore this
PRINT_ODDS: dict[str, list[tuple[str, float]]] = {"base": [("1st Edition", 0.01), ("Shadowless", 0.04)]}
DEFAULT_PRINT_ODDS = [("1st Edition", 0.02)]
BONUS_PRINT_BOOST = 3  # Level-up packs: three times the chance of a rare print
_HOLO = ("rare holo", "double rare", "ace spec")
_ULTRA = ("ultra", "rainbow", "secret", "shining", "illustration", "hyper", "rare holo vmax", "gold", "star", "radiant", "amazing")


def tier_of(rarity: str | None, edition: str | None = None, variant: str | None = None) -> str:
    r = (rarity or "").strip().lower()
    if not r or r == "?":
        return "common"  # energies and the like have no rarity: never the hit card
    if "reverse" in (variant or "").lower() and r != "common" and r != "uncommon":
        return "ultra"  # vintage reverse holos (Legendary Collection "fireworks", e-series) are the chase
    if "holo" in r and (edition or "") in RARE_PRINTS:
        return "ultra"  # the vintage chase: a 1st Edition / Shadowless holo
    if r == "common":
        return "common"
    if r == "uncommon":
        return "uncommon"
    if any(w in r for w in _ULTRA):
        return "ultra"
    if any(w in r for w in _HOLO):
        return "holo"
    return "rare"


# the hit slot: Rare most of the time, sometimes Holo, occasionally the chase (1st Ed / Shadowless holo, Shining, Secret)
HIT_ODDS = [("ultra", 0.08), ("holo", 0.27), ("rare", 0.65)]
BONUS_ODDS = [("ultra", 0.30), ("holo", 0.70)]  # Level-up pack: a guaranteed holo
SLOTS = [("common",), ("common",), ("uncommon",), ("uncommon", "rare"), ("hit",)]

_pools: dict[str, dict[str, list[int]]] = {}
_lock = threading.Lock()


def _print(edition: str | None) -> str:
    return edition if edition in RARE_PRINTS else UNLIMITED  # sets without editions ("—") are one Unlimited print


def pool(db: Session, key: str) -> dict[str, dict[str, list[int]]]:
    """Card ids per print run, then per tier, for one pack (English cards with an image). Built once per process."""
    with _lock:
        if key in _pools:
            return _pools[key]
    p = PACK[key]
    out: dict[str, dict[str, list[int]]] = {}
    for cid, rarity, edition, variant in db.execute(select(PokemonCard.id, PokemonCard.rarity, PokemonCard.edition, PokemonCard.variant).where(
            PokemonCard.set_name == p["set"], PokemonCard.language == "English", PokemonCard.image_url.is_not(None))):
        out.setdefault(_print(edition), {t: [] for t in TIERS})[tier_of(rarity, edition, variant)].append(cid)
    with _lock:
        _pools[key] = out
    return out


def _rand() -> float:
    return secrets.randbelow(1_000_000) / 1_000_000


def _pick(ids: list[int]) -> int:
    return ids[secrets.randbelow(len(ids))]


def roll_print(pl: dict[str, dict[str, list[int]]], key: str, bonus: bool = False) -> str:
    """Which print run this pack is: rare prints by PRINT_ODDS (only those the set has), else Unlimited."""
    x, acc = _rand(), 0.0
    for name, p in PRINT_ODDS.get(key, DEFAULT_PRINT_ODDS):
        acc += p * (BONUS_PRINT_BOOST if bonus else 1)
        if x < acc and name in pl and any(pl[name].values()):
            return name
    return UNLIMITED if UNLIMITED in pl else next(iter(pl))


def draw(db: Session, key: str, bonus: bool = False, print_run: str | None = None) -> tuple[str, list[tuple[int, str]]]:
    by_print = pool(db, key)
    if not any(any(t.values()) for t in by_print.values()):
        raise ValueError("This pack isn't available right now.")
    run = print_run if print_run in by_print else roll_print(by_print, key, bonus)
    pl = by_print[run]
    order = ["ultra", "holo", "rare", "uncommon", "common"]

    def best_available(want: str) -> str:
        i = order.index(want)
        for t in order[i:] + order[:i][::-1]:  # step down first, then up, until a tier has cards
            if pl[t]:
                return t
        return want

    cards: list[tuple[int, str]] = []
    for slot in SLOTS:
        if slot == ("hit",):
            x, acc, want = _rand(), 0.0, "rare"
            for t, p in (BONUS_ODDS if bonus else HIT_ODDS):
                acc += p
                if x < acc:
                    want = t
                    break
            if want == "holo" and not pl["holo"] and pl["ultra"] and run in RARE_PRINTS:
                want = "ultra"  # a rare print's holos ARE its chase
        elif len(slot) == 2:
            want = slot[1] if bonus or _rand() < 0.3 else slot[0]
        else:
            want = slot[0]
        t = best_available(want)
        cards.append((_pick(pl[t]), t))
    return run, cards


def opens_today(db: Session, member: Member, now: datetime | None = None) -> int:
    day = ledger.day_of(now or utcnow())
    return db.scalar(select(func.count()).select_from(MemberPackOpen).where(MemberPackOpen.member_id == member.id, MemberPackOpen.day == day)) or 0


XP_DAY_CAP = 100


def pack_xp_today(db: Session, member: Member, now: datetime | None = None) -> int:
    """Pack XP paid today (for the meter on the Giveaway page)."""
    from app.models.members import ReputationEvent

    day = ledger.day_of(now or utcnow())
    return int(db.scalar(select(func.coalesce(func.sum(ReputationEvent.xp), 0)).where(
        ReputationEvent.member_id == member.id, ReputationEvent.event_type == "pack.open", ReputationEvent.day == day)) or 0)


def level_packs(db: Session, member: Member) -> int:
    """One boosted Level-up pack for every Kiwi Passport level reached (level 2 = 1 pack, ...), minus those opened."""
    used = db.scalar(select(func.count()).select_from(MemberPackOpen).where(MemberPackOpen.member_id == member.id, MemberPackOpen.bonus.is_(True))) or 0
    return max(0, (member.rank_level or 1) - 1 - used)


def resets_at(now: datetime | None = None) -> str:
    n = now or utcnow()
    return (n.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat()


def _values(db: Session, ids: list[int]) -> dict[int, int]:
    """What each card is worth ungraded, from the same engine as the Pokémon Portfolio: real ungraded prices (a
    conservative point) or, when there are none, the PSA-grade equivalent less grading cost. Unknown = left out."""
    from types import SimpleNamespace

    from app.portfolio import valuation

    if not ids:
        return {}
    hs = [SimpleNamespace(card_id=i, kind="raw", grading_company=None, grade=None, condition_low=None, condition_high=None) for i in set(ids)]
    pr = valuation.Pricer(db, hs)
    out = {}
    for h in hs:
        v = pr.value(h).cents
        if v:
            out[h.card_id] = int(v)
    return out


_RANK = {"1st Edition": 2, "Shadowless": 1}


def values_for(db: Session, cards: list[PokemonCard]) -> dict[int, tuple[int | None, bool]]:
    """Each card's ungraded estimate, and whether it is a floor: a rarer print (1st Edition > Shadowless > Unlimited) is
    never shown below the commoner print of the same card. When its own estimate is missing or lower (no ungraded price,
    only a weak estimate from graded prices), it shows the commoner print's price as "at least" that, never a made-up one."""
    own = _values(db, [c.id for c in cards])
    out: dict[int, tuple[int | None, bool]] = {c.id: (own.get(c.id), False) for c in cards}
    rare = [c for c in cards if c.edition in _RANK]
    if not rare:
        return out
    sibs = list(db.scalars(select(PokemonCard).where(PokemonCard.language == "English",
                                                     PokemonCard.set_name.in_({c.set_name for c in rare}),
                                                     PokemonCard.card_number.in_({c.card_number for c in rare if c.card_number}))))
    sib_vals = _values(db, [x.id for x in sibs])
    for c in rare:
        lower = [sib_vals.get(x.id) for x in sibs if x.set_name == c.set_name and x.card_number == c.card_number and x.name == c.name
                 and _RANK.get(x.edition or "", 0) < _RANK[c.edition or ""]]
        floor = max((v for v in lower if v), default=None)
        mine = out[c.id][0]
        if floor and (mine is None or mine < floor):
            out[c.id] = (floor, True)
    return out


_UNLIMITED_IMAGES: dict[str, str] | None = None


def picture(c: PokemonCard) -> str | None:
    """The card's picture for ITS print. The public scans of these vintage sets are 1st Edition prints; an Unlimited or
    Shadowless card shows the same scan without the 1st Edition stamp (scripts/brand/make-unlimited-images.py)."""
    global _UNLIMITED_IMAGES
    if _UNLIMITED_IMAGES is None:
        import json
        from pathlib import Path

        f = Path(__file__).with_name("unlimited_images.json")
        _UNLIMITED_IMAGES = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    if c.image_url and c.edition != "1st Edition":
        return _UNLIMITED_IMAGES.get(c.image_url, c.image_url)
    return c.image_url


def card_out(c: PokemonCard, tier: str, value: tuple[int | None, bool] | None = None, new: bool = False) -> dict[str, Any]:
    cents, floor = value or (None, False)
    return {"id": c.id, "name": c.name, "set": c.set_name, "number": c.card_number, "year": c.year, "rarity": c.rarity, "edition": c.edition,
            "variant": c.variant, "image": picture(c), "tier": tier, "value": cents or None, "value_floor": bool(cents and floor), "new": new}


def open_pack(db: Session, member: Member, key: str, now: datetime | None = None, bonus: bool = False) -> dict[str, Any]:
    if key not in PACK:
        raise ValueError("Unknown pack.")
    now = now or utcnow()
    day = ledger.day_of(now)
    used = opens_today(db, member, now)
    last = db.scalar(select(func.max(MemberPackOpen.opened_at)).where(MemberPackOpen.member_id == member.id))
    if last is not None and now - last < COOLDOWN:
        raise PermissionError("Easy, trainer! One pack at a time.")
    if bonus and level_packs(db, member) <= 0:
        raise PermissionError("No Level-up packs left: level up your Kiwi Passport to earn another.")
    run, cards = draw(db, key, bonus=bonus)
    row = MemberPackOpen(member_id=member.id, pack_key=key, day=day, slot=used + 1, opened_at=now, bonus=bonus,
                         cards=[{"card_id": c, "tier": t} for c, t in cards])
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:  # a double tap: the same slot can't be opened twice
        db.rollback()
        raise PermissionError("That pack is already being opened.") from exc
    owned = {b.card_id: b for b in db.scalars(select(MemberBinderCard).where(MemberBinderCard.member_id == member.id,
                                                                            MemberBinderCard.card_id.in_([c for c, _ in cards])))}
    new_ids: set[int] = set()
    dupes: list[int] = []  # slots holding a copy of a card the member already had (or pulled twice in this pack)
    for i, (cid, tier) in enumerate(cards):
        b = owned.get(cid)
        if b is None:
            b = MemberBinderCard(member_id=member.id, card_id=cid, count=0, tier=tier, first_at=now, pack_key=key)
            db.add(b)
            owned[cid] = b
            new_ids.add(cid)
        elif b.count >= 1:
            dupes.append(i)
        b.count += 1
    r = ledger.award(db, member, "pack.open", f"pack:{row.id}", reason=f"Opened a {PACK[key]['name']} {'Level-up ' if bonus else ''}pack (just for fun)",
                     occurred_at=now, commit=False, source="website")  # shown as website XP; its own 100/day cap
    from app.members import streak

    streak.on_task(db, member, now)  # opening today's pack counts as a task for the daily streak
    db.flush()
    by_id = {c.id: c for c in db.scalars(select(PokemonCard).where(PokemonCard.id.in_([c for c, _ in cards])))}
    vals = values_for(db, list(by_id.values()))
    sold: dict[int, int] = {}  # slot -> Play USDT cents
    if member.auto_sell_dupes and dupes:  # opt-in: extra copies are sold at once (Play Market, virtual)
        from app.members import play_market

        for i in dupes:
            cid = cards[i][0]
            if (vals.get(cid) or (None, False))[0]:
                sold[i] = play_market.sell_now(db, member, cid, kind="auto_sell", ref=f"pack:{row.id}:{i}", now=now, commit=False)["price_cents"]
    if member.giveaway_in_portfolio:
        from app.portfolio.service import sync_giveaway

        sync_giveaway(db, member, now)
    db.commit()
    out = [{**card_out(by_id[cid], tier, vals.get(cid), cid in new_ids), "auto_sold": sold.get(i)} for i, (cid, tier) in enumerate(cards) if cid in by_id]
    return {"pack": key, "bonus": bonus, "print": run, "cards": out, "xp": r.xp if r.awarded else 0, "opens_left": None,
            "xp_packs_left": max(0, XP_PACKS_PER_DAY - used - 1), "xp_today": pack_xp_today(db, member, now), "xp_cap": XP_DAY_CAP,
            "level_packs": level_packs(db, member), "resets_at": resets_at(now),
            "value": sum(c["value"] or 0 for c in out), "best": max((TIERS.index(c["tier"]) for c in out), default=0)}


def shelf(db: Session, member: Member | None) -> dict[str, Any]:
    covers = {}
    for p in PACKS:
        c = db.scalar(select(PokemonCard).where(PokemonCard.set_name == p["set"], PokemonCard.language == "English",
                                                PokemonCard.name == p["mascot"], PokemonCard.image_url.is_not(None)).limit(1))
        covers[p["key"]] = c.image_url if c else None
    owned: dict[str, int] = {}
    if member is not None:
        for key, n in db.execute(select(MemberBinderCard.pack_key, func.count()).where(MemberBinderCard.member_id == member.id,
                                                                                      MemberBinderCard.count > 0)
                                 .group_by(MemberBinderCard.pack_key)):
            owned[key] = n
    packs = []
    for p in PACKS:
        by_print = pool(db, p["key"])
        packs.append({**{k: p[k] for k in ("key", "name", "set", "year", "tagline", "theme", "mascot", "logo", "image")}, "cover": covers[p["key"]],
                      "size": sum(len(v) for pl in by_print.values() for v in pl.values()),
                      "chase": sum(len(pl["ultra"]) + (len(pl["holo"]) if run in RARE_PRINTS else 0) for run, pl in by_print.items()),
                      "prints": [run for run in (*RARE_PRINTS, UNLIMITED) if run in by_print], "owned": owned.get(p["key"], 0)})
    return {"packs": packs, "card_back": CARD_BACK, "per_day": FREE_PER_DAY, "opens_left": None, "xp_per_day": XP_PACKS_PER_DAY,
            "xp_packs_left": max(0, XP_PACKS_PER_DAY - opens_today(db, member)) if member else None,
            "xp_today": pack_xp_today(db, member) if member else 0, "xp_cap": XP_DAY_CAP,
            "level_packs": level_packs(db, member) if member else 0, "rank_level": member.rank_level if member else None,
            "resets_at": resets_at(), "odds": {"hit_slot": dict(HIT_ODDS), "level_up": dict(BONUS_ODDS),
                                               "print": {k: dict(PRINT_ODDS.get(k, DEFAULT_PRINT_ODDS)) for k in PACK},
                                               "level_up_print_boost": BONUS_PRINT_BOOST},
            "signed_in": member is not None}


def binder(db: Session, member: Member) -> dict[str, Any]:
    rows = db.execute(select(MemberBinderCard, PokemonCard).join(PokemonCard, PokemonCard.id == MemberBinderCard.card_id)
                      .where(MemberBinderCard.member_id == member.id, MemberBinderCard.count > 0)  # sold / listed copies aren't here
                      .order_by(MemberBinderCard.first_at.desc()).limit(600)).all()
    vals = values_for(db, [c for _, c in rows])
    cards = [{**card_out(c, b.tier, vals.get(c.id)), "count": b.count, "pack": b.pack_key} for b, c in rows]
    priced = [c for c in cards if c["value"]]
    return {"cards": cards, "unique": len(cards), "pulls": sum(c["count"] for c in cards),
            "value": sum(c["value"] * c["count"] for c in priced),  # every copy pulled, at its market estimate
            "priced": len(priced), "best": max(priced, key=lambda c: c["value"], default=None),
            "in_portfolio": bool(member.giveaway_in_portfolio), "auto_sell_dupes": bool(member.auto_sell_dupes)}


def set_in_portfolio(db: Session, member: Member, show: bool) -> bool:
    """The member's opt-in: show their virtual pulls in their Pokémon Portfolio (a separate section, never holdings)."""
    from app.portfolio.service import sync_giveaway

    member.giveaway_in_portfolio = bool(show)
    sync_giveaway(db, member)  # add the pulls as virtual holdings (or take them out again)
    db.commit()
    return member.giveaway_in_portfolio
