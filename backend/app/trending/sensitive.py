"""Sensitive subjects (owner, 2026-11-14). Some stories must never go out on their own, and the rest of the hard ones only with more evidence
and a plain, attributed sentence:

- approval only (never published automatically, even in Automatic mode): allegations, accusations, lawsuits or charges about named
  people; crimes against people; terrorism; sexual misconduct; suicide and self-harm; anything about children or minors.
- strict (may publish automatically only with at least two independent reliable outlets in the topic, a neutral sentence that keeps the
  attribution, and no person named): war and armed conflict, deaths and casualties, disasters, health scares, legal action against an
  organisation (no person named).

For every sensitive topic the writing is plain: no hook, no attitude, no emoji, no question, no interest statistics; the attribution
("Reuters reports", "according to", "says") stays, and the source's hedges ("alleged", "accused", "says") stay too (gates.sensitive_problems).
Deterministic regexes, deliberately broad: a false alarm costs one approval click, a miss could cost a defamation claim.
"""

import re
from typing import Any

APPROVAL_ONLY = ("minors", "suicide", "sexual", "allegation", "crime", "terrorism")  # most serious first: the first match names the topic
STRICT = ("conflict", "deaths", "disaster", "health", "legal")
LABELS = {"minors": "children or minors", "suicide": "suicide or self-harm", "sexual": "sexual misconduct", "allegation": "allegations about a named person",
          "crime": "a crime against people", "terrorism": "terrorism", "conflict": "war or armed conflict", "deaths": "deaths or casualties",
          "disaster": "a disaster", "health": "a health scare", "legal": "legal action"}

_R = {
    "minors": r"\b(child|children|kids?|toddlers?|bab(?:y|ies)|infants?|newborns?|minors?|teen(?:s|age|agers?)?|schoolchildren|"
              r"school ?(?:girls?|boys?)|pupils?|juveniles?|underage|(?:1[0-7]|[1-9])[- ]year[- ]olds?|boy|girl|boys|girls|students? (?:at|from) .{0,20}school)\b",
    "suicide": r"\b(suicid\w*|self[- ]harm\w*|took (?:his|her|their) own li(?:fe|ves)|killed (?:him|her|them)sel(?:f|ves))\b",
    "sexual": r"\b(sexual\w*|sex abuse|sex offen\w+|rap(?:e|es|ed|ist|ists)|molest\w*|grooming|groped?|harass\w*|misconduct|indecent|"
              r"sex(?:ually)? traffick\w*|child abuse|paedophil\w*|pedophil\w*|epstein)\b",
    "terrorism": r"\b(terror\w*|jihad\w*|isis|islamic state|al[- ]qaeda|al[- ]shabaab|suicide bomb\w*|car bomb\w*|extremist attack)\b",
    "crime": r"\b(murder\w*|homicid\w*|manslaughter|stabb(?:ed|ing|ings)|shooting|shootings|gunm(?:an|en)|shot dead|assault\w*|kidnap\w*|abduct\w*|"
             r"robber(?:y|ies)|arson|hate crime|domestic violence)\b",
    "accuse": r"\b(alleg\w*|accus\w*|scandal\w*|fraud\w*|scam\w*|charged|charges? (?:of|against|over)|indict\w*|arrest\w*|detain\w*|lawsuits?|sue[sd]?|suing|"
              r"convict\w*|guilty|sentenced|jail\w*|prison|(?:on|stands?|faces?) trial|trial (?:of|over)|prosecut\w*|brib\w*|corrupt\w*|embezzl\w*|launder\w*|extort\w*|defam\w*|libel|"
              r"wrongdoing|probe into|investigation into|under investigation|insider trading|lying|lied)\b",
    "conflict": r"\b((?<!trade )(?<!price )(?<!bidding )(?<!currency )(?<!tariff )(?<!culture )(?<!talent )(?<!format )wars?(?! chest)|warfare|wartime|"
                r"air ?strikes?|missiles?|drone (?:strike|attack)s?|shell(?:ing|ed)|bombard\w*|invasion|invade[sd]?|troops|soldiers|army|armed forces|"
                r"militar(?:y|ies)|militants?|insurgen\w*|rebels?|ceasefire|truce|front ?line|offensive|hostages?|warplanes?|artillery|"
                r"naval blockade|annex\w*)\b",
    "deaths": r"\b(kill(?:s|ed|ing)?|dead|deaths?|died|dies|dying|death toll|casualt\w*|wounded|injur(?:ed|ies|y)|fatal\w*|bodies|massacre\w*|"
              r"genocide|famine|starv\w*|mourn\w*|funeral|lost (?:his|her|their) li(?:fe|ves))\b",
    "disaster": r"\b(earthquakes?|quakes?|tsunamis?|hurricanes?|typhoons?|cyclones?|tornado(?:es)?|floods?|flooding|wildfires?|bushfires?|"
                r"volcan\w*|eruptions?|landslides?|mudslides?|avalanches?|droughts?|heatwaves?|deadly heat|(?:plane|air|train|bus|helicopter|ferry|boat) crash\w*|"
                r"derail\w*|capsiz\w*|shipwreck|explosion|blast|building collapse|evacuat\w*|state of emergency)\b",
    "health": r"\b(outbreaks?|pandemic|epidemic|virus(?:es)?|infections?|infected|contagious|bird flu|avian flu|h5n1|ebola|measles|mpox|cholera|"
              r"covid\w*|cases of|recall(?:s|ed)? (?:of|over)|contaminat\w*|poisoning|cancer|carcinogen\w*|health scare|health alert|disease)\b",
}
RX = {k: re.compile(v, re.I) for k, v in _R.items()}
# a person, not an organisation: their name, or words that say a person is involved
PERSON_WORDS = re.compile(r"\b(he|she|his|her|him|ceo|founder|co-?founder|chief executive|executive|chairman|chairwoman|minister|senator|"
                          r"congress(?:man|woman)|governor|mayor|secretary|lawmaker|politician|actor|actress|singer|rapper|star|influencer|"
                          r"billionaire|businessman|businesswoman|man|woman|husband|wife|father|mother|son|daughter|employee|officer|judge|"
                          r"coach|player|priest|teacher|doctor|nurse)\b", re.I)


def people(text: str) -> list[str]:
    from app.newsdesk import photos

    return photos.people_named(text or "")


def classify(text: str) -> dict[str, Any] | None:
    """{"kind", "label", "tier": "approval" | "strict", "matched"} for the first (most serious) sensitive subject in the text, or None."""
    t = text or ""
    hits: dict[str, str] = {}
    for k, rx in RX.items():
        m = rx.search(t)
        if m:
            hits[k] = m.group(0)
    if "accuse" in hits:
        named = bool(people(t)) or bool(PERSON_WORDS.search(t))
        hits["allegation" if named else "legal"] = hits.pop("accuse")
    for k in APPROVAL_ONLY + STRICT:
        if k in hits:
            return {"kind": k, "label": LABELS[k], "tier": "approval" if k in APPROVAL_ONLY else "strict", "matched": hits[k],
                    "also": [x for x in APPROVAL_ONLY + STRICT if x in hits and x != k]}
    return None


ALIASES = {"ap news": "ap", "associated press": "ap", "the associated press": "ap", "wsj": "wall street journal", "the wall street journal": "wall street journal",
           "ft": "financial times", "bbc news": "bbc", "theblock": "the block", "the new york times": "new york times", "nyt": "new york times",
           "the washington post": "washington post", "barron's": "barrons", "the guardian": "guardian", "the economist": "economist", "the hill": "hill",
           "cnbc international": "cnbc", "reuters business": "reuters"}


def outlet_key(name: str) -> str:
    n = re.sub(r"\s+", " ", (name or "").strip().lower())
    return ALIASES.get(n, n)


def independent(outlets: list[str]) -> list[str]:
    """Distinct outlets: AP and AP News, BBC and BBC News count once."""
    out: dict[str, str] = {}
    for o in outlets:
        k = outlet_key(o)
        if k and k not in out:
            out[k] = o
    return list(out.values())


ATTRIBUTION = re.compile(r"\b(reports?|reported|according to|says|said|say|told|via|confirm(?:s|ed)?|announce[sd]?|statement)\b|\(via ", re.I)
HEDGES = re.compile(r"\b(alleg\w*|accus\w*|claims?|claimed|says|said|denies|denied|suspected|suspects?|charged|charges?)\b", re.I)


def auto_problem(sens: dict[str, Any] | None, *, text: str, outlets: list[str]) -> str | None:
    """Why a sensitive topic's tweet may not go out automatically, or None. The owner can still approve it by hand."""
    if not sens:
        return None
    if sens["tier"] == "approval":
        return f"sensitive ({sens['label']}): approval only, never automatic"
    n = len(independent(outlets))
    if n < 2:
        return f"sensitive ({sens['label']}): needs at least 2 independent reliable outlets for automatic ({n} so far)"
    if not ATTRIBUTION.search(text or ""):
        return f"sensitive ({sens['label']}): the tweet must say who reports it"
    if people(text):
        return f"sensitive ({sens['label']}): it names a person, so the owner decides"
    return None
