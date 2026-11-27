"""The Originals writer: words around fixed numbers (doc §3.3).

The model never sees a figure it could retype: every figure is a placeholder ({v1}, {v2}...) whose meaning is explained, and the model must
use the placeholders verbatim. Its answer is refused when it contains ANY digit outside a placeholder or an unknown placeholder; the
placeholders are then substituted with the Finding's own formatted strings and the result goes through the figures gate (app.growth.gate).

Who writes: the intel router's job kind "growth.write" (PC qwen first, then Claude under the shared daily cap, else rules). With rules / no
answer / a refused answer, the deterministic template text stands: templates alone produce postable drafts.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.growth import gate
from app.growth.findings import PLACEHOLDER, Finding

KIND = "growth.write"
PROMPT_VERSION = "g1"
SCHEMA: dict[str, Any] = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
                          "additionalProperties": False}

SYSTEM = """You write one X (Twitter) post for Price Chat, a crypto markets account, from a computed finding.
Rules, all hard:
- Figures: NEVER type a digit. Every number, percentage, price, date or year must be one of the placeholders given, copied exactly with its
  braces, e.g. {v1}. Use each placeholder at most once. Do not invent figures, dates or comparisons.
- Superlatives and comparisons ("record", "lowest", "first since", "only", "most"...) only from the allowed list.
- History, never prediction: no "will", "should", "expect", targets, buy/sell, advice. For crowded-trade findings keep the "has a history
  of ... it doesn't always" framing.
- Plain, confident, analyst tone. No hype words, no emojis, no hashtags, no cashtags, no links, no @mentions, no questions to the reader.
- At most 260 characters after the placeholders are filled. Line breaks are fine.
Answer with JSON: {"text": "..."}"""


def placeholder_map(f: Finding) -> dict[str, str]:
    """finding number name -> v1, v2... (stable order)."""
    return {k: f"v{i}" for i, k in enumerate(sorted(f.numbers), 1)}


def _as_v(template: str, f: Finding, pm: dict[str, str]) -> str:
    return PLACEHOLDER.sub(lambda m: "{" + pm[m.group(1)] + "}" if m.group(1) in pm else f.words.get(m.group(1), m.group(0)), template)


def build(f: Finding, attempt: int = 0, avoid: str = "") -> dict[str, Any]:
    """The job payload: system / user / schema (+ text, which the router's validation reads)."""
    pm = placeholder_map(f)
    lines = [f"Finding ({f.format}, {f.detector}), as of {f.as_of.isoformat()}:", "Fixed words you may use: " + (", ".join(f"{k}={v}" for k, v in sorted(f.words.items())) or "none"), "",
             "Placeholders (meaning = value):"]
    for k, v in sorted(pm.items(), key=lambda x: int(x[1][1:])):
        num = f.numbers[k]
        lines.append(f"{{{v}}} = {num.get('label', k)} = {num['text']}")
    lines += ["", "Allowed superlatives / comparisons: " + (", ".join(sorted(set(f.comparatives))) or "none"),
              "Example (same facts, write your own wording; keep the placeholders):", _as_v(f.templates[attempt % len(f.templates)], f, pm)]
    if avoid:
        lines += ["", "Write it differently from this earlier version:", avoid]
    user = "\n".join(x for x in lines if x is not None)
    return {"system": SYSTEM, "user": user, "schema": SCHEMA, "text": user, "finding_key": f.key, "prompt_version": PROMPT_VERSION}


_DIGIT_OUTSIDE = re.compile(r"\{v\d+\}")


def substitute(f: Finding, raw: str) -> tuple[str | None, list[str]]:
    """(final text, problems). The model's raw text with {vN} placeholders -> the Finding's figures, refused on any stray digit or unknown
    placeholder, then the figures gate."""
    if not isinstance(raw, str) or not raw.strip():
        return None, ["empty answer"]
    pm = placeholder_map(f)
    back = {v: f.numbers[k]["text"] for k, v in pm.items()}
    stripped = _DIGIT_OUTSIDE.sub("", raw)
    probs = []
    if re.search(r"\d", stripped):
        probs.append("the model typed a figure itself (digits outside the placeholders)")
    unknown = [m for m in re.findall(r"\{([^}]*)\}", raw) if m not in back]
    if unknown:
        probs.append(f"unknown placeholders: {', '.join(unknown[:5])}")
    if probs:
        return None, probs
    text = re.sub(r"\{(v\d+)\}", lambda m: back[m.group(1)], raw).strip()
    bad = gate.problems(text, f)
    return (None, bad) if bad else (text, [])


def template_text(f: Finding, variant: int = 0) -> tuple[str | None, int]:
    """The first template (from `variant` on, wrapping) whose text passes the gate: (text, variant used), or (None, -1)."""
    k = len(f.templates)
    for i in range(k):
        v = (variant + i) % k
        t = f.text(v)
        if gate.passes(t, f):
            return t, v
    return None, -1


def parse_output(output: Any) -> str | None:
    if isinstance(output, dict) and isinstance(output.get("text"), str):
        return output["text"]
    if isinstance(output, str):
        try:
            d = json.loads(output)
        except ValueError:
            return None
        return parse_output(d)
    return None
