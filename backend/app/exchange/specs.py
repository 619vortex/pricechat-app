"""The 10 markets and the contract rules.

Chosen on pricing coverage and agreement between sources (Phase A, production data 2026-10-10), not popularity alone:
every market is an exact English printing graded PSA 10, with verified completed sales in the last 180 days AND a
Scrydex PSA 10 estimate that agrees with them. Left out for now (insufficient or conflicting evidence): Pikachu
Illustrator (not in the catalogue), Lugia Neo Genesis 1st Ed (2 sales, sources 30% apart), Umbreon Gold Star (2 sales,
50% apart), Rayquaza Gold Star (no sales), Blastoise / Venusaur / Mewtwo Base Set 1st Ed PSA 10 (3 or fewer sales).

Contract: 1 contract = 1/1,000 of the card's reference value, FIXED (multiplier_den = 1000 never changes after launch).
A $450,000 card -> a 450.00 paper USDT contract. Whole contracts, tick 0.01 paper USDT, taker fee 0.10%, maker 0%.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Spec:
    symbol: str
    title: str
    name: str
    set_name: str
    edition: str
    number: str
    grading_company: str = "PSA"
    grade: str = "10"
    language: str = "English"


MARKETS: list[Spec] = [
    Spec("CHAR1ST", "Charizard · Base Set 1st Edition · PSA 10", "Charizard", "Base Set", "1st Edition", "4/102"),
    Spec("CHARUNL", "Charizard · Base Set Unlimited · PSA 10", "Charizard", "Base Set", "Unlimited", "4/102"),
    Spec("SHCHAR", "Shining Charizard · Neo Destiny 1st Edition · PSA 10", "Shining Charizard", "Neo Destiny", "1st Edition", "107/105"),
    Spec("DKCHAR", "Dark Charizard · Team Rocket 1st Edition · PSA 10", "Dark Charizard", "Team Rocket", "1st Edition", "4/82"),
    Spec("BLASTUNL", "Blastoise · Base Set Unlimited · PSA 10", "Blastoise", "Base Set", "Unlimited", "2/102"),
    Spec("MEWTWOUNL", "Mewtwo · Base Set Unlimited · PSA 10", "Mewtwo", "Base Set", "Unlimited", "10/102"),
    Spec("PIKA1ST", "Pikachu · Base Set 1st Edition · PSA 10", "Pikachu", "Base Set", "1st Edition", "58/102"),
    Spec("GENGAR1ST", "Gengar · Fossil 1st Edition Holo · PSA 10", "Gengar", "Fossil", "1st Edition", "5/62"),
    Spec("ALAKA1ST", "Alakazam · Base Set 1st Edition · PSA 10", "Alakazam", "Base Set", "1st Edition", "1/102"),
    Spec("DRAGO1ST", "Dragonite · Fossil 1st Edition Holo · PSA 10", "Dragonite", "Fossil", "1st Edition", "4/62"),
]

MULTIPLIER_DEN = 1000  # 1 contract = 1/1000 of the card's reference value; fixed forever
TICK_CENTS = 1  # 0.01 paper USDT
MIN_QTY, MAX_QTY = 1, 1000
TAKER_FEE_BPS, MAKER_FEE_BPS = 10, 0  # 0.10% taker, 0% maker; fees round DOWN to the cent (holds always cover them)
LIMIT_BAND_PCT = 50  # a limit price must be within ±50% of the reference (fat-finger protection)
MARKET_SLIPPAGE_PCT = 5  # a market order fills only up to 5% beyond the best opposite price at arrival
START_USDT_CENTS = 10_000_00  # every member's paper wallet
