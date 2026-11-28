"""Community Intelligence: the feature flag, intake (Telegram and the Passport), automatic checks (source class, independent
corroboration from the Terminal's own sources, live market data for anomalies, safety flags), duplicate detection, the editor's
decisions, appeals, and the reputation a tip earns (community score lines and Researcher badges; never XP or money).

Nothing here publishes. An accepted tip joins the Terminal story about the same event once the desk's own sources report it; that story
goes out only through the desk's normal gates when the owner publishes it. The member's link is never fetched by the server (no requests
to addresses a member chose): verification uses what the Terminal already collected and Hyperliquid's public market data.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.community_intel import rules
from app.config.settings import get_settings
from app.db.base import utcnow
from app.models.community_intel import CiEvent, CiSubmission
from app.models.members import Member

Reader = Callable[[str], "dict[str, float] | None"]


class CiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _aware(d: datetime | None) -> datetime | None:
    return d if d is None or d.tzinfo else d.replace(tzinfo=timezone.utc)


def ref(s: CiSubmission) -> str:
    return f"CI-{s.id:04d}"


# --- the flag -----------------------------------------------------------------------------------------------------------------------


def mode(settings: Any = None) -> str:
    return str(getattr(settings or get_settings(), "community_intel_mode", "off") or "off")


def visible(member: Member | None, settings: Any = None) -> bool:
    """Whether this member can send tips and see their track record. Needs Passport 2.0 (reputation and badges live there)."""
    from app.passport import service as passport

    m = mode(settings)
    if member is None or m == "off" or passport.mode() == "off":
        return False
    if m == "live":
        return True
    names = {u.strip().lower().lstrip("@") for u in str(getattr(settings or get_settings(), "community_intel_preview_members", "") or "").split(",") if u.strip()}
    return (member.username or "").lower() in names


def log(db: Session, s: CiSubmission, actor: str, action: str, note: str = "", **details: Any) -> None:
    db.add(CiEvent(submission_id=s.id, actor=actor[:64], action=action[:32], details=details, note=note[:2000]))


# --- links ------------------------------------------------------------------------------------------------------------------------------

URL_RX = re.compile(r"https?://[^\s<>\"'\)\]]+", re.I)
_DROP = re.compile(r"^(utm_\w+|fbclid|gclid|dclid|mc_\w+|igshid|si|ref|ref_src|ref_url|cmpid|smid|s|t|share|src|source|guccounter|__\w+)$", re.I)


def find_url(text: str) -> str:
    m = URL_RX.search(text or "")
    return m.group(0).rstrip(".,;:!?") if m else ""


def host_of(url: str) -> str:
    try:
        h = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    for p in ("www.", "m.", "mobile.", "amp."):
        if h.startswith(p):
            h = h[len(p):]
    return h


def canonical(url: str) -> str:
    """The same page whatever tracking it carries: scheme and host normalised, tracking parameters and the fragment dropped,
    x.com / twitter.com and youtu.be folded together, a trailing slash removed."""
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return ""
    if p.scheme.lower() not in ("http", "https") or not p.hostname:
        return ""
    host = host_of(url)
    path = re.sub(r"/+$", "", p.path or "") or "/"
    if host in ("twitter.com", "x.com"):
        host = "x.com"
        m = re.match(r"^/([^/]+)/status(?:es)?/(\d+)", path)
        if m:
            return f"https://x.com/{m.group(1).lower()}/status/{m.group(2)}"
    if host == "youtu.be":
        return f"https://youtube.com/watch?v={path.strip('/')}"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=False) if not _DROP.match(k)))
    return urlunsplit(("https", host, path, query, ""))


def _in(host: str, names: set[str] | dict[str, str]) -> str | None:
    for n in names:
        if host == n or host.endswith("." + n):
            return n
    return None


def source_class(host: str) -> dict[str, Any]:
    if not host:
        return {"class": "none", "label": "No link", "outlet": ""}
    if _in(host, rules.SHORTENERS):
        return {"class": "shortener", "label": "Shortened link", "outlet": host}
    if _in(host, rules.OFFICIAL_HOSTS) or host.endswith(rules.OFFICIAL_SUFFIXES):
        return {"class": "official", "label": "Official source", "outlet": host}
    hit = _in(host, rules.OUTLETS)
    if hit:
        return {"class": "outlet", "label": "Known outlet", "outlet": rules.OUTLETS[hit]}
    if _in(host, rules.MARKET_DATA):
        return {"class": "market", "label": "Market data", "outlet": host}
    if _in(host, rules.SOCIAL):
        return {"class": "social", "label": "Social post", "outlet": host}
    return {"class": "unknown", "label": "Unknown publisher", "outlet": host}


CASHTAG = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,9})\b")


def tickers_in(text: str) -> list[str]:
    return list(dict.fromkeys(t.upper() for t in CASHTAG.findall(text or "")))[:6]


def guess_kind(host: str, text: str) -> str:
    low = f" {text.lower()} "
    if _in(host, rules.MARKET_DATA) or any(w in low for w in rules.ANOMALY_WORDS):
        return "anomaly"
    if len(text) > 280 and (_in(host, rules.MARKET_DATA) or "data" in low or "chart" in low or "analysis" in low):
        return "research"
    if host.endswith(("github.com", "github.io")) or "tool" in low or "dashboard" in low:
        return "discovery"
    return "news"


def safety_flags(member: Member, text: str, tickers: list[str], host_class: str, now: datetime) -> list[str]:
    low = (text or "").lower()
    out = []
    if any(w in low for w in rules.PUMP_WORDS):
        out.append("Pump wording")
    if len(tickers) > 2:
        out.append(f"{len(tickers)} cashtags")
    age = now - (_aware(member.created_at) or now)
    if age < timedelta(days=14):
        out.append(f"Passport {max(0, age.days)} days old")
    if host_class == "unknown":
        out.append("Unknown publisher")
    return out


# --- intake ----------------------------------------------------------------------------------------------------------------------------


def level_of(db: Session, member: Member) -> str:
    from app.passport import service as passport

    return passport.community_score(db, member)["level"]["name"]


def upheld_spam(db: Session, member: Member, now: datetime) -> int:
    return int(db.scalar(select(func.count()).select_from(CiSubmission).where(
        CiSubmission.member_id == member.id, CiSubmission.status == "rejected", CiSubmission.reason.in_(rules.SPAM_REASONS),
        func.coalesce(CiSubmission.appeal_status, "") != "upheld", CiSubmission.decided_at >= now - timedelta(days=90))) or 0)


def can_submit(db: Session, member: Member, now: datetime) -> None:
    if not visible(member):
        raise CiError("Tips aren't open yet.", 404)
    if member.status != "active" or member.xp_suspended or member.review_seat:
        raise CiError("This account can't send tips.", 403)
    if now - (_aware(member.created_at) or now) < rules.MIN_PASSPORT_AGE:
        raise CiError("Tips open once your Passport is 3 days old.", 403)
    if upheld_spam(db, member, now) >= rules.SPAM_LOCKOUT:
        raise CiError("Tips are paused on this account after repeated spam rejections. You can appeal them from your Passport.", 403)
    last = db.scalar(select(func.max(CiSubmission.created_at)).where(CiSubmission.member_id == member.id))
    if last is not None and now - _aware(last) < rules.MIN_GAP:  # type: ignore[operator]
        raise CiError("One tip a minute, please.", 429)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    sent = int(db.scalar(select(func.count()).select_from(CiSubmission).where(CiSubmission.member_id == member.id, CiSubmission.created_at >= day)) or 0)
    cap = rules.PER_DAY.get(level_of(db, member), 2)
    if sent >= cap:
        raise CiError(f"That's today's {cap} tips. More open as your community score grows.", 429)


def submit(db: Session, member: Member, *, via: str, text: str = "", url: str = "", title: str = "", kind: str | None = None,
           disclosure: str | None = None, credit: bool = True, forward_from: str | None = None, forward_key: str | None = None,
           tg_chat_id: int | None = None, now: datetime | None = None, reader: Reader | None = None) -> CiSubmission:
    from app.tradingos.tgtrade import looks_secret

    now = now or utcnow()
    can_submit(db, member, now)
    text = (text or "").strip()
    if looks_secret(text, private=True):
        raise CiError("That looks like a private key or recovery phrase. It wasn't saved: never send one to anyone.")
    url = (url or find_url(text)).strip()[:500]
    if not url and forward_key:
        url = f"https://t.me/{forward_key}"
    if not url and not forward_from:
        raise CiError("Send a link, or forward the post you mean.")
    canon = canonical(url) if url else ""
    if url and not canon:
        raise CiError("That link isn't a web address. Use a full https:// link.")
    host = host_of(canon) if canon else ""
    src = source_class(host)
    if src["class"] == "shortener":
        raise CiError("Shortened links can't be checked. Send the address it leads to.")
    mine = db.scalar(select(CiSubmission).where(CiSubmission.member_id == member.id, CiSubmission.created_at >= now - rules.DUP_WINDOW,
                                                (CiSubmission.canonical_url == canon) if canon else (CiSubmission.forward_key == forward_key))
                     .limit(1)) if (canon or forward_key) else None
    if mine is not None:
        raise CiError(f"You already sent this one: {ref(mine)}.")
    note = URL_RX.sub("", text).strip()[: rules.NOTE_MAX] if text else ""
    k = kind if kind in rules.KINDS else guess_kind(host, note)
    tick = tickers_in(note)
    s = CiSubmission(member_id=member.id, via=via, kind=k, title=headline(title or note), note=note,
                     url=url, canonical_url=canon, host=host, forward_from=(forward_from or None) and forward_from[:256],
                     forward_key=forward_key, tg_chat_id=tg_chat_id, tickers=tick, disclosure=disclosure if disclosure in ("none", "holds") else None,
                     credit=bool(credit), status="checking", checks={}, dup={})
    s.created_at = now
    db.add(s)
    db.flush()
    log(db, s, f"member:{member.id}", "submitted", via=via)
    run_checks(db, s, member, now, reader)
    db.commit()
    return s


def headline(text: str, limit: int = 140) -> str:
    """A tip's one-line title: its first line or sentence, ended at a word (never "exchange reserv")."""
    line = (text or "").strip().split("\n")[0].strip()
    first = re.split(r"(?<=[.!?])\s+", line, maxsplit=1)[0]
    line = first if 20 <= len(first) <= limit else line
    if len(line) <= limit:
        return line
    cut = line[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "…"


def set_disclosure(db: Session, s: CiSubmission, value: str, credit: bool | None = None, now: datetime | None = None) -> None:
    if value not in ("none", "holds"):
        raise CiError("Choose whether you hold a position.")
    s.disclosure = value
    if credit is not None:
        s.credit = bool(credit)
    if s.status == "checking":
        s.status = "review"
    log(db, s, f"member:{s.member_id}", "disclosed", value=value, credit=s.credit)
    db.commit()


def withdraw(db: Session, s: CiSubmission) -> None:
    if s.status not in rules.OPEN:
        raise CiError("Only a tip still waiting for the desk can be withdrawn.")
    s.status = "withdrawn"
    log(db, s, f"member:{s.member_id}", "withdrawn")
    db.commit()


# --- automatic checks -----------------------------------------------------------------------------------------------------------------


def run_checks(db: Session, s: CiSubmission, member: Member, now: datetime, reader: Reader | None = None) -> None:
    src = source_class(s.host)
    checks: dict[str, Any] = {"source": src, "flags": safety_flags(member, f"{s.title} {s.note}", s.tickers, src["class"], now)}
    checks["corroboration"] = corroborate(db, s, now)
    if s.kind == "anomaly":
        checks["figures"] = check_figures(s, reader)
    verify(s, checks)
    s.checks = checks
    s.dup = dedupe(db, s, now)
    if s.dup.get("kind") == "tip" and s.dup.get("exact") and s.status == "checking":
        s.status = "duplicate"
        log(db, s, "system", "duplicate", of=s.dup.get("ref"))
    elif s.status == "checking" and s.disclosure is not None:
        s.status = "review"


def verify(s: CiSubmission, checks: dict[str, Any]) -> None:
    """Independent verification: an official primary source, a known outlet's own report, at least one reliable outlet or official
    source in the Terminal reporting the same event (other than the member's link), or live market data that matches the figures.
    Research and discoveries are verified when an editor has checked the data (decide(..., data_checked=True))."""
    src, cor, fig = checks["source"], checks.get("corroboration") or {}, checks.get("figures") or {}
    by = None
    if src["class"] == "official":
        by = f"official source ({src['outlet']})"
    elif src["class"] == "outlet":
        by = f"reported by {src['outlet']}"
    elif cor.get("independent"):
        by = "reported by " + ", ".join(cor["independent"][:3])
    elif fig.get("matched") and not fig.get("contradicted"):
        by = "live market data"
    disputed = bool(cor.get("disputed"))
    checks["claim"] = ("disputed" if disputed else "confirmed" if by and (src["class"] in ("official", "outlet") or len(cor.get("independent") or []) >= 1 or fig.get("matched"))
                       else "single source" if src["class"] in ("outlet", "official") else "unverified")
    if s.kind in ("research", "discovery") and not s.verified:
        return  # an editor checks the data
    if not s.verified and by and not disputed:
        s.verified, s.verified_by = True, by[:64]


def _sig(x: Any) -> dict[str, Any]:
    return {"source": x.source, "outlet": x.outlet, "title": x.title, "url": x.url, "reliable": x.reliable, "extra": x.extra or {}}


def corroborate(db: Session, s: CiSubmission, now: datetime) -> dict[str, Any]:
    """What the Terminal's own sources (Trending's signal store: outlets, official feeds) say about the same event in the last 72 h."""
    from app.models.trending import TtSignal
    from app.newsdesk import newsworthy
    from app.terminal import intel

    text = s.title or s.note[:200]
    if not text and not s.canonical_url:
        return {"independent": [], "matches": 0}
    ev = newsworthy.event_of(text)
    own = source_class(s.host).get("outlet", "")
    hits = []
    for x in db.scalars(select(TtSignal).where(TtSignal.last_seen >= now - rules.STORY_WINDOW).order_by(TtSignal.last_seen.desc()).limit(1500)):
        if s.canonical_url and canonical(x.url or "") == s.canonical_url:
            hits.append(x)
            continue
        if text and x.title and newsworthy.same_event(ev, newsworthy.event_of(x.title)):
            hits.append(x)
    # independent: another reliable item about the same event. The member's own page (the same address) can't confirm itself, nor can
    # another item from the same publisher.
    rel = [_sig(x) for x in hits if x.reliable and not (s.canonical_url and canonical(x.url or "") == s.canonical_url)]
    own_key = intel.outlet_of({"outlet": own}) if own else ""
    outlets = sorted({intel.outlet_of(x) for x in rel if not intel.official(x)} - {"", own.lower(), own_key})
    officials = sorted({str(x["outlet"]) for x in rel if intel.official(x)})
    first = min((_aware(x.published_at) or _aware(x.first_seen) for x in hits if x.reliable), default=None)
    return {"matches": len(hits), "independent": [*officials, *outlets][:8], "disputed": any(intel.DISPUTE.search(str(x.title or "")) for x in hits),
            "first_reliable_at": first.isoformat() if first else None}


# Hyperliquid's public market data (the desk's own source for crypto): funding per hour and the move since the previous day
_hl_cache: tuple[float, dict[str, dict[str, float]]] | None = None


def hyperliquid_reader(ticker: str) -> dict[str, float] | None:
    global _hl_cache
    if _hl_cache is None or time.monotonic() - _hl_cache[0] > 60:
        req = urllib.request.Request("https://api.hyperliquid.xyz/info", data=json.dumps({"type": "metaAndAssetCtxs"}).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "PriceChat/1.0"})
        meta, ctxs = json.load(urllib.request.urlopen(req, timeout=10))
        out = {}
        for a, c in zip(meta.get("universe") or [], ctxs):
            try:
                px, prev = float(c["markPx"]), float(c["prevDayPx"])
                out[str(a["name"]).upper()] = {"funding_pct_h": float(c["funding"]) * 100, "change_pct": (px / prev - 1) * 100 if prev else 0.0, "price": px}
            except (KeyError, TypeError, ValueError):
                continue
        _hl_cache = (time.monotonic(), out)
    return _hl_cache[1].get(ticker.upper())


PCT = re.compile(r"(?P<n>[-−+]?\d+(?:\.\d+)?)\s?%(?P<h>\s?/\s?h(?:our|r)?)?", re.I)


def check_figures(s: CiSubmission, reader: Reader | None = None) -> dict[str, Any]:
    """An anomaly's figures against live data: funding (a %/h figure, or one next to "funding") and the day's move."""
    text = s.note if s.title and s.title in s.note else f"{s.title} {s.note}"
    tick = (s.tickers or [None])[0] or _ticker_from_url(s.canonical_url)
    figs = [(m.group("n").replace("−", "-"), bool(m.group("h")) or _about_funding(text[max(0, m.start() - 30): m.start()])) for m in PCT.finditer(text)]
    if not tick:
        return {"ticker": None, "figures": [], "matched": 0, "contradicted": 0, "note": "No asset named: add a $TICKER."}
    try:
        live = (reader or hyperliquid_reader)(tick)
    except Exception:  # noqa: BLE001 - market data unreachable: the next run tries again
        return {"ticker": tick, "figures": [], "matched": 0, "contradicted": 0, "note": "Live data unreachable; checked again later."}
    if not live:
        return {"ticker": tick, "figures": [], "matched": 0, "contradicted": 0, "note": f"{tick} isn't listed on Hyperliquid."}
    out, ok, bad = [], 0, 0
    for n, funding in figs[:4]:
        v = float(n)
        measured = live["funding_pct_h"] if funding else live["change_pct"]
        tol = max(abs(measured) * 0.25, 0.02 if funding else 1.0)
        good = abs(v - measured) <= tol
        ok, bad = ok + good, bad + (not good)
        out.append({"said": f"{v:+g}%{'/h' if funding else ''}", "measured": f"{measured:+.3f}%/h" if funding else f"{measured:+.1f}%",
                    "what": "funding" if funding else "24h move", "ok": good})
    return {"ticker": tick, "figures": out, "matched": ok, "contradicted": bad, "live": {k: round(v, 4) for k, v in live.items()}}


_MOVE = re.compile(r"\b(up|down|rose|fell|price|rall\w*|drop\w*|gain\w*|jump\w*|slid\w*|surg\w*|plung\w*)\b")


def _about_funding(before: str) -> bool:
    """A % figure is a funding rate when "funding" is the last thing named before it (not "up 9%" later in the sentence)."""
    low = before.lower()
    i = low.rfind("funding")
    return i >= 0 and not _MOVE.search(low[i:])


def _ticker_from_url(url: str) -> str | None:
    m = re.search(r"/trade/([A-Za-z0-9]{2,10})(?:\b|$)", url or "")
    return m.group(1).upper() if m else None


# --- duplicates -------------------------------------------------------------------------------------------------------------------------


def _norm(t: str) -> str:
    return " ".join(re.findall(r"[a-z0-9$%.]+", (t or "").lower()))


def dedupe(db: Session, s: CiSubmission, now: datetime) -> dict[str, Any]:
    """Exact: the same page (canonical address) or the same forwarded post, sent earlier by someone else: the earliest keeps the credit.
    Near: very similar words to a recent tip. Story: the Terminal already has the event (and whether it had it before this tip)."""
    from app.models.trending import TtTopic
    from app.newsdesk import checks, memory, newsworthy

    q = select(CiSubmission).where(CiSubmission.id != s.id, CiSubmission.created_at >= now - rules.DUP_WINDOW,
                                   CiSubmission.status != "withdrawn").order_by(CiSubmission.created_at, CiSubmission.id)
    for o in db.scalars(q):
        same = (s.canonical_url and o.canonical_url == s.canonical_url) or (s.forward_key and o.forward_key == s.forward_key)
        if same and (_aware(o.created_at), o.id) < (_aware(s.created_at), s.id):  # type: ignore[operator]
            return {"kind": "tip", "exact": True, "ref": ref(o), "id": o.id, "first_member_id": o.member_id}
    words = _norm(f"{s.title} {s.note}")
    if words:
        for o in db.scalars(q.where(CiSubmission.created_at >= now - rules.SIMILAR_WINDOW, CiSubmission.created_at <= (_aware(s.created_at) or now))):
            if o.id < s.id and checks.similarity(words, _norm(f"{o.title} {o.note}")) >= rules.SIMILAR_AT:
                return {"kind": "tip", "exact": False, "ref": ref(o), "id": o.id, "first_member_id": o.member_id}
    text = s.title or s.note[:200]
    if text:
        ev = newsworthy.event_of(text)
        for t in db.scalars(select(TtTopic).where(TtTopic.last_seen >= now - rules.STORY_WINDOW).order_by(TtTopic.last_seen.desc()).limit(400)):
            if newsworthy.same_event(ev, newsworthy.event_of(t.title)):
                rel = [x for x in (t.sources or []) if x.get("reliable")]
                first = min((str(x.get("at") or "") for x in rel if x.get("at")), default="")
                before = bool(first) and first < (_aware(s.created_at) or now).isoformat()
                return {"kind": "covered" if before else "story", "story_key": t.key, "story": t.title, "story_id": t.id,
                        "first_reliable_at": first or None}
        line = memory.find(db, text, "", now)
        if line is not None:
            return {"kind": "covered", "storyline": line.id, "story": (line.told or {}).get("titles", [text])[0] if line.told else text}
    return {"kind": "new"}


# --- the editor's decisions -------------------------------------------------------------------------------------------------------------


def _deliver(db: Session, s: CiSubmission, title: str, body: str) -> None:
    """Passport notification now; the Telegram message goes out with the next run of the job (pending: details.tg)."""
    from app.members.notify import notify

    notify(db, s.member_id, "intel", title, body, {"submission": s.id})
    log(db, s, "system", "notice", body, title=title, tg="pending")  # sent to the tip's chat or the member's linked Telegram


def decide(db: Session, s: CiSubmission, action: str, editor: str, *, reason: str | None = None, note: str | None = None,
           story_key: str | None = None, new_info: bool = False, data_checked: bool = False, fact_check: bool = False,
           now: datetime | None = None) -> CiSubmission:
    now = now or utcnow()
    note = (note or "").strip()[: rules.EDITOR_NOTE_MAX] or None
    if action == "reopen":
        if s.status not in ("duplicate", "rejected", "accepted", "merged", "needs_info"):
            raise CiError("Only a decided tip can be reopened.")
        s.status, s.reason, s.decided_by, s.decided_at = "review", None, None, None
        log(db, s, editor, "reopened", note or "")
        db.commit()
        return s
    if s.status not in rules.OPEN and not (s.status == "duplicate" and action in ("merge", "reject")):
        raise CiError("This tip has already been decided. Reopen it first.")
    if s.disclosure is None and action in ("accept", "merge"):
        raise CiError("The member hasn't answered the conflict-of-interest question yet.")
    if action == "accept":
        if data_checked and not s.verified:
            s.verified, s.verified_by = True, "an editor checked the data"
        s.status = "accepted"
        if story_key:
            s.story_key = story_key
        s.checks = {**(s.checks or {}), "fact_check": bool(fact_check)}
        body = ("The desk accepted it." + (" It counts towards your community score." if s.verified else
                " It counts towards your community score once another source confirms it.") + " Nothing is published automatically.")
        _deliver(db, s, f"{ref(s)} accepted", body + (f"\n\nEditor: {note}" if note else ""))
    elif action == "merge":
        key = story_key or (s.dup or {}).get("story_key")
        if not key and not (s.dup or {}).get("id"):
            raise CiError("Pick the story to merge it into.")
        s.status, s.story_key, s.new_info = "merged", key, bool(new_info)
        _deliver(db, s, f"{ref(s)} added to a story", "The desk added it to a story it already had"
                 + (" and it added something new: it counts towards your community score." if new_info else ". You're credited as \"also spotted\".")
                 + (f"\n\nEditor: {note}" if note else ""))
    elif action == "ask":
        if not note:
            raise CiError("Write the question for the member.")
        s.status = "needs_info"
        _deliver(db, s, f"{ref(s)}: the desk has a question", note)
    elif action == "reject":
        if reason not in rules.REASONS:
            raise CiError("Choose a reason.")
        s.status, s.reason = "rejected", reason
        spam = reason in rules.SPAM_REASONS
        _deliver(db, s, f"{ref(s)} not accepted: {rules.REASONS[reason]}",
                 (f"{note}\n\n" if note else "") + ("This counts against your community score (−50). " if spam else "No score is lost for this. ")
                 + "You can appeal within 14 days from your Passport.")
    else:
        raise CiError("Unknown action.")
    s.decided_by, s.decided_at, s.editor_note = editor[:64], now, note
    log(db, s, editor, action, note or "", reason=reason, story_key=s.story_key, new_info=new_info, data_checked=data_checked)
    db.commit()
    from app.passport import service as passport

    passport.invalidate_rarity()
    return s


def answer(db: Session, s: CiSubmission, text: str, url: str = "") -> None:
    """The member's reply to the desk's question: back in the queue with the new information."""
    if s.status != "needs_info":
        raise CiError("The desk isn't waiting for an answer on this tip.")
    text = text.strip()[: rules.NOTE_MAX]
    if not text and not url:
        raise CiError("Add the information the desk asked for.")
    s.note = (s.note + "\n\n[Reply] " + text).strip()[: rules.NOTE_MAX * 2]
    if url and not s.url:
        s.url, s.canonical_url, s.host = url[:500], canonical(url), host_of(canonical(url))
    s.status = "review"
    log(db, s, f"member:{s.member_id}", "answered", text)
    member = db.get(Member, s.member_id)
    if member is not None:
        run_checks(db, s, member, utcnow())
    db.commit()


def appeal(db: Session, s: CiSubmission, text: str, now: datetime | None = None) -> None:
    now = now or utcnow()
    text = (text or "").strip()
    if s.status not in ("rejected", "duplicate"):
        raise CiError("Only a rejected tip or a duplicate verdict can be appealed.")
    if s.appeal_status is not None:
        raise CiError("This tip has already been appealed.")
    since = _aware(s.decided_at) or _aware(s.created_at) or now
    if now - since > rules.APPEAL_WINDOW:  # type: ignore[operator]
        raise CiError("Appeals are open for 14 days after the decision.")
    if not 10 <= len(text) <= rules.APPEAL_MAX:
        raise CiError(f"Explain in 10 to {rules.APPEAL_MAX} characters why the decision should change.")
    s.appeal_status, s.appeal_text, s.appeal_at = "open", text, now
    log(db, s, f"member:{s.member_id}", "appealed", text)
    db.commit()


def decide_appeal(db: Session, s: CiSubmission, uphold: bool, editor: str, note: str = "", now: datetime | None = None) -> None:
    """Upheld: the rejection (and any spam penalty) is cancelled and the tip goes back to the queue. Denied: final."""
    now = now or utcnow()
    if s.appeal_status != "open":
        raise CiError("There's no open appeal on this tip.")
    s.appeal_status, s.appeal_decided_by, s.appeal_decided_at = ("upheld" if uphold else "denied"), editor[:64], now
    if uphold:
        s.status, s.reason = "review", None
    _deliver(db, s, f"Appeal on {ref(s)} {'upheld' if uphold else 'not upheld'}",
             ("The decision is cancelled and the tip is back with the desk. Any penalty is removed." if uphold else "The decision stands.")
             + (f"\n\nEditor: {note}" if note else ""))
    log(db, s, editor, "appeal_upheld" if uphold else "appeal_denied", note)
    db.commit()


# --- reputation: points, track records, leaderboards ------------------------------------------------------------------------------------


def _scored_rows(rows: list[CiSubmission]) -> dict[int, dict[str, Any]]:
    """Per member: points and counts, with the anti-farming rules: only accepted (or merged with new information) tips that were
    independently verified score; at most ACCEPTED_PER_DAY a day; one scoring tip per member per story (or page); published +40 only when
    verified; an upheld spam rejection −50 unless overturned on appeal. Computed every time: nothing is stored, nothing to farm."""
    out: dict[int, dict[str, Any]] = defaultdict(lambda: {"points": 0, "accepted": 0, "published": 0, "first_spotted": 0, "penalties": 0,
                                                          "research": 0, "anomalies": 0, "official": 0, "fact_checks": 0})
    per_day: dict[tuple[int, str], int] = defaultdict(int)
    seen: set[tuple[int, str]] = set()
    for s in sorted(rows, key=lambda r: (_aware(r.decided_at) or _aware(r.created_at), r.id)):  # type: ignore[arg-type,return-value]
        o = out[s.member_id]
        if s.status == "rejected" and s.reason in rules.SPAM_REASONS and s.appeal_status != "upheld":
            o["points"] += rules.POINTS["spam"]
            o["penalties"] += 1
            continue
        if not (s.status == "accepted" or (s.status == "merged" and s.new_info)) or not s.verified:
            continue
        if (s.dup or {}).get("kind") == "tip" and (s.dup or {}).get("first_member_id") == s.member_id:
            continue  # a near-copy of the member's own earlier tip never scores twice
        story = s.story_key or s.canonical_url or f"id:{s.id}"
        if (s.member_id, story) in seen:
            continue
        day = (_aware(s.decided_at) or utcnow()).date().isoformat()
        if per_day[(s.member_id, day)] >= rules.ACCEPTED_PER_DAY:
            continue
        seen.add((s.member_id, story))
        per_day[(s.member_id, day)] += 1
        o["accepted"] += 1
        o["points"] += rules.POINTS["research"] if s.kind == "research" else rules.POINTS["accepted"]
        o["research"] += s.kind == "research"
        o["anomalies"] += s.kind == "anomaly" and (s.verified_by or "") == "live market data"
        o["official"] += ((s.checks or {}).get("source") or {}).get("class") == "official"
        o["fact_checks"] += bool((s.checks or {}).get("fact_check"))
        if s.published_at is not None:
            o["published"] += 1
            o["points"] += rules.POINTS["published"]
            o["first_spotted"] += bool(s.first_spotted)
    for v in out.values():
        v["points"] = max(v["points"], -10_000)
    return out


def _decided(db: Session, since: datetime | None = None, until: datetime | None = None, member_id: int | None = None) -> list[CiSubmission]:
    q = select(CiSubmission).where(CiSubmission.decided_at.is_not(None))
    if since is not None:
        q = q.where(CiSubmission.decided_at >= since)
    if until is not None:
        q = q.where(CiSubmission.decided_at < until)
    if member_id is not None:
        q = q.where(CiSubmission.member_id == member_id)
    return list(db.scalars(q))


def score_parts(db: Session, member: Member, since: datetime) -> list[dict[str, Any]]:
    """The community score's tip lines (app/passport/service.community_score). Empty while the feature is off."""
    if mode() == "off":
        return []
    sc = _scored_rows(_decided(db, since=since, member_id=member.id)).get(member.id)
    if not sc:
        sc = {"accepted": 0, "published": 0, "penalties": 0, "points": 0}
    acc_pts = sc["points"] - sc["published"] * rules.POINTS["published"] - sc["penalties"] * rules.POINTS["spam"]
    return [{"key": "tips", "label": "Tips accepted by the desk", "count": sc["accepted"], "points": acc_pts},
            {"key": "tips_published", "label": "Stories published from your tips", "count": sc["published"],
             "points": sc["published"] * rules.POINTS["published"]},
            {"key": "tips_penalty", "label": "Tips rejected as spam", "count": sc["penalties"], "points": sc["penalties"] * rules.POINTS["spam"]}]


def metric(db: Session, member: Member, name: str) -> int:
    """Researcher badge criteria (app/members/badges._meets, {"ci": name, "min": n}); all time."""
    if mode() == "off":
        return 0
    return int((_scored_rows(_decided(db, member_id=member.id)).get(member.id) or {}).get(name, 0))


def record(db: Session, member: Member) -> dict[str, Any]:
    rows = list(db.scalars(select(CiSubmission).where(CiSubmission.member_id == member.id)))
    sc = _scored_rows([r for r in rows if r.decided_at is not None]).get(member.id) or {}
    decided = [r for r in rows if r.status in ("accepted", "merged", "rejected", "duplicate")]
    acc = [r for r in rows if r.status in ("accepted", "merged")]
    hours = sorted(((_aware(r.decided_at) - _aware(r.created_at)).total_seconds() / 3600) for r in rows  # type: ignore[operator]
                   if r.decided_at is not None and r.created_at is not None)
    return {"sent": len(rows), "accepted": len(acc), "published": sum(1 for r in rows if r.published_at), "first_spotted": sum(1 for r in rows if r.first_spotted),
            "duplicates": sum(1 for r in rows if r.status == "duplicate"), "rejected": sum(1 for r in rows if r.status == "rejected"),
            "acceptance": round(len(acc) / len(decided), 3) if len(decided) >= 5 else None, "acceptance_after": 5,
            "median_hours": round(hours[len(hours) // 2], 1) if hours else None, "points": sc.get("points", 0),
            "by_kind": {k: sum(1 for r in acc if r.kind == k) for k in rules.KINDS}}


def period_bounds(period: str, now: datetime) -> tuple[datetime | None, datetime | None]:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        return day - timedelta(days=day.weekday()), None
    if period == "month":
        return day.replace(day=1), None
    return None, None


def leaderboard(db: Session, period: str, now: datetime | None = None, limit: int = 50, since: datetime | None = None,
                until: datetime | None = None) -> dict[str, Any]:
    from app.members import rewards

    now = now or utcnow()
    if since is None and until is None:
        since, until = period_bounds(period, now)
    scored = _scored_rows(_decided(db, since, until))
    members = {m.id: m for m in db.scalars(select(Member).where(Member.id.in_(list(scored)), Member.status == "active",
                                                                  Member.leaderboard_opt_out.is_(False)))} if scored else {}
    rows = sorted(((mid, v) for mid, v in scored.items() if mid in members and v["points"] > 0),
                  key=lambda x: (-x[1]["points"], -x[1]["published"], -x[1]["accepted"], x[0]))
    out = []
    for i, (mid, v) in enumerate(rows[:limit], start=1):
        m = members[mid]
        out.append({"position": i, "member_id": mid, "username": m.username, "name": rewards.custom_display_name(m) or m.username,
                    "points": v["points"], "accepted": v["accepted"], "published": v["published"], "first_spotted": v["first_spotted"]})
    return {"period": period, "since": since.isoformat() if since else None, "rows": out}


# --- Researcher of the Month (numbered) -------------------------------------------------------------------------------------------------


def award_month(db: Session, now: datetime) -> int | None:
    """On the 1st (UTC), the previous month's top researcher (at least MONTH_MIN_POINTS) gets the numbered badge. Once per month (the
    number is the month's place in the series). A member holds the badge once; a second win is recorded in the reason only."""
    from app.models.members import Badge, MemberBadge
    from app.members.notify import notify

    if now.day != 1:
        return None
    start = (now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)).replace(day=1)
    label = f"{start:%B %Y}"
    badge = db.scalar(select(Badge).where(Badge.key == "ci_researcher_month"))
    if badge is None or db.scalar(select(MemberBadge.id).where(MemberBadge.badge_id == badge.id, MemberBadge.reason.like(f"%{label}%"))):
        return None
    board = leaderboard(db, "month", since=start, until=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), limit=1)
    if not board["rows"] or board["rows"][0]["points"] < rules.MONTH_MIN_POINTS:
        return None
    win = board["rows"][0]
    member = db.get(Member, win["member_id"])
    from app.passport import service as passport

    if member is None or not passport.may_earn_new_badges(db, member):
        return None
    have = db.scalar(select(MemberBadge).where(MemberBadge.badge_id == badge.id, MemberBadge.member_id == member.id))
    if have is not None:
        have.reason = f"{have.reason} · {label}"[:256]
    else:
        top = int(db.scalar(select(func.max(MemberBadge.serial)).where(MemberBadge.badge_id == badge.id)) or 0)
        db.add(MemberBadge(member_id=member.id, badge_id=badge.id, awarded_by="system", serial=top + 1, reason=f"Researcher of the Month · {label}"))
        notify(db, member.id, "badge", f"Badge earned: Researcher of the Month ({label})", badge.description, {"badge": badge.key})
    db.commit()
    return member.id


# --- the Terminal: joining stories, publication and first spotted ----------------------------------------------------------------------


def follow_stories(db: Session, now: datetime, reader: Reader | None = None) -> dict[str, int]:
    """Re-check open and accepted tips from the last 7 days: corroboration that appeared since (independent verification), the Terminal
    story about the same event, and whether that story was published (and whether the tip came before every reliable source)."""
    from app.models.trending import TtTopic

    stats = {"rechecked": 0, "verified": 0, "linked": 0, "published": 0}
    q = select(CiSubmission).where(CiSubmission.created_at >= now - timedelta(days=7),
                                   CiSubmission.status.in_(("checking", "review", "needs_info", "accepted", "merged")))
    for s in list(db.scalars(q)):
        member = db.get(Member, s.member_id)
        if member is None:
            continue
        was = s.verified
        if not s.verified or not s.story_key:
            cor = corroborate(db, s, now)
            checks = {**(s.checks or {}), "corroboration": cor}
            if s.kind == "anomaly" and not (checks.get("figures") or {}).get("figures"):
                checks["figures"] = check_figures(s, reader)
            verify(s, checks)
            s.checks = checks
            stats["rechecked"] += 1
            if s.verified and not was:
                stats["verified"] += 1
                log(db, s, "system", "verified", s.verified_by or "")
            if not s.story_key:
                d = dedupe(db, s, now)
                if d.get("story_key"):
                    s.story_key = d["story_key"]
                    s.dup = {**(s.dup or {}), **{k: v for k, v in d.items() if k != "kind"}}
                    stats["linked"] += 1
        if s.status in ("accepted", "merged") and s.story_key and s.published_at is None:
            t = db.scalar(select(TtTopic).where(TtTopic.key == s.story_key))
            if t is not None and (t.posted_at or (t.parts or {}).get("telegram")):
                s.published_at = _aware(t.posted_at) or now
                rel = [str(x.get("at") or "") for x in (t.sources or []) if x.get("reliable") and x.get("at")]
                s.first_spotted = bool(s.verified) and bool(rel) and (_aware(s.created_at) or now).isoformat() < min(rel)
                log(db, s, "system", "published", first_spotted=s.first_spotted)
                _deliver(db, s, f"A story built on {ref(s)} was published", "Thanks for the tip." + (" You spotted it before every outlet in the Terminal." if s.first_spotted else ""))
                stats["published"] += 1
    db.commit()
    return stats


def credit_line(db: Session, story_key: str) -> str | None:
    """"Spotted by …" for a published story: the earliest accepted, verified tip whose member agreed to be credited."""
    if mode() == "off":
        return None
    from app.members import rewards

    s = db.scalar(select(CiSubmission).where(CiSubmission.story_key == story_key, CiSubmission.status.in_(("accepted", "merged")),
                                             CiSubmission.verified.is_(True), CiSubmission.credit.is_(True)).order_by(CiSubmission.created_at).limit(1))
    m = db.get(Member, s.member_id) if s is not None else None
    if m is None or m.status != "active":
        return None
    return f"Spotted by {rewards.custom_display_name(m) or m.username} (Price Chat community)"


# --- outputs --------------------------------------------------------------------------------------------------------------------------


STAGE = {"checking": 1, "review": 2, "needs_info": 2, "accepted": 3, "merged": 3, "duplicate": 3, "rejected": 3, "withdrawn": 3}


def out(s: CiSubmission, editor: bool = False, member: Member | None = None) -> dict[str, Any]:
    o = {"id": s.id, "ref": ref(s), "kind": s.kind, "kind_label": rules.KIND_LABEL[s.kind], "title": s.title, "note": s.note, "url": s.url,
         "host": s.host, "via": s.via, "status": s.status, "stage": 4 if s.published_at else STAGE.get(s.status, 1), "verified": s.verified,
         "verified_by": s.verified_by, "source": (s.checks or {}).get("source"), "claim": (s.checks or {}).get("claim"),
         "dup": s.dup or {}, "story": (s.dup or {}).get("story"), "reason": s.reason, "reason_label": rules.REASONS.get(s.reason or ""),
         "editor_note": s.editor_note, "published_at": _aware(s.published_at).isoformat() if s.published_at else None,  # type: ignore[union-attr]
         "first_spotted": s.first_spotted, "credit": s.credit, "disclosure": s.disclosure,
         "created_at": _aware(s.created_at).isoformat() if s.created_at else None,  # type: ignore[union-attr]
         "decided_at": _aware(s.decided_at).isoformat() if s.decided_at else None,  # type: ignore[union-attr]
         "appeal": {"status": s.appeal_status, "text": s.appeal_text} if s.appeal_status else None,
         "can_appeal": s.status in ("rejected", "duplicate") and s.appeal_status is None, "can_withdraw": s.status in rules.OPEN,
         "can_answer": s.status == "needs_info"}
    if editor:
        o.update({"checks": s.checks or {}, "tickers": s.tickers, "forward_from": s.forward_from, "member_id": s.member_id,
                  "decided_by": s.decided_by, "story_key": s.story_key, "new_info": s.new_info})
        if member is not None:
            from app.members import rewards

            o["member"] = {"username": member.username, "name": rewards.custom_display_name(member) or member.username,
                           "joined_at": _aware(member.created_at).isoformat() if member.created_at else None}  # type: ignore[union-attr]
    return o
