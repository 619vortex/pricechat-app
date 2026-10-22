"""Is this listing a VINTAGE JAPANESE POKÉMON CARD? Rule-based, explainable, no AI (eBay's API licence forbids feeding
eBay data into AI models), so every decision has a plain reason an admin can check.

Evidence, strongest first: the listing's item specifics (Language, Year Manufactured, Set, Character, Graded) when the
source gives them, then the title. Vintage = Japanese releases from 1996 (Base / Expansion Pack) to 2002 (the last
e-Card sets): Base, Jungle, Fossil, Rocket Gang, Gym, Vending, Neo, promos, Southern Islands, VS, Web and e-Card.
Anything that says it is modern, English, sealed, a lot, an accessory or another game is excluded with its reason.
"""

import re
from dataclasses import dataclass
from typing import Any

VINTAGE_FIRST, VINTAGE_LAST = 1996, 2002

# (pattern on the lower-cased title or Set aspect, display name, approximate Japanese release year). First match wins,
# so specific patterns come before generic ones.
SETS: tuple[tuple[str, str, int], ...] = (
    (r"no rarity|expansion pack|\bbase set\b|\bbase\b", "Base Set (Expansion Pack)", 1996),
    (r"\bjungle\b", "Jungle", 1997),
    (r"mystery of the fossils|\bfossil\b", "Fossil (Mystery of the Fossils)", 1997),
    (r"rocket gang|team rocket|\brocket\b(?!'s)", "Team Rocket (Rocket Gang)", 1997),
    (r"leaders'? stadium|gym ?1\b|gym heroes", "Gym Heroes (Leaders' Stadium)", 1998),
    (r"challenge from the darkness|gym ?2\b|gym challenge", "Gym Challenge (Challenge from the Darkness)", 1999),
    (r"\bvending\b", "Vending Series", 1998),
    (r"\bneo ?1\b|neo genesis|gold,? silver,? to a new world", "Neo Genesis (Gold, Silver, to a New World...)", 2000),
    (r"\bneo ?2\b|neo discovery|crossing the ruins", "Neo Discovery (Crossing the Ruins...)", 2000),
    (r"\bneo ?3\b|neo revelation|awakening legends", "Neo Revelation (Awakening Legends)", 2000),
    (r"\bneo ?4\b|neo destiny|darkness,? and to light", "Neo Destiny (Darkness, and to Light...)", 2001),
    (r"southern islands?", "Southern Islands", 2001),
    (r"\bvs\b(?: series| pack)?|pokemon vs\b", "Pokémon VS", 2001),
    (r"\bweb\b(?: series)?", "Pokémon Web", 2001),
    (r"e-?card|\be-series\b|mysterious mountains|split earth|wind from the sea", "e-Card Series", 2001),
    (r"\bgym\b", "Gym Series", 1998),
    (r"\bneo\b", "Neo Series", 2000),
)

# Japanese era markers that are NOT vintage (ADV onwards). Any one excludes the listing.
MODERN = re.compile(
    r"\b(?:ex|gx|v|vmax|vstar|v-union|tag team|break|lv\.? ?x|prime|legend|radiant|mega|sar|sr|ar|chr|csr|ur|hr|ssr|"
    r"scarlet|violet|sword|shield|sun ?& ?moon|xy|bw|black ?& ?white|dp|diamond|pearl|platinum|hgss|adv|pcg|"
    r"25th|30th|celebrations?|shiny treasure|vstar universe|[sm]\d{1,2}[a-z]?|sv\d{1,2}[a-z]?|s\d{1,2}[a-z]?|m\d[a-z]?)\b")
OTHER_GAMES = re.compile(r"one piece|yu-?gi-?oh|dragon ?ball|digimon|gundam|magic the gathering|\bmtg\b|weiss|union arena|lorcana|naruto")
NOT_A_CARD = re.compile(
    r"\[sealed\]|\bsealed\b|booster|\bbox\b|\bpacks?\b|\bdeck\b|sleeves?|playmat|binder|\blot\b|\blots\b|bundle|\bset of\b|complete set|"
    r"\bcase\b|\btin\b|\betb\b|elite trainer|starter|figure|plush|\bproxy\b|\bcustom\b|reprint|\bfake\b|\borica\b|\bmetal\b|jumbo|"
    r"\bstickers?\b|\bseal\b|\bcoins?\b|\bpins?\b|\bempty\b")
JAPANESE = re.compile(r"\bjapanese\b|\bjapan\b|\bjpn\b|\bjp\b|日本")
ENGLISH = re.compile(r"\benglish\b|\beng\b|\bwotc\b(?!.*japanese)")
GRADED = re.compile(r"^\[?(?:psa|bgs|cgc|ars|ace|sgc|tag)\b|\b(?:psa|bgs|cgc|ars|sgc)\s?\d{1,2}(?:\.5)?\b")
YEAR = re.compile(r"\b(19[89]\d|20[0-3]\d)\b")
POKEMON = re.compile(r"pok[eé]mon")

_GEN12 = (
    "Bulbasaur Ivysaur Venusaur Charmander Charmeleon Charizard Squirtle Wartortle Blastoise Caterpie Metapod Butterfree Weedle Kakuna "
    "Beedrill Pidgey Pidgeotto Pidgeot Rattata Raticate Spearow Fearow Ekans Arbok Pikachu Raichu Sandshrew Sandslash Nidoran Nidorina "
    "Nidoqueen Nidorino Nidoking Clefairy Clefable Vulpix Ninetales Jigglypuff Wigglytuff Zubat Golbat Oddish Gloom Vileplume Paras "
    "Parasect Venonat Venomoth Diglett Dugtrio Meowth Persian Psyduck Golduck Mankey Primeape Growlithe Arcanine Poliwag Poliwhirl "
    "Poliwrath Abra Kadabra Alakazam Machop Machoke Machamp Bellsprout Weepinbell Victreebel Tentacool Tentacruel Geodude Graveler Golem "
    "Ponyta Rapidash Slowpoke Slowbro Magnemite Magneton Farfetch'd Doduo Dodrio Seel Dewgong Grimer Muk Shellder Cloyster Gastly Haunter "
    "Gengar Onix Drowzee Hypno Krabby Kingler Voltorb Electrode Exeggcute Exeggutor Cubone Marowak Hitmonlee Hitmonchan Lickitung Koffing "
    "Weezing Rhyhorn Rhydon Chansey Tangela Kangaskhan Horsea Seadra Goldeen Seaking Staryu Starmie Mr. Mime Scyther Jynx Electabuzz "
    "Magmar Pinsir Tauros Magikarp Gyarados Lapras Ditto Eevee Vaporeon Jolteon Flareon Porygon Omanyte Omastar Kabuto Kabutops "
    "Aerodactyl Snorlax Articuno Zapdos Moltres Dratini Dragonair Dragonite Mewtwo Mew "
    "Chikorita Bayleef Meganium Cyndaquil Quilava Typhlosion Totodile Croconaw Feraligatr Sentret Furret Hoothoot Noctowl Ledyba Ledian "
    "Spinarak Ariados Crobat Chinchou Lanturn Pichu Cleffa Igglybuff Togepi Togetic Natu Xatu Mareep Flaaffy Ampharos Bellossom Marill "
    "Azumarill Sudowoodo Politoed Hoppip Skiploom Jumpluff Aipom Sunkern Sunflora Yanma Wooper Quagsire Espeon Umbreon Murkrow Slowking "
    "Misdreavus Unown Wobbuffet Girafarig Pineco Forretress Dunsparce Gligar Steelix Snubbull Granbull Qwilfish Scizor Shuckle Heracross "
    "Sneasel Teddiursa Ursaring Slugma Magcargo Swinub Piloswine Corsola Remoraid Octillery Delibird Mantine Skarmory Houndour Houndoom "
    "Kingdra Phanpy Donphan Porygon2 Stantler Smeargle Tyrogue Hitmontop Smoochum Elekid Magby Miltank Blissey Raikou Entei Suicune "
    "Larvitar Pupitar Tyranitar Lugia Ho-Oh Celebi"
)
NAMES = sorted({n for n in _GEN12.split()} | {"Mr. Mime"}, key=len, reverse=True)
_NAME_RE = re.compile(r"(?<![a-z])(" + "|".join(re.escape(n.lower()).replace("\\.", "\\.?") for n in NAMES if n != "Mr.") + r")(?![a-z])")
_PREFIX = re.compile(r"(dark|light|shining|rocket's|[a-z]+'s)\s+$")
TRAINER = re.compile(r"\btrainer\b|\benergy\b|\bprofessor\b|\bbill\b|computer search|item finder")


@dataclass
class Verdict:
    eligible: bool
    reason: str | None  # why it's excluded (None when eligible)
    pokemon: str | None
    set_name: str | None
    year: int | None
    language: str | None
    graded: bool
    evidence: list[str]


def _aspect(aspects: dict[str, Any] | None, *names: str) -> str | None:
    if not aspects:
        return None
    low = {k.lower(): v for k, v in aspects.items()}
    for n in names:
        v = low.get(n.lower())
        if isinstance(v, list):
            v = v[0] if v else None
        if v not in (None, ""):
            return str(v)
    return None


def pokemon_name(title: str, aspects: dict[str, Any] | None = None) -> str | None:
    """The Pokémon on the card (with its owner / Dark / Shining prefix), from the Character specific or the title."""
    character = _aspect(aspects, "Character")
    text = title.lower()
    m = _NAME_RE.search(text)
    if m is None:
        if character:
            return character.strip()[:60]
        return "Trainer" if TRAINER.search(text) else None
    canon = next(n for n in NAMES if re.fullmatch(re.escape(n.lower()).replace("\\.", "\\.?"), m.group(1)))
    pre = _PREFIX.search(text[: m.start()])
    if pre is None:
        return canon
    p = pre.group(1)
    owner = p[:-2].title() + "'s" if p.endswith("'s") else p.title()
    return f"{owner} {canon}"


def find_set(text: str) -> tuple[str, int] | None:
    low = text.lower()
    for pattern, name, year in SETS:
        if re.search(pattern, low):
            return name, year
    return None


def classify(title: str, aspects: dict[str, Any] | None = None, *, include_graded: bool = False,
             year_override: int | None = None, set_override: str | None = None) -> Verdict:
    t = title.lower()
    ev: list[str] = []
    language = _aspect(aspects, "Language")
    graded = bool(GRADED.search(t.strip())) or (_aspect(aspects, "Graded") or "").lower() == "yes"
    name = pokemon_name(title, aspects)

    def out(ok: bool, reason: str | None, set_name: str | None = None, year: int | None = None) -> Verdict:
        lang = language or ("Japanese" if JAPANESE.search(t) else "English" if ENGLISH.search(t) else None)
        return Verdict(ok, reason, name, set_name, year, lang, graded, ev)

    game = _aspect(aspects, "Game")
    if OTHER_GAMES.search(t) or (game and not POKEMON.search(game.lower())):
        return out(False, "not Pokémon")
    if not POKEMON.search(t) and not (game and POKEMON.search(game.lower())):
        return out(False, "doesn't say Pokémon")
    if NOT_A_CARD.search(t):
        return out(False, "not a single card (sealed product, lot or accessory)")
    # language: the item specific decides when present; otherwise the title must say Japanese
    if language:
        ev.append(f"Language: {language}")
        if language.lower() != "japanese":
            return out(False, f"{language}, not Japanese")
    elif JAPANESE.search(t):
        ev.append("title says Japanese")
    elif ENGLISH.search(t):
        return out(False, "English, not Japanese")
    else:
        return out(False, "language not stated")
    if graded and not include_graded:
        return out(False, "graded slab (the store shows ungraded cards)")
    # era: modern markers exclude; the year comes from an admin correction, the item specific, the title, then the set
    found = find_set(_aspect(aspects, "Set") or "") or find_set(title)
    set_name, set_year = (found if found else (None, None))
    if set_override:
        set_name = set_override
    year: int | None = None
    if year_override:
        year = year_override
        ev.append(f"year corrected by admin: {year}")
    else:
        ay = _aspect(aspects, "Year Manufactured", "Release Year", "Year")
        m = YEAR.search(ay or "") or YEAR.search(t)
        if m:
            year = int(m.group(1))
            ev.append(f"year {year} ({'item specifics' if ay and YEAR.search(ay) else 'title'})")
        elif set_year:
            year = set_year
            ev.append(f"year {year} from the set ({set_name})")
    if year is not None and year > VINTAGE_LAST:
        return out(False, f"modern ({year})", set_name, year)
    if year is not None and year < VINTAGE_FIRST:
        return out(False, f"year {year} is before Pokémon cards", set_name, year)
    modern = MODERN.search(re.sub(r"\[[^\]]*\]", " ", t))
    if modern and not year_override:
        return out(False, f"modern marker “{modern.group(0)}”", set_name, year)
    if year is None and not set_name:
        if "vintage" in t:
            ev.append("title says Vintage (set and year not stated)")
        else:
            return out(False, "no vintage set or year stated", set_name, year)
    if set_name:
        ev.append(f"set: {set_name}")
    return out(True, None, set_name, year)


CONDITION_PREFIX = re.compile(r"^\s*\[(NM|EX|VG|LP|MP|HP|DMG|PL|GD|A|B|C|D|S|NM-MT|NM-M)\]", re.I)


def seller_grade(title: str) -> str | None:
    """The seller's own condition tag at the start of the title ("[NM] Gengar…" -> "NM"); never our assessment."""
    m = CONDITION_PREFIX.match(title)
    return m.group(1).upper() if m else None
