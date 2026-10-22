"""Formatting community messages for model prompts.

Members are pseudonymised ("Member 3") inside prompts: the model needs to know
*that* different people said things, not *who* they are. Message ids are kept so
outputs can cite their evidence.
"""

import re
from collections.abc import Sequence

from app.models.telegram import TelegramMessage


class Pseudonyms:
    def __init__(self) -> None:
        self._map: dict[int | str, str] = {}

    def name(self, message: TelegramMessage) -> str:
        key: int | str = message.author_id if message.author_id is not None else (message.author_name or "unknown")
        if key not in self._map:
            self._map[key] = f"Member {len(self._map) + 1}"
        return self._map[key]


def format_messages(messages: Sequence[TelegramMessage], char_budget: int = 12000, max_chars_each: int = 500) -> tuple[str, list[int]]:
    """Render messages as '[m<ID>] Member N (reply to m<ID>): text'. Returns (text, ids included)."""
    names = Pseudonyms()
    by_tg_id = {(m.chat_id, m.message_id): m.id for m in messages}
    lines: list[str] = []
    included: list[int] = []
    used = 0
    for m in messages:
        body = " ".join(m.text.split())[:max_chars_each] or f"[{m.media_type or 'media'}]"
        reply = ""
        if m.reply_to_message_id is not None:
            target = by_tg_id.get((m.chat_id, m.reply_to_message_id))
            reply = f" (reply to m{target})" if target else " (reply)"
        line = f"[m{m.id}] {names.name(m)}{reply}: {body}"
        if used + len(line) > char_budget:
            break
        lines.append(line)
        included.append(m.id)
        used += len(line) + 1
    return "\n".join(lines), included


def sample_evenly(messages: Sequence[TelegramMessage], limit: int) -> list[TelegramMessage]:
    """Keep chronology while thinning very busy periods; questions are always kept."""
    if len(messages) <= limit:
        return list(messages)
    questions = [m for m in messages if m.is_question]
    others = [m for m in messages if not m.is_question]
    room = max(limit - len(questions), 0)
    step = max(len(others) / room, 1) if room else float("inf")
    picked = [others[int(i * step)] for i in range(room) if int(i * step) < len(others)]
    return sorted(questions[:limit] + picked, key=lambda m: m.sent_at)[:limit]


_CITATION = re.compile(r"\s*[\(\[]?\[?\bm\d+\]?(?:\s*,\s*\[?m\d+\]?)*[\)\]]?")


def strip_citations(text: str) -> str:
    """Remove inline '[m12, m15]' / '(m12)' references. Provenance links keep the evidence; the text stays readable."""
    cleaned = _CITATION.sub("", text)
    return re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r"\s{2,}", " ", cleaned)).strip()


def valid_ids(cited: Sequence[int | str], allowed: set[int]) -> list[int]:
    """Keep only citations of messages the model was actually shown (drops hallucinated ids)."""
    out: list[int] = []
    for raw in cited:
        try:
            value = int(str(raw).lstrip("mM"))
        except ValueError:
            continue
        if value in allowed and value not in out:
            out.append(value)
    return out
