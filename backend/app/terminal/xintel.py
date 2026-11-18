"""X Intelligence (owner, 2026-11-18): narratives that start on X, from a watchlist of accounts with credibility tiers, through the official
X API only. It is Trending's "x_accounts" source grown up, not a second pipeline: posts become Trending signals (source "x_accounts"),
cluster with the news into topics, and get the Terminal's viral, evidence, rumour and fast-lane treatment.

ACCESS (audited 2026-11-18, docs.x.com/x-api/getting-started/pricing): the account is on X's pay-per-use API. Posts: Read $0.005 per post
returned, User: Read $0.010 per user, List: Read $0.005, owned reads $0.001, Trends $0.010 per request; every resource is billed once per
UTC day (re-reading the same post the same day is free); 3 million post reads per monthly cycle. Filtered stream is available on
pay-per-use (1,000 rules, 1 connection) with no separate per-post price published. Reading other accounts' posts is therefore PAID: this
source is OFF until the owner turns it on in the Terminal with an explicit confirmation, a daily cap of its own, and the account-wide
X budget guard (X_DAILY_BUDGET_USD / X_MONTHLY_BUDGET_USD) still in front of every request.

CREDIBILITY (never follower counts):
- A: wires, official agencies and the affected company's own account. A tier-A post may back a factual claim (a reliable signal).
- B: established newsrooms, named investigators, specialist reporters. Partial evidence: B accounts alone never verify a story.
- C: aggregators, OSINT accounts, alert bots, eyewitnesses. Momentum and leads only, never evidence.
A story seen only on X is a rumour (app/terminal/intel.evidence) and never publishes automatically.

INDEPENDENCE: reposts are not read (-is:retweet); quotes and near-identical texts from different accounts are copies, not independent
reports; three or more accounts posting near-identical text within ten minutes is flagged as coordinated amplification.
"""

import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.newsdesk import checks

GROUPS = {
    "breaking": "Breaking news and global events",
    "markets": "Crypto and financial markets",
    "onchain": "On-chain investigators and exchange alerts",
    "geopolitics": "Geopolitics, military conflicts and aviation",
    "outages": "Cyberattacks, banking and infrastructure outages",
    "journalists": "Independent journalists and eyewitness reporting",
}
TIERS = {"A": "Official or wire: may back a claim", "B": "Established reporter or investigator: partial evidence",
         "C": "Aggregator, OSINT or eyewitness: leads only"}
# A starting watchlist the owner edits in the Terminal. Handles are the accounts' public X usernames.
DEFAULT_WATCHLIST: list[dict[str, str]] = [
    *({"handle": h, "group": "breaking", "tier": "A"} for h in ("Reuters", "AP", "AFP", "BBCBreaking", "business", "WSJ", "FT")),
    *({"handle": h, "group": "breaking", "tier": "C"} for h in ("BNONews", "spectatorindex")),
    *({"handle": h, "group": "markets", "tier": "A"} for h in ("markets", "ReutersBiz", "federalreserve", "SECGov")),
    *({"handle": h, "group": "markets", "tier": "C"} for h in ("DeItaone", "FirstSquawk", "unusual_whales", "KobeissiLetter")),
    *({"handle": h, "group": "onchain", "tier": "B"} for h in ("zachxbt", "PeckShieldAlert", "CertiKAlert", "CoinDesk", "TheBlock__")),
    *({"handle": h, "group": "onchain", "tier": "C"} for h in ("lookonchain", "whale_alert", "WatcherGuru", "tier10k")),
    *({"handle": h, "group": "geopolitics", "tier": "A"} for h in ("CENTCOM", "DeptofDefense", "StateDept", "NATO")),
    *({"handle": h, "group": "geopolitics", "tier": "B"} for h in ("flightradar24",)),
    *({"handle": h, "group": "geopolitics", "tier": "C"} for h in ("sentdefender", "Faytuks", "IntelCrab", "Osinttechnical")),
    *({"handle": h, "group": "outages", "tier": "A"} for h in ("CISAgov", "FAANews", "Cloudflare", "githubstatus")),
    *({"handle": h, "group": "outages", "tier": "B"} for h in ("BleepinComputer", "TheRecord_Media")),
    *({"handle": h, "group": "outages", "tier": "C"} for h in ("downdetector",)),
    *({"handle": h, "group": "journalists", "tier": "B"} for h in ("bellingcat", "EliotHiggins")),
]
HANDLE = re.compile(r"^@?([A-Za-z0-9_]{1,15})$")
QUERY_MAX = 480  # X recent search accepts 512 characters on this access level; a margin for the operators
DEFAULTS: dict[str, Any] = {"on": False, "accounts": DEFAULT_WATCHLIST, "max_posts": 50, "daily_usd": 1.0, "interval": 5}
POST_USD, USER_USD = 0.005, 0.010


def clean_watchlist(rows: Any) -> list[dict[str, str]]:
    """Valid, unique entries only: a handle, a known group and tier, an optional note."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for r in rows or []:
        if isinstance(r, str):
            r = {"handle": r, "group": "breaking", "tier": "C"}
        if not isinstance(r, dict):
            continue
        m = HANDLE.match(str(r.get("handle") or "").strip())
        if not m or m.group(1).lower() in seen:
            continue
        group = str(r.get("group") or "breaking")
        tier = str(r.get("tier") or "C").upper()
        if group not in GROUPS or tier not in TIERS:
            continue
        seen.add(m.group(1).lower())
        out.append({"handle": m.group(1), "group": group, "tier": tier, "note": str(r.get("note") or "")[:120]})
    return out


def queries(accounts: list[dict[str, str]]) -> list[tuple[str, list[str]]]:
    """(query, handles) batches that each fit X's query length: originals only (no reposts, no replies)."""
    out: list[tuple[str, list[str]]] = []
    batch: list[str] = []
    tail = ") -is:retweet -is:reply"

    def q(hs: list[str]) -> str:
        return "(" + " OR ".join(f"from:{h}" for h in hs) + tail

    for a in accounts:
        if batch and len(q(batch + [a["handle"]])) > QUERY_MAX:
            out.append((q(batch), batch))
            batch = []
        batch.append(a["handle"])
    if batch:
        out.append((q(batch), batch))
    return out


def tier_of(accounts: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {a["handle"].lower(): a for a in accounts}


def parse(body: dict[str, Any], accounts: list[dict[str, str]]) -> list[dict[str, Any]]:
    """X's answer -> Trending items. Only what X returned: text, author, time, public metrics as X reports them."""
    tiers = tier_of(accounts)
    users = {u["id"]: u for u in ((body.get("includes") or {}).get("users") or []) if u.get("id")}
    out = []
    for p in body.get("data") or []:
        u = users.get(p.get("author_id") or "") or {}
        handle = str(u.get("username") or "")
        meta = tiers.get(handle.lower()) or {"tier": "C", "group": "breaking"}
        m = p.get("public_metrics") or {}
        raw = str(p.get("text") or "")
        text = re.sub(r"\s+", " ", re.sub(r"https?://\S+", "", raw)).strip()[:300]
        if not text:
            continue
        try:
            at = datetime.fromisoformat(str(p.get("created_at")).replace("Z", "+00:00")) if p.get("created_at") else None
        except ValueError:
            at = None
        refs = [r for r in p.get("referenced_tweets") or [] if isinstance(r, dict)]
        quoted = next((str(r.get("id")) for r in refs if r.get("type") == "quoted"), None)
        out.append({"source": "x_accounts", "external_id": f"xpost:{p.get('id')}", "title": text, "summary": "",
                    "outlet": f"@{handle}" if handle else "X", "url": f"https://x.com/{handle or 'i'}/status/{p.get('id')}",
                    "reliable": meta["tier"] == "A", "engagement": engagement(m), "published_at": at,
                    "extra": {"handle": handle, "tier": meta["tier"], "group": meta["group"], "metrics": m, "post_id": str(p.get("id")),
                              "quoted": quoted, "conversation": str(p.get("conversation_id") or ""), "fp": checks.fingerprint(text),
                              "tags": tags(raw), "kind": "x"}})
    return out


def engagement(m: dict[str, Any]) -> float:
    """X's public counts as X reports them: likes + 2 x reposts + replies + quotes. Never an impression estimate."""
    return float(m.get("like_count") or 0) + 2 * float(m.get("retweet_count") or 0) + float(m.get("reply_count") or 0) + float(m.get("quote_count") or 0)


CASHTAG = re.compile(r"(?<![\w$])\$([A-Za-z]{2,6})\b(?![\d.,])")
HASHTAG = re.compile(r"(?<!\w)#(\w{2,40})")
STOP = set("""the a an and or of to in on at for from with by as is are was were be been it its this that these those breaking just new news
report reports reported says said after over into amid about more than will has have had not no yes our your their his her they we you i
via update updates live today now first""".split())


def tags(text: str) -> dict[str, list[str]]:
    return {"cashtags": sorted({m.upper() for m in CASHTAG.findall(text or "")}), "hashtags": sorted({m.lower() for m in HASHTAG.findall(text or "")})}


def words(text: str) -> set[str]:
    """Distinctive words: capitalised names and long words, no stopwords."""
    t = re.sub(r"https?://\S+|[@#$]\w+", " ", text or "")
    return {w.lower() for w in re.findall(r"\b[A-Za-z][A-Za-z'-]{3,}\b", t) if w.lower() not in STOP}


# --- independence -------------------------------------------------------------------------------------------------------------------


def independence(posts: list[dict[str, Any]]) -> dict[str, Any]:
    """posts: {handle, tier, at, fp, quoted, post_id}. Independent = distinct accounts with their own wording; copies = quotes of another
    watched post or near-identical text from another account; coordinated = 3+ accounts, near-identical text, within 10 minutes."""
    rows = sorted([p for p in posts if p.get("handle")], key=lambda p: str(p.get("at") or ""))
    ids = {str(p.get("post_id")) for p in rows}
    originals: list[dict[str, Any]] = []
    copies: list[dict[str, Any]] = []
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for p in rows:
        if p.get("quoted") and str(p["quoted"]) in ids:
            copies.append(p)
            continue
        twin = next((i for i, o in enumerate(originals) if o["handle"].lower() != p["handle"].lower() and checks.similarity(o.get("fp") or "", p.get("fp") or "") >= 0.8), None)
        if twin is not None:
            copies.append(p)
            groups[twin].append(p)
            continue
        originals.append(p)
    coordinated = False
    for i, dup in groups.items():
        cluster = [originals[i], *dup]
        times = [_dt(c.get("at")) for c in cluster if _dt(c.get("at"))]
        if len({c["handle"].lower() for c in cluster}) >= 3 and times and max(times) - min(times) <= timedelta(minutes=10):
            coordinated = True
    indep = sorted({p["handle"] for p in originals})
    return {"posts": len(rows), "independent": len(indep), "independent_accounts": indep, "copies": len(copies),
            "coordinated": coordinated, "tiers": dict(Counter(p.get("tier") or "C" for p in originals))}


def _dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def velocity(posts: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Engagement per minute, from X's own counts. 'between reads' when we hold two readings of a post, else 'since posting'
    (its count divided by its age). Nothing estimated."""
    between, since = 0.0, 0.0
    n_between = 0
    for p in posts:
        hist = [h for h in p.get("history") or [] if isinstance(h, (list, tuple)) and len(h) == 2]
        if len(hist) >= 2:
            (t0, e0), (t1, e1) = hist[-2], hist[-1]
            d0, d1 = _dt(t0), _dt(t1)
            if d0 and d1 and d1 > d0:
                between += max(0.0, float(e1) - float(e0)) / max(1.0, (d1 - d0).total_seconds() / 60)
                n_between += 1
                continue
        at = _dt(p.get("at"))
        if at:
            since += float(p.get("engagement") or 0) / max(1.0, (now - at).total_seconds() / 60)
    return {"per_min": round(between + since, 2), "between_reads": n_between, "basis": "X public counts (likes, reposts, replies, quotes)"}


# --- emerging keywords, cashtags, hashtags --------------------------------------------------------------------------------------------


def emerging(posts: list[dict[str, Any]], now: datetime, window: timedelta = timedelta(minutes=30), base: timedelta = timedelta(hours=6),
             min_accounts: int = 3) -> list[dict[str, Any]]:
    """Terms several distinct accounts started using in the last window at well above their baseline rate. Counted per account, so
    one account repeating itself is not a trend."""
    recent: dict[str, set[str]] = defaultdict(set)
    before: dict[str, set[str]] = defaultdict(set)
    for p in posts:
        at = _dt(p.get("at"))
        if not at or now - at > base + window:
            continue
        h = str(p.get("handle") or "").lower()
        t = p.get("tags") or {}
        terms = {f"${c}" for c in t.get("cashtags") or []} | {f"#{x}" for x in t.get("hashtags") or []} | words(str(p.get("text") or ""))
        bucket = recent if now - at <= window else before
        for term in terms:
            bucket[term].add(h)
    out = []
    for term, accts in recent.items():
        if len(accts) < min_accounts:
            continue
        rate_now = len(accts) / (window.total_seconds() / 3600)
        rate_before = len(before.get(term, set())) / (base.total_seconds() / 3600)
        ratio = rate_now / max(rate_before, 0.5)
        if ratio >= 3:
            out.append({"term": term, "kind": "cashtag" if term.startswith("$") else "hashtag" if term.startswith("#") else "keyword",
                        "accounts": len(accts), "accounts_before": len(before.get(term, set())), "ratio": round(ratio, 1)})
    out.sort(key=lambda r: (-r["accounts"], -r["ratio"]))
    return out[:30]


# --- cost ------------------------------------------------------------------------------------------------------------------------------


def estimate(accounts: list[dict[str, str]], posts_per_account_day: float = 15.0, interval_min: int = 5) -> dict[str, Any]:
    """A planning estimate (not a measurement): posts billed once each (since_id: only new posts are read), authors billed once a day."""
    n = len(accounts)
    posts = n * posts_per_account_day
    day = posts * POST_USD + n * USER_USD
    return {"accounts": n, "queries": len(queries(accounts)), "assumed_posts_per_account_day": posts_per_account_day, "posts_per_day": int(posts),
            "usd_per_day": round(day, 2), "usd_per_month": round(day * 30, 2), "reads_per_month": int(posts * 30),
            "interval_min": interval_min, "note": "estimate: real spend is measured and shown once reads run"}


def spent_today(state: dict[str, Any], now: datetime) -> float:
    s = (state.get("x_intel") or {}).get("spend") or {}
    return float(s.get(now.strftime("%Y-%m-%d"), 0.0))


def record_spend(state: dict[str, Any], now: datetime, usd: float) -> dict[str, Any]:
    xi = dict(state.get("x_intel") or {})
    s = {k: v for k, v in (xi.get("spend") or {}).items() if k >= (now - timedelta(days=40)).strftime("%Y-%m-%d")}
    day = now.strftime("%Y-%m-%d")
    s[day] = round(float(s.get(day, 0.0)) + usd, 4)
    xi["spend"] = s
    return {**state, "x_intel": xi}


def month_spend(state: dict[str, Any], now: datetime) -> float:
    s = (state.get("x_intel") or {}).get("spend") or {}
    return round(sum(float(v) for k, v in s.items() if k.startswith(now.strftime("%Y-%m"))), 4)


def per_post_cost(n_posts: int, n_users: int) -> float:
    return round(n_posts * POST_USD + n_users * USER_USD, 4)


__all__ = ["DEFAULTS", "DEFAULT_WATCHLIST", "GROUPS", "TIERS", "clean_watchlist", "emerging", "estimate", "independence", "parse", "queries",
           "velocity", "math"]


# --- the read (Trending's "x_accounts" source) ---------------------------------------------------------------------------------------


async def read(db: Any, s: Any, settings: Any, runtime: Any, opts: dict[str, Any], now: datetime) -> tuple[list[dict[str, Any]], str | None, dict[str, Any]]:
    """One pass over the watchlist: one recent-search request per batch of accounts, only posts newer than the last one we hold
    (since_id), so each post is paid for once. Stops before the Terminal's own daily cap and behind the account-wide X budget guard."""
    from app.connectors.x.errors import XBudgetExceededError, XError, XForbiddenError, XNotConfiguredError, XRateLimitError
    from app.security import credentials
    from app.social.x_service import make_x_client

    if not credentials.x_credentials(settings).configured:
        return [], "X is not connected (X › Connection)", {"requests": 0, "unavailable": True}
    accounts = clean_watchlist(opts.get("accounts"))
    if not accounts:
        return [], "the watchlist is empty", {"requests": 0, "idle": True}
    state = dict(s.state or {})
    xi = dict(state.get("x_intel") or {})
    since: dict[str, str] = dict(xi.get("since") or {})
    billed_users: dict[str, list[str]] = dict(xi.get("users_billed") or {})
    day = now.strftime("%Y-%m-%d")
    users_today = set(billed_users.get(day) or [])
    cap = float(opts.get("daily_usd") or 1.0)
    max_posts = max(10, min(100, int(opts.get("max_posts") or 50)))
    client = make_x_client(db, settings, runtime)
    items: list[dict[str, Any]] = []
    problems: list[str] = []
    requests, cost = 0, 0.0
    try:
        for q, handles in queries(accounts):
            worst = max_posts * POST_USD + len(handles) * USER_USD
            if spent_today({"x_intel": xi}, now) + cost + worst > cap + 1e-9:
                problems.append(f"stopped at the X Intelligence daily cap (${cap:.2f})")
                break
            key = ",".join(sorted(h.lower() for h in handles))[:200]
            try:
                body = await client.search_recent(q, max_results=max_posts, since_id=since.get(key))
            except XForbiddenError:
                return [], "not available on your X plan (X answered 403)", {"requests": requests + 1, "unavailable": True, "plan": True}
            requests += 1
            got = parse(body, accounts)
            items += got
            newest = (body.get("meta") or {}).get("newest_id")
            if newest:
                since[key] = str(newest)
            authors = {str(x["extra"]["handle"]).lower() for x in got}
            cost += per_post_cost(len(body.get("data") or []), len(authors - users_today))
            users_today |= authors
    except XNotConfiguredError as e:
        return [], str(e)[:200], {"requests": requests, "unavailable": True}
    except XBudgetExceededError as e:
        problems.append(f"stopped by the account's X budget: {str(e)[:150]}")
    except XRateLimitError as e:
        problems.append(f"X rate limit: {str(e)[:150]}")
    except XError as e:
        problems.append(f"{type(e).__name__}: {str(e)[:150]}")
    finally:
        await client.aclose()
    xi["since"] = since
    xi["users_billed"] = {day: sorted(users_today)}
    xi["last_read"] = {"at": now.isoformat(), "posts": len(items), "requests": requests, "usd": round(cost, 4), "problem": "; ".join(problems) or None}
    state = record_spend({**state, "x_intel": xi}, now, cost)
    s.state = state
    meta = {"requests": requests, "cost_usd": round(cost, 4), "idle": not items and not problems, "partial": "; ".join(problems) if items else ""}
    return items, (None if items or not problems else "; ".join(problems)), meta
