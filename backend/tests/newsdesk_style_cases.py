"""Representative real stories (posts 162-176 on t.me/Pricechatupdates, 2026-11-11..14, and their outlets' own titles and summaries) for
comparing what a post style writes. Plain data plus one builder, so a script can run the same cases against another commit (the clean
golden file was written by production 1374981's own code: tests/newsdesk_clean_golden_1374981.json)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

RELEASED = datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)

CASES: list[dict[str, Any]] = [
    {"name": "custody", "label": "", "age": 480, "rows": [
        ("Unchained", "https://unchainedcrypto.com/deutsche-banks-bitcoin-and-ether-custody-is-open-to-clients-bank-says/",
         "Deutsche Bank's Bitcoin and Ether Custody Is Open to Clients, Bank Says",
         "Unchained counted 30 European countries on the bank's EU crypto register entry, which rests on a notification to its regulator, not a new license."),
        ("The Block", "https://www.theblock.co/news/business/2026-11-11-deutsche-bank-goes-live-with-digital-asset-custody-service-signs-asset-management-deal-437851",
         "Deutsche Bank goes live with digital asset custody service, signs asset management deal",
         "Deutsche Bank and its asset management arm will also explore tokenized money market funds, crypto-asset ETCs, and stablecoins under the new agreement."),
        ("Cointelegraph", "https://cointelegraph.com/news/deutsche-bank-debuts-digital-asset-custody-services-eu", "Deutsche Bank Launches Institutional Crypto Custody",
         "Deutsche Bank launched crypto custody for institutional and corporate clients, with initial support for Bitcoin, Ether, USDC, EURC and EURAU."),
    ]},
    {"name": "tariffs", "label": "⚡ JUST IN", "age": 6, "rows": [
        ("CNBC", "https://www.cnbc.com/2026/11/14/china-us-tariffs-xi-trump-meeting.html", "China, US discuss tariffs, farm trade ahead of Xi-Trump meeting",
         "Beijing has repeatedly urged Washington to implement the $60 billion tariff-reduction deal, calling it a prerequisite for increasing purchases of US farm goods."),
    ]},
    {"name": "btc70k", "label": "⚡ JUST IN", "age": 37, "rows": [
        ("Unchained", "https://unchainedcrypto.com/bitcoin-breaks-below-70000-as-liquidations-sweep-through-leveraged-longs/",
         "Bitcoin Breaks Below $70,000 as Liquidations Sweep Through Leveraged Longs",
         "More than $1 billion in crypto positions were forced shut in 12 hours as bitcoin broke below $70,000."),
    ]},
    {"name": "saudi", "label": "", "age": 300, "rows": [
        ("OilPrice", "https://oilprice.com/Latest-Energy-News/World-News/Saudi-Crude-Output-Jumps-26-as-Riyadh-Reports-Near-Pre-War-Production.html",
         "Saudi Crude Output Jumps 26% as Riyadh Reports Near Pre-War Production",
         "Saudi Arabia told OPEC it raised crude production in October to 10.563 million barrels per day, back near pre-war levels and slightly "
         "above its OPEC quota of about 10.5 million bpd, Bloomberg columnist Javier Blas reported on Wednesday."),
    ]},
    {"name": "fraudster", "label": "", "age": 148, "rows": [
        ("The Block", "https://www.theblock.co/news/regulation/2026-11-11-crypto-fraudster-jailed-for-eight-years-over-2-7-million-theft-plot-directed-from-prison-437858",
         "Crypto fraudster jailed for eight years over $2.7 million theft plot directed from prison",
         "The plot was foiled after the would-be accomplices failed to gain entry to the victims' home, while Barr's phone later exposed his role."),
        ("Decrypt", "https://decrypt.co/382984/prisoner-jailed-for-8-years-over-2-7m-crypto-theft-plot-run-from-his-cell",
         "Prisoner Jailed for 8 Years Over $2.7M Crypto Theft Plot Run From His Cell",
         "Robert Barr planned to have accomplices force a crypto investor to install remote-access software so he could drain the wallet from prison."),
    ]},
]


def build(case: dict[str, Any], NdEvent: Any) -> tuple[Any, list[Any]]:
    """(event, rows) for a case; NdEvent passed in so another commit's model class can be used."""
    rows = [SimpleNamespace(source=s, link=u, title=t, summary=m) for s, u, t, m in case["rows"]]
    lead = rows[0]
    first = RELEASED - timedelta(minutes=case["age"])
    ev = NdEvent(key=f"case:{case['name']}", kind="news", title=lead.title, headline=lead.title, summary=lead.summary, topic="", label=case["label"],
                 evidence="two outlets" if len(rows) > 1 else "attributed", category="news", slot="slot", released_at=RELEASED, first_seen=first,
                 sources=[{"name": r.source, "url": r.link, "title": r.title} for r in rows],
                 facts={"told": lead.title, "published_at": first.isoformat(), "outlets": [r.source for r in rows]})
    ev.id = 900 + CASES.index(case)
    return ev, rows
