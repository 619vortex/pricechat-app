"""Local spending guard for pay-per-use platform APIs (X).

X bills per resource returned and deduplicates within a UTC day, so usage is
tracked per UTC day. This is a guard rail in addition to the spending limit the
user sets in the X Developer Console; estimates are conservative upper bounds.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.connectors.state import get_connector_state
from app.connectors.x.errors import XBudgetExceededError
from app.models.enums import Platform

KEEP_DAYS = 62


def _day(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")


class PlatformBudget:
    def __init__(self, db: Session, settings: Settings, platform: Platform = Platform.X) -> None:
        self.db = db
        self.settings = settings
        self.platform = platform

    @property
    def state(self):  # type: ignore[no-untyped-def]
        return get_connector_state(self.db, self.platform)

    def _usage(self) -> dict[str, dict[str, float]]:
        return {k: v for k, v in (self.state.usage or {}).items() if not k.startswith("_")}

    def today(self) -> dict[str, float]:
        base = {"requests": 0, "posts": 0, "users": 0, "cost": 0.0, "writes": 0, "write_cost": 0.0}
        return {**base, **self._usage().get(_day(), {})}

    def month(self) -> dict[str, float]:
        prefix = _day()[:7]
        total = {"requests": 0, "posts": 0, "users": 0, "cost": 0.0, "writes": 0, "write_cost": 0.0}
        for day, u in self._usage().items():
            if day.startswith(prefix):
                for k in total:
                    total[k] += u.get(k, 0)
        total["cost"] = round(total["cost"], 4)
        total["write_cost"] = round(total["write_cost"], 4)
        return total

    def limits(self) -> dict[str, float]:
        return {"daily_usd": self.settings.x_daily_budget_usd, "monthly_usd": self.settings.x_monthly_budget_usd}

    def check(self, estimated_cost: float) -> None:
        daily, monthly = self.settings.x_daily_budget_usd, self.settings.x_monthly_budget_usd
        if self.today()["cost"] + estimated_cost > daily + 1e-9:
            raise XBudgetExceededError(f"Daily X budget of ${daily:.2f} reached; X reads resume tomorrow (UTC) or raise X_DAILY_BUDGET_USD.")
        if self.month()["cost"] + estimated_cost > monthly + 1e-9:
            raise XBudgetExceededError(f"Monthly X budget of ${monthly:.2f} reached; raise X_MONTHLY_BUDGET_USD to continue.")

    def record(self, endpoint: str, posts: int, users: int, cost: float) -> None:
        state = self.state
        usage = dict(state.usage or {})
        day = _day()
        u = {"requests": 0, "posts": 0, "users": 0, "cost": 0.0, "writes": 0, "write_cost": 0.0, **usage.get(day, {})}
        u["requests"] += 1
        if endpoint.startswith("write/"):  # writes tracked separately (and included in the total)
            u["writes"] += 1
            u["write_cost"] = round(u["write_cost"] + cost, 4)
        u["posts"] += posts
        u["users"] += users
        u["cost"] = round(u["cost"] + cost, 4)
        usage[day] = u
        keep = sorted(k for k in usage if not k.startswith("_"))[-KEEP_DAYS:]
        state.usage = {k: v for k, v in usage.items() if k in keep or k.startswith("_")}
        # Commit (not flush): callers await further X requests next, and an open write
        # transaction would block every other writer, including the scheduler, meanwhile.
        self.db.commit()

    def watermark(self, name: str) -> str | None:
        return ((self.state.usage or {}).get("_watermarks") or {}).get(name)

    def set_watermark(self, name: str, value: str) -> None:
        state = self.state
        usage = dict(state.usage or {})
        marks = dict(usage.get("_watermarks") or {})
        marks[name] = value
        usage["_watermarks"] = marks
        state.usage = usage
