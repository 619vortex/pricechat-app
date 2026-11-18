"""Held-out labels (11-14 23:00 .. 11-15 13:00 UTC), written before either gate was run on these candidates."""
IMPORTANT = {
    "Iran-Gulf strikes resume": ["Iran targets US military site in Gulf", "Iran targets Bahrain airbase", "Bahrain intercepts Iranian aerial attacks"],
    "Bitcoin four-month low, $800M liquidations": ["Binance Liquidations Near $800 Million", "Bitcoin Hits Four-Month Low", "Bitcoin falls to four-month low"],
}
BORDERLINE = ["XRP crashes to its lowest since November 2024", "ICC stops probe", "SpaceX successfully catches", "Lutnick Pushed to Weaken"]


def label(title):
    t = title.strip().lower()
    for ev, ps in IMPORTANT.items():
        if any(t.startswith(p.lower()[:40]) for p in ps):
            return "important", ev
    if any(t.startswith(p.lower()[:35]) for p in BORDERLINE):
        return "borderline", t[:60]
    return "filler", t[:60]
