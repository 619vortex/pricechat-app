"""Terminal Intelligence Engine, Phase 0: the epistemic class of every claim, computed from the SOURCES, never from a model.

A model may extract a claim and say it is hedged or its own inference (app.intel.schema.Claim); it never says a claim is verified. The
class follows from evidence the desk already measures (app/newsdesk/chaos.py evidence levels, events.STRONG / CREDIBLE, the terminal's
independent-outlet count) and from the hedge and rumour patterns the lanes already use (chaos.RUMOUR / OPINION, newsworthy.HEDGE,
terminal.intel.RUMOR). Design: docs/intelligence/2026-11-25-terminal-intelligence-engine.md section 4.2.

| Class        | Rule                                                                              | In an automatic post       |
|--------------|-----------------------------------------------------------------------------------|----------------------------|
| VERIFIED     | an official source, or >= 2 independent outlets, or our own price / exchange data | yes, plainly               |
| REPORTED     | one outlet (attributed to it)                                                     | yes, always attributed     |
| CORRELATION  | measured co-movement (moves.cross_asset) or a historical analogue                 | figures only, no causation |
| SPECULATION  | hedged / rumour / forecast / opinion wording, the model's hedged flag, no source  | never                      |
| INFERENCE    | the model's own conclusion (model_inference)                                      | never                      |
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.intel.schema import Claim

VERIFIED, REPORTED, CORRELATION, SPECULATION, INFERENCE = "VERIFIED", "REPORTED", "CORRELATION", "SPECULATION", "INFERENCE"
CLASSES = (VERIFIED, REPORTED, CORRELATION, SPECULATION, INFERENCE)
# how each class may appear in a post that goes out without the owner
AUTO_POST = {VERIFIED: "plain", REPORTED: "attributed", CORRELATION: "figures_only", SPECULATION: "never", INFERENCE: "never"}
RULES = {
    VERIFIED: "stated plainly",
    REPORTED: "always attributed (\"..., Reuters reports\")",
    CORRELATION: "figures only, in its own same-window sentence; never as a cause (moves.cross_problems)",
    SPECULATION: "never in an automatic post; the Terminal shows it labelled",
    INFERENCE: "never published; Admin and the Terminal only, labelled \"model view\"",
}


def _strong_evidence() -> tuple[str, ...]:
    from app.newsdesk import events

    return tuple(events.STRONG)  # official, two outlets, price data, exchange data


def independent_outlets(outlets: Iterable[str]) -> list[str]:
    """Distinct reporting organisations (one outlet through two feeds or its X account counts once): trending.sensitive.outlet_key."""
    from app.trending import sensitive

    return [o for o in sensitive.independent([str(o or "") for o in outlets or []]) if o.strip()]


def hedge_words(text: str) -> str:
    """The hedge, rumour or forecast wording in a text, from the patterns the lanes already use, or ""."""
    from app.newsdesk import chaos, newsworthy
    from app.terminal import intel

    for rx in (chaos.RUMOUR, intel.RUMOR, newsworthy.HEDGE, chaos.OPINION):
        m = rx.search(text or "")
        if m:
            return m.group(0)
    return ""


def classify(text: str, *, evidence: str = "", outlets: Iterable[str] = (), official: bool = False, hedged: bool = False,
             model_inference: bool = False, correlation: bool = False) -> str:
    """The class of one assertion. Strongest restriction first: the model's own conclusion, then any hedge, then a measured co-movement,
    then what the sources establish. evidence: the desk's evidence level (chaos / events vocabulary: official, two outlets, price data,
    exchange data, attributed). correlation: the claim is a measured co-movement or an analogue (moves.cross_asset), never a model's."""
    if model_inference:
        return INFERENCE
    if hedged or hedge_words(text):
        return SPECULATION
    if correlation:
        return CORRELATION
    n = len(independent_outlets(outlets))
    if official or evidence in _strong_evidence() or n >= 2:
        return VERIFIED
    if n == 1 or evidence == "attributed":
        return REPORTED
    return SPECULATION  # nobody we can name stands behind it (unnamed sources, social chatter only)


def claim_class(c: Claim, *, evidence: str = "", outlets: Iterable[str] = (), official: bool = False) -> str:
    """A model-extracted claim. The model's hedged / model_inference flags can only make a claim WEAKER, never stronger. A claim attributed
    to a party ("the company said") is backed by the outlets that carry it, as the headline is."""
    return classify(c.text, evidence=evidence, outlets=outlets, official=official, hedged=c.hedged, model_inference=c.model_inference)


def cluster_class(title: str, summary: str = "", *, evidence: str = "", outlets: Iterable[str] = (), official: bool = False) -> str:
    """The class of a story (cluster) as its headline tells it. The summary is read only for hedges: a summary that says "reportedly"
    makes the headline a report of a rumour."""
    hedge = hedge_words(title) or (hedge_words(summary) if summary and summary.strip() != (title or "").strip() else "")
    return classify(title, evidence=evidence, outlets=outlets, official=official, hedged=bool(hedge))


def correlation_claims(facts: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The measured same-window moves the News Desk attaches to a market event (facts["cross"], from moves.cross_asset): CORRELATION,
    figures only. Never a model's output."""
    out = []
    for c in (facts or {}).get("cross") or []:
        if c.get("name"):
            ch = c.get("change")
            fig = f" {float(ch):+.1f}%" if isinstance(ch, (int, float)) else ""
            out.append({"text": f"{c['name']}{fig} in the same window", "class": CORRELATION, "auto_post": AUTO_POST[CORRELATION]})
    return out


def allowed_in_auto_post(classes: Iterable[str]) -> list[str]:
    """Which of these classes may appear in an automatic post (VERIFIED plainly, REPORTED attributed, CORRELATION as figures only);
    SPECULATION and INFERENCE never."""
    return [c for c in dict.fromkeys(classes) if AUTO_POST.get(c, "never") != "never"]


def auto_post_rules() -> list[dict[str, str]]:
    """The table above, for Admin and the daily report."""
    return [{"class": c, "auto_post": AUTO_POST[c], "rule": RULES[c]} for c in CLASSES]
