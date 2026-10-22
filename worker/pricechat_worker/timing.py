"""Caption timing: the script's written words, each with the time it is actually spoken.

Whisper hears the voice and returns word timestamps; those are aligned to the script's words (a Needleman-Wunsch match
that tolerates "$17,225" being heard as "17,225" or "seventeen thousand..."). Script words Whisper didn't match get times
interpolated between their matched neighbours, always inside their own segment's spoken span (from the voice takes), so a
bad recognition can never push a caption into the wrong sentence. Without Whisper, words share their segment's span by
length (the v1 method). The match rate doubles as the "narration matches the script" quality check."""

import re
from difflib import SequenceMatcher
from typing import Any

PUNCT_BREAK = ".,!?;:—–"


def norm(w: str) -> str:
    w = w.lower().replace("’", "'")
    if re.search(r"\d", w):
        return re.sub(r"[^\d.]", "", w).rstrip(".")
    return re.sub(r"[^a-z0-9']", "", w)


def _merge_asr(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Whisper splits numbers ("$17" + ",225"): glue the pieces back together."""
    out: list[dict[str, Any]] = []
    for w in words:
        t = str(w.get("w") or "").strip()
        if not t:
            continue
        if out and re.match(r"^[,.]\d", t) and re.search(r"\d$", out[-1]["w"]):
            out[-1] = {**out[-1], "w": out[-1]["w"] + t, "e": w["e"]}
        else:
            out.append({"w": t, "s": float(w["s"]), "e": float(w["e"])})
    return out


def _sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def align(script_words: list[str], asr_words: list[dict[str, Any]]) -> list[int | None]:
    """For each script word, the index of the ASR word it was matched to (or None)."""
    a = [norm(w) for w in script_words]
    b = [norm(w["w"]) for w in asr_words]
    n, m = len(a), len(b)
    gap = -0.45
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0], back[i][0] = i * gap, 1
    for j in range(1, m + 1):
        score[0][j], back[0][j] = j * gap, 2
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            s = _sim(a[i - 1], b[j - 1])
            diag = score[i - 1][j - 1] + (2.0 if s >= 0.99 else 1.0 if s >= 0.72 else -0.8)
            up, left = score[i - 1][j] + gap, score[i][j - 1] + gap
            best = max(diag, up, left)
            score[i][j] = best
            back[i][j] = 0 if best == diag else 1 if best == up else 2
    out: list[int | None] = [None] * n
    i, j = n, m
    while i > 0 and j > 0:
        if back[i][j] == 0:
            if _sim(a[i - 1], b[j - 1]) >= 0.72:
                out[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif back[i][j] == 1:
            i -= 1
        else:
            j -= 1
    return out


def _proportional(words: list[str], start: float, end: float) -> list[tuple[float, float]]:
    weights = [len(re.sub(r"\W", "", w)) + 2 for w in words]
    total = sum(weights) or 1
    t, out = start, []
    for wt in weights:
        d = (end - start) * wt / total
        out.append((t, t + d))
        t += d
    return out


def words(segments: list[dict[str, Any]], spans: list[tuple[float, float]], asr: dict[str, Any] | None,
          caption_mode: str = "pop") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """[{t, s, e, hl, p}] for the renderer + {"matched": share of script words Whisper confirmed, "heard": its transcript}."""
    from pricechat_worker.voice2 import BEAT

    seg_words = []
    for s in segments:  # a lone dash or ellipsis is not a caption word: it sticks to the word before it
        ws: list[str] = []
        for w in BEAT.sub(" ", str(s["text"])).split():
            if not re.search(r"[\w$%]", w) and ws:
                ws[-1] += "," if w in ("—", "–", "-") else w
            elif re.search(r"[\w$%]", w):
                ws.append(w)
        seg_words.append(ws)
    flat = [(k, w) for k, ws in enumerate(seg_words) for w in ws]
    asr_words = _merge_asr((asr or {}).get("words") or [])
    times: list[tuple[float, float] | None] = [None] * len(flat)
    matched = 0
    if asr_words:
        idx = align([w for _, w in flat], asr_words)
        for i, j in enumerate(idx):
            if j is None:
                continue
            k = flat[i][0]
            s0, s1 = spans[k]
            a = asr_words[j]
            if a["s"] >= s0 - 0.35 and a["e"] <= s1 + 0.35:  # only trust a match inside its own segment
                times[i] = (max(s0, a["s"]), min(s1, max(a["e"], a["s"] + 0.05)))
                matched += 1
    # fill the gaps: interpolate between matched neighbours inside each segment (or the whole span if nothing matched)
    pos = 0
    for k, ws in enumerate(seg_words):
        s0, s1 = spans[k]
        ids = list(range(pos, pos + len(ws)))
        pos += len(ws)
        known = [i for i in ids if times[i] is not None]
        if len(known) < max(1, len(ids) // 3):
            for i, tt in zip(ids, _proportional(ws, s0, s1)):
                times[i] = tt
            continue
        run: list[int] = []
        for i in ids + [None]:  # type: ignore[list-item]
            if i is not None and times[i] is None:
                run.append(i)
                continue
            if run:
                left = times[run[0] - 1][1] if run[0] - 1 >= ids[0] and times[run[0] - 1] else s0  # type: ignore[index]
                right = times[i][0] if i is not None else s1  # type: ignore[index]
                for r, tt in zip(run, _proportional([flat[x][1] for x in run], left, max(left + 0.05 * len(run), right))):
                    times[r] = tt
                run = []
    # highlights: numbers, money and the script's emphasis words
    emph = [{norm(x) for e in (s.get("emphasis") or []) for x in str(e).split()} for s in segments]
    out = []
    for i, (k, w) in enumerate(flat):
        s, e = times[i]  # type: ignore[misc]
        out.append({"t": w, "s": round(s, 3), "e": round(max(e, s + 0.04), 3), "hl": bool(re.search(r"\d", w)) or norm(w) in emph[k], "k": k})
    for a, b in zip(out, out[1:]):  # never overlapping, always in order, every word on screen for at least 40 ms
        if b["s"] < a["s"] + 0.04:
            b["s"] = round(a["s"] + 0.04, 3)
            b["e"] = max(b["e"], round(b["s"] + 0.04, 3))
        a["e"] = min(a["e"], b["s"])
    _phrases(out, caption_mode)
    stats = {"matched": round(matched / len(flat), 3) if flat else 0.0, "heard": (asr or {}).get("text", ""), "asr": bool(asr_words)}
    return out, stats


def _phrases(ws: list[dict[str, Any]], mode: str) -> None:
    """Group words into caption phrases (phrase id `p`): punchy styles show up to 3 words, calm ones up to 5. A sentence is
    split at its commas into clauses, each clause into balanced chunks (4 words -> 2+2, never 3+1); a one-word clause
    joins its neighbour when there's room, so no phrase is a lone "2026." unless it stands alone in the voice."""
    max_words, max_chars = {"pop": (3, 18), "boxed": (3, 16), "karaoke": (5, 26), "clean": (5, 28)}.get(mode, (3, 18))
    clauses: list[list[int]] = []
    for i, w in enumerate(ws):
        if not clauses or ws[i - 1]["k"] != w["k"] or ws[i - 1]["t"][-1:] in PUNCT_BREAK or w["s"] - ws[i - 1]["e"] > 0.45:
            clauses.append([])
        clauses[-1].append(i)
    merged: list[list[int]] = []
    for c in clauses:
        prev = merged[-1] if merged else None
        lone = len(c) == 1 and len(ws[c[0]]["t"]) < 9
        same_sentence = prev is not None and ws[prev[-1]]["k"] == ws[c[0]]["k"] and ws[prev[-1]]["t"][-1:] not in ".!?"
        if prev and lone and same_sentence and len(prev) < max_words + 1 and sum(len(ws[i]["t"]) + 1 for i in prev + c) <= max_chars + 6:
            prev.extend(c)
        else:
            merged.append(list(c))
    p = 0
    for c in merged:
        chars = sum(len(ws[i]["t"]) + 1 for i in c)
        n = max(-(-len(c) // max_words), -(-chars // (max_chars + 2)), 1)
        n = min(n, len(c))
        if len(c) >= 2 and len(c) < 2 * n:  # a split would leave a lone word: fewer, slightly longer phrases (they wrap to 2 lines)
            n = max(1, len(c) // 2)
        size, extra = divmod(len(c), n)
        k = 0
        for part in range(n):
            take = size + (1 if part < extra else 0)
            for i in c[k:k + take]:
                ws[i]["p"] = p
            k += take
            p += 1
    # never split a grade or ticker from its number ("PSA / 10"): the number joins the acronym's phrase
    for i in range(1, len(ws)):
        if ws[i]["p"] != ws[i - 1]["p"] and re.fullmatch(r"[A-Z]{2,5}", ws[i - 1]["t"]) and re.match(r"\d", ws[i]["t"]):
            ws[i]["p"] = ws[i - 1]["p"]
    for w in ws:
        w.pop("k", None)
