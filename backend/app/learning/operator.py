"""Interpretable learning from the X approval queue (no model training).

Every decision on a proposed X action is stored as a FeedbackEvent with readable features
(action, length, hashtags, emoji, brand mentions...). Edits also record *what changed*
(shortened, removed hashtags, removed the opening, ...). From these counts Price Chat derives
plain-English signals, shown on the dashboard and used as guidance when drafting replies/posts.
"""

import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import FeedbackDecision, FeedbackTarget
from app.models.social_intel import ApprovalRequest, FeedbackEvent

_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
_WORD = re.compile(r"[a-z0-9']+")
GENERIC_OPENINGS = ("great", "absolutely", "this is so", "couldn't agree", "love this", "so true", "100%", "well said", "interesting")


def text_features(text: str) -> list[str]:
    t = text or ""
    low = t.lower()
    n = len(t)
    feats = [f"length:{'short' if n <= 120 else 'medium' if n <= 220 else 'long'}"]
    if "#" in t:
        feats.append("has:hashtag")
    if _EMOJI.search(t):
        feats.append("has:emoji")
    if "?" in t:
        feats.append("has:question")
    if "price chat" in low or "pricechat" in low:
        feats.append("has:brand_mention")
    if low.lstrip().startswith(GENERIC_OPENINGS):
        feats.append("has:generic_opening")
    return feats


def edit_features(original: str, edited: str) -> list[str]:
    """What the human changed, in words."""
    o, e = original or "", edited or ""
    feats: list[str] = []
    if len(e) < 0.8 * len(o):
        feats.append("edit:shortened")
    elif len(e) > 1.2 * len(o):
        feats.append("edit:lengthened")
    if o.count("#") > e.count("#"):
        feats.append("edit:removed_hashtags")
    if len(_EMOJI.findall(o)) > len(_EMOJI.findall(e)):
        feats.append("edit:removed_emoji")
    if ("price chat" in o.lower()) and ("price chat" not in e.lower()):
        feats.append("edit:removed_brand_mention")
    ow, ew = _WORD.findall(o.lower()), _WORD.findall(e.lower())
    if ow[:3] and ew[:3] != ow[:3] and " ".join(ow[3:8]) in " ".join(ew):
        feats.append("edit:removed_opening")
    if o.lower().lstrip().startswith(GENERIC_OPENINGS) and not e.lower().lstrip().startswith(GENERIC_OPENINGS):
        feats.append("edit:removed_generic_opening")
    if o.count("?") > e.count("?"):
        feats.append("edit:removed_question")
    if len(re.findall(r"\d", e)) > len(re.findall(r"\d", o)):
        feats.append("edit:added_specifics")
    return feats or ["edit:reworded"]


def record_decision(db: Session, approval: ApprovalRequest, decision: FeedbackDecision, original: str | None = None,
                    final: str | None = None, note: str = "") -> FeedbackEvent:
    payload = approval.payload or {}
    text = final if final is not None else (payload.get("text") or "")
    feats = [f"action:{approval.action.value}", f"source:{approval.source_kind}", *text_features(text)]
    if payload.get("topic"):
        feats.append(f"topic:{str(payload['topic']).lower()[:40]}")
    if any(str(c).startswith("Telegram community") for c in payload.get("context") or []):
        feats.append("ctx:community")
    if payload.get("origin"):
        feats.append(f"origin:{payload['origin']}")
    if decision == FeedbackDecision.EDIT and original is not None:
        feats += edit_features(original, final or "")
    ev = FeedbackEvent(target_kind=FeedbackTarget.SOCIAL_ACTION, target_id=approval.id, decision=decision, features=feats,
                       label=f"{approval.action.value}: {(text or approval.target_username or '')[:200]}", note=note[:1000])
    db.add(ev)
    db.flush()
    return ev


PLURAL = {"reply": "replies", "post": "posts", "like": "likes", "repost": "reposts", "follow": "follows"}
FEATURE_TEXT = {
    "length:long": "long texts", "length:medium": "medium-length texts", "length:short": "short texts",
    "has:hashtag": "texts with hashtags", "has:emoji": "texts with emoji", "has:question": "texts that ask a question",
    "has:brand_mention": "texts that mention Price Chat", "has:generic_opening": "texts with a generic opening",
}


@dataclass(slots=True)
class Signal:
    key: str
    text: str
    count: int
    of: int
    state: str = "active"  # active | accepted | disabled


EDIT_PHRASES = {
    "edit:shortened": "shorten the proposed text",
    "edit:removed_hashtags": "remove hashtags",
    "edit:removed_emoji": "remove emoji",
    "edit:removed_brand_mention": "remove mentions of Price Chat",
    "edit:removed_opening": "cut the opening sentence",
    "edit:removed_generic_opening": "remove generic openings",
    "edit:removed_question": "remove questions",
    "edit:lengthened": "add more detail",
    "edit:added_specifics": "add a concrete number or detail",
}
HINTS = {
    "edit:shortened": "Keep it shorter than your first instinct; the reviewer usually shortens drafts.",
    "edit:removed_hashtags": "Do not use hashtags.",
    "rejects:has:hashtag": "Do not use hashtags.",
    "edit:removed_emoji": "Do not use emoji.",
    "rejects:has:emoji": "Do not use emoji.",
    "edit:removed_brand_mention": "Do not mention Price Chat.",
    "rejects:has:brand_mention": "Do not mention Price Chat.",
    "edit:removed_opening": "Start with the substance; no warm-up sentence.",
    "edit:removed_generic_opening": "Start with the substance; no warm-up sentence.",
    "rejects:has:generic_opening": "Start with the substance; no warm-up sentence.",
    "edit:removed_question": "Do not end with a question unless it genuinely moves the conversation.",
    "rejects:has:question": "Do not end with a question unless it genuinely moves the conversation.",
    "rejects:length:long": "Keep it short.",
    "edit:added_specifics": "Make one concrete observation instead of a general statement.",
    "approves:community_backed": "When the context has a Telegram community signal, use it as an aggregate observation (never name members).",
}
PINNED_TEXT = {k: f"Pinned by you: {v}" for k, v in HINTS.items()}


def _compute(events: list[FeedbackEvent]) -> list[Signal]:
    if not events:
        return []
    out: list[Signal] = []
    by_action: dict[str, Counter] = {}
    for ev in events:
        action = next((f.split(":", 1)[1] for f in ev.features if f.startswith("action:")), "?")
        by_action.setdefault(action, Counter())[ev.decision.value] += 1
    for action, c in sorted(by_action.items()):
        decided = c["approve"] + c["reject"] + c["edit"]
        if decided >= 2:
            ok = c["approve"] + c["edit"]
            out.append(Signal(f"approval_rate:{action}", f"You approve {ok} of {decided} proposed {PLURAL.get(action, action + 's')}"
                              + (f" (editing {c['edit']})" if c["edit"] else ""), ok, decided))
    edits = [ev for ev in events if ev.decision == FeedbackDecision.EDIT]
    if edits:
        ec = Counter(f for ev in edits for f in ev.features if f.startswith("edit:"))
        for key, phrase in EDIT_PHRASES.items():
            if ec[key] >= 2:
                out.append(Signal(key, f"You frequently {phrase} ({ec[key]} of {len(edits)} edits)", ec[key], len(edits)))
    approved = [ev for ev in events if ev.decision in (FeedbackDecision.APPROVE, FeedbackDecision.EDIT)]
    rejects = [ev for ev in events if ev.decision == FeedbackDecision.REJECT]
    if len(rejects) >= 2:
        # Features that are much more common in rejected proposals than in approved ones.
        rc = Counter(f for ev in rejects for f in ev.features if f.startswith(("has:", "length:")))
        ac = Counter(f for ev in approved for f in ev.features if f.startswith(("has:", "length:")))
        for f, n in rc.most_common(4):
            share_rejected = n / len(rejects)
            share_approved = ac[f] / len(approved) if approved else 0.0
            if n >= 2 and share_rejected >= 0.5 and share_rejected >= share_approved + 0.3:
                out.append(Signal(f"rejects:{f}", f"You often reject {FEATURE_TEXT.get(f, f.split(':', 1)[1].replace('_', ' '))} "
                                  f"({n} of {len(rejects)} rejections)", n, len(rejects)))
    decided = approved + rejects
    with_ctx = [ev for ev in decided if "ctx:community" in ev.features]
    without = [ev for ev in decided if "ctx:community" not in ev.features]
    if len(with_ctx) >= 3 and without:
        rate_c = sum(ev in approved for ev in with_ctx) / len(with_ctx)
        rate_o = sum(ev in approved for ev in without) / len(without)
        if rate_c >= rate_o + 0.2:
            out.append(Signal("approves:community_backed", f"Community-backed observations get approved more often "
                              f"({round(100 * rate_c)}% vs {round(100 * rate_o)}%)", round(100 * rate_c), 100))
    return out


def operator_signals(db: Session, include_disabled: bool = False) -> list[Signal]:
    """What Price Chat is learning, after the human's controls: reset evidence is ignored, disabled
    signals are hidden (unless asked), accepted ones are kept even if the evidence later drops."""
    from app.models.social_intel import OperatorPreference

    events = list(db.scalars(select(FeedbackEvent).where(FeedbackEvent.target_kind == FeedbackTarget.SOCIAL_ACTION)))
    prefs = {p.key: p for p in db.scalars(select(OperatorPreference))}
    by_key = {s.key: s for s in _compute(events)}
    for key, p in prefs.items():
        if p.reset_at is not None:
            after = [ev for ev in events if ev.created_at and _aware(ev.created_at) > _aware(p.reset_at)]
            fresh = {s.key: s for s in _compute(after)}
            if key in fresh:
                by_key[key] = fresh[key]
            else:
                by_key.pop(key, None)
    out: list[Signal] = []
    for key, sig in by_key.items():
        state = prefs[key].state if key in prefs else "active"
        if state == "disabled" and not include_disabled:
            continue
        sig.state = state
        out.append(sig)
    for key, p in prefs.items():
        if p.state == "accepted" and key not in by_key:
            out.append(Signal(key, PINNED_TEXT.get(key, f"Pinned by you: {key}"), 0, 0, "accepted"))
    return out


def _aware(dt):  # type: ignore[no-untyped-def]
    from datetime import timezone

    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def set_preference(db: Session, key: str, action: str) -> None:
    """accept | disable | reset | activate."""
    from app.db.base import utcnow
    from app.models.social_intel import OperatorPreference

    p = db.scalar(select(OperatorPreference).where(OperatorPreference.key == key))
    if p is None:
        p = OperatorPreference(key=key[:96], state="active")
        db.add(p)
    if action == "accept":
        p.state = "accepted"
    elif action == "disable":
        p.state = "disabled"
    elif action == "activate":
        p.state = "active"
    elif action == "reset":
        p.state, p.reset_at = "active", utcnow()
    else:
        raise ValueError("Use accept, disable, activate or reset")
    db.commit()


def drafting_hints(db: Session) -> list[str]:
    """Short instructions for the reply/post writer, from active and accepted signals (never disabled ones)."""
    hints: list[str] = []
    for s in operator_signals(db):
        h = HINTS.get(s.key)
        if h and h not in hints:
            hints.append(h)
    return hints
