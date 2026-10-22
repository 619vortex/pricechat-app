import { useEffect, useMemo, useRef, useState } from "react";
import { memberApi } from "../members";
import { NODES, NODE_LABEL, STRAT, fmt, usePoll, type Check, type Ev, type Evaluation, type Node, type Overview, type Position } from "./api";

/* LIVE BRAIN: the pipeline graph from a live, rule-by-rule evaluation by the real strategy code, the member's positions with
   their verified protection, and the decision timeline with repetitive evaluations grouped. Pulses only when a NEW real
   evaluation arrives. */

const ST_LABEL: Record<string, string> = { pass: "PASS", fail: "FAIL", waiting: "WAITING", open: "OPEN", none: "—", done: "DONE", unavailable: "N/A" };

function explain(name: string, n: Node): string {
  const cs = n.checks ?? [];
  const ok = cs.filter((c) => c.ok === true).length, bad = cs.filter((c) => c.ok === false);
  if (name === "risk" || name === "execution" || name === "position" || name === "signal") return n.output ?? "";
  if (!cs.length) return n.output ?? "Not evaluated (an earlier rule decides first).";
  if (cs.every((c) => c.ok === null)) return `Not evaluated yet (needs a trend first): ${cs.map((c) => c.value).join(" · ")}`;
  if (!bad.length) return `All ${ok} rule${ok === 1 ? "" : "s"} pass.`;
  return `${bad.length} of ${cs.filter((c) => c.ok !== null).length} rules fail: ${bad.map((c) => c.rule).join("; ")}.`;
}

function Graph({ ev, sel, onSel, pulse }: { ev: Evaluation; sel: string; onSel: (n: string) => void; pulse: number }) {
  return (
    <ol className="kbg" aria-label="Decision pipeline">
      {NODES.map((k, i) => {
        const n = ev.nodes[k];
        return (
          <li key={k} className={`kbg-node is-${n.status}${sel === k ? " is-sel" : ""}`} style={{ ["--d" as string]: `${i * 90}ms` }} data-pulse={pulse}>
            <button onClick={() => onSel(k)} aria-pressed={sel === k}>
              <span className="kbg-top"><b>{NODE_LABEL[k]}</b><em>{ST_LABEL[n.status] ?? n.status.toUpperCase()}</em></span>
              <span className="kbg-out">{explain(k, n)}</span>
              <span className="kbg-meta">{fmt.time(ev.at)}{n.ms != null ? ` · ${n.ms} ms` : ""}{n.checks?.length ? ` · ${n.checks.length} rules` : ""}</span>
            </button>
            {i < NODES.length - 1 && <span className="kbg-link" aria-hidden><i /></span>}
          </li>
        );
      })}
    </ol>
  );
}

function Rule({ c }: { c: Check }) {
  const [open, setOpen] = useState(false);
  return (
    <li className={`kbr is-${c.ok === true ? "ok" : c.ok === false ? "bad" : "na"}`}>
      <button onClick={() => setOpen(!open)} aria-expanded={open}>
        <span aria-hidden>{c.ok === true ? "✓" : c.ok === false ? "✗" : "·"}</span> {c.rule}
        {c.strategy && <small> · {STRAT[c.strategy] ?? c.strategy}</small>}
      </button>
      {open && <p className="kbr-ev">Measured: <b>{c.value}</b></p>}
    </li>
  );
}

function Inspector({ ev, node }: { ev: Evaluation; node: string }) {
  const n = ev.nodes[node];
  return (
    <section className="kbp-panel kbi" aria-label={`${NODE_LABEL[node]} details`}>
      <header className="kbp-ph"><h3>{NODE_LABEL[node]} · {ev.coin}</h3><span className={`kb-pill is-${n.status}`}>{ST_LABEL[n.status] ?? n.status}</span></header>
      <p className="kbi-expl">{explain(node, n)}</p>
      {n.source && <p className="kb-src">{n.source}</p>}
      {n.checks && n.checks.length > 0 && <ul className="kbr-list">{n.checks.map((c, i) => <Rule key={i} c={c} />)}</ul>}
      {node === "orderflow" && <p className="kb-src">Bid {fmt.big(ev.book.bid_depth_usd)} vs ask {fmt.big(ev.book.ask_depth_usd)} within 0.5% · spread {fmt.n(ev.book.spread_bps, 1)} bps</p>}
      {node === "risk" && n.plan && <dl className="kb-dl">
        <div><dt>Size</dt><dd>{fmt.n(n.plan.size, 6)} ({fmt.usd(n.plan.notional)})</dd></div>
        <div><dt>Leverage</dt><dd>{n.plan.leverage}x</dd></div><div><dt>Loss at stop</dt><dd>{fmt.usd(n.plan.max_loss_usd)}</dd></div>
        <div><dt>Margin</dt><dd>{fmt.usd(n.plan.margin)}</dd></div></dl>}
      {node === "execution" && n.note && <p className="kb-src">{n.note}</p>}
      {node === "signal" && ev.runs.map((r) => (
        <div key={r.strategy} className={`kbv ${r.decision === "NO TRADE" ? "is-rej" : "is-go"}`}>
          <p className="kbv-h"><b>{ev.coin} {r.decision === "NO TRADE" ? "NO TRADE" : r.decision}</b> · {r.name}{!r.enabled && <em> · shadow (not traded)</em>}<small> {r.ms} ms</small></p>
          <ul className="kbr-list">{r.checks.map((c, i) => <Rule key={i} c={c} />)}</ul>
          {r.decision !== "NO TRADE" && <p className="kb-src">Entry {r.entry?.map(fmt.px).join(" – ")} · stop {fmt.px(r.stop)} · target {fmt.px(r.target)} · R/R {r.rr}</p>}
        </div>
      ))}
    </section>
  );
}

export function PositionsPanel({ ps, onReplay }: { ps: Position[]; onReplay: (signalId: number) => void }) {
  if (!ps.length) return <section className="kbp-panel"><header className="kbp-ph"><h3>Positions</h3></header><p className="kb-empty">No bot positions open.</p></section>;
  return (
    <section className="kbp-panel" aria-label="Open positions">
      <header className="kbp-ph"><h3>Positions</h3><span className="kb-src">from {ps[0].mode === "PAPER" ? "paper records" : "Hyperliquid"}</span></header>
      {ps.map((p) => (
        <article key={p.id} className={`kbpos is-${p.side.toLowerCase()}${p.protected ? "" : " is-unprot"}`}>
          <div className="kbpos-h">
            <b>{p.coin} {p.side}</b><span className="kb-pill">{p.mode}</span>{p.test && <span className="kb-pill is-warn">MANUAL</span>}
            <span className={`kbpos-pnl ${p.upnl == null ? "" : p.upnl >= 0 ? "is-up" : "is-down"}`}>{fmt.pnl(p.upnl)} {p.upnl_pct != null && <small title="Return on margin (ROE)">{fmt.pct(p.upnl_pct)} ROE</small>}</span>
          </div>
          <dl className="kb-dl is-dense">
            <div><dt>Entry</dt><dd>{fmt.px(p.entry_px)}</dd></div><div><dt>Mark</dt><dd>{fmt.px(p.mark)}</dd></div>
            <div><dt>Size</dt><dd>{fmt.n(p.size, 6)}{p.size_usd ? ` · ${fmt.usd(p.size_usd)}` : ""}</dd></div><div><dt>Leverage</dt><dd>{p.leverage ?? "—"}x</dd></div>
            <div><dt>Stop</dt><dd className="is-down">{fmt.px(p.stop)}</dd></div><div><dt>TP1 / TP2</dt><dd className="is-up">{fmt.px(p.tp1)}{p.tp1_done ? " ✓" : ""} / {fmt.px(p.tp2)}</dd></div>
            <div><dt>Liquidation</dt><dd>{p.liq_px ? fmt.px(p.liq_px) : p.mode === "PAPER" ? "n/a (paper)" : "—"}</dd></div><div><dt>Loss at stop</dt><dd>{fmt.usd(p.loss_at_stop)}</dd></div>
            <div><dt>Fees</dt><dd>{fmt.usd(p.fees, 4)}</dd></div><div><dt>Funding</dt><dd>{p.funding == null ? (p.mode === "PAPER" ? "n/a (paper)" : "—") : fmt.usd(p.funding, 4)}</dd></div>
            <div><dt>Age</dt><dd>{fmt.dur(p.age_s)}</dd></div><div><dt>Strategy</dt><dd>{p.strategy_name ?? "—"}{p.signal_number ? ` · #${String(p.signal_number).padStart(3, "0")}` : ""}</dd></div>
          </dl>
          <p className={`kbpos-prot ${p.protection.verified ? "is-ok" : p.protected ? "is-sim" : "is-bad"}`}>{p.protection.verified ? "🛡" : p.protected ? "🧪" : "⚠️"} {p.protection.label}
            <small> · TP1 {p.protection.tp1.toLowerCase()} · TP2 {p.protection.tp2.toLowerCase()}</small></p>
          <p className="kb-src">{p.managed}</p>
          {p.warnings.map((w) => <p key={w} className="kb-warn">{w}</p>)}
          <button className="kb-link" onClick={() => onReplay(p.signal_id)}>Replay this trade →</button>
        </article>
      ))}
    </section>
  );
}

/* ---- timeline with repetitive evaluations grouped ---- */
type Row = { kind: "ev"; e: Ev } | { kind: "group"; key: string; coin: string; items: Ev[] };

export function group(evs: Ev[]): Row[] {
  const rows: Row[] = [];
  for (const e of evs) {
    const d = e.data as Record<string, unknown>;
    const routine = e.kind === "strategy" && d.decision === "NO TRADE";
    const prev = rows[rows.length - 1];
    if (routine) {
      const g = rows.find((r): r is Extract<Row, { kind: "group" }> => r.kind === "group" && r.coin === e.coin
        && new Date(r.items[r.items.length - 1].at).getTime() - new Date(e.at).getTime() < 6 * 3600_000);
      if (g) { g.items.push(e); continue; }
      rows.push({ kind: "group", key: `g${e.id}`, coin: e.coin ?? "?", items: [e] });
      continue;
    }
    void prev;
    rows.push({ kind: "ev", e });
  }
  return rows;
}

function topReasons(items: Ev[]): [string, number][] {
  const n = new Map<string, number>();
  for (const e of items) for (const r of ((e.data as { reasons?: string[] }).reasons ?? [])) n.set(r, (n.get(r) ?? 0) + 1);
  return [...n.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3);
}

function EvRow({ e, onJump }: { e: Ev; onJump: (e: Ev) => void }) {
  const [open, setOpen] = useState(false);
  const d = e.data as Record<string, unknown>;
  const checks = (d.checks as Check[] | undefined) ?? [];
  return (
    <li className={`kbt is-${e.kind}`}>
      <time>{fmt.time(e.at)}</time><span className="kbt-kind">{e.kind}</span>
      <div className="kbt-b">
        <button className="kbt-title" onClick={() => setOpen(!open)} aria-expanded={open}>{e.title}{e.mine && <em> · yours</em>}</button>
        {open && <>
          {checks.length > 0 && <ul className="kbr-list">{checks.map((c, i) => <Rule key={i} c={c} />)}</ul>}
          {checks.length === 0 && Array.isArray(d.reasons) && <ul className="kbr-list">{(d.reasons as string[]).map((r) => <li key={r} className="kbr is-bad"><span>✗</span> {r}</li>)}</ul>}
          <p className="kb-src">{e.source ?? "—"}{e.source_at ? ` · data from ${new Date(e.source_at).toLocaleString()}` : ""}</p>
          {(e.coin && (e.kind === "strategy" || e.kind === "signal" || e.kind === "risk" || e.kind === "position")) &&
            <button className="kb-link" onClick={() => onJump(e)}>Show on chart →</button>}
        </>}
      </div>
    </li>
  );
}

function Group({ g, onJump }: { g: Extract<Row, { kind: "group" }>; onJump: (e: Ev) => void }) {
  const [open, setOpen] = useState(false);
  const first = g.items[g.items.length - 1], last = g.items[0];
  const span = (new Date(last.at).getTime() - new Date(first.at).getTime()) / 3600_000;
  const shadow = g.items.some((x) => (x.data as { shadow?: boolean }).shadow);
  return (
    <li className="kbt is-group">
      <time>{fmt.time(last.at)}</time><span className="kbt-kind">×{g.items.length}</span>
      <div className="kbt-b">
        <button className="kbt-title" onClick={() => setOpen(!open)} aria-expanded={open}>
          <b>{g.coin}</b> evaluated {g.items.length} time{g.items.length === 1 ? "" : "s"}{span >= 0.1 ? ` over ${span.toFixed(1)}h` : ""}{shadow ? " (shadow)" : ""} · no trade
        </button>
        <p className="kbt-why">{topReasons(g.items).map(([r, n]) => `${r}${n > 1 ? ` (${n}×)` : ""}`).join(" · ")}</p>
        {open && <ol className="kbt-sub">{g.items.map((e) => <EvRow key={e.id} e={e} onJump={onJump} />)}</ol>}
      </div>
    </li>
  );
}

export function Timeline({ onJump, evs, loadMore, more }: { onJump: (e: Ev) => void; evs: Ev[]; loadMore: () => void; more: boolean }) {
  const rows = useMemo(() => group(evs), [evs]);
  return (
    <section className="kbp-panel kbt-panel" aria-label="Decision timeline">
      <header className="kbp-ph"><h3>Decision log</h3><span className="kb-src">{evs.length} events · full audit log kept</span></header>
      <ol className="kbt-list">{rows.map((r) => r.kind === "group" ? <Group key={r.key} g={r} onJump={onJump} /> : <EvRow key={r.e.id} e={r.e} onJump={onJump} />)}</ol>
      {!evs.length && <p className="kb-empty">No decisions logged yet.</p>}
      {more && evs.length > 0 && <button className="kb-more" onClick={loadMore}>Load older</button>}
    </section>
  );
}

export function useEvents(): { evs: Ev[]; more: boolean; loadMore: () => void; err: string | null } {
  const [evs, setEvs] = useState<Ev[]>([]);
  const [more, setMore] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const q = "strategy,signal,risk,order,exchange,position,system";
  useEffect(() => {
    let live = true;
    const load = async () => {
      if (document.hidden) return;
      try {
        const r = await memberApi<{ items: Ev[] }>(`/members/kiwi-brain/events?limit=200&kind=${q}`);
        if (!live) return;
        setErr(null);
        setEvs((cur) => {
          const ids = new Set(r.items.map((x) => x.id));
          const older = cur.filter((x) => !ids.has(x.id) && x.id < (r.items[r.items.length - 1]?.id ?? 0));
          return [...r.items, ...older];
        });
      } catch (e) {
        if (live) setErr(e instanceof Error ? e.message : "Unavailable");
      }
    };
    void load();
    const id = window.setInterval(() => { if (!document.hidden) void load(); }, 20_000);
    return () => { live = false; window.clearInterval(id); };
  }, []);
  const loadMore = async () => {
    const last = evs[evs.length - 1];
    if (!last) return;
    const r = await memberApi<{ items: Ev[] }>(`/members/kiwi-brain/events?limit=200&kind=${q}&before_id=${last.id}`);
    setMore(r.items.length === 200);
    setEvs((cur) => [...cur, ...r.items]);
  };
  return { evs, more, loadMore: () => void loadMore(), err };
}

export function LiveBrain({ o, coins, onJump, onReplay, ev: events }: { o: Overview; coins: string[]; onJump: (e: Ev) => void;
  onReplay: (signalId: number) => void; ev: ReturnType<typeof useEvents> }) {
  const [coin, setCoin] = useState(coins[0] ?? "BTC");
  const [sel, setSel] = useState<string>("signal");
  const ev = usePoll<Evaluation>(`/members/kiwi-brain/evaluate?coin=${coin}`, 30_000);
  const [pulse, setPulse] = useState(0);
  const lastAt = useRef<string | null>(null);
  useEffect(() => {
    if (ev.data && ev.data.at !== lastAt.current) { lastAt.current = ev.data.at; setPulse((p) => p + 1); }  // a new real evaluation
  }, [ev.data]);
  const e = ev.data && ev.data.coin === coin ? ev.data : null;
  return (
    <div className="kbl">
      <div className="kbl-main">
        <section className="kbp-panel">
          <header className="kbp-ph">
            <h3>Decision pipeline</h3>
            <div className="kb-tabs" role="tablist">{coins.map((c) => <button key={c} role="tab" aria-selected={c === coin} onClick={() => setCoin(c)}>{c}</button>)}</div>
          </header>
          <p className="kb-src">The real strategy rules run now on live Hyperliquid data, every 30 s while you watch. {o.health.engine.state === "shadow" ?
            "Shadow mode: nothing is traded from these evaluations." : ""} Nothing on this page sends orders.</p>
          {ev.err && <p className="kb-warn">Evaluation unavailable: {ev.err}</p>}
          {e ? <Graph ev={e} sel={sel} onSel={setSel} pulse={pulse} /> : !ev.err && <div className="kb-skel" style={{ height: 320 }} />}
        </section>
        {e && <Inspector ev={e} node={sel} />}
      </div>
      <div className="kbl-side">
        <PositionsPanel ps={o.positions} onReplay={onReplay} />
        <section className="kbp-panel kbeng">
          <header className="kbp-ph"><h3>Strategy engine</h3><span className={`kb-pill is-${o.health.engine.state}`}>{o.health.engine.state.toUpperCase()}</span></header>
          <p>{o.health.engine.reason}</p>
          <dl className="kb-dl is-dense">
            <div><dt>Last run</dt><dd>{fmt.ago(o.health.engine.job?.last_finished_at)}{o.health.engine.job?.duration_ms ? ` · ${(o.health.engine.job.duration_ms / 1000).toFixed(1)}s` : ""}</dd></div>
            <div><dt>Next run</dt><dd>{o.health.engine.next_at ? fmt.until(o.health.engine.next_at) : "not scheduled"}</dd></div>
            {o.health.engine.strategies.map((s) => <div key={s.id}><dt>{s.name}</dt><dd className={s.on ? "is-up" : ""}>{s.on ? "trading" : "off (shadow)"}</dd></div>)}
          </dl>
          {o.health.engine.job?.last_error && <p className="kb-warn">{o.health.engine.job.last_error}</p>}
        </section>
        <Timeline evs={events.evs} more={events.more} loadMore={events.loadMore} onJump={onJump} />
      </div>
    </div>
  );
}
