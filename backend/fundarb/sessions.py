"""US equity sessions (Module 4, rule 4): which session an instant falls in, so the oracle study can compare the HIP-3
equity oracles across session boundaries and the equity bucket can apply the measured policy per session.

Sessions, in the exchange's own time zone (FUNDARB_EQUITY_TZ, default America/New_York, so daylight saving is handled by
zoneinfo, never by a fixed UTC offset):
- rth        regular trading hours, Mon-Fri FUNDARB_EQUITY_RTH_OPEN..FUNDARB_EQUITY_RTH_CLOSE (default 09:30-16:00)
- extended   pre-market and after-hours, Mon-Fri FUNDARB_EQUITY_EXT_OPEN..rth open and rth close..FUNDARB_EQUITY_EXT_CLOSE
             (default 04:00 and 20:00)
- overnight  Mon-Thu ext close -> next day's ext open (US overnight venues trade then)
- weekend    Fri ext close -> Sun ext close
- holiday    a day the operator calendar marks as an exchange holiday (fundarb.events); without the calendar, holidays are
             counted in their weekday session, which the oracle study states in its output
Market hours are a convention of the underlying exchange, not of any market listed on Hyperliquid."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

SESSIONS = ("rth", "extended", "overnight", "weekend", "holiday")


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


@dataclass(frozen=True)
class SessionClock:
    tz: str = "America/New_York"
    rth_open: str = "09:30"
    rth_close: str = "16:00"
    ext_open: str = "04:00"
    ext_close: str = "20:00"
    holidays: frozenset[date] = frozenset()

    def session(self, ts: float | datetime) -> str:
        t = (ts if isinstance(ts, datetime) else datetime.fromtimestamp(ts, timezone.utc)).astimezone(ZoneInfo(self.tz))
        clock, wd = t.time(), t.weekday()  # Monday = 0
        ext_close = _hm(self.ext_close)
        if wd == 5 or (wd == 6 and clock < ext_close) or (wd == 4 and clock >= ext_close):
            return "weekend"
        if t.date() in self.holidays:
            return "holiday"
        if wd < 5 and _hm(self.rth_open) <= clock < _hm(self.rth_close):
            return "rth"
        if wd < 5 and (_hm(self.ext_open) <= clock < _hm(self.rth_open) or _hm(self.rth_close) <= clock < ext_close):
            return "extended"
        return "overnight"

    @classmethod
    def from_settings(cls, s: object, holidays: frozenset[date] = frozenset()) -> SessionClock:
        return cls(getattr(s, "equity_tz"), getattr(s, "equity_rth_open"), getattr(s, "equity_rth_close"),
                   getattr(s, "equity_ext_open"), getattr(s, "equity_ext_close"), holidays)
