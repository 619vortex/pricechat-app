"""Kiwi VPN: who may use it, members' devices, servers and the 5-minute sync with each node agent.

The gate follows the Kiwi Passport rule used by the other perks: the member's CURRENT rank from the ledger (never a stored
flag) against the `kiwi_vpn` entitlement (its required rank mirrors Admin › Kiwi VPN's minimum rank), or a manual grant of
that entitlement; banned members never qualify. A device is created by allocating a tunnel IP, adding the peer on the
node, then committing; if the node call fails nothing is kept. Only the public key is ever stored.
"""

import base64
import ipaddress
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.members import xp as ledger
from app.members.catalog import ensure_catalog
from app.models.members import Entitlement, Member, MemberEntitlement, Rank
from app.models.vpn import VpnDevice, VpnServer, VpnSettings
from app.security.secrets import protect, unprotect
from app.vpn import keys, node

logger = logging.getLogger(__name__)

ENTITLEMENT = "kiwi_vpn"
MAX_DEVICES_LIMIT = 10
MAX_CAP_GB = 100000
GB = 1024 ** 3  # the website shows sizes in powers of 1024
SETTINGS_DEFAULTS = {"enabled": False, "min_rank": "warrior", "max_devices": 3, "monthly_cap_gb": 0}


class VpnError(ValueError):
    """A problem the member (or admin) can act on; the message is shown. `status` is the HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# --- secrets ---------------------------------------------------------------------------------------------------------


def enc(token: str) -> str:
    return base64.b64encode(protect(token.encode())).decode()


def agent_token(server: VpnServer) -> str:
    return unprotect(base64.b64decode(server.agent_token_enc)).decode()


# --- settings & the gate -----------------------------------------------------------------------------------------------


def settings(db: Session) -> VpnSettings:
    row = db.get(VpnSettings, 1)
    if row is None:
        row = VpnSettings(id=1, **SETTINGS_DEFAULTS)
        db.add(row)
        db.flush()
    return row


def settings_out(s: VpnSettings) -> dict[str, Any]:
    return {"enabled": s.enabled, "min_rank": s.min_rank, "max_devices": s.max_devices, "monthly_cap_gb": s.monthly_cap_gb}


def _ranks(db: Session) -> list[Rank]:
    ensure_catalog(db)
    return ledger.ranks(db)


def required_rank(db: Session, s: VpnSettings | None = None) -> Rank:
    s = s or settings(db)
    all_ranks = _ranks(db)
    return next((r for r in all_ranks if r.key == s.min_rank), all_ranks[-1])


def _entitlement(db: Session) -> Entitlement | None:
    ensure_catalog(db)
    return db.scalar(select(Entitlement).where(Entitlement.key == ENTITLEMENT))


@dataclass
class Rules:
    """What qualifies() needs that is the same for every member: read once per server sync, not once per member."""

    ent: Entitlement | None
    all_ranks: list[Rank]
    need: Rank
    grants: dict[int, MemberEntitlement] | None = None  # member id -> the VPN grant, for the members being checked


def rules(db: Session, s: VpnSettings | None = None, member_ids: list[int] | None = None) -> Rules:
    s = s or settings(db)
    ent, all_ranks = _entitlement(db), _ranks(db)
    need = next((r for r in all_ranks if r.key == s.min_rank), all_ranks[-1])
    grants = None
    if member_ids is not None:
        grants = {} if ent is None else {g.member_id: g for g in db.scalars(select(MemberEntitlement).where(
            MemberEntitlement.entitlement_id == ent.id, MemberEntitlement.member_id.in_(member_ids)))}
    return Rules(ent, all_ranks, need, grants)


def qualifies(db: Session, member: Member, s: VpnSettings | None = None, r: Rules | None = None) -> tuple[bool, str | None]:
    """The member-level rule (rank / manual grant / not banned), regardless of whether the VPN is open."""
    s = s or settings(db)
    if member.status != "active":
        return False, "Your account can't use Kiwi VPN."
    ent = r.ent if r is not None else _entitlement(db)
    if ent is not None and not ent.enabled:
        return False, "Kiwi VPN isn't available right now."
    if ent is not None:
        now = utcnow()
        if r is not None and r.grants is not None:
            grant = r.grants.get(member.id)
        else:
            grant = db.scalar(select(MemberEntitlement).where(MemberEntitlement.member_id == member.id,
                                                              MemberEntitlement.entitlement_id == ent.id))
        if grant is not None and (grant.expires_at is None or grant.expires_at > now):
            return True, None
    need = r.need if r is not None else required_rank(db, s)
    if ledger.rank_for(db, member.rank_score, r.all_ranks if r is not None else None).level >= need.level:
        return True, None
    return False, f"Reach Kiwi Rank {need.name} to unlock Kiwi VPN."


def eligibility(db: Session, member: Member) -> tuple[bool, str | None]:
    s = settings(db)
    if not s.enabled:
        return False, "Kiwi VPN isn't open yet."
    return qualifies(db, member, s)


def update_settings(db: Session, changes: dict[str, Any]) -> dict[str, Any]:
    s = settings(db)
    if "min_rank" in changes and changes["min_rank"] is not None:
        rank = next((r for r in _ranks(db) if r.key == changes["min_rank"]), None)
        if rank is None:
            raise VpnError("Unknown rank.", 400)
        s.min_rank = rank.key
        ent = _entitlement(db)
        if ent is not None:
            ent.required_rank = rank.level  # the entitlement mirrors the VPN's minimum rank
    if changes.get("max_devices") is not None:
        n = int(changes["max_devices"])
        if not 1 <= n <= MAX_DEVICES_LIMIT:
            raise VpnError(f"Devices per member: 1 to {MAX_DEVICES_LIMIT}.", 400)
        s.max_devices = n
    if changes.get("monthly_cap_gb") is not None:
        cap = int(changes["monthly_cap_gb"])
        if not 0 <= cap <= MAX_CAP_GB:
            raise VpnError(f"Monthly data per member: 0 (unlimited) to {MAX_CAP_GB} GB.", 400)
        s.monthly_cap_gb = cap  # the next sync pauses / resumes members to match
    if changes.get("enabled") is not None:
        s.enabled = bool(changes["enabled"])
    db.commit()
    return settings_out(s)


# --- output ------------------------------------------------------------------------------------------------------------


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def device_out(d: VpnDevice) -> dict[str, Any]:
    return {"id": d.id, "name": d.name, "server_id": d.server_id, "ip": d.ip, "created_at": d.created_at.isoformat(),
            "last_handshake": _iso(d.last_handshake), "rx_bytes": d.rx_bytes, "tx_bytes": d.tx_bytes,
            "paused_until": _iso(d.paused_until)}


def device_counts(db: Session) -> dict[int, int]:
    return dict(db.execute(select(VpnDevice.server_id, func.count()).group_by(VpnDevice.server_id)).all())  # type: ignore[arg-type]


_device_counts = device_counts


def _load(server: VpnServer, count: int) -> float:
    return round(min(1.0, count / server.capacity), 4) if server.capacity > 0 else 1.0


def recommended_id(servers: list[VpnServer], counts: dict[int, int]) -> int | None:
    """The best server for a new device: online and not full, the lowest load, ties -> the lowest id."""
    ok = [v for v in servers if v.online and counts.get(v.id, 0) < v.capacity]
    return min(ok, key=lambda v: (_load(v, counts.get(v.id, 0)), v.id)).id if ok else None


def _month(now: datetime) -> str:
    return now.strftime("%Y-%m")


def next_month(now: datetime) -> datetime:
    """00:00 UTC on the 1st of the month after `now`."""
    return datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=timezone.utc)


def _aware(d: datetime | None) -> datetime | None:
    return d.replace(tzinfo=timezone.utc) if d is not None and d.tzinfo is None else d


def _paused(dev: VpnDevice, now: datetime) -> bool:
    until = _aware(dev.paused_until)
    return until is not None and until > now


def member_usage(db: Session, member_id: int, now: datetime | None = None) -> int:
    """Bytes (down + up) the member's devices used this UTC month."""
    month = _month(now or utcnow())
    return int(db.scalar(select(func.coalesce(func.sum(VpnDevice.rx_bytes + VpnDevice.tx_bytes), 0))
                         .where(VpnDevice.member_id == member_id, VpnDevice.usage_month == month)) or 0)


def usage_out(db: Session, member: Member, s: VpnSettings, now: datetime | None = None) -> dict[str, Any]:
    """This month's data for the member page: used / cap (0 = unlimited) and, over the cap, when it resumes."""
    now = now or utcnow()
    paused = [u for d in db.scalars(select(VpnDevice).where(VpnDevice.member_id == member.id))
              if _paused(d, now) and (u := _aware(d.paused_until)) is not None]
    return {"month": _month(now), "used_bytes": member_usage(db, member.id, now), "cap_bytes": s.monthly_cap_gb * GB,
            "paused_until": _iso(max(paused)) if paused else None}


def _dns(server: VpnServer) -> list[str]:
    """The server's own resolver: the first DNS its agent reports (no logs; with `adblock` on it also blocks ads &
    trackers), or Cloudflare if it reports none."""
    dns = [str(d) for d in server.dns or []]
    return dns[:1] or ["1.1.1.1"]


def _v6_pool(value: str) -> str:
    """The agent's IPv6 pool if it's a valid v6 interface, else "" (IPv4 only)."""
    try:
        return value if value and ipaddress.ip_interface(value).version == 6 else ""
    except ValueError:
        return ""


def overview(db: Session, member: Member) -> dict[str, Any]:
    s = settings(db)
    ok, reason = eligibility(db, member)
    counts = _device_counts(db)
    servers = list(db.scalars(select(VpnServer).where(VpnServer.enabled.is_(True)).order_by(VpnServer.name, VpnServer.id)))
    best = recommended_id(servers, counts)
    devices = db.scalars(select(VpnDevice).where(VpnDevice.member_id == member.id).order_by(VpnDevice.created_at, VpnDevice.id))
    out = {"enabled": s.enabled, "eligible": ok, "reason": reason, "required_rank": required_rank(db, s).name, "max_devices": s.max_devices,
           "servers": [{"id": v.id, "name": v.name, "region": v.region, "country_code": v.country_code, "online": v.online,
                        "load": _load(v, counts.get(v.id, 0)), "adblock": v.adblock, "recommended": v.id == best} for v in servers],
           "devices": [device_out(d) for d in devices], "usage": usage_out(db, member, s)}
    db.commit()  # first call seeds the settings row
    return out


# --- devices -----------------------------------------------------------------------------------------------------------


def _used_ips(db: Session, server_id: int) -> set[str]:
    return set(db.scalars(select(VpnDevice.ip).where(VpnDevice.server_id == server_id)))


def allocate_ip(db: Session, server: VpnServer, skip: set[str] | frozenset[str] = frozenset()) -> str:
    """The lowest free tunnel IP in the server's pool: never the network, the server itself, or a .0 / .1 / .255."""
    try:
        iface = ipaddress.ip_interface(server.address)
    except ValueError:
        raise VpnError("This server has no address pool yet. Try again shortly.", 503) from None
    used = _used_ips(db, server.id) | set(skip)
    for host in iface.network.hosts():
        if host == iface.ip or int(host) & 0xFF in (0, 1, 255):
            continue
        ip = str(host)
        if ip not in used:
            return ip
    raise VpnError("This server is full. Pick another one.", 503)


def clean_name(name: str) -> str:
    n = " ".join((name or "").split())
    if not 1 <= len(n) <= 32:
        raise VpnError("Device name: 1 to 32 characters.", 400)
    return n


def _checked_server(db: Session, member: Member, server_id: int, max_devices: int) -> VpnServer:
    db.scalar(select(Member.id).where(Member.id == member.id).with_for_update())  # one device creation per member at a time
    if db.scalar(select(func.count()).select_from(VpnDevice).where(VpnDevice.member_id == member.id)) >= max_devices:
        raise VpnError(f"You can have up to {max_devices} devices. Delete one first.", 409)
    server = db.get(VpnServer, server_id)
    if server is None or not server.enabled:
        raise VpnError("Server not found.", 404)
    if not server.online or not server.public_key or not server.endpoint_host:
        raise VpnError("This server is offline right now. Pick another one or try again later.", 503)
    if db.scalar(select(func.count()).select_from(VpnDevice).where(VpnDevice.server_id == server.id)) >= server.capacity:
        raise VpnError("This server is full. Pick another one.", 503)
    return server


def create_device(db: Session, member: Member, name: str, server_id: int) -> tuple[VpnDevice, str, str]:
    """(device, config text, filename). The config, holding the private key, exists only in this return value.

    The tunnel IP is protected by the unique (server_id, ip) constraint: if a concurrent creation took the same IP first,
    the flush fails, the transaction is rolled back and the next free IP is tried (checks re-run on the fresh state)."""
    name = clean_name(name)
    ok, reason = eligibility(db, member)
    if not ok:
        raise VpnError(reason or "Kiwi VPN isn't available.", 403)
    s = settings(db)
    max_devices = s.max_devices
    if s.monthly_cap_gb > 0:
        use = usage_out(db, member, s)
        if use["paused_until"] or use["used_bytes"] >= use["cap_bytes"]:
            raise VpnError(f"You've used this month's {s.monthly_cap_gb} GB of Kiwi VPN. It resumes on the 1st of next month.", 403)
    private_key, public_key = keys.generate_keypair()
    tried: set[str] = set()
    for _ in range(8):
        server = _checked_server(db, member, server_id, max_devices)
        ip = allocate_ip(db, server, tried)
        dev = VpnDevice(member_id=member.id, server_id=server.id, name=name, public_key=public_key, ip=ip,
                        usage_month=utcnow().strftime("%Y-%m"))
        db.add(dev)
        try:
            db.flush()
            break
        except IntegrityError:
            db.rollback()
            tried.add(ip)
    else:
        raise VpnError("The server is busy. Try again in a moment.", 503)
    try:
        with node.client_for(server) as agent:
            agent.put_peer(public_key, f"{ip}/32")
    except node.NodeError as e:
        db.rollback()
        logger.warning("vpn: adding a peer on server %s failed: %s", server_id, e)
        raise VpnError("This server is offline right now. Pick another one or try again later.", 503) from None
    db.commit()
    config = keys.render_config(private_key, ip, _dns(server), server.public_key, server.endpoint_host,
                                server.listen_port, keys.ipv6_for(server.address, ip, server.address6))
    return dev, config, keys.filename(server.name, dev.name)


def delete_device(db: Session, member: Member, device_id: int) -> None:
    dev = db.get(VpnDevice, device_id)
    if dev is None or dev.member_id != member.id:
        raise VpnError("Device not found.", 404)
    _remove(db, dev)
    db.commit()


def _remove(db: Session, dev: VpnDevice, server: VpnServer | None = None) -> None:
    """Remove the peer on the node (best effort: sync removes leftovers), then the row."""
    server = server or db.get(VpnServer, dev.server_id)
    if server is not None:
        try:
            with node.client_for(server) as agent:
                agent.delete_peer(dev.public_key)
        except node.NodeError as e:
            logger.info("vpn: removing a peer on server %s failed (sync retries): %s", server.id, e)
    db.delete(dev)


# --- servers (admin) ---------------------------------------------------------------------------------------------------


def _apply_status(server: VpnServer, st: dict[str, Any], now: datetime) -> None:
    server.public_key, server.listen_port = st["public_key"], st["listen_port"]
    server.endpoint_host, server.address, server.dns = st["endpoint_host"], st["address"], st["dns"]
    server.online, server.last_seen, server.last_error = True, now, None
    server.adblock, server.fail_streak, server.address6 = bool(st.get("adblock")), 0, _v6_pool(str(st.get("address6") or ""))


def _check_pool(address: str) -> None:
    try:
        ipaddress.ip_interface(address)
    except ValueError:
        raise node.NodeError("The VPN agent reported an invalid address pool") from None


def _clean_server_fields(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(data)
    if out.get("country_code") is not None:
        cc = str(out["country_code"]).strip().upper()
        if len(cc) != 2 or not cc.isalpha():
            raise VpnError("Country code: two letters (ISO-2).", 400)
        out["country_code"] = cc
    if out.get("cert_sha256") is not None:
        try:
            out["cert_sha256"] = node.normalize_fingerprint(out["cert_sha256"])
        except ValueError as e:
            raise VpnError(str(e), 400) from None
    if out.get("agent_url") is not None:
        url = str(out["agent_url"]).strip().rstrip("/")
        if not url.lower().startswith("https://"):
            raise VpnError("The agent URL must start with https://", 400)
        out["agent_url"] = url
    if out.get("agent_token") is not None and len(str(out["agent_token"]).strip()) < 32:
        raise VpnError("The agent token is too short (at least 32 characters).", 400)
    for k in ("name", "region"):
        if out.get(k) is not None:
            out[k] = " ".join(str(out[k]).split())
    if out.get("name") == "":
        raise VpnError("The server needs a name.", 400)
    return out


def add_server(db: Session, data: dict[str, Any]) -> VpnServer:
    d = _clean_server_fields(data)
    token = str(d["agent_token"]).strip()
    try:
        with node.connect(d["agent_url"], token, d["cert_sha256"]) as agent:
            st = agent.status()
        _check_pool(st["address"])
    except node.NodeError as e:
        raise VpnError(f"Couldn't verify the VPN agent: {e}", 400) from None
    server = VpnServer(name=d["name"], region=d.get("region") or "", country_code=d.get("country_code") or "", agent_url=d["agent_url"],
                       agent_token_enc=enc(token), cert_sha256=d["cert_sha256"], capacity=int(d.get("capacity") or 250), enabled=True)
    _apply_status(server, st, utcnow())
    db.add(server)
    db.commit()
    return server


def update_server(db: Session, server_id: int, data: dict[str, Any]) -> VpnServer:
    server = db.get(VpnServer, server_id)
    if server is None:
        raise VpnError("Server not found.", 404)
    d = _clean_server_fields(data)
    for k in ("name", "region", "country_code", "capacity", "enabled", "cert_sha256"):
        if d.get(k) is not None:
            setattr(server, k, d[k])
    if d.get("agent_token"):
        server.agent_token_enc = enc(str(d["agent_token"]).strip())
    db.commit()
    return server


def delete_server(db: Session, server_id: int) -> None:
    server = db.get(VpnServer, server_id)
    if server is None:
        raise VpnError("Server not found.", 404)
    devices = list(db.scalars(select(VpnDevice).where(VpnDevice.server_id == server.id)))
    if devices:
        try:
            with node.client_for(server) as agent:
                for dev in devices:
                    try:
                        agent.delete_peer(dev.public_key)
                    except node.NodeError:
                        pass  # best effort: the server is going away
        except node.NodeError:
            pass
    for dev in devices:
        db.delete(dev)
    db.delete(server)
    db.commit()


def test_server(db: Session, server_id: int) -> dict[str, Any]:
    server = db.get(VpnServer, server_id)
    if server is None:
        raise VpnError("Server not found.", 404)
    try:
        with node.client_for(server) as agent:
            st = agent.status()
        _check_pool(st["address"])
    except node.NodeError as e:
        server.online, server.last_error = False, str(e)[:500]
        db.commit()
        return {"ok": False, "error": str(e), "peers": 0}
    _apply_status(server, st, utcnow())
    db.commit()
    return {"ok": True, "error": None, "peers": len(st["peers"])}


def admin_overview(db: Session) -> dict[str, Any]:
    s = settings(db)
    since = utcnow() - timedelta(hours=24)
    counts = _device_counts(db)
    active = dict(db.execute(select(VpnDevice.server_id, func.count()).where(VpnDevice.last_handshake >= since)
                             .group_by(VpnDevice.server_id)).all())  # type: ignore[arg-type]
    servers = [{"id": v.id, "name": v.name, "region": v.region, "country_code": v.country_code, "endpoint_host": v.endpoint_host,
                "listen_port": v.listen_port, "agent_url": v.agent_url, "capacity": v.capacity, "enabled": v.enabled, "online": v.online,
                "adblock": v.adblock,
                "last_seen": _iso(v.last_seen), "last_error": v.last_error, "devices": counts.get(v.id, 0), "active_24h": active.get(v.id, 0)}
               for v in db.scalars(select(VpnServer).order_by(VpnServer.name, VpnServer.id))]
    out = {"settings": settings_out(s), "ranks": [{"key": r.key, "name": r.name} for r in _ranks(db)], "servers": servers,
           "totals": {"devices": sum(counts.values()),
                      "members": db.scalar(select(func.count(func.distinct(VpnDevice.member_id)))) or 0,
                      "active_24h": sum(active.values())}}
    db.commit()
    return out


# --- sync (the vpn.sync job) -------------------------------------------------------------------------------------------


def _ts(seconds: int) -> datetime | None:
    return datetime.fromtimestamp(seconds, tz=timezone.utc) if seconds > 0 else None


def _usage(dev: VpnDevice, peer: dict[str, Any], month: str) -> None:
    if dev.usage_month != month:
        dev.usage_month, dev.rx_bytes, dev.tx_bytes = month, 0, 0
    rx, tx = int(peer["rx_bytes"]), int(peer["tx_bytes"])
    dev.rx_bytes += rx - dev.rx_counter if rx >= dev.rx_counter else rx  # a smaller counter: the agent restarted / re-added it
    dev.tx_bytes += tx - dev.tx_counter if tx >= dev.tx_counter else tx
    dev.rx_counter, dev.tx_counter = rx, tx
    hs = _ts(int(peer["latest_handshake"]))
    if hs is not None:
        dev.last_handshake = hs


def sync_server(db: Session, server: VpnServer, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    out = {"server": server.id, "online": False, "updated": 0, "readded": 0, "removed": 0, "revoked": 0, "errors": 0}
    s = settings(db)
    try:
        with node.client_for(server) as agent:
            st = agent.status()
            _check_pool(st["address"])
            _apply_status(server, st, now)
            out["online"] = True
            peers = {p["public_key"]: p for p in st["peers"]}
            month = now.strftime("%Y-%m")
            ours: set[str] = set()
            members: dict[int, bool] = {}
            devices = list(db.scalars(select(VpnDevice).where(VpnDevice.server_id == server.id)))
            ids = sorted({d.member_id for d in devices})
            # members, ranks, the entitlement and grants read once for the server, not ~15 queries per member
            people = {m.id: m for m in db.scalars(select(Member).where(Member.id.in_(ids)))} if ids else {}
            r = rules(db, s, ids)
            for dev in devices:
                if dev.member_id not in members:
                    m = people.get(dev.member_id)
                    members[dev.member_id] = m is not None and qualifies(db, m, s, r)[0]
                if not members[dev.member_id]:  # rank dropped / banned / grant expired: revoke
                    try:
                        agent.delete_peer(dev.public_key)
                    except node.NodeError:
                        out["errors"] += 1
                        ours.add(dev.public_key)  # keep the row: retried next run
                        continue
                    db.delete(dev)
                    ours.add(dev.public_key)  # its peer is already gone: not an "unknown" one below
                    out["revoked"] += 1
                    continue
                ours.add(dev.public_key)
                peer = peers.get(dev.public_key)
                if _paused(dev, now):  # over the monthly cap: its peer stays off the node until the month rolls over
                    if peer is not None:
                        _usage(dev, peer, month)
                        try:
                            agent.delete_peer(dev.public_key)
                        except node.NodeError:
                            out["errors"] += 1
                    continue
                dev.paused_until = None  # a pause that ran out (a new month): re-added below like any missing peer
                if peer is None or peer["allowed_ip"] != f"{dev.ip}/32":
                    try:
                        agent.put_peer(dev.public_key, f"{dev.ip}/32")
                        dev.rx_counter = dev.tx_counter = 0  # a fresh peer starts counting from zero
                        out["readded"] += 1
                    except node.NodeError:
                        out["errors"] += 1
                    continue
                _usage(dev, peer, month)
                out["updated"] += 1
            for key in peers.keys() - ours:  # peers nobody here owns (a deleted device whose DELETE failed, ...)
                try:
                    agent.delete_peer(key)
                    out["removed"] += 1
                except node.NodeError:
                    out["errors"] += 1
    except node.NodeError as e:
        server.online, server.last_error = False, str(e)[:500]
        server.fail_streak = (server.fail_streak or 0) + 1
        out["error"] = str(e)
    db.commit()
    return out


def enforce_caps(db: Session, now: datetime | None = None) -> dict[str, int]:
    """The monthly data cap: a member at or over it has every device paused (peer removed at the node, the device kept)
    until the 1st of next month; a member under it again (a new month, or the owner raised / removed the cap) resumes."""
    now = now or utcnow()
    cap = settings(db).monthly_cap_gb * GB
    used = dict(db.execute(select(VpnDevice.member_id, func.sum(VpnDevice.rx_bytes + VpnDevice.tx_bytes))
                           .where(VpnDevice.usage_month == _month(now)).group_by(VpnDevice.member_id)).all())  # type: ignore[arg-type]
    pause: list[VpnDevice] = []
    resume: list[VpnDevice] = []
    for dev in db.scalars(select(VpnDevice).order_by(VpnDevice.id)):
        over = cap > 0 and int(used.get(dev.member_id) or 0) >= cap
        if over and not _paused(dev, now):
            dev.paused_until = next_month(now)
            pause.append(dev)
        elif not over and dev.paused_until is not None:
            dev.paused_until = None
            resume.append(dev)
    db.commit()
    errors = 0
    for server_id in sorted({d.server_id for d in pause + resume}):  # best effort: the next sync finishes what fails here
        server = db.get(VpnServer, server_id)
        if server is None or not server.enabled or not server.online:
            continue
        try:
            with node.client_for(server) as agent:
                for dev in pause + resume:
                    if dev.server_id != server_id:
                        continue
                    try:
                        if dev in pause:
                            agent.delete_peer(dev.public_key)
                        else:
                            agent.put_peer(dev.public_key, f"{dev.ip}/32")
                            dev.rx_counter = dev.tx_counter = 0  # a fresh peer starts counting from zero
                    except node.NodeError:
                        errors += 1
        except node.NodeError:
            errors += 1
    db.commit()
    return {"paused": len(pause), "resumed": len(resume), "errors": errors}


def sync_all(db: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    results = [sync_server(db, v, now) for v in list(db.scalars(select(VpnServer).where(VpnServer.enabled.is_(True)).order_by(VpnServer.id)))]
    caps = enforce_caps(db, now)
    return {"servers": len(results), "online": sum(r["online"] for r in results),
            **{k: sum(r[k] for r in results) for k in ("updated", "readded", "removed", "revoked")},
            "errors": sum(r["errors"] for r in results) + caps["errors"], "paused": caps["paused"], "resumed": caps["resumed"]}
