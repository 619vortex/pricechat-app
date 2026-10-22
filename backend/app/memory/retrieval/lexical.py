"""Keyword + tag retrieval. Works with no model at all; always available as a fallback."""

import math
import re
import time
from dataclasses import dataclass

from app.memory.records import MemoryRecord
from app.memory.retrieval.base import RetrievalQuery, RetrievalResult, RetrievedMemory
from app.memory.store import MemoryStore

_WORD = re.compile(r"[a-z0-9][a-z0-9'\-]*")

STOPWORDS = frozenset(
    """
    a about above after again all also am an and any are as at be because been before being below
    between both but by can could did do does doing down during each few for from further had has
    have having he her here hers him his how i if in into is it its itself just me more most my no
    nor not now of off on once only or other our ours out over own same she should so some such
    than that the their theirs them then there these they this those through to too under until up
    very was we were what when where which while who whom why will with would you your yours
    tell know think want please give show get make let us
    """.split()
)


def stem(word: str) -> str:
    """Tiny suffix stripper so 'launches'/'launched'/'launch' match. Not linguistics, just recall."""
    for suffix, repl in (("ies", "y"), ("sses", "ss"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)] + repl
    return word


def query_terms(text: str, max_terms: int = 12) -> list[str]:
    terms: list[str] = []
    for word in _WORD.findall(text.lower()):
        word = word.strip("'-")
        if len(word) < 3 and not word.isdigit():
            continue
        if word in STOPWORDS:
            continue
        s = stem(word)
        if s not in terms:
            terms.append(s)
    return terms[:max_terms]


@dataclass(frozen=True, slots=True)
class LexicalScore:
    score: float  # normalised 0..1
    matched: tuple[str, ...]


def score_records(records: list[MemoryRecord], terms: list[str], corpus_size: int) -> dict[int, LexicalScore]:
    if not records or not terms:
        return {}

    docs = []
    for r in records:
        tags = " ".join(r.tags)
        docs.append((r, (r.title or "").lower(), r.content.lower(), tags))

    df = {t: sum(1 for _, title, body, tags in docs if t in title or t in body or t in tags) for t in terms}
    n = max(corpus_size, len(records))
    idf = {t: math.log(1 + (n + 1) / (df[t] + 1)) for t in terms}

    raw: dict[int, tuple[float, tuple[str, ...]]] = {}
    for record, title, body, tags in docs:
        total, matched = 0.0, []
        for t in terms:
            hits = body.count(t)
            in_title = t in title
            in_tags = t in tags
            if not (hits or in_title or in_tags):
                continue
            matched.append(t)
            total += idf[t] * (math.log1p(hits) + (1.0 if in_title else 0.0) + (1.5 if in_tags else 0.0))
        if matched:
            raw[record.id] = (total, tuple(matched))

    if not raw:
        return {}
    top = max(v[0] for v in raw.values()) or 1.0
    return {
        rid: LexicalScore(
            # Blend relative strength with query coverage so one rare-word hit doesn't dominate.
            score=0.5 * (total / top) + 0.5 * (len(matched) / len(terms)),
            matched=matched,
        )
        for rid, (total, matched) in raw.items()
    }


class LexicalRetriever:
    def __init__(self, store: MemoryStore, candidate_limit: int = 300) -> None:
        self.store = store
        self.candidate_limit = candidate_limit

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        started = time.perf_counter()
        terms = query_terms(query.text)
        candidates = self.store.search_terms(terms, query.filters, self.candidate_limit)
        scores = score_records(candidates, terms, self.store.count())
        by_id = {r.id: r for r in candidates}
        ranked = sorted(scores.items(), key=lambda kv: (kv[1].score, by_id[kv[0]].importance), reverse=True)
        memories = [
            RetrievedMemory(
                record=by_id[rid],
                score=round(s.score, 4),
                lexical=round(s.score, 4),
                reasons=(f"keywords: {', '.join(s.matched)}",),
            )
            for rid, s in ranked[: query.limit]
        ]
        return RetrievalResult(memories, "lexical", round((time.perf_counter() - started) * 1000, 1))
