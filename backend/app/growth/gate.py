"""The figures-match gate (doc §3.3) and the Originals' wording rules. A hard gate: a draft whose text fails it is refused, whoever wrote it
(template, PC model, Claude or the owner's edit).

Every number, percentage, money figure, multiple, year/date and month name in the text must be one of the Finding's own formatted figures;
a signed figure must carry the Finding's sign, and an unsigned one may not be described in the opposite direction ("up 27.2%" for a fall).
Every comparative ("record", "lowest", "first since", "only"...) must be one the Finding allows. No advice, no forecasts, no links, no
mentions, at most one cashtag, at most 280 characters. The News Desk's own post checks (app/newsdesk/checks.py) run on top.
"""

from __future__ import annotations

import re
from typing import Any

from app.growth import fmt
from app.growth.findings import Finding
from app.newsdesk import checks

MAX_LEN = 280
_NUM = re.compile(r"(\$?)(\d[\d,]*(?:\.\d+)?)(%|x(?![a-z])|[kKMB](?![a-z]))?")
_SIGN = {"+": "+", "-": "-", fmt.MINUS: "-", "–": "-"}
UP_WORDS = re.compile(r"\b(up|rose|rise[sn]?|gain(?:ed|s)?|jump(?:ed|s)?|rall(?:y|ied|ies)|climb(?:ed|s)?|higher|surg(?:e|ed|es)|soar(?:ed|s)?|added)\b", re.I)
DOWN_WORDS = re.compile(r"\b(down|fell|fall(?:s|en)?|drop(?:ped|s)?|lost|los(?:e|es)|slid|slump(?:ed|s)?|lower|declin(?:e|ed|es)|sank|plung(?:e|ed|es)|crash(?:ed|es)?|below)\b", re.I)
COMPARATIVES = re.compile(r"\b(record(?:s)?|all[- ]time|ever|never|first since|first|last time|longest|shortest|highest|lowest|widest|narrowest|"
                          r"biggest|largest|smallest|worst|best|deepest|steepest|fastest|sharpest|strongest|weakest|most|least|only|just|every|always|"
                          r"unprecedented|historic|extreme|none earlier|almost|nearly)\b", re.I)
NUMBER_WORDS = re.compile(r"\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
                          r"nineteen|twenty|thirty|forty|fifty|hundred|thousand|million|billion|trillion|twice|double[ds]?|triple[ds]?|half|halved|dozen|quarter)\b", re.I)
MONTH_WORDS = re.compile(r"\b(" + "|".join(fmt.MONTHS + tuple(m for m in fmt.MON if m != "May")) + r")\b")
PREDICTION = re.compile(r"\b(will|won't|going to|is set to|are set to|poised to|expected to|bound to|likely to|about to|due for|should|must|"
                        r"next leg|bottom is in|top is in|price target|target|forecast|predict\w*|guarantee\w*|buy|sell|long now|short now|"
                        r"not financial advice|nfa|dyor)\b", re.I)


def _tokens(text: str) -> list[tuple[str, str, int]]:
    """(sign '+'/'-'/'', normalised core like '$124753' / '27.2%', start) for every figure in text."""
    out = []
    for m in _NUM.finditer(text):
        s = m.start()
        if s > 0 and (text[s - 1].isalnum() or text[s - 1] in "._"):
            continue  # part of a word / ticker (kPEPE, 1INCH...) or a decimal tail
        sign = ""
        if s > 0 and text[s - 1] in _SIGN and (s < 2 or not text[s - 2].isalnum()):
            sign = _SIGN[text[s - 1]]
        core = (m.group(1) or "") + m.group(2).replace(",", "") + (m.group(3) or "").lower()
        out.append((sign, core, s))
    return out


def allowed(f: Finding) -> tuple[dict[str, set[str]], set[str], set[str]]:
    """(core -> signs it may carry, allowed comparatives, allowed month words) from the Finding's figures and templates."""
    cores: dict[str, set[str]] = {}
    months: set[str] = set()
    for v in f.numbers.values():
        t = str(v.get("text") or "")
        for sign, core, _ in _tokens(t):
            neg = sign == "-" or (not sign and isinstance(v.get("value"), (int, float)) and v["value"] < 0 and len(_tokens(t)) == 1)
            cores.setdefault(core, set()).add("-" if neg else ("+" if sign == "+" else "0"))
        months |= {m.lower() for m in MONTH_WORDS.findall(t)}
    comps = {c.lower() for c in f.comparatives}
    for tpl in f.templates:
        comps |= {c.lower() for c in COMPARATIVES.findall(f.fill(tpl))}
    return cores, comps, months


def problems(text: str, f: Finding, *, as_edited: bool = False) -> list[str]:
    """Every reason the text can't be queued for this Finding ([] = it passes)."""
    t = (text or "").strip()
    out: list[str] = []
    if not t:
        return ["empty text"]
    if len(t) > MAX_LEN:
        out.append(f"{len(t)} characters (limit {MAX_LEN})")
    if re.search(r"\{[a-z0-9_]+\}", t):
        out.append("an unfilled placeholder is left in the text")
    low = t.lower()
    for phrase in f.required_phrases():
        if phrase.lower() not in low:
            out.append(f"the text must say \"{phrase}\" (what the figures are measured in)")
    cores, comps, months = allowed(f)
    template_text = " ".join(f.fill(x) for x in f.templates).lower()
    for sign, core, pos in _tokens(t):
        signs = cores.get(core)
        if signs is None:
            out.append(f"figure {sign}{core} isn't in the finding")
            continue
        if sign == "-" and "-" not in signs:
            out.append(f"figure {core} carries a minus sign the finding doesn't")
        elif sign == "+" and "+" not in signs and "0" not in signs:
            out.append(f"figure {core} carries a plus sign the finding doesn't")
        elif not sign and core.endswith("%") and signs <= {"-"} | {"+"}:
            before = t[max(0, pos - 28):pos]
            if "-" in signs and "+" not in signs and UP_WORDS.search(before) and not DOWN_WORDS.search(before):
                out.append(f"{core} is a fall in the finding but the text calls it a rise")
            if "+" in signs and "-" not in signs and DOWN_WORDS.search(before) and not UP_WORDS.search(before):
                out.append(f"{core} is a rise in the finding but the text calls it a fall")
    for c in {c.lower() for c in COMPARATIVES.findall(t)}:
        if c not in comps and c.replace("-", " ") not in comps:
            out.append(f"comparative '{c}' isn't one the finding supports")
    for w in {w.lower() for w in NUMBER_WORDS.findall(t)}:
        if w not in template_text:
            out.append(f"number word '{w}' isn't in the finding")
    for mth in {m.lower() for m in MONTH_WORDS.findall(t)}:
        if mth not in months and mth not in template_text:
            out.append(f"month '{mth}' isn't in the finding")
    pred = {p.lower() for p in PREDICTION.findall(t)} - {p.lower() for p in PREDICTION.findall(template_text)}
    if pred:
        out.append(f"advice or forecast wording: {', '.join(sorted(pred))}")
    if checks.cashtag_count(t) > checks.X_MAX_CASHTAGS:
        out.append("more than one cashtag")
    # the News Desk's post checks: links, mentions, hashtags, promises / advice, rumour wording, unsupported tickers
    source_text = " ".join(str(v.get("text") or "") for v in f.numbers.values()) + " " + " ".join(f.words.values()) + " " + template_text
    res = checks.check_post(t, source_text=source_text, source_names=[], as_edited=as_edited, max_len=MAX_LEN)
    for c in res.checks:
        if not c["passed"] and c["check"] not in ("length", "figures", "figure_units"):  # length and figures are checked above, stricter
            out.append(c["detail"])
    return out


def passes(text: str, f: Finding, **kw: Any) -> bool:
    return not problems(text, f, **kw)
