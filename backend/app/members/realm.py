"""Vortex Realm, the Warrior Kiwi reward: a 3D fantasy RPG (Godot 4) played in the browser inside the Kiwi Passport.

The game is a static web build (backend/realm/: the Godot web export, every file stored gzipped). It is served only to members
who have unlocked the Warrior Kiwi reward (the same gate the old Kiwi Arena used) and to the owner (Admin → Realm, to test it),
through same-origin URLs, so the member's sign-in cookie comes along and the browser can cache the big files.

The game keeps its saves in the player's browser and gives no Passport XP. Online play (other Kiwis, chat, parties) goes through
app.members.realm_online, a relay that trusts and stores nothing.
"""

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.members import realm_online, rewards
from app.members import xp as ledger
from app.models.members import Member

RANK_KEY = "warrior"
GAME_DIR = Path(__file__).resolve().parents[2] / "realm"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".wasm": "application/wasm", ".pck": "application/octet-stream",
         ".png": "image/png"}


def unlocked(db: Session, member: Member | None) -> bool:
    return member is not None and rewards.unlocked(db, member, RANK_KEY)


def status(db: Session, member: Member | None, staff: bool = False) -> dict[str, Any]:
    """What the Realm tab shows. `staff`: the owner (admin login) can always play, to test it."""
    from app.members.service import rank_progress

    rank = next(r for r in ledger.ranks(db) if r.key == RANK_KEY)
    return {"unlocked": staff or unlocked(db, member), "rank": rank_progress(db, member) if member else None,
            "needs": {"name": rank.name, "min_xp": rank.min_xp}, "available": game_file("index.html") is not None,
            "online": realm_online.hub.online()}


def allowed(db: Session, member: Member | None, staff: bool) -> bool:
    return staff or unlocked(db, member)


def etag(path: Path) -> str:
    """A content tag for a game file, so browsers revalidate cheaply. Hashed once per file version."""
    st = path.stat()
    return _etag(str(path), st.st_size, st.st_mtime_ns)


@lru_cache(maxsize=32)
def _etag(path: str, _size: int, _mtime: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return f'"{h.hexdigest()[:20]}"'


def game_file(name: str) -> Path | None:
    """The gzipped file for a published name (index.html, index.pck, ...), or None. Only plain names in GAME_DIR: no paths."""
    if "/" in name or "\\" in name or name.startswith(".") or Path(name).suffix not in TYPES:
        return None
    p = GAME_DIR / f"{name}.gz"
    return p if p.is_file() else None
