import { useEffect, useRef, useState } from "react";
import { myIp, type Phase } from "./vpn";

/**
 * The public address, from Price Chat's own endpoint: no outside service learns anything. Asked each time the connection changes, and compared, so the window can warn if connecting
 * didn't change it. An answer that arrives after the connection has moved on is thrown away, so an old address can't be shown as the tunnel's.
 */

const RETRY_MS = 3000;
const TRIES = 4;
/** The tunnel's routes settle just after the first handshake. */
const SETTLE_MS = 1500;

const wait = (ms: number, signal: AbortSignal) => new Promise<void>((done) => {
  if (signal.aborted) return done();
  const timer = setTimeout(done, ms);
  signal.addEventListener("abort", () => { clearTimeout(timer); done(); }, { once: true });
});

export interface ExitView { ip: string | null; checking: boolean; unchanged: boolean }

export function useExit(phase: Phase): ExitView {
  const [ip, setIp] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const normal = useRef<string | null>(null);

  useEffect(() => {
    setIp(null);
    if (phase !== "on" && phase !== "off") { setChecking(false); return; }
    const control = new AbortController();
    setChecking(true);
    void (async () => {
      if (phase === "on") await wait(SETTLE_MS, control.signal);
      for (let i = 0; i < TRIES && !control.signal.aborted; i++) {
        const found = await myIp().catch(() => null);
        if (control.signal.aborted) return;
        if (found) {
          if (phase === "off") normal.current = found;
          setIp(found);
          setChecking(false);
          return;
        }
        await wait(RETRY_MS, control.signal);
      }
      if (!control.signal.aborted) setChecking(false);
    })();
    return () => control.abort();
  }, [phase]);

  return { ip, checking, unchanged: phase === "on" && ip !== null && ip === normal.current };
}
