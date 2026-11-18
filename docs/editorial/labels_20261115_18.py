"""Editorial labels for the 270 real candidates of 2026-11-15 13:00 .. 11-18 13:00 UTC, written BEFORE the gate change (Claude's editorial
judgment for a sharp markets-and-geopolitics account; the owner can overrule any line). One entry per real-world EVENT: the title prefixes of
every candidate row that tells it. IMPORTANT = should post once. BORDERLINE = either decision is acceptable (not scored). Everything
else is filler for this account (scored as filler if it posts)."""

IMPORTANT = {
    "Iran-Bahrain strikes 11-15": ["Bahrain intercepts Iranian aerial attacks", "Iran targets US military radar in Bahrain"],
    "Estonia closes Russia border after sabotage": ["Estonia to close Russia border", "NATO member Estonia to close Russia border",
                                                     "Estonia Says Russia Behind Failed Arson"],
    "Ukraine drones hit Russia's petrochemical capital": ["Ukrainian drones strike Russia's petrochemical"],
    "Japan economy contracts": ["Japan's economy unexpectedly contracts", "Japan's economy unexpectedly shrinks"],
    "Supreme Court ends Custodia Fed account fight": ["Supreme Court Ends Custodia"],
    "Nasdaq -2% third day": ["Nasdaq falls more than 2%"],
    "United Airlines widespread outage": ["United Airlines experiencing a widespread", "United Airlines Says It Has a Widespread"],
    "Bitcoin/crypto rout ($2B liquidations, -13% week)": ["Bitcoin's $2 Billion Liquidation Weekend", "Bitcoin, Ether Lose Over 13%"],
    "US bank outages (Chase, BofA)": ["Outages reported at major US banks", "Widespread US bank system glitches", "Financial stocks slide as online banking",
                                      "Widespread outages hit banks", "Banking outage rattles"],
    "MIT brothers acquitted in $25M MEV case": ["Judge Acquits MIT Brothers"],
    "UK first sanctions on Israeli settlement firms": ["Ed Miliband announces first sanctions"],
    "California sues OpenAI": ["California sues ChatGPT maker OpenAI"],
    "Apple iCloud/App Store outage": ["Apple outage impacts iCloud", "Apple Addresses Outages", "Apple disruption affects iCloud"],
    "Russian military plane crash, 19 dead": ["Russian military plane crashes killing all 19", "CCTV shows military plane crash in Russia"],
    "Kuwait drones, US-Iran strikes on Qeshm/Bahrain": ["KUWAIT REPORTS DRONE ATTACKS"],
    "UK inflation 3.6%": ["UK inflation rises to 3.6%", "UK inflation rate rises again", "UK inflation accelerated to 3.6%", "Record high diesel prices drive UK inflation",
                          "UK Inflation Print at Fastest"],
    "Ukraine strikes Crimean Bridge": ["Ukraine strikes Crimean Bridge", "After Ukraine’s latest attack, Moscow calls damage to the Crimean",
                                       "RUSSIA’S PRIZED KERCH BRIDGE STRUCK"],  # added after the first scoring: the same strike (label omission, disclosed)
    "Equinor ongoing cyberattack": ["Norway's Equinor detects ongoing cyberattack"],
    "EU sanctions Russia's richest woman": ["EU sanctions Wildberries CEO"],
    "China and Canada agree tariff cuts": ["China, Canada agree to further reduce tariffs"],
}

BORDERLINE = [
    "Lutnick Pushed to Weaken Chip Rules", "UK Payrolls Fell Unexpectedly", "Jensen Huang Says He Would Return", "Jensen Huang Breaks Silence",
    "Former Nvidia CEO Jensen Huang says he", "JENSEN HUANG AGREES TO FACE CONGRESS", "OpenAI releases its updated GPT-6.2", "Peltola Wins Alaska Senate",
    "UK energy giant quits North Sea", "TRUMP TO HOLD NUCLEAR TALKS", "US moves to block China", "Congress moves to pass tighter AI chip",
    "UK transfers NATO-assigned American soldier", "American soldier held in UK suspected", "Man accessed US military plane", "Rocket from Boeing-Lockheed",
    "Anthropic Is Said to Guide IPO", "Nvidia board forms committee", "In a first, Google detects AI-automated", "Cyberattack targets BBC, Zoom and Ikea",
    "Cyber Attack Hits 178 Major Websites", "Thousands of US flights still cancelled", "Investors sue chip giant Intel", "Singapore’s non-oil exports surge",
    "Ukrainian drones strike Russia", "Charles Schwab Website Goes Down", "Earnings Snapshot: Home Depot", "Invesco's Stablecoin Reserve Fund Goes Live",
    "Experts and reporters break down FAA meltdown", "Trump Downplays Microsoft, JPMorgan Outages", "JPMorgan Chase says temporary software bug",
    "Chase says transactions back to normal", "Japan proposes ‘Asian IEA’", "Congress agrees on civilian plane locator", "Gold and silver settle at new 52-week lows",
    "XRP Hits Its Lowest Price Since 2024", "SOL Hits Lowest Since June", "Nvidia: Fiscal 2028 Revenue", "Gunfire erupts in occupied West Bank",
    "Ukraine allies reject U.S. criticism", "Fed’s Musalem Says ‘Several’ Rate Hikes", "Fed’s Waller says central bank may need to ‘shock",
    "Scoop: White House drops Heidi Overton", "UK and allies call on Israel", "Kim Jong Un’s Top Aide Meets Putin", "Record number of migrants",
    "Widespread outages hit banks, air travel",  # (also counted in the bank-outage event)
]


def label(title: str) -> tuple[str, str]:
    t = title.strip()
    for ev, prefixes in IMPORTANT.items():
        if any(t.lower().startswith(p.lower()[:40]) for p in prefixes):
            return "important", ev
    if any(t.lower().startswith(p.lower()[:35]) for p in BORDERLINE):
        return "borderline", t[:60]
    return "filler", t[:60]
