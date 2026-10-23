"""Grounding: a language model's committee text may only state numbers that are in the evidence pack.

A number in the text is grounded when it equals a number in the evidence (or in the run's own deterministic derivations:
the trade plan built from the evidence scenarios) at the precision it was written, or is a k / M / B or % rewrite of one
("67.1k" for 67123.5, "0.8%" for 0.008). Signs are ignored. A few small integers are ordinary prose ("1 round", "EMA 200",
"24h") and always pass. Anything else is ungrounded: the caller replaces that role's text with its deterministic rules text
and records `degraded: ["<role>: ungrounded number 12345"]`. Probability talk ("70% chance", "likely odds") is refused too:
the committee never states probabilities. Pure functions, no I/O.
"""

import math
import re
from typing import Any

FREE = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 15, 20, 24, 26, 30, 48, 50, 100, 200}
_NUM = re.compile(r"(?<![\w.])[-+−]?\$?(?P<num>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\.\d+)"
                  r"(?:\s?(?P<suf>[kKMbB])(?![A-Za-z])|(?P<pct>\s?%)|(?P<x>[xX×])(?![A-Za-z]))?(?![\w])")
_PROB = re.compile(r"(?i)\b(probabilit\w*|odds\b|chances?\b|likelihood)")
_MULT = {"k": 1e3, "m": 1e6, "b": 1e9}  # lower-case "m" is minutes ("15m"): only "M" is millions


def numbers_in_text(text: str) -> list[tuple[float, float, str, str]]:
    """[(value, tolerance from the written precision, kind 'pct'|'plain', raw)] for every number written in text."""
    out = []
    for m in _NUM.finditer(text or ""):
        raw = m.group("num").replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        dec = len(raw.split(".", 1)[1]) if "." in raw else 0
        tol = 0.5 * 10 ** -dec
        mult = _MULT.get((m.group("suf") or "").lower(), 1.0)
        out.append((abs(v) * mult, tol * mult, "pct" if m.group("pct") else "plain", m.group(0).strip()))
    return out


def evidence_numbers(obj: Any, acc: list[float] | None = None) -> list[float]:
    """Every number anywhere in a JSON-like object (strings included: '1h close above 67,800' gives 1 and 67800)."""
    acc = [] if acc is None else acc
    if obj is None or isinstance(obj, bool):
        return acc
    if isinstance(obj, (int, float)):
        if math.isfinite(float(obj)):
            acc.append(abs(float(obj)))
    elif isinstance(obj, str):
        acc.extend(v for v, _, _, _ in numbers_in_text(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            evidence_numbers(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            evidence_numbers(v, acc)
    return acc


class Grounder:
    """Built once per run from the evidence pack (+ the run's deterministic plan numbers)."""

    def __init__(self, *sources: Any) -> None:
        vals: set[float] = set()
        for s in sources:
            vals.update(round(v, 10) for v in evidence_numbers(s))
        self.values = sorted(vals)

    def grounded(self, value: float, tol: float, kind: str) -> bool:
        if value in FREE:
            return True
        cands = [(value, tol)]  # the evidence value rounded to the written precision ("67.1k" is 67123.5 to the 100)
        if kind == "pct":
            cands.append((value / 100, tol / 100))  # "0.8%" written for a 0.008 fraction
        return any(abs(c - g) <= t * (1 + 1e-9) + 1e-12 for c, t in cands for g in self.values)

    def ungrounded(self, text: str) -> list[str]:
        return [raw for v, tol, kind, raw in numbers_in_text(text) if not self.grounded(v, tol, kind)]


def texts_of(obj: Any) -> list[str]:
    """The free-text strings of a role's output (what grounding checks)."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [t for k, v in obj.items() if k not in ("refs", "author") for t in texts_of(v)]
    if isinstance(obj, (list, tuple)):
        return [t for v in obj for t in texts_of(v)]
    return []


def check(role: str, output: Any, grounder: Grounder) -> str | None:
    """None when every number in the role's text is grounded and nothing talks probabilities; else the degraded note."""
    for t in texts_of(output):
        if _PROB.search(t):
            return f"{role}: probability language"
        bad = grounder.ungrounded(t)
        if bad:
            return f"{role}: ungrounded number {bad[0].lstrip('$+-−')}"
    return None
