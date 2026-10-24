import { useCallback, useEffect, useRef, useState } from "react";
import { CandlestickSeries, ColorType, LineStyle, createChart, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import { SitePage } from "./community";
import { pageHead } from "./seo";
import { openLogin } from "./emailLogin";
import { MemberApiError, memberApi, useSession } from "./members";
import { EarnTabs } from "./arbitrage";
import { useApi } from "../api/useApi";
import { fmt, usePoll, type Candles } from "./brain/api";
import "./tradingbot.css";
import "./arbitrage.css";
import "./committee.css";

/* Earn › AI Investment Committee (/earn/committee). Research only: every value comes from the committee's run JSON
   (GET /api/committee/runs/{id}); nothing here places, sizes or tracks a real trade, and none of it is the member's P&L. */

export type Lean = "bullish" | "bearish" | "neutral" | "unknown";
export type Action = "LONG" | "SHORT" | "WAIT";
export interface Point { text: string; refs?: string[] }
export interface Ref { id: string; source: string; url: string | null; ts: string | null; fetched_at?: string | null }
export interface Analyst { lean: Lean; confidence: string; summary: string; points: Point[]; limitations: string[]; author: string }
export interface Side { thesis: string; points: Point[]; rebuttal?: string; author?: string }
export interface Scenario { trigger: number; trigger_rule: string; invalidation: number; invalidation_rule: string; targets: number[]; target_rule: string;
  entry_zone?: number[]; derivation?: string }
export interface Outcome { ret_pct: number; direction_ok: boolean | null; price_from?: number; price_to?: number; mfe_pct?: number; mae_pct?: number;
  source?: string | null }
export interface Asset { symbol: string; venue?: string; market_type?: string; market_id?: string; label?: string; hl_tradable?: boolean }
export interface Run {
  id: string; status: "queued" | "running" | "done" | "failed"; error?: string | null; created_at?: string;
  asset: Asset; timeframe: string; as_of?: string;
  price?: { value: number | null; kind?: string; source?: string; ts?: string; stale?: boolean };
  analysts?: Record<"technical" | "fundamental" | "news" | "sentiment", Analyst>;
  debate?: { rounds: number; bull: Side; bear: Side; missing_info: string[]; manager: { rating: string; summary: string; author?: string } };
  trader?: { action: Action; rationale: string; plan: { entry_zone: number[]; stop: number; targets: number[]; derivation: string } | null; author?: string };
  risk_committee?: Record<"aggressive" | "neutral" | "conservative", { stance: string; concerns: string[]; author?: string }> & { summary: string };
  verdict?: { action: Action; rating: string; summary: string; scenarios: { long: Scenario | null; short: Scenario | null }; what_changes_view: string[]; author?: string };
  execution?: { status: "APPROVED" | "REJECTED" | "NOT_APPLICABLE"; reasons: string[]; checks: { name: string; ok: boolean; value: string | null; limit: string | null }[];
    engine: string; note: string };
  meta?: { backend: string; models: Record<string, string | null>; calls: number; tokens_in: number; tokens_out: number; cost_usd: number; duration_s: number;
    cached: boolean; framework: string; prompt_version: string; degraded: string[] };
  evidence?: { refs: Ref[]; sections?: Record<string, { status: string; items?: { title: string; url: string; source: string; published: string; ref?: string }[] }> };
  outcomes?: Record<string, Outcome>;
}
export interface RunSummary { id: string; status: Run["status"]; asset: Asset; timeframe: string; created_at: string | null; price_at: number | null;
  verdict: Action | null; rating: string | null; exec_status: string | null; outcomes: Record<string, Outcome> }
export interface Track { by_verdict: Record<string, Record<string, { n: number; hits?: number | null; hit_rate: number | null; avg_ret_pct: number }>>;
  horizons?: string[]; scored_runs?: number; recent: RunSummary[]; note: string }
interface Status { enabled: boolean; backend: string; backend_note?: string | null; backends: Record<string, { ok: boolean; detail?: string }>;
  providers: Record<string, { ok: boolean; age_s?: number | null; detail?: string }>;
  budget: { calls_today: number; calls_limit: number; usd_today: number; usd_limit: number } }

export const TIMEFRAMES = ["15m", "1h", "4h", "1d"] as const;
export const POLL_MS = 3000;
export const POLL_MAX_MS = 5 * 60 * 1000;
const HORIZONS = ["1h", "4h", "24h", "7d"];
const LEAN_LABEL: Record<Lean, string> = { bullish: "Bullish", bearish: "Bearish", neutral: "Neutral", unknown: "Unknown" };
const EXEC_LABEL: Record<string, string> = { APPROVED: "Risk engine: would approve", REJECTED: "Risk engine: rejected", NOT_APPLICABLE: "Risk engine: not applicable" };
const ANALYSTS = [["technical", "Technical"], ["fundamental", "Fundamental"], ["news", "News"], ["sentiment", "Sentiment"]] as const;

/** Only http(s) links from the evidence are ever rendered as links. */
export function safeUrl(u: unknown): string | null {
  if (typeof u !== "string") return null;
  try {
    const url = new URL(u.trim());
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
  } catch {
    return null;
  }
}
const SCENARIO_REASON = /^(long|short) scenario now: ([\s\S]*)$/i;
/** A WAIT scenario's "price X is outside the entry zone" (risk.plan_trade) means the setup hasn't triggered. */
const plainReason = (r: string) => r.trim().replace(/^price (\S+) is outside the entry zone$/, "not triggered yet (price $1 is outside its entry zone)");
const NOT_TRIGGERED = /^not triggered yet \(price (\S+) is outside its entry zone\)$/;
const uniq = (xs: string[]) => [...new Set(xs.map((x) => x.replace(/\s+/g, " ").trim()).filter(Boolean))];

/** The risk engine's reasons, each said once (as the Telegram Risk Review shows them): reasons that block both scenarios on one
    line ("Both setups blocked: …"), the rest labelled long / short, and "no trade proposed" left out when the verdict is WAIT. */
export function whyLines(run: Pick<Run, "execution" | "verdict">): string[] {
  const wait = (run.verdict?.action ?? "WAIT") === "WAIT";
  const general: string[] = [];
  const per: Partial<Record<"long" | "short", string[]>> = {};
  const passed: ("long" | "short")[] = [];
  for (const r of run.execution?.reasons ?? []) {
    const m = SCENARIO_REASON.exec(r.trim());
    if (!m) { general.push(r); continue; }
    const side = m[1].toLowerCase() as "long" | "short";
    const body = m[2].trim();
    if (body.replace(/\.$/, "") === "the risk engine would approve it") { passed.push(side); per[side] ??= []; continue; }
    per[side] = uniq([...(per[side] ?? []), ...body.split("; ").map(plainReason)]);
  }
  const shared = per.long && per.short ? per.long.filter((x) => per.short!.includes(x)) : [];
  const trig = shared.map((x) => NOT_TRIGGERED.exec(x)).find((m) => m);
  const both = shared.filter((x) => !NOT_TRIGGERED.test(x));
  const out = uniq(general).filter((x) => !(wait && x === "no trade proposed"));
  if (trig) out.push(`Neither setup has triggered yet (price ${trig[1]})`);
  if (both.length) out.push(`Both setups blocked: ${both.join("; ")}`);
  for (const side of ["long", "short"] as const) {
    const own = (per[side] ?? []).filter((x) => !shared.includes(x));
    const label = side === "long" ? "Long" : "Short";
    if (own.length) out.push(`${label}${both.length ? " only" : ""}: ${own.join("; ")}`);
    else if (passed.includes(side)) out.push(`${label}: would pass the risk checks${wait ? "; the verdict is still WAIT" : ""}`);
  }
  return out.length ? out : [wait ? "the committee's verdict is WAIT" : "the risk engine rejected it"];
}

const when = (iso: string | null | undefined) => (iso ? `${fmt.day(iso)} ${fmt.time(iso).slice(0, 5)}` : "—");
const move = (o: Outcome | undefined) => (o ? fmt.pct(o.ret_pct) : "—");

/** Poll one run every 3 s until it is done or failed. Pauses while the tab is hidden; gives up after 5 minutes (stale). */
export function useRunPoll(id: string | null): { run: Run | null; err: string | null; gaveUp: boolean } {
  const [run, setRun] = useState<Run | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [gaveUp, setGaveUp] = useState(false);
  useEffect(() => {
    setRun(null);
    setErr(null);
    setGaveUp(false);
    if (!id) return;
    let alive = true;
    let timer: number | undefined;
    const started = Date.now();
    const finished = (r: Run | null) => r != null && (r.status === "done" || r.status === "failed");
    let last: Run | null = null;
    const tick = async () => {
      timer = undefined;
      if (!alive) return;
      if (Date.now() - started > POLL_MAX_MS) { setGaveUp(true); return; }
      if (document.hidden) return;  // resumes on visibilitychange
      try {
        const r = await memberApi<Run>(`/committee/runs/${encodeURIComponent(id)}`);
        if (!alive) return;
        last = r;
        setRun(r);
        setErr(null);
      } catch (e) {
        if (!alive) return;
        setErr(e instanceof Error ? e.message : "Couldn't load this run.");
        if (e instanceof MemberApiError && e.status === 404) return;
      }
      if (alive && !finished(last)) timer = window.setTimeout(() => void tick(), POLL_MS);
    };
    const vis = () => { if (!document.hidden && timer === undefined && !finished(last)) void tick(); };
    void tick();
    document.addEventListener("visibilitychange", vis);
    return () => { alive = false; if (timer !== undefined) window.clearTimeout(timer); document.removeEventListener("visibilitychange", vis); };
  }, [id]);
  return { run, err, gaveUp };
}

function readRunParam(): string | null {
  try {
    const v = new URLSearchParams(window.location.search).get("run");
    return v && /^cm_[a-z0-9]{1,24}$/i.test(v) ? v : null;
  } catch {
    return null;
  }
}

export function CommitteePage() {
  const [selected, setSelected] = useState<string | null>(readRunParam);
  const runs = useApi(() => memberApi<{ runs: RunSummary[] }>("/committee/runs?limit=20"), 30_000);
  const choose = useCallback((id: string) => {
    setSelected(id);
    try { window.history.replaceState(null, "", `/earn/committee?run=${encodeURIComponent(id)}`); } catch { /* not in a browser */ }
  }, []);
  const firstDone = runs.data?.runs.find((r) => r.status === "done")?.id ?? null;
  const shown = selected ?? firstDone;
  return (
    <SitePage {...pageHead("earn/committee")}>
      <div className="tb cm">
        <EarnTabs current="committee" />
        <CommitteeSection runs={runs} shown={shown} onPick={choose} />
      </div>
    </SitePage>
  );
}

type RunsState = { data: { runs: RunSummary[] } | null; error: string | null; loading: boolean; reload: () => Promise<void> };

export function CommitteeSection({ runs, shown, onPick }: { runs: RunsState; shown: string | null; onPick: (id: string) => void }) {
  const status = useApi(() => memberApi<Status>("/committee/status"), 60_000);
  const track = useApi(() => memberApi<Track>("/committee/track?limit=30"), 120_000);
  const { run, err, gaveUp } = useRunPoll(shown);
  const lastStatus = useRef<string | null>(null);
  useEffect(() => {  // a run just finished: refresh the list (its verdict appears there)
    if (run && lastStatus.current && lastStatus.current !== run.status && (run.status === "done" || run.status === "failed")) void runs.reload();
    lastStatus.current = run?.status ?? null;
  }, [run, runs]);
  return (
    <section className="cm-section" aria-labelledby="cm-title">
      <header className="cm-head">
        <span className="tb-kicker">Kiwi AI Investment Committee</span>
        <h1 id="cm-title">AI Investment Committee</h1>
        <p className="cm-lede">Four analysts read one market, a bull and a bear argue it out, a trader proposes, a risk committee pushes back, and a
          portfolio manager decides. Kiwi's deterministic risk engine has the last word on whether a trade would even be allowed.</p>
        <p className="cm-research" role="note"><b>Research — not your trades or P&amp;L.</b> Nothing here is traded, and none of it is part of your
          bot's results. Not financial advice.</p>
      </header>
      <StatusChip s={status.data} err={status.error} />
      <RunForm status={status.data} onStarted={(id) => { onPick(id); void runs.reload(); }} />
      <RunList runs={runs} shown={shown} onPick={onPick} />
      <div className="cm-detail-slot" aria-live="polite">
        {shown ? <RunDetail run={run} err={err} gaveUp={gaveUp} /> : runs.data && <p className="cm-empty">No committee runs yet. Ask about a market above.</p>}
      </div>
      <TrackRecord t={track.data} err={track.error} onPick={onPick} />
    </section>
  );
}

function StatusChip({ s, err }: { s: Status | null; err: string | null }) {
  if (!s) return <p className="cm-chip-row cm-muted">{err ? "Committee status unavailable right now." : "Checking the committee…"}</p>;
  const prov = Object.entries(s.providers ?? {});
  const missing = prov.filter(([, p]) => !p.ok).map(([k]) => k);
  return (
    <div className="cm-chip-row" aria-label="Committee status">
      <span className={`cm-chip ${s.enabled ? "is-ok" : "is-bad"}`}>{s.enabled ? "Open" : "Switched off"}</span>
      <span className="cm-chip" title={s.backend_note ?? undefined}>Backend: {s.backend === "rules" ? "rules (no AI model)" : s.backend}</span>
      {prov.length > 0 && <span className={`cm-chip ${missing.length ? "is-warn" : "is-ok"}`}>
        Data: {prov.length - missing.length}/{prov.length} sources ok{missing.length ? ` · missing ${missing.join(", ")}` : ""}</span>}
      <span className="cm-chip">AI budget today: {s.budget.calls_today}/{s.budget.calls_limit} calls</span>
    </div>
  );
}

function RunForm({ status, onStarted }: { status: Status | null; onStarted: (id: string) => void }) {
  const member = useSession();
  const [symbol, setSymbol] = useState("BTC");
  const [tf, setTf] = useState<(typeof TIMEFRAMES)[number]>("1h");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  if (member === undefined) return <div className="cm-form cm-form-skel" aria-hidden />;
  if (member === null) {
    return (
      <div className="cm-form cm-form-out">
        <p>Signed-in Kiwi Passport members can ask the committee about a market (a few runs a day).</p>
        <button type="button" className="tb-btn" onClick={openLogin}>Sign in to ask</button>
      </div>
    );
  }
  const submit = async (ev: React.FormEvent) => {
    ev.preventDefault();
    const sym = symbol.trim().replace(/^\$/, "").toUpperCase();
    if (!sym) { setMsg("Type a coin, e.g. BTC."); return; }
    setBusy(true);
    setMsg(null);
    try {
      const r = await memberApi<{ id: string; cached: boolean }>("/committee/runs", { method: "POST", body: JSON.stringify({ symbol: sym, timeframe: tf }) });
      setMsg(r.cached ? "Same market and timeframe in the last 15 minutes: showing that run (it doesn't count against your limit)." : "The committee is meeting…");
      onStarted(r.id);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Couldn't start the committee.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="cm-form" onSubmit={submit} aria-label="Ask the committee">
      <label>Coin<input value={symbol} onChange={(e) => setSymbol(e.target.value)} maxLength={20} autoCapitalize="characters" spellCheck={false}
        placeholder="BTC" aria-describedby="cm-form-msg" /></label>
      <label>Timeframe<select value={tf} onChange={(e) => setTf(e.target.value as typeof tf)}>
        {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
      </select></label>
      <button type="submit" className="tb-btn" disabled={busy || status?.enabled === false}>{busy ? "Starting…" : "Ask the committee"}</button>
      <p id="cm-form-msg" className="cm-form-msg" role="status">{msg ?? (status?.enabled === false ? "The committee is switched off right now." : "")}</p>
    </form>
  );
}

function latestOutcome(o: Record<string, Outcome> | undefined): [string, Outcome] | null {
  for (const h of [...HORIZONS].reverse()) if (o?.[h]) return [h, o[h]];
  return null;
}

function RunList({ runs, shown, onPick }: { runs: RunsState; shown: string | null; onPick: (id: string) => void }) {
  const list = runs.data?.runs ?? [];
  return (
    <section className="cm-block" aria-labelledby="cm-runs-title">
      <h2 id="cm-runs-title">Latest runs</h2>
      {runs.error && runs.data && <p className="cm-stale" role="status">Couldn't refresh ({runs.error}); showing the last list.</p>}
      {runs.error && !runs.data && <p className="site-error" role="alert">{runs.error}</p>}
      {runs.loading && !runs.data && <div className="cm-skel" aria-busy="true" />}
      {runs.data && !list.length && <p className="cm-empty">No runs yet.</p>}
      {list.length > 0 && (
        <div className="cm-scroll">
          <table className="cm-table">
            <thead><tr><th scope="col">Asset</th><th scope="col">Price at decision</th><th scope="col">Verdict</th><th scope="col">Execution</th>
              <th scope="col">Time</th><th scope="col">Outcome so far</th></tr></thead>
            <tbody>
              {list.map((r) => {
                const lo = latestOutcome(r.outcomes);
                return (
                  <tr key={r.id} className={r.id === shown ? "is-on" : ""}>
                    <td><button type="button" className="cm-linkbtn" onClick={() => onPick(r.id)} aria-current={r.id === shown ? "true" : undefined}>
                      {r.asset.symbol} <small>{r.timeframe}</small></button></td>
                    <td>{fmt.px(r.price_at)}</td>
                    <td>{r.status === "done" ? <><Verdict a={r.verdict} /> <small>{r.rating}</small></> : <span className="cm-chip">{r.status}</span>}</td>
                    <td><small>{r.exec_status ? EXEC_LABEL[r.exec_status] ?? r.exec_status : "—"}</small></td>
                    <td><small>{r.created_at ? when(r.created_at) : "—"}</small></td>
                    <td>{lo ? <span>{move(lo[1])} <small>after {lo[0]}</small></span> : <small className="cm-muted">pending</small>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function Verdict({ a }: { a: Action | null | undefined }) {
  const v = a ?? "WAIT";
  return <span className={`cm-verdict v-${v.toLowerCase()}`}>{v}</span>;
}

function RefChips({ ids, refs }: { ids: string[] | undefined; refs: Map<string, Ref> }) {
  const [open, setOpen] = useState(false);
  if (!ids?.length) return null;
  const known = ids.map((i) => refs.get(i)).filter((r): r is Ref => !!r);
  return (
    <span className="cm-refs">
      <button type="button" className="cm-ref" aria-expanded={open} onClick={() => setOpen((o) => !o)} aria-label={`Sources ${ids.join(", ")}`}>
        {ids.join(" · ")}
      </button>
      {open && (
        <span className="cm-ref-list">
          {known.length ? known.map((r) => <RefLine key={r.id} r={r} />) : <span className="cm-muted">Source details not recorded.</span>}
        </span>
      )}
    </span>
  );
}

function RefLine({ r }: { r: Ref }) {
  const href = safeUrl(r.url);
  return (
    <span className="cm-ref-line">
      <b>{r.id}</b> {href ? <a href={href} target="_blank" rel="noopener noreferrer">{r.source}</a> : r.source}
      <small> · data {r.ts ? when(r.ts) : "time not given"}{r.fetched_at ? ` · fetched ${when(r.fetched_at)}` : ""}</small>
    </span>
  );
}

function Points({ points, refs }: { points: Point[] | undefined; refs: Map<string, Ref> }) {
  if (!points?.length) return <p className="cm-muted">No evidence points.</p>;
  return <ul className="cm-points">{points.map((p, i) => <li key={i}>{p.text} <RefChips ids={p.refs} refs={refs} /></li>)}</ul>;
}

function Levels({ s }: { s: Scenario }) {
  return (
    <dl className="cm-kv">
      <dt>Trigger</dt><dd>{fmt.px(s.trigger)}</dd>
      {s.entry_zone?.length === 2 && <><dt>Entry zone</dt><dd>{fmt.px(s.entry_zone[0])} – {fmt.px(s.entry_zone[1])}</dd></>}
      <dt>Invalidation</dt><dd>{fmt.px(s.invalidation)}</dd>
      <dt>Targets</dt><dd>{s.targets.map((t) => fmt.px(t)).join(" · ") || "—"}</dd>
    </dl>
  );
}

function ScenarioCard({ side, s }: { side: "long" | "short"; s: Scenario | null }) {
  const title = side === "long" ? "Potential LONG" : "Potential SHORT";
  return (
    <article className={`cm-card cm-${side}`} aria-label={title}>
      <h3>{title}</h3>
      {s ? (
        <>
          <Levels s={s} />
          <details className="cm-derive">
            <summary>How derived</summary>
            <p><b>Trigger:</b> {s.trigger_rule}</p>
            <p><b>Invalidation:</b> {s.invalidation_rule}</p>
            <p><b>Targets:</b> {s.target_rule}</p>
            {s.derivation && <p><b>Full:</b> {s.derivation}</p>}
          </details>
        </>
      ) : <p className="cm-muted">No {side} setup could be derived from the evidence.</p>}
    </article>
  );
}

export function RunDetail({ run, err, gaveUp }: { run: Run | null; err: string | null; gaveUp: boolean }) {
  if (!run) {
    if (err) return <p className="site-error" role="alert">{err}</p>;
    return <div className="cm-skel cm-skel-tall" aria-busy="true" aria-label="Loading the run" />;
  }
  if (run.status === "queued" || run.status === "running") {
    return (
      <article className="cm-card cm-pending" aria-busy={!gaveUp}>
        <h2>{run.asset.symbol} · {run.timeframe}</h2>
        {gaveUp
          ? <p className="cm-stale" role="status">Still running after 5 minutes. Stopped checking: reload the page later to see the verdict.</p>
          : <p>{run.status === "queued" ? "Waiting for a free seat at the table…" : "The committee is meeting: analysts → bull vs bear → trader → risk committee → verdict."}</p>}
      </article>
    );
  }
  if (run.status === "failed") {
    return <article className="cm-card"><h2>{run.asset.symbol} · {run.timeframe}</h2><p className="site-error" role="alert">This run failed: {run.error ?? "unknown error"}.</p></article>;
  }
  const refs = new Map((run.evidence?.refs ?? []).map((r) => [r.id, r] as const));
  const v = run.verdict;
  const ex = run.execution;
  const action = v?.action ?? "WAIT";
  const price = run.price;
  const noTrade = action === "WAIT" || ex?.status !== "APPROVED";
  return (
    <article className="cm-run" aria-label={`Committee run ${run.asset.symbol} ${run.timeframe}`}>
      {err && <p className="cm-stale" role="status">Couldn't refresh this run ({err}).</p>}
      <section className={`cm-card cm-verdict-card v-${action.toLowerCase()}`} aria-label="Verdict">
        <div className="cm-verdict-top">
          <div>
            <span className="cm-muted">{run.asset.label ?? run.asset.venue} · {run.timeframe}</span>
            <h2>{run.asset.symbol} <Verdict a={action} /> <span className="cm-rating">Rating {v?.rating}</span></h2>
          </div>
          <a className="tb-btn tb-btn-ghost cm-dl" href={`/api/committee/runs/${encodeURIComponent(run.id)}/report.html`} download>Download HTML report</a>
        </div>
        <dl className="cm-facts">
          <div><dt>Price at decision</dt><dd>{fmt.px(price?.value)} {price?.stale && <span className="cm-chip is-warn">stale</span>}</dd></div>
          <div><dt>Price time</dt><dd>{when(price?.ts)}</dd></div>
          <div><dt>Execution</dt><dd>{ex ? EXEC_LABEL[ex.status] ?? ex.status : "—"}</dd></div>
          <div><dt>Status</dt><dd>Research only — nothing was traded.{run.meta?.cached ? " (cached run)" : ""}</dd></div>
        </dl>
        <p className="cm-summary">{v?.summary}</p>
      </section>

      <section className="cm-block" aria-labelledby="cm-an-title">
        <h2 id="cm-an-title">Analysts</h2>
        <div className="cm-grid4">
          {ANALYSTS.map(([k, label]) => {
            const a = run.analysts?.[k];
            return (
              <article key={k} className="cm-card cm-analyst" aria-label={`${label} analyst`}>
                <h3>{label} <span className={`cm-lean l-${a?.lean ?? "unknown"}`}>{LEAN_LABEL[a?.lean ?? "unknown"]}</span></h3>
                <p className="cm-muted cm-card-meta">confidence {a?.confidence ?? "—"} · {a?.author === "rules" ? "rules" : a?.author ?? "—"}</p>
                <p>{a?.summary}</p>
                <details>
                  <summary>Evidence ({a?.points.length ?? 0})</summary>
                  <Points points={a?.points} refs={refs} />
                  {a?.limitations.length ? <><h4>Limitations</h4><ul className="cm-points">{a.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul></> : null}
                </details>
              </article>
            );
          })}
        </div>
      </section>

      <section className="cm-block" aria-labelledby="cm-deb-title">
        <h2 id="cm-deb-title">Bull vs bear</h2>
        <div className="cm-grid2">
          {(["bull", "bear"] as const).map((k) => {
            const s = run.debate?.[k];
            return (
              <article key={k} className={`cm-card cm-${k === "bull" ? "long" : "short"}`} aria-label={k === "bull" ? "Bull thesis" : "Bear thesis"}>
                <h3>{k === "bull" ? "Bull thesis" : "Bear thesis"}</h3>
                <p>{s?.thesis}</p>
                <Points points={s?.points} refs={refs} />
                {s?.rebuttal && <p className="cm-muted"><b>Rebuttal:</b> {s.rebuttal}</p>}
              </article>
            );
          })}
        </div>
        {run.debate?.manager && <p><b>Research manager · {run.debate.manager.rating}:</b> {run.debate.manager.summary}</p>}
        {run.debate?.missing_info?.length ? <p className="cm-muted"><b>Missing:</b> {run.debate.missing_info.join(" · ")}</p> : null}
      </section>

      <section className="cm-block" aria-labelledby="cm-risk-title">
        <h2 id="cm-risk-title">Risk committee</h2>
        <div className="cm-grid3">
          {(["aggressive", "neutral", "conservative"] as const).map((k) => {
            const r = run.risk_committee?.[k];
            return (
              <article key={k} className="cm-card">
                <h3>{k[0].toUpperCase() + k.slice(1)} <span className="cm-chip">{r?.stance ?? "—"}</span></h3>
                {r?.concerns.length ? <ul className="cm-points">{r.concerns.map((c, i) => <li key={i}>{c}</li>)}</ul> : <p className="cm-muted">No concerns.</p>}
              </article>
            );
          })}
        </div>
        <p>{run.risk_committee?.summary}</p>
      </section>

      <section className="cm-block" aria-labelledby="cm-final-title">
        <h2 id="cm-final-title">Final verdict</h2>
        <p><Verdict a={action} /> <b>{v?.rating}</b> — {run.trader?.rationale}</p>
        {run.trader?.plan && <p className="cm-muted">Trader plan: entry {fmt.px(run.trader.plan.entry_zone[0])} – {fmt.px(run.trader.plan.entry_zone[1])},
          stop {fmt.px(run.trader.plan.stop)}, targets {run.trader.plan.targets.map((t) => fmt.px(t)).join(" · ")}</p>}
        <div className="cm-grid2">
          <ScenarioCard side="long" s={v?.scenarios.long ?? null} />
          <ScenarioCard side="short" s={v?.scenarios.short ?? null} />
        </div>
        <h3>What changes our view</h3>
        {v?.what_changes_view.length ? <ul className="cm-points">{v.what_changes_view.map((w, i) => <li key={i}>{w}</li>)}</ul> : <p className="cm-muted">Nothing listed.</p>}
        {noTrade ? (
          <div className="cm-notrade" aria-label="No trade approved">
            <h3>No trade approved because…</h3>
            <ul className="cm-points">{whyLines(run).map((r, i) => <li key={i}>{r}</li>)}</ul>
          </div>
        ) : (
          <p className="cm-approved">The risk engine would approve this on a reference account. Nothing was traded.</p>
        )}
        {ex?.checks.length ? (
          <details className="cm-checks">
            <summary>Kiwi risk engine checks ({ex.checks.filter((c) => c.ok).length}/{ex.checks.length} pass)</summary>
            <div className="cm-scroll"><table className="cm-table">
              <thead><tr><th scope="col">Check</th><th scope="col">Result</th><th scope="col">Value</th><th scope="col">Limit</th></tr></thead>
              <tbody>{ex.checks.map((c, i) => <tr key={i}><td>{c.name}</td><td className={c.ok ? "cm-ok" : "cm-bad"}>{c.ok ? "pass" : "fail"}</td>
                <td>{c.value ?? "—"}</td><td>{c.limit ?? "—"}</td></tr>)}</tbody>
            </table></div>
          </details>
        ) : null}
      </section>

      <CommitteeChart run={run} />

      <section className="cm-block" aria-labelledby="cm-out-title">
        <h2 id="cm-out-title">What happened next</h2>
        <Outcomes o={run.outcomes} />
      </section>

      <details className="cm-block cm-sources">
        <summary>Sources ({run.evidence?.refs.length ?? 0})</summary>
        <ul className="cm-points">{(run.evidence?.refs ?? []).map((r) => <li key={r.id}><RefLine r={r} /></li>)}</ul>
      </details>
      {run.meta && (
        <p className="cm-meta">
          {run.meta.backend === "rules" ? "Written by the rules engine (no AI model)" : `Backend ${run.meta.backend} · models ${Object.values(run.meta.models).filter(Boolean).join(", ")}`}
          {` · ${run.meta.calls} calls · $${run.meta.cost_usd.toFixed(4)} · ${run.meta.duration_s}s · ${run.meta.framework} · prompts ${run.meta.prompt_version}`}
          {run.meta.degraded.length ? ` · fallbacks: ${run.meta.degraded.join("; ")}` : ""}
        </p>
      )}
    </article>
  );
}

function Outcomes({ o }: { o: Record<string, Outcome> | undefined }) {
  const rows = HORIZONS.filter((h) => o?.[h]);
  if (!rows.length) return <p className="cm-muted">Not resolved yet. Hyperliquid perpetuals are scored 1h, 4h, 24h and 7d after the decision.</p>;
  return (
    <div className="cm-scroll"><table className="cm-table">
      <thead><tr><th scope="col">After</th><th scope="col">Move</th><th scope="col">Direction</th><th scope="col">Best / worst</th></tr></thead>
      <tbody>{rows.map((h) => {
        const x = o![h];
        return <tr key={h}><td>{h}</td><td>{fmt.pct(x.ret_pct)}</td><td>{x.direction_ok == null ? "no position" : x.direction_ok ? "right" : "wrong"}</td>
          <td>{fmt.pct(x.mfe_pct)} / {fmt.pct(x.mae_pct)}</td></tr>;
      })}</tbody>
    </table></div>
  );
}

export type RunCandles = Candles & { run: string; symbol: string; venue: string; price_at: number | null;
  levels: Record<"long" | "short", { trigger: number; invalidation: number; targets: number[]; entry_zone?: number[] } | null> };

/** The run's own market and timeframe (GET /api/committee/runs/{id}/candles: public, cached, closed candles only), for everyone. */
function CommitteeChart({ run }: { run: Run }) {
  const cand = usePoll<RunCandles>(run.status === "done" ? `/committee/runs/${encodeURIComponent(run.id)}/candles` : null, 120_000);
  const where = run.asset.label ?? run.asset.venue ?? "";
  return (
    <section className="cm-block" aria-label={`${run.asset.symbol} ${run.timeframe} chart with the scenario levels`}>
      <h2>Chart <small className="cm-muted">{run.asset.symbol} · {run.timeframe}{where ? ` · ${where}` : ""}</small></h2>
      <div className="cm-chart-box">
        {cand.data ? <LevelsChart data={cand.data} run={run} />
          : <p className="cm-muted">{cand.err ? "Chart unavailable for this market right now." : "Loading candles…"}</p>}
      </div>
      {cand.data && <p className="cm-muted cm-chart-src">{cand.data.source}</p>}
    </section>
  );
}

function LevelsChart({ data, run }: { data: Candles; run: Run }) {
  const box = useRef<HTMLDivElement>(null);
  const ref = useRef<{ ch: IChartApi; c: ISeriesApi<"Candlestick"> } | null>(null);
  useEffect(() => {
    if (!box.current) return;
    const ch = createChart(box.current, {
      autoSize: true, layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8b968f", fontSize: 11 },
      grid: { vertLines: { color: "rgba(255,255,255,0.05)" }, horzLines: { color: "rgba(255,255,255,0.05)" } },
      rightPriceScale: { borderColor: "rgba(255,255,255,.08)" }, timeScale: { borderColor: "rgba(255,255,255,.08)", timeVisible: true, secondsVisible: false },
    });
    const c = ch.addSeries(CandlestickSeries, { upColor: "#00e58f", downColor: "#ff5468", borderVisible: false, wickUpColor: "#00e58f", wickDownColor: "#ff5468" });
    ref.current = { ch, c };
    return () => { ch.remove(); ref.current = null; };
  }, []);
  useEffect(() => {
    const r = ref.current;
    if (!r) return;
    r.c.setData(data.candles.map((k) => ({ time: Math.floor(k[0] / 1000) as UTCTimestamp, open: k[1], high: k[2], low: k[3], close: k[4] })));
    const sc = run.verdict?.scenarios;
    const lines: [number | undefined, string, string, LineStyle][] = [
      [sc?.long?.trigger, "#00e58f", "long trigger", LineStyle.Dashed], [sc?.long?.invalidation, "#00e58f", "long invalid", LineStyle.Dotted],
      [sc?.short?.trigger, "#ff5468", "short trigger", LineStyle.Dashed], [sc?.short?.invalidation, "#ff5468", "short invalid", LineStyle.Dotted],
      [run.price?.value ?? undefined, "#9b5cff", "at decision", LineStyle.Solid],
    ];
    const made = lines.filter(([p]) => typeof p === "number").map(([p, color, title, style]) =>
      r.c.createPriceLine({ price: p as number, color, title, lineStyle: style, lineWidth: 1, axisLabelVisible: true }));
    r.ch.timeScale().fitContent();
    return () => { made.forEach((l) => r.c.removePriceLine(l)); };
  }, [data, run]);
  return <div ref={box} className="cm-chart" />;
}

function TrackRecord({ t, err, onPick }: { t: Track | null; err: string | null; onPick: (id: string) => void }) {
  const hs = t?.horizons ?? HORIZONS;
  const verdicts = (["LONG", "SHORT", "WAIT"] as const).filter((v) => t?.by_verdict[v]);
  return (
    <section className="cm-block" aria-labelledby="cm-track-title">
      <h2 id="cm-track-title">Track record <small className="cm-muted">research calls, not trades</small></h2>
      {!t && (err ? <p className="cm-stale" role="status">Track record unavailable right now.</p> : <div className="cm-skel" aria-busy="true" />)}
      {t && !verdicts.length && <p className="cm-empty">No resolved outcomes yet: calls are scored once 1h, 4h, 24h and 7d have passed.</p>}
      {t && verdicts.length > 0 && (
        <div className="cm-scroll"><table className="cm-table">
          <thead><tr><th scope="col">Verdict</th>{hs.map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
          <tbody>{verdicts.map((v) => (
            <tr key={v}><th scope="row"><Verdict a={v} /></th>{hs.map((h) => {
              const c = t.by_verdict[v]?.[h];
              if (!c) return <td key={h} className="cm-muted">—</td>;
              return <td key={h}>{v === "WAIT" ? <span>no position · move {fmt.pct(c.avg_ret_pct)}</span>
                : <span>{c.hits ?? Math.round((c.hit_rate ?? 0) * c.n)}/{c.n} right · avg {fmt.pct(c.avg_ret_pct)}</span>}<small className="cm-n"> n={c.n}</small></td>;
            })}</tr>
          ))}</tbody>
        </table></div>
      )}
      {t && <p className="cm-muted">{t.note} Small samples say little; this is not a performance claim.</p>}
      {t && t.recent.length > 0 && (
        <details className="cm-history">
          <summary>Historical recommendations ({t.recent.length})</summary>
          <div className="cm-scroll"><table className="cm-table">
            <thead><tr><th scope="col">Run</th><th scope="col">Price</th><th scope="col">Verdict</th>{HORIZONS.map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
            <tbody>{t.recent.map((r) => (
              <tr key={r.id}>
                <td><button type="button" className="cm-linkbtn" onClick={() => onPick(r.id)}>{r.asset.symbol} {r.timeframe}</button> <small>{when(r.created_at)}</small></td>
                <td>{fmt.px(r.price_at)}</td><td><Verdict a={r.verdict} /> <small>{r.rating}</small></td>
                {HORIZONS.map((h) => <td key={h}>{move(r.outcomes[h])}</td>)}
              </tr>
            ))}</tbody>
          </table></div>
        </details>
      )}
    </section>
  );
}
