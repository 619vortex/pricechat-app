import { useEffect, useRef, useState } from "react";
import type { Phase } from "./vpn";

/**
 * What the outside world sees: the public address and where it's placed, asked of an outside service each time the connection changes. It's the app's check that traffic
 * really leaves through the tunnel. Until sign-in brings the server list (milestone 4), it's also the only source of the server's location, so the window labels it as a lookup.
 * Those services see the person's address, which the release checklist covers. Each answer is treated as untrusted text. The sites must also be in tauri.conf.json's CSP.
 */

export const LOOKUPS = ["https://ipinfo.io/json", "https://ipapi.co/json/"];
const TIMEOUT_MS = 8000;
const RETRY_MS = 3000;
const TRIES = 4;
/** The tunnel's routes settle just after the first handshake. */
const SETTLE_MS = 1500;

export interface Exit { ip: string; country: string | null; city: string | null }

const IPV4 = /^(\d{1,3}\.){3}\d{1,3}$/;
const IPV6 = /^[0-9a-fA-F:]{2,39}$/;

export function readLookup(body: unknown): Exit | null {
  if (typeof body !== "object" || body === null) return null;
  const { ip, country, city } = body as Record<string, unknown>;
  if (typeof ip !== "string" || !(IPV4.test(ip) || (ip.includes(":") && IPV6.test(ip)))) return null;
  const code = typeof country === "string" && /^[A-Za-z]{2}$/.test(country) ? country.toUpperCase() : null;
  const town = typeof city === "string" && city.trim() && city.length <= 64 ? city.trim() : null;
  return { ip, country: code, city: town };
}

export function countryName(code: string): string {
  try { return new Intl.DisplayNames(["en"], { type: "region" }).of(code) ?? code; } catch { return code; }
}

/** "Falkenstein, Germany", or as much of it as the lookup knew. */
export function placeLine(exit: Exit): string | null {
  const country = exit.country ? countryName(exit.country) : null;
  return [exit.city, country].filter(Boolean).join(", ") || null;
}

async function lookup(signal: AbortSignal): Promise<Exit | null> {
  for (const url of LOOKUPS) {
    const limit = new AbortController();
    const stop = () => limit.abort();
    signal.addEventListener("abort", stop);
    const timer = setTimeout(stop, TIMEOUT_MS);
    try {
      const reply = await fetch(url, { cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer", signal: limit.signal });
      if (reply.ok) {
        const found = readLookup(await reply.json());
        if (found) return found;
      }
    } catch { /* try the next one */ } finally {
      clearTimeout(timer);
      signal.removeEventListener("abort", stop);
    }
    if (signal.aborted) return null;
  }
  return null;
}

const wait = (ms: number, signal: AbortSignal) => new Promise<void>((done) => {
  if (signal.aborted) return done();
  const timer = setTimeout(done, ms);
  signal.addEventListener("abort", () => { clearTimeout(timer); done(); }, { once: true });
});

export interface ExitView { exit: Exit | null; checking: boolean; unchanged: boolean }

/** Starts over whenever the phase changes, and an answer that arrives after the phase has moved on is thrown away, so an old address can't be shown as the tunnel's. */
export function useExit(phase: Phase): ExitView {
  const [exit, setExit] = useState<Exit | null>(null);
  const [checking, setChecking] = useState(false);
  const normal = useRef<string | null>(null);

  useEffect(() => {
    setExit(null);
    if (phase !== "on" && phase !== "off") { setChecking(false); return; }
    const control = new AbortController();
    setChecking(true);
    void (async () => {
      if (phase === "on") await wait(SETTLE_MS, control.signal);
      for (let i = 0; i < TRIES && !control.signal.aborted; i++) {
        const found = await lookup(control.signal);
        if (control.signal.aborted) return;
        if (found) {
          if (phase === "off") normal.current = found.ip;
          setExit(found);
          setChecking(false);
          return;
        }
        await wait(RETRY_MS, control.signal);
      }
      if (!control.signal.aborted) setChecking(false);
    })();
    return () => control.abort();
  }, [phase]);

  return { exit, checking, unchanged: phase === "on" && exit !== null && exit.ip === normal.current };
}
