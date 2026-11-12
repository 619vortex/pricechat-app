"""World Monitor bridge: connects the World Monitor desktop app installed on this PC (open source, worldmonitor.app) to Admin › World
Monitor › Desktop app (backend app/newsdesk/wm_app.py). Owner, 2026-11-12: "I downloaded and installed World Monitor on the machine,
connect it to the admin panel".

    <python> -m pricechat_worker.worldmonitor          (reads worker.env: PRICECHAT_SERVER, PRICECHAT_WORKER_TOKEN)

The app ships a local data server (sidecar/local-api-server.mjs with its own node.exe). The desktop window starts it on 127.0.0.1:46123
with a token we can't read, so the bridge starts its OWN headless copy on 127.0.0.1:46124 with a fresh random token. It works whether or
not the World Monitor window is open, and it never listens beyond this PC. Every 5 minutes it reads the feeds that work without keys,
turns them into one compact snapshot (signals, each feed's health, a short summary) and posts it to the server with the worker's token.
It only calls out; nothing here is reachable from outside. Feeds that need a key (ACLED, NASA FIRMS, Cloudflare Radar...) are reported
as "needs a key"; the keys can go in worker.env (WM_ACLED_ACCESS_TOKEN=... style names are passed through as ACLED_ACCESS_TOKEN=...).
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

VERSION = "1.0.0"
PORT = 46124  # the desktop window's own copy uses 46123
INTERVAL_S = 300
STOP = False
log = logging.getLogger("worldmonitor")
SEV_RANK = {"critical": 0, "high": 1, "elevated": 2, "low": 3}


def _iso_ms(ms: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(ms) / 1000, timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _ts(at: Any) -> float:
    try:
        return datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def find_install() -> Path | None:
    for base in (os.environ.get("WM_INSTALL_DIR"), str(Path(os.environ.get("LOCALAPPDATA", "")) / "World Monitor"), r"C:\Program Files\World Monitor"):
        if base and (Path(base) / "sidecar" / "local-api-server.mjs").exists():
            return Path(base)
    return None


def app_version(install: Path) -> str:
    """The installed app's version, from Windows' uninstall entry (the installer writes it); "" if unknown."""
    try:
        import winreg

        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                root = winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")
            except OSError:
                continue
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    k = winreg.OpenKey(root, winreg.EnumKey(root, i))
                    if "World Monitor" in str(winreg.QueryValueEx(k, "DisplayName")[0]):
                        return str(winreg.QueryValueEx(k, "DisplayVersion")[0])[:20]
                except OSError:
                    continue
    except ImportError:
        pass
    return ""


class Sidecar:
    """Our own headless copy of the app's local data server, restarted if it dies."""

    def __init__(self, install: Path, port: int = PORT) -> None:
        self.install, self.port = install, port
        self.token = secrets.token_hex(24)
        self.proc: subprocess.Popen[bytes] | None = None
        self.base = f"http://127.0.0.1:{port}"

    def env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("PRICECHAT_")}  # never hand our server token to the app
        for k, v in os.environ.items():
            if k.startswith("WM_") and k not in ("WM_INSTALL_DIR",):
                env[k[3:]] = v  # WM_ACLED_ACCESS_TOKEN -> ACLED_ACCESS_TOKEN
        env.update(LOCAL_API_PORT=str(self.port), LOCAL_API_TOKEN=self.token, LOCAL_API_RESOURCE_DIR=str(self.install / "_up_"),
                   LOCAL_API_MODE="desktop", LOCAL_API_CLOUD_FALLBACK="false")
        return env

    def alive(self) -> bool:
        try:
            return httpx.get(f"{self.base}/api/local-status", timeout=3).json().get("success") is True
        except (httpx.HTTPError, ValueError):
            return False

    def ensure(self) -> bool:
        if self.proc is not None and self.proc.poll() is None and self.alive():
            return True
        self.stop()
        node = self.install / "sidecar" / "node" / "node.exe"
        script = self.install / "sidecar" / "local-api-server.mjs"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen([str(node), str(script)], env=self.env(), cwd=str(self.install / "sidecar"),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        for _ in range(30):
            if self.alive():
                log.info("sidecar up on %s (pid %s)", self.base, self.proc.pid)
                return True
            time.sleep(1)
        log.warning("sidecar didn't answer on %s", self.base)
        return False

    def get(self, route: str, timeout: float = 40) -> httpx.Response:
        return httpx.get(f"{self.base}/api/{route}", headers={"Authorization": f"Bearer {self.token}"}, timeout=timeout)

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None


# ---------------------------------------------------------------- feeds -> signals (pure; tests/test_worldmonitor.py)

def from_earthquakes(d: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    out = []
    for f in d.get("features") or []:
        p, g = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates") or [None, None]
        mag = p.get("mag")
        if not isinstance(mag, (int, float)):
            continue
        tsunami = bool(p.get("tsunami"))
        sev = "critical" if mag >= 7 or tsunami or p.get("alert") in ("orange", "red") else "high" if mag >= 6 else "elevated" if mag >= 5 else "low"
        out.append({"id": f"usgs:{f.get('id') or p.get('code')}", "layer": "earthquake", "severity": sev,
                    "title": f"M{mag:.1f} earthquake, {p.get('place') or 'unknown place'}" + (" (tsunami flag)" if tsunami else ""),
                    "detail": f"USGS, {p.get('status') or ''}{', PAGER ' + p['alert'] if p.get('alert') else ''}".strip(", "),
                    "at": _iso_ms(p.get("time")), "url": p.get("url") or "", "lat": g[1] if len(g) > 1 else None, "lon": g[0]})
    return out, {"count": len(out)}


LEVEL = {"critical": "critical", "high": "high", "elevated": "elevated"}


def from_risk(d: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = sorted(d.get("cii") or [], key=lambda r: -(r.get("score") or 0))
    out = []
    for r in rows:
        sev = LEVEL.get(str(r.get("level")))
        if not sev:
            continue
        ch = r.get("change24h") or 0
        out.append({"id": f"cii:{r.get('code')}", "layer": "instability", "severity": sev,
                    "title": f"{r.get('name')}: instability {r.get('score')}/100 ({r.get('level')}, {r.get('trend') or 'stable'}{f', {ch:+}' if ch else ''} in 24 h)",
                    "detail": ", ".join(f"{k} {v}" for k, v in (r.get("components") or {}).items() if v), "at": r.get("lastUpdated")})
    top = [{"code": r.get("code"), "name": r.get("name"), "score": r.get("score"), "level": r.get("level")} for r in rows[:10]]
    return out[:25], {"count": len(rows), "top": top}


def from_theaters(d: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    out, all_ = [], []
    for p in d.get("postures") or []:
        lvl = str(p.get("postureLevel") or "normal")
        all_.append({"theater": p.get("shortName") or p.get("theaterName"), "level": lvl, "aircraft": p.get("totalAircraft") or 0,
                     "vessels": p.get("totalVessels") or 0, "trend": p.get("trend")})
        if lvl == "normal":
            continue
        sev = "critical" if lvl in ("critical", "surge") else "high" if lvl in ("high", "heightened") else "elevated"
        out.append({"id": f"theater:{p.get('theaterId')}", "layer": "military", "severity": sev, "title": p.get("headline") or f"{p.get('theaterName')}: {lvl}",
                    "detail": f"{p.get('summary') or ''}{'; strike-capable' if p.get('strikeCapable') else ''}", "at": d.get("timestamp"),
                    "lat": p.get("centerLat"), "lon": p.get("centerLon")})
    return out, {"count": len(all_), "theaters": all_, "flights": d.get("totalFlights")}


def from_climate(d: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    out = []
    for a in d.get("anomalies") or []:
        sev = {"extreme": "high", "severe": "elevated", "moderate": "low"}.get(str(a.get("severity")))
        if not sev:
            continue
        out.append({"id": f"climate:{a.get('zone')}", "layer": "climate", "severity": sev,
                    "title": f"{a.get('zone')}: {a.get('severity')} {a.get('type')} anomaly, {a.get('tempDelta'):+}°C vs normal",
                    "detail": f"rain {a.get('precipDelta'):+} mm/day; {a.get('period')}", "at": d.get("timestamp"), "lat": a.get("lat"), "lon": a.get("lon")})
    return out, {"count": len(d.get("anomalies") or [])}


def from_faa(xml: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    out = []
    root = ET.fromstring(xml)
    upd = root.findtext("Update_Time") or ""
    for dt in root.iter("Delay_type"):
        kind = (dt.findtext("Name") or "").strip()
        for el in dt.iter():
            arpt = el.findtext("ARPT")
            if not arpt or el.tag in ("Delay_type",):
                continue
            reason = (el.findtext("Reason") or "").strip()
            if "Ground Stop" in kind:
                sev, what = "high", f"ground stop{', until ' + el.findtext('End_Time') if el.findtext('End_Time') else ''}"
            elif "Ground Delay" in kind:
                sev, what = "elevated", f"ground delay, avg {el.findtext('Avg') or '?'}, max {el.findtext('Max') or '?'}"
            elif "Closure" in kind:
                sev, what = "low", "closure"
            else:
                sev, what = "low", kind.lower() or "delay"
            out.append({"id": f"faa:{arpt}:{kind}", "layer": "aviation", "severity": sev, "title": f"{arpt}: {what}",
                        "detail": f"FAA: {reason[:200]}", "at": None, "url": "https://nasstatus.faa.gov/"})
    seen, uniq = set(), []
    for s in out:
        if s["id"] not in seen:
            seen.add(s["id"])
            uniq.append(s)
    return uniq, {"count": len(uniq), "updated": upd}


def from_cyber(d: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = d.get("data") or []
    fam: dict[str, int] = {}
    ctry: dict[str, int] = {}
    for r in rows:
        if r.get("malwareFamily"):
            fam[r["malwareFamily"]] = fam.get(r["malwareFamily"], 0) + 1
        if r.get("country"):
            ctry[r["country"]] = ctry.get(r["country"], 0) + 1
    srcs = {k: ("ok" if v.get("ok") else v.get("reason") or "down") for k, v in (d.get("sources") or {}).items()}
    top_f = sorted(fam.items(), key=lambda kv: -kv[1])[:5]
    top_c = sorted(ctry.items(), key=lambda kv: -kv[1])[:5]
    sig = []
    if rows:
        sig.append({"id": "cyber:c2", "layer": "cyber", "severity": "low", "title": f"{len(rows)} active malware command servers tracked",
                    "detail": "top: " + ", ".join(f"{k} {v}" for k, v in top_f) + (" · hosted in " + ", ".join(f"{k} {v}" for k, v in top_c) if top_c else ""),
                    "at": d.get("cachedAt")})
    return sig, {"count": len(rows), "families": dict(top_f), "countries": dict(top_c), "sources": srcs}


def from_macro(d: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    fg = (d.get("signals") or {}).get("fearGreed") or {}
    return [], {"count": 1, "verdict": d.get("verdict"), "fear_greed": fg.get("value"), "fear_greed_label": fg.get("status"),
                "bullish": d.get("bullishCount"), "of": d.get("totalCount")}


def needs_key(d: Any) -> str:
    """'' if the feed is configured, else a short reason."""
    if not isinstance(d, dict):
        return ""
    if d.get("configured") is False or d.get("skipped"):
        return str(d.get("reason") or d.get("error") or "needs an API key")[:150]
    return ""


FEEDS: list[tuple[str, Callable[[Any], tuple[list[dict[str, Any]], dict[str, Any]]] | None, bool]] = [
    ("earthquakes", from_earthquakes, True),
    ("risk-scores", from_risk, True),
    ("theater-posture", from_theaters, True),
    ("climate-anomalies", from_climate, True),
    ("faa-status", from_faa, False),  # XML
    ("cyber-threats", from_cyber, True),
    ("macro-signals", from_macro, True),
    ("acled-conflict", None, True),
    ("firms-fires", None, True),
    ("cloudflare-outages", None, True),
]


def collect(get: Callable[[str], httpx.Response]) -> dict[str, Any]:
    feeds: dict[str, Any] = {}
    signals: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    for name, parse, is_json in FEEDS:
        t0 = time.monotonic()
        try:
            r = get(name)
            ms = int((time.monotonic() - t0) * 1000)
            if r.status_code != 200:
                feeds[name] = {"ok": False, "count": 0, "note": f"HTTP {r.status_code}", "ms": ms}
                continue
            data = r.json() if is_json else r.text
            missing = needs_key(data)
            if missing or parse is None:
                feeds[name] = {"ok": not missing, "count": len((data or {}).get("data") or []) if isinstance(data, dict) else 0,
                               "note": missing or "not shown", "ms": ms}
                continue
            sig, summ = parse(data)
            signals += sig
            summary[name] = summ
            feeds[name] = {"ok": True, "count": int(summ.get("count") or len(sig)), "note": "", "ms": ms}
        except Exception as e:  # noqa: BLE001 - one bad feed never stops the snapshot
            feeds[name] = {"ok": False, "count": 0, "note": f"{type(e).__name__}: {str(e)[:120]}", "ms": int((time.monotonic() - t0) * 1000)}
    signals.sort(key=lambda s: (SEV_RANK.get(s.get("severity"), 9), -_ts(s.get("at"))))
    return {"feeds": feeds, "signals": signals[:200], "summary": summary}


def push(server: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    r = httpx.post(server.rstrip("/") + "/api/newsdesk-worker/world-monitor", json=payload, timeout=30,
                   headers={"Authorization": f"Bearer {token}", "User-Agent": f"pricechat-worldmonitor/{VERSION}"})
    r.raise_for_status()
    return r.json()


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _stop(*_: Any) -> None:
    global STOP
    STOP = True


def main(argv: list[str] | None = None) -> int:
    load_env(Path(__file__).resolve().parent.parent / "worker.env")
    ap = argparse.ArgumentParser(prog="worldmonitor")
    ap.add_argument("--server", default=os.environ.get("PRICECHAT_SERVER", "https://api.pricechat.live"))
    ap.add_argument("--token", default=os.environ.get("PRICECHAT_WORKER_TOKEN"))
    ap.add_argument("--interval", type=float, default=float(os.environ.get("WM_BRIDGE_INTERVAL", INTERVAL_S)))
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="collect and print, don't send")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not a.token and not a.dry_run:
        print("No worker token (PRICECHAT_WORKER_TOKEN in worker.env, or --token)", file=sys.stderr)
        return 2
    install = find_install()
    if install is None:
        print("World Monitor isn't installed (no sidecar under %LOCALAPPDATA%\\World Monitor)", file=sys.stderr)
        return 2
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    side = Sidecar(install)
    version = app_version(install)
    log.info("World Monitor %s at %s; bridge %s -> %s", version or "?", install, VERSION, a.server)
    try:
        while not STOP:
            started = time.monotonic()
            if side.ensure():
                snap = collect(side.get)
                payload = {"app_version": version, "bridge_version": VERSION, "collected_at": datetime.now(timezone.utc).isoformat(), **snap}
                if a.dry_run:
                    import json

                    print(json.dumps({**payload, "signals": payload["signals"][:15]}, indent=1, default=str)[:6000])
                else:
                    try:
                        log.info("pushed: %s", push(a.server, a.token, payload))
                    except httpx.HTTPError as e:
                        log.warning("push failed: %s", e)
            if a.once or a.dry_run:
                break
            while not STOP and time.monotonic() - started < a.interval:
                time.sleep(1)
    finally:
        side.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
