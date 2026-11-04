import { useCallback, useEffect, useRef, useState } from "react";
import { placeLine, useExit } from "./exit";
import { type Answer, type Phase, type Sample, formatBytes, formatDuration, formatRate, isTestBuild, phaseOf, ratesBetween, secondsUp, vpnConnectTestFile, vpnDisconnect, vpnStatus } from "./vpn";

const POLL_MS = 1000;
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
  if (broken) return <span className="flag is-code" aria-hidden>{code}</span>;
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

function detail(phase: Phase, answer: Answer | null, slow: boolean, testBuild: boolean): string {
  const age = answer?.status?.handshake_age_seconds ?? null;
  switch (phase) {
    case "loading": return "Asking the Kiwi VPN service.";
    case "no-service": return answer?.message ?? "The Kiwi VPN service isn't running.";
    case "off": return testBuild ? "Sites see your normal address. For now, this beta needs a config file from My Devices." : "Sites see your normal address.";
    case "connecting": return slow ? "The server hasn't answered yet. You can keep waiting or disconnect." : "Waiting for the server to answer.";
    case "on": return age === null ? "" : `The server answered ${age === 0 ? "just now" : `${age} s ago`}.`;
    case "stopping": return "Closing the tunnel.";
    case "failed": return sentence(answer?.status?.error ?? "The tunnel didn't start");
  }
}

export function App() {
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [testBuild, setTestBuild] = useState(false);
  const [action, setAction] = useState<"connect" | "disconnect" | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [rates, setRates] = useState({ rx: 0, tx: 0 });
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const last = useRef<Sample | null>(null);

  const reported = phaseOf(answer);
  // A request in flight may say disconnecting. Nothing here can say connected: that only ever comes from the service's answer.
  const phase: Phase = action === "disconnect" ? "stopping" : reported;
  const { exit, checking } = useExit(phase);

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

  useEffect(() => {
    void isTestBuild().then(setTestBuild).catch(() => setTestBuild(false));
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
    return () => { alive = false; window.clearTimeout(timer); };
  }, [take]);

  async function connect() {
    setProblem(null);
    setAction("connect");
    try {
      const next = await vpnConnectTestFile();
      if (next) take(next);
    } catch (e) { setProblem(String(e)); }
    finally { setAction(null); }
  }

  async function disconnect() {
    setProblem(null);
    setAction("disconnect");
    try { take(await vpnDisconnect()); }
    catch (e) { setProblem(String(e)); }
    finally { setAction(null); }
  }

  const slow = phase === "connecting" && startedAt !== null && now - startedAt > SLOW_CONNECT_MS;
  const live = phase === "on";
  const status = answer?.status ?? null;
  const up = live ? secondsUp(status?.connected_since, now) : null;
  const pending = checking ? "Checking" : "Couldn't check";
  const place = exit ? placeLine(exit) : null;
  // Disconnect stays available while the tunnel exists, including while it's still trying to reach the server.
  const tunnelUp = reported === "on" || reported === "connecting";

  let button: { label: string; run?: () => void; off?: boolean };
  if (phase === "stopping") button = { label: "Disconnecting", off: true };
  else if (action === "connect") button = { label: "Choosing a config", off: true };
  else if (tunnelUp) button = { label: "Disconnect", run: () => void disconnect() };
  else if (phase === "loading" || phase === "no-service") button = { label: "Connect", off: true };
  else if (!testBuild) button = { label: "Sign in to connect", off: true };
  else button = { label: phase === "failed" ? "Try again" : "Connect", run: () => void connect() };

  return (
    <main className={`app is-${phase}`}>
      <header className="bar">
        <img src="/kiwi.webp" alt="" />
        <span className="brand">Kiwi VPN</span>
        {testBuild && <span className="tag">Beta</span>}
      </header>

      <section className="stage">
        <Slice phase={phase} />
        <h1 aria-live="polite">{HEADLINE[phase]}</h1>
        <p className="detail" role={phase === "failed" || phase === "no-service" ? "alert" : "status"}>{detail(phase, answer, slow, testBuild)}</p>
        {problem && <p className="problem" role="alert">{problem}</p>}
      </section>

      <dl className="route">
        <div>
          <dt>Server location</dt>
          <dd className="place">
            {!live ? "–" : place ? <>{exit?.country && <Flag code={exit.country} />}<span>{place}</span></> : pending}
          </dd>
          <dd className="sub">{live && place ? "from an IP lookup" : ""}</dd>
        </div>
        <div>
          <dt>Public IP</dt>
          <dd className="ip">{!live ? "–" : exit ? exit.ip : pending}</dd>
          <dd className="sub">{live && exit ? "what sites see now" : ""}</dd>
        </div>
      </dl>

      <section className="controls">
        <button type="button" className={`go${tunnelUp ? " is-stop" : ""}`} disabled={button.off} onClick={button.run}>{button.label}</button>
        <dl className="stats" aria-label="Connection statistics">
          <div><dt>Download</dt><dd>{live ? formatRate(rates.rx) : "–"}</dd><dd className="sub">{live && status ? `${formatBytes(status.rx_bytes)} total` : ""}</dd></div>
          <div><dt>Upload</dt><dd>{live ? formatRate(rates.tx) : "–"}</dd><dd className="sub">{live && status ? `${formatBytes(status.tx_bytes)} total` : ""}</dd></div>
          <div><dt>Time</dt><dd>{up !== null ? formatDuration(up) : "–"}</dd><dd className="sub">{up !== null ? "since handshake" : ""}</dd></div>
        </dl>
      </section>
    </main>
  );
}
