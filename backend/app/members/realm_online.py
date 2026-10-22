"""Vortex Realm online: other Kiwis in the same world, live. One in-memory hub (the API runs as ONE process, numReplicas 1).

How a player gets in: the game (same origin as the Passport, so the sign-in cookie comes along) asks for a ticket at
/api/members/realm/online/ticket, which only Warrior Kiwis and up (or the owner) get. The ticket is a random bearer token
held in memory, tied to the member and the name everyone sees (their Passport username, as on the leaderboards, never an email or
id). The game then opens the WebSocket /api/members/realm/ws?ticket=... straight to the API host (Vercel's proxy doesn't
carry WebSockets).

What is shared: where each hero is, which animation it plays, its class / outfit / gear look, HP and level, chat (everyone
online or just the party) and parties (invite / accept / leave, at most 4). Monsters, loot and saves stay in each player's
own game, so nothing a client sends is trusted for anything that matters: no Passport XP, no rewards, nothing stored. The
server only relays, after clamping every number and cleaning every text.
"""

import asyncio
import contextlib
import json
import math
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

TICKET_TTL = 12 * 3600.0
MAX_PLAYERS = 80
PARTY_MAX = 4
STATE_MIN_GAP = 0.045        # at most ~20 position updates a second per player
CHAT_GAP, CHAT_BURST = 0.8, 4  # chat: a token bucket, one token every 0.8 s, 4 at most
CHAT_MAX = 160
INVITE_TTL = 60.0
QUEUE_MAX = 256              # a client that stops reading is dropped, it never slows the others
MAX_MSG = 2048
WORLD = 120.0                # |x|, |z| clamp (the map is about ±70)
AREAS = ("world", "lair")
CLASSES = ("knight", "ranger", "sorceress")
_ID = re.compile(r"^[a-z0-9_]{1,24}$")
_CTRL = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩]")


@dataclass
class Ticket:
    member_id: int | None
    name: str
    exp: float


@dataclass
class Player:
    pid: str
    name: str
    member_id: int | None
    queue: "asyncio.Queue[str | None]"
    look: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    party: str | None = None
    last_state: float = 0.0
    chat_tokens: float = CHAT_BURST
    chat_at: float = 0.0
    invites: dict[str, float] = field(default_factory=dict)  # from pid -> when


def _num(v: Any, lo: float, hi: float, default: float = 0.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(x) or math.isinf(x):
        return default
    return round(min(hi, max(lo, x)), 3)


def _int(v: Any, lo: int, hi: int, default: int = 0) -> int:
    return int(_num(v, lo, hi, default))


def _word(v: Any, default: str = "") -> str:
    s = str(v or "").strip().lower()
    return s if _ID.match(s) else default


def clean_text(v: Any, limit: int = CHAT_MAX) -> str:
    s = _CTRL.sub("", str(v or ""))
    return re.sub(r"\s+", " ", s).strip()[:limit]


def clean_look(v: Any) -> dict[str, Any]:
    v = v if isinstance(v, dict) else {}
    eq = v.get("eq") if isinstance(v.get("eq"), dict) else {}
    cls = _word(v.get("cls"), "knight")
    return {"cls": cls if cls in CLASSES else "knight", "tint": _int(v.get("tint"), 0, 31), "helm": bool(v.get("helm", True)),
            "eq": {k: _word(eq.get(k)) for k in ("weapon", "armor", "helm", "shield")}}


def clean_state(v: dict[str, Any]) -> dict[str, Any]:
    p = v.get("p") if isinstance(v.get("p"), list) and len(v.get("p")) == 3 else [0, 0, 0]
    area = str(v.get("ar") or "world")
    return {"p": [_num(p[0], -WORLD, WORLD), _num(p[1], -20, 60), _num(p[2], -WORLD, WORLD)], "r": _num(v.get("r"), -100, 100),
            "c": clean_text(v.get("c"), 40), "k": _num(v.get("k"), 0, 4, 1), "n": _int(v.get("n"), 0, 1_000_000),
            "b": _int(v.get("b"), 0, 4), "hp": _int(v.get("hp"), 0, 1_000_000), "mh": _int(v.get("mh"), 1, 1_000_000, 1),
            "lv": _int(v.get("lv"), 1, 999, 1), "ar": area if area in AREAS else "world"}


class Hub:
    def __init__(self) -> None:
        self.tickets: dict[str, Ticket] = {}
        self.players: dict[str, Player] = {}
        self.parties: dict[str, list[str]] = {}  # party id -> member pids, the first is the leader
        self._seq = 0

    # ---------------------------------------------------------------- tickets
    def mint(self, member_id: int | None, name: str, now: float | None = None) -> str:
        now = time.time() if now is None else now
        self.tickets = {k: t for k, t in self.tickets.items() if t.exp > now}
        token = secrets.token_urlsafe(24)
        self.tickets[token] = Ticket(member_id, clean_text(name, 24) or "Kiwi", now + TICKET_TTL)
        return token

    def redeem(self, token: str, now: float | None = None) -> Ticket | None:
        t = self.tickets.get(token or "")
        return t if t is not None and t.exp > (time.time() if now is None else now) else None

    # ---------------------------------------------------------------- sessions
    def online(self) -> int:
        return len(self.players)

    def join(self, ticket: Ticket) -> Player | None:
        """A new connection. The same member signing in again replaces the older one (one hero per Kiwi)."""
        if ticket.member_id is not None:
            for old in [p for p in self.players.values() if p.member_id == ticket.member_id]:
                self.send(old, {"t": "sys", "m": "You signed in somewhere else."})
                self.leave(old.pid)
                old.queue.put_nowait(None)
        if len(self.players) >= MAX_PLAYERS:
            return None
        self._seq += 1
        p = Player(pid=f"p{self._seq}", name=self._unique(ticket.name), member_id=ticket.member_id, queue=asyncio.Queue())
        self.players[p.pid] = p
        return p

    def _unique(self, name: str) -> str:
        taken = {p.name.lower() for p in self.players.values()}
        if name.lower() not in taken:
            return name
        n = 2
        while f"{name} {n}".lower() in taken:
            n += 1
        return f"{name} {n}"

    def public(self, p: Player) -> dict[str, Any]:
        return {"id": p.pid, "name": p.name, "look": p.look, "s": p.state, "party": p.party}

    def send(self, p: Player, msg: dict[str, Any]) -> None:
        if p.queue.qsize() >= QUEUE_MAX:
            return  # this client is far behind: skip (its writer drops it)
        p.queue.put_nowait(json.dumps(msg, separators=(",", ":")))

    def broadcast(self, msg: dict[str, Any], skip: str | None = None) -> None:
        for p in list(self.players.values()):
            if p.pid != skip:
                self.send(p, msg)

    def leave(self, pid: str) -> None:
        p = self.players.pop(pid, None)
        if p is None:
            return
        self._quit_party(p)
        for o in self.players.values():
            o.invites.pop(pid, None)
        self.broadcast({"t": "bye", "id": pid})

    # ---------------------------------------------------------------- messages
    def handle(self, p: Player, raw: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if len(raw) > MAX_MSG:
            return
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("t")
        if kind == "hello":
            first = not p.look
            p.look = clean_look(msg.get("look"))
            p.state = clean_state(msg.get("s") if isinstance(msg.get("s"), dict) else {})
            if first:
                self.send(p, {"t": "welcome", "id": p.pid, "name": p.name,
                              "players": [self.public(o) for o in self.players.values() if o.pid != p.pid and o.look]})
                self.broadcast({"t": "join", **self.public(p)}, skip=p.pid)
                self.broadcast({"t": "sys", "m": f"{p.name} entered the Realm."}, skip=p.pid)
            return
        if not p.look:
            return  # nothing before hello
        if kind == "s":
            if now - p.last_state < STATE_MIN_GAP:
                return
            p.last_state = now
            p.state = clean_state(msg)
            self.broadcast({"t": "s", "id": p.pid, **p.state}, skip=p.pid)
        elif kind == "look":
            p.look = clean_look(msg.get("look"))
            self.broadcast({"t": "look", "id": p.pid, "look": p.look}, skip=p.pid)
        elif kind == "chat":
            self._chat(p, msg, now)
        elif kind == "inv":
            self._invite(p, str(msg.get("id") or ""), now)
        elif kind == "accept":
            self._accept(p, str(msg.get("id") or ""), now)
        elif kind == "decline":
            src = self.players.get(str(msg.get("id") or ""))
            if src and p.invites.pop(src.pid, None) is not None:
                self.send(src, {"t": "sys", "m": f"{p.name} declined your party invite."})
        elif kind == "leave":
            self._quit_party(p)
        elif kind == "kick":
            self._kick(p, str(msg.get("id") or ""))
        elif kind == "kill":
            # a party kill: members nearby get a share in their own game (local XP only, never Passport XP)
            if p.party:
                share = {"t": "pxp", "id": p.pid, "xp": _int(msg.get("xp"), 0, 500), "x": _num(msg.get("x"), -WORLD, WORLD),
                         "z": _num(msg.get("z"), -WORLD, WORLD), "ar": p.state.get("ar", "world")}
                for mid in self.parties.get(p.party, []):
                    if mid != p.pid and mid in self.players:
                        self.send(self.players[mid], share)

    def _chat(self, p: Player, msg: dict[str, Any], now: float) -> None:
        p.chat_tokens = min(CHAT_BURST, p.chat_tokens + (now - p.chat_at) / CHAT_GAP) if p.chat_at else p.chat_tokens
        p.chat_at = now
        text = clean_text(msg.get("m"))
        if not text:
            return
        if p.chat_tokens < 1:
            self.send(p, {"t": "sys", "m": "Slow down a little."})
            return
        p.chat_tokens -= 1
        scope = "party" if msg.get("to") == "party" else "all"
        out = {"t": "chat", "id": p.pid, "name": p.name, "m": text, "to": scope}
        if scope == "party":
            if not p.party:
                self.send(p, {"t": "sys", "m": "You're not in a party. Invite someone from the player list (P)."})
                return
            for mid in self.parties.get(p.party, []):
                if mid in self.players:
                    self.send(self.players[mid], out)
        else:
            self.broadcast(out)

    # ---------------------------------------------------------------- parties
    def _party_msg(self, party: str | None) -> None:
        members = self.parties.get(party or "", [])
        info = {"t": "party", "pid": party, "leader": members[0] if members else None, "members": members}
        for mid in members:
            if mid in self.players:
                self.send(self.players[mid], info)
        for mid in members:
            if mid in self.players:
                self.broadcast({"t": "pm", "id": mid, "party": party})

    def _invite(self, p: Player, to: str, now: float) -> None:
        o = self.players.get(to)
        if o is None or o.pid == p.pid:
            return
        if p.party and o.party == p.party:
            self.send(p, {"t": "sys", "m": f"{o.name} is already in your party."})
            return
        if p.party and self.parties[p.party][0] != p.pid:
            self.send(p, {"t": "sys", "m": "Only the party leader can invite."})
            return
        if p.party and len(self.parties[p.party]) >= PARTY_MAX:
            self.send(p, {"t": "sys", "m": f"Your party is full ({PARTY_MAX})."})
            return
        if o.party:
            self.send(p, {"t": "sys", "m": f"{o.name} is already in a party."})
            return
        last = o.invites.get(p.pid)
        if last is not None and now - last < 5:
            return
        o.invites[p.pid] = now
        self.send(o, {"t": "invite", "id": p.pid, "name": p.name})
        self.send(p, {"t": "sys", "m": f"Party invite sent to {o.name}."})

    def _accept(self, p: Player, src_id: str, now: float) -> None:
        at = p.invites.pop(src_id, None)
        src = self.players.get(src_id)
        if at is None or src is None or now - at > INVITE_TTL:
            self.send(p, {"t": "sys", "m": "That invite has expired."})
            return
        if src.party and (self.parties[src.party][0] != src.pid or len(self.parties[src.party]) >= PARTY_MAX):
            self.send(p, {"t": "sys", "m": "That party is full or changed leader."})
            return
        self._quit_party(p)
        if not src.party:
            self._seq += 1
            src.party = f"party{self._seq}"
            self.parties[src.party] = [src.pid]
        self.parties[src.party].append(p.pid)
        p.party = src.party
        p.invites.clear()
        for mid in self.parties[src.party]:
            if mid != p.pid and mid in self.players:
                self.send(self.players[mid], {"t": "sys", "m": f"{p.name} joined the party."})
        self._party_msg(src.party)

    def _quit_party(self, p: Player) -> None:
        party = p.party
        if not party:
            return
        p.party = None
        members = [m for m in self.parties.get(party, []) if m != p.pid]
        self.send(p, {"t": "party", "pid": None, "leader": None, "members": []})
        self.broadcast({"t": "pm", "id": p.pid, "party": None})
        if len(members) <= 1:  # a party of one is no party
            self.parties.pop(party, None)
            for mid in members:
                if mid in self.players:
                    self.players[mid].party = None
                    self.send(self.players[mid], {"t": "party", "pid": None, "leader": None, "members": []})
                    self.send(self.players[mid], {"t": "sys", "m": "Your party broke up."})
                    self.broadcast({"t": "pm", "id": mid, "party": None})
            return
        self.parties[party] = members
        for mid in members:
            if mid in self.players:
                self.send(self.players[mid], {"t": "sys", "m": f"{p.name} left the party."})
        self._party_msg(party)

    def _kick(self, p: Player, target: str) -> None:
        o = self.players.get(target)
        if not p.party or o is None or o.party != p.party or self.parties[p.party][0] != p.pid or o.pid == p.pid:
            return
        self.send(o, {"t": "sys", "m": "You were removed from the party."})
        self._quit_party(o)


hub = Hub()


async def serve(ws: Any, player: Player) -> None:
    """Pump one connection: a writer task drains the player's queue, the reader feeds the hub. Either side ending ends both."""
    async def writer() -> None:
        while True:
            item = await player.queue.get()
            if item is None:
                return
            await ws.send_text(item)

    w = asyncio.create_task(writer())
    try:
        while not w.done():
            recv = asyncio.create_task(ws.receive_text())
            done, _ = await asyncio.wait({recv, w}, return_when=asyncio.FIRST_COMPLETED)
            if recv not in done:
                recv.cancel()
                with contextlib.suppress(BaseException):
                    await recv
                break
            hub.handle(player, recv.result())
    except Exception:  # noqa: BLE001 - a dropped socket is a normal way to leave
        pass
    finally:
        if hub.players.get(player.pid) is player:
            hub.leave(player.pid)
        w.cancel()
        with contextlib.suppress(BaseException):
            await w
