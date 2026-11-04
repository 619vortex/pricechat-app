import { useCallback, useEffect, useRef, useState } from "react";
import { useExit } from "./exit";
import { type AccountView, type Answer, type Phase, type Sample, type Server, accountState, cancelSignIn, formatBytes, formatDuration, formatRate, hasTestConfig, openSite, phaseOf, ratesBetween, secondsUp, serverPlace, signIn, signOut, startTrial, vpnConnect, vpnConnectTestFile, vpnDisconnect, vpnStatus } from "./vpn";

const POLL_MS = 1000;
const ACCOUNT_MS = 60_000;
/** How long before saying the server hasn't answered. The tunnel keeps trying; the person decides. */
const SLOW_CONNECT_MS = 30_000;

const SEEDS = Array.from({ length: 26 }, (_, i) => (i * 360) / 26);
const RAYS = Array.from({ length: 52 }, (_, i) => (i * 360) / 52);

/** The mascot on its kiwi slice, from the website's hero. It shows the state the service reported, and nothing more. */
function Slice({ phase }: { phase: Phase }) {
  return (
    <div className={`slice is-${phase}`} aria-hidden>
      <svg viewBox="0 0 440 440" focusable="false">
        <defs>
          <radialGradient id="flesh"><stop offset="0" stopColor="#d8f57a" /><stop offset=".62" stopColor="#b1ea45" /><stop offset="1" stopColor="#84c51f" /></radialGradient>
        </defs>
        <circle className="slice-halo" cx="220" cy="220" r="216" />
        <g className="slice-fruit">
          <circle cx="220" cy="220" r="196" fill="#2a4a1c" />
          <circle cx="220" cy="220" r="186" fill="url(#flesh)" />
          {RAYS.map((a) => <path key={a} d="M220 158V48" stroke="#f1ffc4" strokeOpacity=".2" strokeWidth="2" strokeLinecap="round" transform={`rotate(${a} 220 220)`} />)}
          {SEEDS.map((a, i) => <ellipse key={a} cx="220" cy={i % 2 ? 106 : 122} rx="4.6" ry="9.6" fill="#14210c" transform={`rotate(${a} 220 220)`} />)}
          <circle cx="220" cy="220" r="68" fill="#f0fbc8" fillOpacity=".78" />
        </g>
      </svg>
      <img src="/kiwi.webp" alt="" />
    </div>
  );
}

/** A flag picture, or the country's two letters if there's no picture for it. */
function Flag({ code }: { code: string }) {
  const [broken, setBroken] = useState(false);
  if (broken || !/^[A-Za-z]{2}$/.test(code)) return <span className="flag is-code" aria-hidden>{code.toUpperCase()}</span>;
  return <span className="flag" aria-hidden><img src={`/flags/${code.toLowerCase()}.svg`} alt="" onError={() => setBroken(true)} /></span>;
}

const HEADLINE: Record<Phase, string> = {
  loading: "Checking",
  "no-service": "Service offline",
  off: "Not connected",
  connecting: "Connecting",
  on: "Connected",
  stopping: "Disconnecting",
  failed: "Couldn't connect",
};

/** The service's errors are short lower-case phrases. */
const sentence = (text: string) => `${text.charAt(0).toUpperCase()}${text.slice(1)}${/[.!?]$/.test(text) ? "" : "."}`;

function detail(phase: Phase, answer: Answer | null, slow: boolean): string {
  const age = answer?.status?.handshake_age_seconds ?? null;
  switch (phase) {
    case "loading": return "Asking the Kiwi VPN service.";
    case "no-service": return answer?.message ?? "The Kiwi VPN service isn't running.";
    case "off": return "Sites see your normal address.";
    case "connecting": return slow ? "The server hasn't answered yet. You can keep waiting or disconnect." : "Waiting for the server to answer.";
    case "on": return age === null ? "" : `The server answered ${age === 0 ? "just now" : `${age} s ago`}.`;
    case "stopping": return "Closing the tunnel.";
    case "failed": return sentence(answer?.status?.error ?? "The tunnel didn't start");
  }
}

export function App() {
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [view, setView] = useState<AccountView | null>(null);
  const [action, setAction] = useState<"connect" | "disconnect" | "sign-in" | "trial" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [rates, setRates] = useState({ rx: 0, tx: 0 });
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [chosen, setChosen] = useState<number | null>(null);
  const [settings, setSettings] = useState(false);
  const [testConfig, setTestConfig] = useState(false);
  const last = useRef<Sample | null>(null);

  const reported = phaseOf(answer);
  // A request in flight may say connecting or disconnecting. Nothing here can say connected: that only ever comes from the service's answer.
  const phase: Phase = action === "disconnect" ? "stopping" : action === "connect" && reported !== "on" ? "connecting" : reported;
  const { ip, checking, unchanged } = useExit(phase);

  const take = useCallback((next: Answer) => {
    setAnswer(next);
    setNow(Date.now());
    const seen = phaseOf(next);
    if (seen === "on" && next.status) {
      const sample = { at: Date.now(), rx: next.status.rx_bytes, tx: next.status.tx_bytes };
      setRates(ratesBetween(last.current, sample));
      last.current = sample;
    } else {
      last.current = null;
      setRates({ rx: 0, tx: 0 });
    }
    if (seen === "connecting") setStartedAt((s) => s ?? Date.now());
    else setStartedAt(null);
  }, []);

  const loadAccount = useCallback(async () => {
    try { setView(await accountState()); }
    catch (e) { setProblem(String(e)); }
  }, []);

  useEffect(() => {
    let alive = true;
    let timer = 0;
    const tick = async () => {
      try {
        const next = await vpnStatus();
        if (alive) take(next);
      } catch (e) {
        if (alive) take({ service: false, status: null, message: String(e) });
      }
      if (alive) timer = window.setTimeout(() => void tick(), POLL_MS);
    };
    void tick();
    void loadAccount();
    void hasTestConfig().then(setTestConfig).catch(() => setTestConfig(false));
    const refresh = window.setInterval(() => void loadAccount(), ACCOUNT_MS);
    return () => { alive = false; window.clearTimeout(timer); window.clearInterval(refresh); };
  }, [take, loadAccount]);

  const run = async (kind: NonNullable<typeof action>, work: () => Promise<void>) => {
    setProblem(null);
    setAction(kind);
    try { await work(); }
    catch (e) { setProblem(String(e)); }
    finally { setAction(null); }
  };
  const connect = (serverId: number) => run("connect", async () => { take(await vpnConnect(serverId)); await loadAccount(); });
  const disconnect = () => run("disconnect", async () => { take(await vpnDisconnect()); });
  const login = () => run("sign-in", async () => { await signIn(); await loadAccount(); });
  const trial = () => run("trial", async () => { await startTrial(); await loadAccount(); });
  const logout = () => run("disconnect", async () => { await signOut(); setChosen(null); setSettings(false); await loadAccount(); });
  const fromFile = () => run("connect", async () => { const next = await vpnConnectTestFile(); if (next) { take(next); setSettings(false); } });

  const account = view?.account ?? null;
  const servers: Server[] = account?.servers ?? [];
  const picked = chosen ?? view?.suggested ?? null;
  const shown = servers.find((s) => s.id === (phase === "on" || phase === "connecting" ? view?.last_server ?? picked : picked)) ?? null;

  const slow = phase === "connecting" && startedAt !== null && now - startedAt > SLOW_CONNECT_MS;
  const live = phase === "on";
  const status = answer?.status ?? null;
  const up = live ? secondsUp(status?.connected_since, now) : null;
  const showsAddress = live || phase === "off";
  const pending = checking ? "Checking" : "Couldn't check";
  // Disconnect stays available while the tunnel exists, including while it's still trying to reach the server.
  const tunnelUp = reported === "on" || reported === "connecting";
  const offline = phase === "no-service" || phase === "loading";
  const signedOut = view !== null && !view.signed_in;
  const locked = !!account && !account.eligible;

  let button: { label: string; run?: () => void; off?: boolean; quiet?: boolean };
  if (phase === "stopping") button = { label: "Disconnecting", off: true };
  else if (action === "connect") button = { label: "Connecting", off: true };
  else if (tunnelUp) button = { label: "Disconnect", run: () => void disconnect(), quiet: true };
  else if (offline || view === null) button = { label: "Connect", off: true };
  else if (signedOut) button = action === "sign-in" ? { label: "Cancel", run: () => void cancelSignIn(), quiet: true } : { label: "Sign in with Price Chat", run: () => void login() };
  else if (locked && account?.trial?.available) button = { label: action === "trial" ? "Starting" : `Start free trial: ${account.trial.days} days`, off: action === "trial", run: () => void trial() };
  else if (locked || !picked) button = { label: "Connect", off: true };
  else button = { label: phase === "failed" ? "Try again" : "Connect", run: () => void connect(picked) };

  const note = signedOut && !offline ? (action === "sign-in" ? "Finish signing in in your browser." : "Sign in to choose a country and connect.")
    : locked && !account?.trial?.available ? account?.reason ?? "Your account doesn't include Kiwi VPN right now."
    : view?.error ?? null;

  if (settings) {
    return (
      <main className="app is-settings">
        <header className="bar">
          <img src="/kiwi.webp" alt="" />
          <span className="brand">Settings</span>
          <button type="button" className="link" onClick={() => setSettings(false)}>Back</button>
        </header>
        <section className="settings">
          {view?.signed_in && <button type="button" className="row" onClick={() => void logout()}>Sign out of Kiwi VPN</button>}
          <button type="button" className="row" onClick={() => void openSite("devices")}>My Devices on pricechat.live</button>
          <details className="advanced">
            <summary>Advanced</summary>
            {testConfig && (
              <div>
                <p>For testing only. Connect with a WireGuard config file instead of your account.</p>
                <button type="button" className="row" disabled={action !== null || offline} onClick={() => void fromFile()}>Use a config file</button>
              </div>
            )}
          </details>
          {problem && <p className="problem" role="alert">{problem}</p>}
        </section>
      </main>
    );
  }

  return (
    <main className={`app is-${phase}`}>
      <header className="bar">
        <img src="/kiwi.webp" alt="" />
        <span className="brand">Kiwi VPN</span>
        <span className="tag">Beta</span>
        <button type="button" className="link" onClick={() => setSettings(true)}>Settings</button>
      </header>

      <section className={`stage${unchanged ? " is-warned" : ""}`}>
        <Slice phase={phase} />
        <h1 aria-live="polite">{HEADLINE[phase]}</h1>
        <p className="detail" role={phase === "failed" || phase === "no-service" ? "alert" : "status"}>{note && !tunnelUp && !offline ? note : detail(phase, answer, slow)}</p>
        {problem && <p className="problem" role="alert">{problem}</p>}
        {unchanged && <p className="problem" role="alert">Your public IP is the same as before connecting, so traffic may not be leaving through the server.</p>}
        {locked && !account?.trial?.available && <button type="button" className="link" onClick={() => void openSite("plans")}>See plans</button>}
        {problem?.includes("My Devices") && <button type="button" className="link" onClick={() => void openSite("devices")}>Open My Devices</button>}
      </section>

      {view?.signed_in && servers.length > 0 && !locked && (
        <div className="picker" role="radiogroup" aria-label="Country">
          {servers.map((s) => (
            <button key={s.id} type="button" role="radio" aria-checked={picked === s.id} className={picked === s.id ? "is-picked" : ""}
                    disabled={!s.online || action !== null || phase === "stopping" || offline} onClick={() => {
              if (picked === s.id) return;
              setChosen(s.id);
              if (tunnelUp) void connect(s.id);  // switching: the service closes the old tunnel first
            }}>
              <Flag code={s.country_code} />
              <span>{serverPlace(s)}{s.online ? "" : " (offline)"}</span>
            </button>
          ))}
        </div>
      )}

      <dl className="route">
        <div>
          <dt>Server location</dt>
          <dd className="place">{live && shown ? <><Flag code={shown.country_code} /><span>{serverPlace(shown)}</span></> : "–"}</dd>
        </div>
        <div>
          <dt>Public IP</dt>
          <dd className="ip">{!showsAddress ? "–" : ip ?? pending}</dd>
          <dd className="sub">{showsAddress && ip ? (live ? "what sites see now" : "your normal address") : ""}</dd>
        </div>
      </dl>

      <section className="controls">
        <button type="button" className={`go${button.quiet ? " is-stop" : ""}`} disabled={button.off} onClick={button.run}>{button.label}</button>
        <dl className="stats" aria-label="Connection statistics">
          <div><dt>Download</dt><dd>{live ? formatRate(rates.rx) : "–"}</dd><dd className="sub">{live && status ? `${formatBytes(status.rx_bytes)} total` : ""}</dd></div>
          <div><dt>Upload</dt><dd>{live ? formatRate(rates.tx) : "–"}</dd><dd className="sub">{live && status ? `${formatBytes(status.tx_bytes)} total` : ""}</dd></div>
          <div><dt>Time</dt><dd>{up !== null ? formatDuration(up) : "–"}</dd><dd className="sub">{up !== null ? "since handshake" : ""}</dd></div>
        </dl>
      </section>
    </main>
  );
}
