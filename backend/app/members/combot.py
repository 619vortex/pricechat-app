"""Historical Telegram reputation from Combot, through Combot's official API (https://combot.org/api, v5) or an
admin-uploaded export. Never scraped, never matched by username.

API (group admin's API key, Settings on combot.org):
  getChatUsers      Pro plan: paginated members with user_id, xp, reputation, msg_total, active_days, username.
  getChatMember     Pro plan: one member by numeric user_id.
  getXpLeaderboard  rows [xp, name, rank, user_id?, username?]; user_id only for admin keys (fallback when no Pro).
  getRepLeaderboard rows [rep, name, user_id?, username?].
Rows without a numeric Telegram user id are skipped: a name or @username is never enough to match a person.

Imports are batches: validated, previewed (matched / unmatched / duplicates / invalid), applied only on confirmation
(API syncs apply directly), and never overwrite a record with older data.
"""

import csv
import io
import json
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.db.base import utcnow
from app.members import reputation
from app.models.members import CombotImport, CombotRecord, LinkedIdentity, Member
from app.security.credentials import resolve

MAX_PAGES = 200
FIELD_ALIASES = {
    "telegram_user_id": ("telegram_user_id", "user_id", "userid", "tg_id", "id"),
    "xp": ("combot_xp", "xp", "exp", "experience"),
    "messages": ("combot_messages", "messages", "msg_total", "message_count"),
    "rep": ("combot_rep", "rep", "reputation"),
    "rank": ("combot_rank", "rank", "level"),
    "active_days": ("combot_active_days", "active_days"),
    "username": ("username", "user_name"),
    "name": ("name", "title", "full_name"),
}


class CombotError(Exception):
    pass


# --- normalise & validate -------------------------------------------------------------------------------------


def _pick(row: dict[str, Any], key: str) -> Any:
    lower = {str(k).strip().lower(): v for k, v in row.items()}
    for alias in FIELD_ALIASES[key]:
        if alias in lower and lower[alias] not in (None, ""):
            return lower[alias]
    return None


def _int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(str(v).replace(",", "").strip()))
    except ValueError:
        return None


def normalise(row: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """A validated row, or (None, reason). The numeric Telegram user id is mandatory."""
    uid = _int(_pick(row, "telegram_user_id"))
    if uid is None or uid <= 0:
        return None, "no numeric Telegram user id"
    xp = _int(_pick(row, "xp"))
    if xp is None or xp < 0:
        return None, "missing or negative XP"
    username = _pick(row, "username")
    return {
        "telegram_user_id": str(uid), "xp": xp, "messages": _int(_pick(row, "messages")), "rep": _int(_pick(row, "rep")),
        "rank": (str(_pick(row, "rank"))[:64] if _pick(row, "rank") is not None else None),
        "active_days": _int(_pick(row, "active_days")),
        "username": str(username).lstrip("@")[:128] if username else None, "name": (str(_pick(row, "name"))[:256] if _pick(row, "name") else None),
    }, None


def parse_file(content: str, fmt: str) -> list[dict[str, Any]]:
    """Raw rows from a Combot CSV export or a JSON array / {"data": [...]} object."""
    if fmt == "csv":
        return list(csv.DictReader(io.StringIO(content.lstrip("﻿"))))
    data = json.loads(content)
    if isinstance(data, dict):
        data = data.get("data") or data.get("result") or data.get("users") or []
        if isinstance(data, dict):
            data = data.get("data") or data.get("users") or []
    if not isinstance(data, list):
        raise CombotError("JSON must be a list of members (or an object with a 'data' list)")
    return [r for r in data if isinstance(r, dict)]


# --- preview / apply ------------------------------------------------------------------------------------------


def preview(db: Session, raw_rows: list[dict[str, Any]], source_type: str, source_timestamp: datetime,
            filename: str | None = None, actor: str = "dashboard") -> CombotImport:
    valid: dict[str, dict[str, Any]] = {}
    invalid: list[dict[str, Any]] = []
    duplicates = 0
    for i, raw in enumerate(raw_rows):
        row, why = normalise(raw)
        if row is None:
            invalid.append({"row": i + 1, "reason": why})
            continue
        if row["telegram_user_id"] in valid:
            duplicates += 1
            if row["xp"] <= valid[row["telegram_user_id"]]["xp"]:
                continue  # keep the higher XP of duplicated rows, reported in the preview
        valid[row["telegram_user_id"]] = row
    ids = list(valid)
    linked = {u: m for u, m in db.execute(select(LinkedIdentity.platform_user_id, LinkedIdentity.member_id).where(
        LinkedIdentity.platform == "telegram", LinkedIdentity.platform_user_id.in_(ids)))} if ids else {}
    existing = {r.telegram_user_id: r for r in db.scalars(select(CombotRecord).where(CombotRecord.telegram_user_id.in_(ids)))} if ids else {}
    older = [u for u in ids if u in existing and existing[u].source_timestamp > source_timestamp]
    renamed = [u for u in ids if u in existing and existing[u].username and valid[u]["username"]
               and existing[u].username.lower() != valid[u]["username"].lower()]
    stats = {
        "rows": len(raw_rows), "valid": len(valid), "invalid": len(invalid), "duplicates": duplicates,
        "matched": sum(1 for u in ids if u in linked), "unmatched": sum(1 for u in ids if u not in linked),
        "new": sum(1 for u in ids if u not in existing), "updates": sum(1 for u in ids if u in existing and u not in older),
        "older_skipped": len(older), "username_changed": len(renamed),
        "invalid_examples": invalid[:10], "source_timestamp": source_timestamp.isoformat(),
        "matched_examples": [{"telegram_user_id": u, "username": valid[u]["username"], "xp": valid[u]["xp"]} for u in ids if u in linked][:20],
    }
    imp = CombotImport(source_type=source_type, status="preview", filename=filename, created_by=actor, stats=stats,
                       rows=[{**r, "source_timestamp": source_timestamp.isoformat()} for r in valid.values()])
    db.add(imp)
    db.commit()
    return imp


def apply(db: Session, imp: CombotImport) -> dict[str, int]:
    """Write the batch. A record is only replaced by data at least as new as what we have (never silently older)."""
    if imp.status != "preview":
        raise CombotError(f"This import is already {imp.status}")
    now = utcnow()
    written = skipped = 0
    for row in imp.rows:
        ts = datetime.fromisoformat(row["source_timestamp"])
        rec = db.scalar(select(CombotRecord).where(CombotRecord.telegram_user_id == row["telegram_user_id"]))
        if rec is not None and rec.source_timestamp > ts:
            skipped += 1
            continue
        if rec is None:
            rec = CombotRecord(telegram_user_id=row["telegram_user_id"], source_timestamp=ts, imported_at=now, source_type=imp.source_type)
            db.add(rec)
        rec.username, rec.name = row.get("username"), row.get("name")
        rec.combot_xp, rec.combot_messages, rec.combot_rep = row["xp"], row.get("messages"), row.get("rep")
        rec.combot_rank, rec.combot_active_days = row.get("rank"), row.get("active_days")
        rec.source_timestamp, rec.imported_at, rec.source_type, rec.import_id = ts, now, imp.source_type, imp.id
        written += 1
    imp.status, imp.applied_at = "applied", now
    imp.stats = {**imp.stats, "written": written, "skipped_older": skipped}
    imp.rows = []  # applied: the batch keeps its statistics, not a second copy of everyone's data
    db.flush()
    _recalc_matched(db)
    db.commit()
    return {"written": written, "skipped_older": skipped}


def _recalc_matched(db: Session) -> None:
    from app.members import xp as ledger

    for m in db.scalars(select(Member).join(LinkedIdentity, LinkedIdentity.member_id == Member.id).where(LinkedIdentity.platform == "telegram")):
        ledger.recalc(db, m)


# --- official API ---------------------------------------------------------------------------------------------


class CombotClient:
    def __init__(self, api_key: str, base: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base = base.rstrip("/")
        self._key = api_key
        self._client = httpx.AsyncClient(timeout=30.0, transport=transport)

    def __repr__(self) -> str:  # never show the key
        return f"CombotClient({self.base})"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def call(self, method: str, **params: Any) -> dict[str, Any]:
        try:
            r = await self._client.get(f"{self.base}/{method}", params={"api_key": self._key, **{k: v for k, v in params.items() if v is not None}})
        except httpx.HTTPError as exc:
            raise CombotError(f"Could not reach Combot ({type(exc).__name__})") from exc
        try:
            body = r.json()
        except ValueError as exc:
            raise CombotError(f"Combot returned an unreadable answer (HTTP {r.status_code})") from exc
        if not body.get("ok", r.status_code == 200):
            raise CombotError(f"Combot {method}: {body.get('description') or 'error'} (code {body.get('error_code', r.status_code)})")
        return body

    async def chat_users(self, chat_id: str) -> list[dict[str, Any]]:
        """getChatUsers, all pages (Pro plan)."""
        out: list[dict[str, Any]] = []
        for page in range(MAX_PAGES):
            body = await self.call("getChatUsers", chat_id=chat_id, page=page, limit=500)
            data = body.get("data") or (body.get("result") or {}).get("data") or (body.get("result") or {}).get("users") or []
            out.extend(r for r in data if isinstance(r, dict))
            if not (body.get("has_next") or (body.get("result") or {}).get("has_next")) or not data:
                break
        return out

    async def leaderboards(self, chat_id: str) -> list[dict[str, Any]]:
        """Fallback: XP and reputation leaderboards merged by user_id. Rows without a user_id are dropped."""
        xp: dict[str, dict[str, Any]] = {}
        cursor = None
        for _ in range(MAX_PAGES):
            res = (await self.call("getXpLeaderboard", chat_id=chat_id, check_from=cursor, usernames="true")).get("result") or {}
            for t in res.get("users") or []:  # [xp, name, rank, user_id?, username?]
                if isinstance(t, list) and len(t) >= 4 and _int(t[3]):
                    xp[str(_int(t[3]))] = {"user_id": t[3], "xp": t[0], "name": t[1], "rank": t[2], "username": t[4] if len(t) > 4 else None}
            if not res.get("has_more"):
                break
            cursor = res.get("check_from")
        cursor = None
        for _ in range(MAX_PAGES):
            res = (await self.call("getRepLeaderboard", chat_id=chat_id, check_from=cursor, usernames="true")).get("result") or {}
            for t in res.get("users") or []:  # [rep, name, user_id?, username?]
                if isinstance(t, list) and len(t) >= 3 and _int(t[2]) and str(_int(t[2])) in xp:
                    xp[str(_int(t[2]))]["rep"] = t[0]
            if not res.get("has_more"):
                break
            cursor = res.get("check_from")
        return list(xp.values())

    async def chat_member(self, chat_id: str, user_id: str) -> dict[str, Any] | None:
        body = await self.call("getChatMember", chat_id=chat_id, user_id=user_id)
        res = body.get("result") or body.get("data")
        return res if isinstance(res, dict) else None


async def sync(db: Session, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Refresh every record from the official API (getChatUsers, or the leaderboards if the plan has no export),
    then fill in linked members Combot didn't return (getChatMember). Status is recorded either way."""
    rs = reputation.settings(db)
    key = resolve("combot_api_key", settings)
    if rs.combot_mode != "api" or not key or not rs.combot_chat_id:
        raise CombotError("Combot API sync is not set up (API key, group id and mode 'api')")
    rs.last_sync_at = utcnow()
    db.commit()
    client = CombotClient(key, settings.combot_api_base, transport)
    try:
        try:
            rows, source = await client.chat_users(rs.combot_chat_id), "api"
        except CombotError as exc:
            if "code 4" not in str(exc):  # 4xx: plan/permission -> try the leaderboards; other errors stop here
                raise
            rows, source = await client.leaderboards(rs.combot_chat_id), "leaderboard"
        imp = preview(db, rows, source, utcnow(), actor="combot.sync")
        result = apply(db, imp)
        filled = 0
        have = set(db.scalars(select(CombotRecord.telegram_user_id)))
        missing = [u for u in db.scalars(select(LinkedIdentity.platform_user_id).where(LinkedIdentity.platform == "telegram")) if u not in have]
        for uid in missing[:50]:
            try:
                one = await client.chat_member(rs.combot_chat_id, uid)
            except CombotError:
                continue
            if one:
                imp1 = preview(db, [one], "api", utcnow(), actor="combot.sync")
                filled += apply(db, imp1)["written"]
        rs = reputation.settings(db)
        rs.last_success_at, rs.last_error, rs.last_records = utcnow(), None, result["written"] + filled
        db.commit()
        return {"source": source, **result, "filled_linked": filled, "rows": imp.stats.get("rows", 0)}
    except CombotError as exc:
        rs = reputation.settings(db)
        rs.last_error = str(exc)[:500]
        db.commit()
        raise
    finally:
        await client.aclose()


def on_telegram_linked(db: Session, member: Member) -> str:
    """After a member links Telegram: look up history by the exact numeric id and tell them what happened."""
    from app.members.notify import notify

    rs = reputation.settings(db)
    rec = reputation.combot_record_for(db, member)
    if rs.combot_mode == "off" and rec is None:
        return "none"
    if rec is None:
        notify(db, member.id, "legacy", "No historical Combot record found",
               "Your Telegram account has no imported Combot history (yet). New activity still counts.")
        return "not_found"
    pts = reputation.legacy_points(rs, rec)
    notify(db, member.id, "legacy", "Telegram history imported",
           f"{rec.combot_xp:,} Combot XP found for your Telegram account: +{pts} legacy rank points.", {"combot_xp": rec.combot_xp, "points": pts})
    return "imported"
