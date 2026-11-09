import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { loadProviders, memberApi, signInUrl } from "./members";

/** Sign-in / connect buttons for Google, X and Telegram (Kiwi Passport). Shared by the sign-in window, the join page,
 * the Passport and missions. */

const ICONS = {
  youtube: "M22.5 7.2a2.8 2.8 0 0 0-2-2C18.8 4.7 12 4.7 12 4.7s-6.8 0-8.5.5a2.8 2.8 0 0 0-2 2C1 8.9 1 12 1 12s0 3.1.5 4.8a2.8 2.8 0 0 0 2 2c1.7.5 8.5.5 8.5.5s6.8 0 8.5-.5a2.8 2.8 0 0 0 2-2c.5-1.7.5-4.8.5-4.8s0-3.1-.5-4.8ZM9.8 15.1V8.9l5.7 3.1-5.7 3.1Z",
  website: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm0 3.5a6.5 6.5 0 1 1 0 13 6.5 6.5 0 0 1 0-13Z",
  google: "M21.35 11.1H12v2.98h5.35c-.5 2.4-2.6 3.92-5.35 3.92a6 6 0 1 1 0-12c1.5 0 2.86.55 3.9 1.45l2.2-2.2A9.1 9.1 0 0 0 12 2.9a9.1 9.1 0 1 0 0 18.2c4.7 0 8.7-3.4 8.7-9.1 0-.6-.1-1.2-.15-1.9Z",
  x: "M17.8 3h3.1l-6.8 7.7L22 21h-6.2l-4.9-6.4L5.3 21H2.2l7.3-8.3L1.8 3h6.4l4.4 5.8L17.8 3Zm-1.1 16.2h1.7L7.4 4.7H5.6l11.1 14.5Z",
  telegram: "M21.4 3.6 2.9 10.7c-1.3.5-1.2 1.2-.2 1.5l4.7 1.5 1.8 5.6c.2.6.4.8.9.8.4 0 .6-.2.9-.5l2.3-2.2 4.7 3.5c.9.5 1.5.2 1.7-.8l3.1-14.6c.3-1.3-.5-1.9-1.4-1.5ZM9.6 14.3l-.4 4-1.5-4.9 10.2-6.5-8.3 7.4Z",
};

export function PIcon({ p }: { p: keyof typeof ICONS }) {
  return (
    <svg viewBox="0 0 24 24" className="site-icon" aria-hidden>
      <path d={ICONS[p]} />
    </svg>
  );
}

// --- Telegram ------------------------------------------------------------------------------------------------------

const TG_KEY = "pc-tg-signin";
const TG_TTL_MS = 4 * 60_000;  // the server's attempt lives 5 minutes

interface TgFlow { request: number; link: string; at: number; purpose: "login" | "link" }

function savedFlow(purpose: string): TgFlow | null {
  try {
    const f = JSON.parse(sessionStorage.getItem(TG_KEY) ?? "null") as TgFlow | null;
    return f && f.purpose === purpose && Date.now() - f.at < TG_TTL_MS ? f : null;
  } catch {
    return null;
  }
}

/**
 * Telegram: one tap opens the Price Chat bot with a one-time code; the member presses Start and taps "Yes, sign me in" under the bot's question; the bot confirms in
 * Telegram and this page continues by itself (it keeps checking, also after coming back from the Telegram app or a reload).
 * The link is prepared before the tap, so Telegram opens in the tap itself (mobile browsers block late pop-ups).
 */
export function TelegramButton({ purpose, available, onDone, label, className = "site-provider" }: {
  purpose: "login" | "link"; available: boolean; onDone: () => void; label: ReactNode; className?: string;
}) {
  const [flow, setFlow] = useState<TgFlow | null>(() => savedFlow(purpose));
  const [ready, setReady] = useState<TgFlow | null>(null);
  const [status, setStatus] = useState<"idle" | "waiting" | "expired" | "error">(() => (savedFlow(purpose) ? "waiting" : "idle"));
  const [error, setError] = useState("");
  const done = useRef(onDone);
  done.current = onDone;

  const prepare = useCallback(async (): Promise<TgFlow | null> => {
    try {
      const r = await memberApi<{ request: number; link: string }>("/members/auth/telegram/start", { method: "POST", body: JSON.stringify({ purpose }) });
      const f = { ...r, at: Date.now(), purpose };
      setReady(f);
      return f;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Telegram sign-in is not available right now.");
      return null;
    }
  }, [purpose]);
  useEffect(() => { if (available && !flow) void prepare(); }, [available, flow, prepare]);

  useEffect(() => {
    if (!flow) return;
    let alive = true;
    let timer = 0;
    const tick = async () => {
      try {
        const r = await memberApi<{ status: string; error?: string }>(`/members/auth/telegram/status/${flow.request}`);
        if (!alive) return;
        if (r.status === "done") {
          try { sessionStorage.removeItem(TG_KEY); } catch { /* fine */ }
          done.current();
        } else if (r.status === "pending") timer = window.setTimeout(tick, 2000);
        else {
          try { sessionStorage.removeItem(TG_KEY); } catch { /* fine */ }
          setFlow(null);
          setStatus(r.status === "expired" ? "expired" : "error");
          setError(r.error ?? (r.status === "expired" ? "" : "That sign-in can no longer be used. Please start again."));
        }
      } catch {
        if (alive) timer = window.setTimeout(tick, 4000);
      }
    };
    timer = window.setTimeout(tick, 1500);
    const back = () => { if (document.visibilityState === "visible") { window.clearTimeout(timer); void tick(); } };  // straight back from the Telegram app
    document.addEventListener("visibilitychange", back);
    return () => {
      alive = false;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", back);
    };
  }, [flow]);

  function begin(f: TgFlow) {
    setFlow(f);
    setStatus("waiting");
    setError("");
    try { sessionStorage.setItem(TG_KEY, JSON.stringify(f)); } catch { /* fine */ }
  }
  async function start() {
    const fresh = ready && Date.now() - ready.at < TG_TTL_MS ? ready : null;
    if (fresh) {
      window.open(fresh.link, "_blank", "noopener");  // in the tap itself: never blocked
      begin(fresh);
      setReady(null);
      return;
    }
    const w = window.open("about:blank", "_blank");  // reserve the tab in the tap, fill it once the link exists
    const f = await prepare();
    if (!f) {
      w?.close();
      setStatus("error");
      return;
    }
    if (w) w.location.href = f.link;
    begin(f);
    setReady(null);
  }
  function cancel() {
    try { sessionStorage.removeItem(TG_KEY); } catch { /* fine */ }
    setFlow(null);
    setStatus("idle");
  }
  return (
    <div className="site-provider-wrap">
      <button type="button" className={className} onClick={() => void start()} disabled={!available || status === "waiting"}>
        {status === "waiting" ? <><span className="tg-spin" aria-hidden /> Waiting for Telegram…</> : label}
      </button>
      {status === "waiting" && flow && (
        <div className="site-provider-note tg-steps" role="status">
          <ol>
            <li>Telegram opens the <strong>Price Chat</strong> bot.</li>
            <li>Tap <strong>Start</strong>, then <strong>Yes, sign me in</strong> (or <strong>Yes, connect it</strong>). The bot replies “✅ Done!”.</li>
            <li>Come back here: you're signed in automatically.</li>
          </ol>
          <a className="site-link" href={flow.link} target="_blank" rel="noopener noreferrer">Telegram didn't open? Tap here</a>
          {" · "}<button type="button" className="site-textbutton" onClick={cancel}>Cancel</button>
        </div>
      )}
      {status === "expired" && <p className="site-provider-note">That link expired. Tap the button again.</p>}
      {status === "error" && error && <p className="site-provider-note site-error">{error}</p>}
      {!available && <p className="site-provider-note">Telegram sign-in isn't available right now.</p>}
    </div>
  );
}

// --- Google / X ---------------------------------------------------------------------------------------------------------

const CHECK_TIMEOUT_MS = 10_000;
const LEAVE_TIMEOUT_MS = 15_000;

function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const t = window.setTimeout(() => reject(new Error("timeout")), ms);
    p.then(resolve, reject).finally(() => window.clearTimeout(t));
  });
}

/**
 * Connect Google / X. Checks first that the option works and says so if it doesn't: never a silent click.
 * The browser only ever goes to Price Chat's own start endpoint; the provider URL and callback are built server-side.
 */
export function ConnectButton({ provider, purpose = "link", label, className = "site-button pp-cta" }: {
  provider: "google" | "x"; purpose?: "login" | "link"; label: ReactNode; className?: string;
}) {
  const [state, setState] = useState<"idle" | "checking" | "leaving" | "failed">("idle");
  const [reason, setReason] = useState("");
  const name = provider === "x" ? "X" : "Google";
  const fail = (why: string) => {
    setReason(why);
    setState("failed");
  };
  // Never stay on "Opening…": the member may come Back from the provider's page (the browser restores this page
  // as it was, from its back/forward cache), or the navigation may never start.
  useEffect(() => {
    if (state !== "leaving") return;
    const back = (e: PageTransitionEvent) => {
      if (e.persisted) fail(`${name} didn't finish connecting, so nothing was linked. You can try again.`);
    };
    const stuck = window.setTimeout(() => fail(`${name} didn't open. Please try again.`), LEAVE_TIMEOUT_MS);
    window.addEventListener("pageshow", back);
    return () => {
      window.clearTimeout(stuck);
      window.removeEventListener("pageshow", back);
    };
  }, [state, name]);
  async function go() {
    setState("checking");
    try {
      const info = await withTimeout(loadProviders(true), CHECK_TIMEOUT_MS).then((p) => p[provider]);
      if (!info.available) {
        fail(`The Price Chat team hasn't finished setting up ${name} sign-in yet, so it can't be connected right now.`); // setup details stay in the dashboard
        return;
      }
      setState("leaving");
      location.assign(signInUrl(provider, purpose));
    } catch {
      fail("Price Chat didn't answer. Check your connection and try again.");
    }
  }
  return (
    <span className="pp-connect">
      <button type="button" className={className} disabled={state === "checking" || state === "leaving"} onClick={() => void go()}>
        {state === "leaving" ? `Opening ${name}…` : state === "checking" ? "Checking…" : label}
      </button>
      {provider === "x" && typeof navigator !== "undefined" && /Android/i.test(navigator.userAgent) && (
        <span className="pp-connect-tip">On Android, if the X app opens and says “You weren't able to give access to the App”: open phone
          <b> Settings → Apps → X → Open by default</b>, switch off <b>Open supported links</b>, then try again. X then asks in your browser, which works.</span>
      )}
      {state === "failed" && (
        <span className="pp-connect-error" role="alert">
          <strong>Couldn't connect {name}.</strong> {reason}{" "}
          <button type="button" className="site-textbutton" onClick={() => void go()}>Try again</button>
        </span>
      )}
    </span>
  );
}


/** Sign in (or create a Passport) with Telegram, X or Google: the buttons the sign-in window and join page show. */
export function SocialSignIn({ onTelegramDone }: { onTelegramDone: () => void }) {
  const [p, setP] = useState<Awaited<ReturnType<typeof loadProviders>> | null>(null);
  useEffect(() => { void loadProviders(true).then(setP).catch(() => setP(null)); }, []);
  if (!p) return <p className="lg-info">Loading…</p>;
  const any = p.telegram.available || p.x.available || p.google.available;
  if (!any) return null;
  return (
    <div className="lg-social">
      {p.telegram.available && <TelegramButton purpose="login" available onDone={onTelegramDone} label={<><PIcon p="telegram" /> Continue with Telegram</>} />}
      {p.x.available && <ConnectButton provider="x" purpose="login" className="site-provider" label={<><PIcon p="x" /> Continue with X</>} />}
      {p.google.available && <ConnectButton provider="google" purpose="login" className="site-provider" label={<><PIcon p="google" /> Continue with Google</>} />}
    </div>
  );
}
