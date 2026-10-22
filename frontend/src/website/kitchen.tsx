import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BRAIN_PATH } from "./kiwibutt";
import { memberApi } from "./members";
import "./kitchen.css";

/** Kiwi Kitchen: a small chef Kiwi beside the butt Kiwi on Earn. What it does is the strategy engine's REAL state for each
 *  market, streamed from the server (the same kb_events rows the Kiwi Brain lists): SEARCHING (no setup), COOKING (a 15m
 *  candidate waiting for its 30m confirmation), READY (every check passed, a proposal), BURNT (a check failed: the real
 *  reason). No timers, no random thinking: if nothing arrives, the Kiwi keeps doing what the last real event said. */

export type KState = "SEARCHING" | "COOKING" | "READY" | "BURNT";
export interface Check { id: string; label: string; status: "pass" | "fail" | "pending" | "skip"; detail: string }
export interface KMarket {
  symbol: string; timeframe: string; confirm_timeframe: string; state: KState; reason: string; at?: string; candle_t?: number | null;
  strength?: number | null; checks: Check[]; passed: number; total: number; risk?: Check[]; shadow?: boolean;
  indicators?: Record<string, number | string | null>;
  market?: { mark: number | null; funding_h: number | null; oi_usd: number | null; spread_bps: number | null; day_volume: number | null;
             change_24h?: number | null; regime?: string | null };
  setup?: { label: string; side: "LONG" | "SHORT"; ref: number; stop: number; tp1: number; tp2: number; max_hold_min: number; reasons: string[]; cost_r: number;
           ml_p?: number | null } | null;
  risk_team?: RiskTeam | null;
  proposal?: { signal_id: number | null; number: number | null; side: string; entry: number | number[]; stop: number; target: number;
               status: string; approval_required: boolean; shadow: boolean } | null;
}
export interface KEvent extends Partial<KMarket> { id: number; at: string; title: string; symbol: string }
/** The Kiwi Risk Team's review (backend riskteam.py): rule-based desks decide, the AI Chief Risk Officer only writes the memo. */
export interface RiskTeam { verdict: "APPROVE" | "VETO"; reason: string;
  votes: { desk: string; label: string; vote: "approve" | "veto" | "warn"; detail: string }[]; memo?: { text: string; by: string } | null }
type MlRecord = { trades: number | null; win_rate: number | null; expectancy_r: number | null; trades_per_day: number | null };
/** Kiwi ML: the model's state and how it did on history it never saw (backend ml.py). */
export interface KMl { mode: string; filtering: boolean; validated: boolean; profitable: boolean; threshold: number; trained_at: string;
  period: string[]; source: string; samples: number; auc: number | null; without_ml: MlRecord; with_ml: MlRecord }
export interface KSnapshot { strategy: string; label: string; timeframe: string; confirm_timeframe: string; shadow: boolean; live: boolean;
  paused: boolean; markets: KMarket[]; events: KEvent[]; last_id: number; ml?: KMl | null }

const ORDER: Record<KState, number> = { READY: 0, COOKING: 1, BURNT: 2, SEARCHING: 3 };
const TITLE: Record<KState, string> = { SEARCHING: "Searching", COOKING: "Cooking", READY: "Order ready", BURNT: "Burnt" };
const px = (n: number | null | undefined) => n == null ? "—" : n.toLocaleString("en-US", { maximumSignificantDigits: 6 });

/** The kitchen's live state: one snapshot, then Server-Sent Events (the browser reconnects by itself and resends the last id).
 *  Without EventSource (old browsers, tests) it re-reads the snapshot every 30 s instead. */
export function useKitchen(): { snap: KSnapshot | null; live: boolean; error: boolean } {
  const [snap, setSnap] = useState<KSnapshot | null>(null);
  const [live, setLive] = useState(false);
  const [error, setError] = useState(false);
  const apply = useCallback((e: KEvent) => {
    setSnap((s) => {
      if (!s || s.events.some((x) => x.id === e.id)) return s;
      const markets = s.markets.map((m) => (m.symbol === e.symbol && e.state ? { ...m, ...e } as KMarket : m));
      return { ...s, markets, events: [...s.events, e].slice(-20), last_id: Math.max(s.last_id, e.id) };
    });
  }, []);
  useEffect(() => {
    let es: EventSource | null = null;
    let poll: number | undefined;
    let gone = false;
    const load = () => memberApi<KSnapshot>("/members/trading-bot/kitchen").then((s) => { if (!gone) { setSnap(s); setError(false); } return s; });
    load().then((s) => {
      if (gone) return;
      if (typeof EventSource === "undefined") {
        poll = window.setInterval(() => void load().catch(() => setError(true)), 30_000);
        return;
      }
      es = new EventSource(`/api/members/trading-bot/kitchen/stream?after=${s.last_id}`, { withCredentials: true });
      es.addEventListener("open", () => setLive(true));
      es.addEventListener("kitchen", (m) => { try { apply(JSON.parse((m as MessageEvent).data) as KEvent); } catch { /* a malformed event is skipped */ } });
      es.addEventListener("error", () => {
        setLive(false);
        if (es && es.readyState === EventSource.CLOSED) {  // signed out, or the server refused: fall back to reading the snapshot
          es = null;
          poll = window.setInterval(() => void load().catch(() => setError(true)), 30_000);
        }
      });
    }).catch(() => setError(true));
    return () => { gone = true; es?.close(); if (poll) window.clearInterval(poll); };
  }, [apply]);
  return { snap, live, error };
}

/** The chef: black Kiwi, white belly, big green eye, emerald chef hat band, and a prop set per state (pure SVG + CSS). */
export function KiwiChef({ state, size = 150 }: { state: KState; size?: number }) {
  return (
    <svg className={`kk-svg kk-${state.toLowerCase()}`} viewBox="0 0 200 160" width={size} height={size * 0.8} aria-hidden>
      <ellipse cx="100" cy="150" rx="78" ry="7" className="kk-floor" />
      {/* SEARCHING: fridge with a swinging door, a shelf of jars */}
      <g className="kk-p kk-p-search">
        <rect x="8" y="40" width="44" height="106" rx="6" className="kk-fridge" />
        <rect x="11" y="44" width="38" height="98" rx="3" className="kk-fridge-in" />
        <path d="M14 70 h32 M14 96 h32 M14 122 h32" className="kk-shelf" />
        <circle cx="22" cy="64" r="4" className="kk-jar" /><rect x="32" y="58" width="8" height="11" rx="2" className="kk-jar g" />
        <g className="kk-door"><rect x="8" y="40" width="44" height="106" rx="6" className="kk-fridge-door" /><rect x="44" y="80" width="3" height="18" rx="1.5" className="kk-handle" /></g>
        <g className="kk-mag"><circle cx="150" cy="58" r="10" className="kk-glass" /><path d="M157 66 l9 10" className="kk-glass-h" /></g>
        <text x="168" y="40" className="kk-q">?</text>
      </g>
      {/* COOKING: stove, pot with steam, board with a carrot and a chopping knife */}
      <g className="kk-p kk-p-cook">
        <rect x="128" y="112" width="64" height="34" rx="4" className="kk-stove" />
        <path d="M134 112 h52 v-18 a6 6 0 0 0 -6 -6 h-40 a6 6 0 0 0 -6 6 z" className="kk-pot" />
        <path d="M150 80 q-6 -10 0 -18 q6 -8 0 -16 M166 80 q-6 -10 0 -18 q6 -8 0 -16" className="kk-steam" />
        <g className="kk-spoon"><path d="M160 98 l12 -30" className="kk-spoon-s" /><ellipse cx="173" cy="66" rx="3" ry="5" className="kk-spoon-s f" /></g>
        <rect x="10" y="124" width="52" height="8" rx="3" className="kk-board" />
        <path d="M18 122 l26 -4 l4 3 l-26 4 z" className="kk-carrot" /><path d="M44 117 l6 -4 M46 120 l7 -2" className="kk-leaf" />
        <g className="kk-knife"><path d="M50 96 l14 -2 l-2 22 z" className="kk-blade" /><rect x="56" y="84" width="5" height="12" rx="2" className="kk-hilt" /></g>
        <g className="kk-bits"><rect x="24" y="118" width="4" height="4" rx="1" /><rect x="31" y="117" width="4" height="4" rx="1" /></g>
      </g>
      {/* READY: a service bell and a cloche lifting off a finished dish */}
      <g className="kk-p kk-p-ready">
        <g className="kk-bell"><path d="M20 128 a14 14 0 0 1 28 0 z" className="kk-bell-b" /><rect x="16" y="128" width="36" height="4" rx="2" className="kk-bell-b" /><circle cx="34" cy="111" r="3" className="kk-bell-b" /></g>
        <path d="M12 104 l-6 -4 M14 96 l-4 -8 M56 104 l6 -4 M54 96 l4 -8" className="kk-ding" />
        <ellipse cx="160" cy="128" rx="32" ry="6" className="kk-plate" />
        <path d="M140 126 q8 -16 20 -14 q14 -2 20 14 z" className="kk-food" />
        <g className="kk-cloche"><path d="M132 124 a28 22 0 0 1 56 0 z" className="kk-cloche-c" /><circle cx="160" cy="100" r="3.5" className="kk-cloche-c" /></g>
        <path d="M136 92 l3 -6 l3 6 l-6 0 M182 86 l3 -6 l3 6 l-6 0 M160 72 l3 -6 l3 6 l-6 0" className="kk-spark" />
      </g>
      {/* BURNT: a smoking black pan, then a toss into the bin */}
      <g className="kk-p kk-p-burnt">
        <path d="M150 128 h50" className="kk-pan-h" /><ellipse cx="140" cy="128" rx="22" ry="6" className="kk-pan" />
        <g className="kk-char"><ellipse cx="140" cy="124" rx="12" ry="5" /></g>
        <g className="kk-smoke"><circle cx="134" cy="104" r="8" /><circle cx="146" cy="92" r="10" /><circle cx="138" cy="76" r="12" /></g>
        <path d="M14 114 h34 l-4 32 h-26 z" className="kk-bin" /><path d="M10 112 h42" className="kk-bin-lid" />
      </g>
      {/* the chef Kiwi (the body sways; arms and eye change per state) */}
      <g className="kk-kiwi">
        <path d="M84 132 l-4 14 M80 146 l-6 2 M80 146 l2 3 M108 132 l4 14 M112 146 l6 2 M112 146 l-2 3" className="kk-legs" />
        <ellipse cx="96" cy="104" rx="34" ry="32" className="kk-body" />
        <ellipse cx="100" cy="112" rx="20" ry="20" className="kk-belly" />
        <path d="M80 112 q4 3 8 0 M84 122 q4 3 8 0" className="kk-feather" />
        <g className="kk-arm kk-arm-l"><path d="M66 104 q-12 4 -16 -6" className="kk-wing" /></g>
        <g className="kk-arm kk-arm-r"><path d="M126 102 q12 2 18 -8" className="kk-wing" /></g>
        <g className="kk-head">
          <circle cx="112" cy="64" r="20" className="kk-body" />
          <path d="M128 70 C 146 76, 156 88, 160 104" className="kk-beak" />
          <circle cx="118" cy="60" r="10" className="kk-eye-w" />
          <circle cx="120" cy="61" r="6" className="kk-iris" />
          <circle className="kk-pupil" cx="121" cy="62" r="3" />
          <circle cx="123" cy="58" r="1.6" className="kk-glint" />
          <rect className="kk-lid" x="107" y="49" width="22" height="13" />
          <path d="M106 48 q4 -4 14 -2" className="kk-brow" />
          {/* chef hat with the green Price Chat band */}
          <path d="M94 46 h34 v-10 h-34 z" className="kk-hat-band" />
          <path d="M92 36 q-10 -6 -4 -16 q6 -8 14 -4 q4 -10 14 -8 q10 2 10 12 q10 -2 12 8 q2 8 -8 8 z" className="kk-hat" />
        </g>
        <path className="kk-sweat" d="M138 52 q4 6 0 9 q-4 -3 0 -9 z" />
      </g>
    </svg>
  );
}

const r = (x: number | null | undefined) => x == null ? "—" : `${x > 0 ? "+" : ""}${x.toFixed(2)}R`;

/** Kiwi ML in the analysis: this setup's estimate, and the model's honest record on data it never saw. */
export function MlPanel({ ml, p }: { ml: KMl; p: number | null | undefined }) {
  const role = ml.mode === "off" ? "switched off" : ml.filtering ? "filtering: setups under the bar are skipped" : "advisory: shown, blocks nothing";
  return (
    <section aria-label="Kiwi ML" className="kk-ml">
      <h4>🧠 Kiwi ML · {role}</h4>
      {p != null && <p className="kk-ml-p"><b>{Math.round(p * 100)}%</b> estimated chance this setup ends in profit after costs
        {" "}<span className="kk-fine">(bar {Math.round(ml.threshold * 100)}%)</span></p>}
      <p className="kk-fine">Gradient-boosted trees trained on {ml.samples.toLocaleString()} past setups ({ml.source}, {ml.period[0]} to {ml.period[1]}).
        On the last 40% it never saw: {ml.without_ml.trades ?? 0} trades at {r(ml.without_ml.expectancy_r)} without it,
        {" "}{ml.with_ml.trades ?? 0} at {r(ml.with_ml.expectancy_r)} with it (AUC {ml.auc ?? "—"}).
        {ml.profitable ? "" : " Neither is profitable: the filter cuts losses, it doesn't create an edge."}
        {ml.validated ? "" : " It didn't pass its pre-set validation bar, so it stays advisory unless the owner switches it to filter."}</p>
    </section>
  );
}

export function RiskTeamPanel({ rt }: { rt: RiskTeam }) {
  return (
    <section aria-label="AI Risk Team" className={`kk-rt is-${rt.verdict.toLowerCase()}`}>
      <h4>🛡️ AI Risk Team · {rt.verdict === "VETO" ? "vetoed" : "approved"}</h4>
      {rt.memo ? <blockquote className="kk-rt-memo">{rt.memo.text}<cite>{rt.memo.by === "rules" ? "Risk team summary" : "Chief Risk Officer (AI)"}</cite></blockquote>
        : <p className="kk-fine">{rt.reason}</p>}
      <ul className="kk-checks">
        {rt.votes.map((v) => (
          <li key={v.desk} className={`is-${v.vote === "approve" ? "pass" : v.vote === "veto" ? "fail" : "pending"}`}>
            <span className="kk-ci" aria-hidden>{v.vote === "approve" ? "✓" : v.vote === "veto" ? "✕" : "!"}</span>
            <b>{v.label}</b> <span className="kk-sr">{v.vote}</span> · {v.detail}
          </li>
        ))}
      </ul>
      <p className="kk-fine">The desks are fixed rules and can only block a trade. The AI writes the explanation, it doesn't decide.</p>
    </section>
  );
}

function Analysis({ m, events, markets, onPick, shadow, paper, onClose, ml }: {
  m: KMarket; events: KEvent[]; markets: KMarket[]; onPick: (symbol: string) => void; shadow: boolean; paper: boolean; onClose: () => void;
  ml?: KMl | null;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  const mk = m.market;
  const passed = m.checks.filter((c) => c.status === "pass");
  const failed = m.checks.filter((c) => c.status === "fail");
  const mine = events.filter((e) => e.symbol === m.symbol).slice(-6).reverse();
  return (
    <div className="kk-modal" role="dialog" aria-modal="true" aria-label={`${m.symbol} trade analysis`} onClick={onClose}>
      <div className="kk-sheet" ref={ref} tabIndex={-1} onClick={(e) => e.stopPropagation()}>
        <header className="kk-sheet-head">
          <h3>{m.symbol} · {m.timeframe}{m.confirm_timeframe ? ` / ${m.confirm_timeframe}` : ""} · <span className={`kk-tag is-${m.state.toLowerCase()}`}>{TITLE[m.state]}</span></h3>
          <button className="kk-x" onClick={onClose} aria-label="Close">×</button>
        </header>
        {markets.length > 1 && (
          <div className="kk-tabs" role="tablist" aria-label="Markets">
            {markets.map((x) => (
              <button key={x.symbol} role="tab" aria-selected={x.symbol === m.symbol} className={`is-${x.state.toLowerCase()}`} onClick={() => onPick(x.symbol)}>
                {x.symbol}</button>
            ))}
          </div>
        )}
        {!shadow && paper && <p className="kk-fine"><span className="kk-mode">PAPER</span></p>}
        {m.state === "READY" && (m.proposal?.side ?? m.setup?.side) ? (
          <p className="kk-dish"><b className={(m.proposal?.side ?? m.setup?.side) === "LONG" ? "is-up" : "is-down"}>{m.symbol} {m.proposal?.side ?? m.setup?.side}</b>
            {m.setup ? ` · ${m.setup.label} · stop ${px(m.setup.stop)} · TP1 ${px(m.setup.tp1)}` : ""}
            {m.proposal?.approval_required ? " · waiting for approval" : ""}</p>
        ) : null}
        {m.setup && m.total > 0 && (
          <div className="kk-progress" role="progressbar" aria-valuemin={0} aria-valuemax={m.total} aria-valuenow={m.passed}
               aria-label={`Ingredients: ${m.passed} of ${m.total} checks passed`}>
            <span>Ingredients: {m.passed}/{m.total} checks passed</span>
            <i style={{ ["--p" as string]: m.passed / m.total }} />
          </div>
        )}
        <p className="kk-why"><b>{m.state === "SEARCHING" ? "Why Kiwi is waiting" : m.state === "BURNT" ? "Why it burnt" : "Now"}:</b> {m.state === "SEARCHING" && !m.setup ? "Kiwi is looking for ingredients." : m.reason}</p>
        {(m.shadow || shadow) && <p className="kk-note">Shadow mode: the strategy is evaluated live but isn't switched on, so nothing is proposed or traded.</p>}
        {m.checks.length > 0 && (
          <section aria-label="Checks">
            <h4>Ingredients · {m.passed}/{m.total} checks passed</h4>
            <ul className="kk-checks">
              {m.checks.map((c) => (
                <li key={c.id} className={`is-${c.status}`}>
                  <span className="kk-ci" aria-hidden>{c.status === "pass" ? "✓" : c.status === "fail" ? "✕" : c.status === "pending" ? "…" : "–"}</span>
                  <b>{c.label}</b> <span className="kk-sr">{c.status === "skip" ? (c.id === "ml" && c.detail.includes("%") ? "advisory" : c.detail ? "off" : "not reached") : c.status}</span>{c.detail && <> · {c.detail}</>}
                </li>
              ))}
            </ul>
            <p className="kk-fine">{passed.length} passed · {failed.length} failed. The score is a ranking of the setup, not a chance of winning.</p>
          </section>
        )}
        {m.setup && (
          <section aria-label="Entry criteria">
            <h4>Entry criteria</h4>
            <p>{m.setup.label} · <b>{m.symbol} {m.setup.side}</b> at about {px(m.setup.ref)} · stop {px(m.setup.stop)} · TP1 {px(m.setup.tp1)} · TP2 {px(m.setup.tp2)}
              {" "}· held up to {Math.round(m.setup.max_hold_min / 60)} h · costs {m.setup.cost_r.toFixed(2)}R</p>
            {m.setup.reasons.map((r) => <p key={r} className="kk-fine">{r}</p>)}
          </section>
        )}
        {m.setup && m.risk_team && <RiskTeamPanel rt={m.risk_team} />}
        {ml && <MlPanel ml={ml} p={m.setup?.ml_p} />}
        {m.proposal && (
          <section aria-label="Proposal">
            <h4>Proposal</h4>
            <p>{m.proposal.number ? `Signal #${String(m.proposal.number).padStart(3, "0")} · ` : ""}{m.symbol} {m.proposal.side} · {m.proposal.status}
              {m.proposal.approval_required ? " · waiting for approval before anything executes" : ""}{m.proposal.shadow ? " · shadow (not proposed)" : ""}</p>
          </section>
        )}
        {mk && (
          <section aria-label="Market conditions">
            <h4>Market conditions</h4>
            <dl className="kk-grid">
              <div><dt>Mark</dt><dd>{px(mk.mark)}</dd></div>
              <div><dt>24h</dt><dd>{mk.change_24h == null ? "—" : `${mk.change_24h > 0 ? "+" : ""}${mk.change_24h}%`}</dd></div>
              <div><dt>Regime</dt><dd>{mk.regime?.replace("_", " ").toLowerCase() ?? "—"}</dd></div>
              <div><dt>Funding</dt><dd>{mk.funding_h == null ? "—" : `${(mk.funding_h * 100).toFixed(4)}%/h`}</dd></div>
              <div><dt>Open interest</dt><dd>{mk.oi_usd == null ? "—" : `$${(mk.oi_usd / 1e6).toFixed(0)}M`}</dd></div>
              <div><dt>Spread</dt><dd>{mk.spread_bps == null ? "—" : `${mk.spread_bps} bps`}</dd></div>
            </dl>
          </section>
        )}
        {m.risk && m.risk.length > 0 && (
          <section aria-label="Risk checks">
            <h4>Risk checks</h4>
            <ul className="kk-checks">{m.risk.map((c) => <li key={c.id} className={`is-${c.status}`}><b>{c.label}</b> · {c.detail || c.status}</li>)}</ul>
            <p className="kk-fine">Every member's own trade is checked again by the risk engine: limits, daily loss, cooldown, trades per day.</p>
          </section>
        )}
        {mine.length > 0 && (
          <section aria-label="Recent kitchen events">
            <h4>Recent</h4>
            <ul className="kk-events">{mine.map((e) => <li key={e.id}><time>{new Date(e.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time> {e.title}</li>)}</ul>
          </section>
        )}
        <a className="kk-brain" href={BRAIN_PATH}>Open the full Kiwi Brain →</a>
      </div>
    </div>
  );
}

/** The Kiwi chef on Earn: animation only (searching the fridge, cooking, ORDER UP, burnt), no text. The most interesting
 *  market first (READY, then COOKING, BURNT, SEARCHING). Tap it for the trade analysis, where the other markets are too. */
export function KiwiKitchen() {
  const { snap, error } = useKitchen();
  const [pick, setPick] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const markets = useMemo(() => [...(snap?.markets ?? [])].sort((a, b) => ORDER[a.state] - ORDER[b.state]), [snap]);
  if (error && !snap) return null;
  if (!snap || !markets.length) return <div className="kk kk-skel" aria-busy="true" />;
  const m = markets.find((x) => x.symbol === pick) ?? markets[0];
  return (
    <section className={`kk is-${m.state.toLowerCase()}`} aria-label="Kiwi Kitchen">
      <button className="kk-chef" onClick={() => setOpen(true)} aria-label={`Kiwi chef: ${m.symbol} ${TITLE[m.state]}. Open the trade analysis`}>
        <KiwiChef state={m.state} />
      </button>
      {open && <Analysis m={m} events={snap.events} markets={markets} onPick={setPick} shadow={snap.shadow} paper={!snap.live}
                         onClose={() => setOpen(false)} ml={snap.ml} />}
    </section>
  );
}
