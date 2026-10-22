import { useEffect, useRef } from "react";
import { fmt } from "./api";

/* MARKET STRUCTURE: what order books, derivatives positioning and trade flow show across exchanges (public feeds only).
   Every layer says what kind of number it is: observed (one exchange's feed), aggregated (several added up), a model
   estimate (liquidation zones: never confirmed positions) or an inferred pattern (TWAP-like slicing: never proof of who
   traded). A layer that couldn't be read says why; nothing is drawn in its place. */

export type SLayer = "oi" | "cvd" | "liq" | "zones" | "book" | "twap" | "heat";
export const SLAYERS: { k: SLayer; label: string }[] = [
  { k: "oi", label: "Aggregated OI" }, { k: "cvd", label: "CVD" }, { k: "liq", label: "Liquidations" }, { k: "zones", label: "Est. liq zones" },
  { k: "book", label: "Book walls" }, { k: "twap", label: "TWAP-like" }, { k: "heat", label: "Heatmap" },
];

type Base = { kind: string; basis: "observed" | "aggregated" | "estimate" | "inferred"; basis_label: string; title: string; unit: string;
  venues: string[]; skipped: Record<string, string>; fetched_at: number; notes?: string[] };
export type OiL = Base & { total_usd: number; by_venue: Record<string, number>; history: [number, number][]; history_venues: string[]; period: string;
  history_skipped: Record<string, string> };
export type CvdL = Base & { points: [number, number][]; flow: [number, number, number][]; buy_usd: number; sell_usd: number; delta_usd: number; tf: string };
export type LiqL = Base & { events: { venue: string; t: number; side: "long" | "short"; px: number; qty: number; usd: number }[]; long_usd: number;
  short_usd: number; count: number; first: number | null; by_venue?: Record<string, { long_usd: number; short_usd: number }> };
export type Zone = { px: number; px_hi: number; side: "long" | "short"; usd: number };
export type ZonesL = Base & { price: number; bins: Zone[]; top: Zone[]; assumptions: string[]; period: string };
export type Wall = { venue: string; side: "bid" | "ask"; px: number; usd: number; dist_pct: number };
export type BookL = Base & { mid: number; walls: Wall[]; imbalance: Record<string, { bid_usd: number; ask_usd: number; imbalance: number }>; common_pct: number };
export type Twap = { side: "buy" | "sell"; prints: number; interval_s: number; slice_qty: number; total_usd: number; start: number; end: number; times: number[];
  avg_px: number; regularity_cv: number; venue?: string; address?: string };
export type TwapL = Base & { patterns: Twap[]; trades: number; start: number; end: number; minutes: number };
export type HeatL = Base & { snapshots: [number, number, number, [number, number][]][]; recorded_coins: string[] };
type Err = { error: string };
export type Structure = { coin: string; timeframe: string; layers: Partial<{ oi: OiL | Err; cvd: CvdL | Err; liq: LiqL | Err; zones: ZonesL | Err;
  book: BookL | Err; twap: TwapL | Err; heat: HeatL | Err }> };

export const ok = <T,>(x: T | Err | undefined): T | null => (x && !("error" in (x as object)) ? (x as T) : null);
const utc = (ms: number) => new Date(ms).toISOString().slice(5, 16).replace("T", " ") + " UTC";

function Badge({ l }: { l: Base }) {
  return <span className={`kbs-basis is-${l.basis}`} title={l.basis === "estimate" ? "A model's output, not confirmed positions"
    : l.basis === "inferred" ? "A pattern in observed trades, not proof of who traded" : "Read from exchange feeds"}>{l.basis_label}</span>;
}

function Foot({ l, extra }: { l: Base; extra?: string }) {
  const skipped = Object.entries(l.skipped ?? {});
  return (
    <p className="kb-src">{l.venues.join(" + ") || "—"}{extra ? ` · ${extra}` : ""} · fetched {utc(l.fetched_at)}
      {skipped.length > 0 && <> · not included: {skipped.map(([v, w]) => `${v} (${w})`).join("; ")}</>}
      {(l.notes ?? []).length > 0 && <> · {(l.notes ?? []).join(" · ")}</>}</p>
  );
}

/** A small time series (SVG): a line, or bars around zero; start / end times under it. */
function Spark({ pts, bars, zero }: { pts?: [number, number][]; bars?: [number, number, number][]; zero?: boolean }) {
  const W = 600, H = 96;
  const xs = (pts ?? bars ?? []).map((p) => p[0]);
  if (xs.length < 2) return <p className="kb-empty">Not enough data in this window.</p>;
  const x0 = xs[0], x1 = xs[xs.length - 1];
  const X = (t: number) => ((t - x0) / (x1 - x0 || 1)) * (W - 8) + 4;
  let lo: number, hi: number;
  if (bars) { const m = Math.max(...bars.map((b) => Math.max(b[1], b[2])), 1); lo = -m; hi = m; }
  else { const v = pts!.map((p) => p[1]); lo = Math.min(...v, zero ? 0 : Infinity); hi = Math.max(...v, zero ? 0 : -Infinity); }
  const Y = (v: number) => H - 4 - ((v - lo) / (hi - lo || 1)) * (H - 8);
  const bw = Math.max(1, (W - 8) / xs.length - 1);
  return (
    <figure className="kbs-spark">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img">
        {(zero || bars) && <line x1={0} x2={W} y1={Y(0)} y2={Y(0)} stroke="rgba(255,255,255,.14)" />}
        {bars ? bars.map(([t, up, dn]) => <g key={t}>
          {up > 0 && <rect x={X(t) - bw / 2} width={bw} y={Y(up)} height={Y(0) - Y(up)} fill="#00e58f" />}
          {dn > 0 && <rect x={X(t) - bw / 2} width={bw} y={Y(0)} height={Y(-dn) - Y(0)} fill="#ff5468" />}</g>)
          : <polyline fill="none" stroke="#6ea8ff" strokeWidth={2} vectorEffect="non-scaling-stroke" points={pts!.map((p) => `${X(p[0])},${Y(p[1])}`).join(" ")} />}
      </svg>
      <figcaption><span>{utc(x0)}</span><span>{utc(x1)}</span></figcaption>
    </figure>
  );
}

function RecordedHeat({ h }: { h: HeatL }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current;
    if (!cv || h.snapshots.length < 2) return;
    const dpr = window.devicePixelRatio || 1;
    const W = cv.width = cv.clientWidth * dpr, H = cv.height = cv.clientHeight * dpr;
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, W, H);
    const mids = h.snapshots.map((s) => s[1]);
    const lo = Math.min(...mids) * 0.992, hi = Math.max(...mids) * 1.008;
    const vals = h.snapshots.flatMap((s) => s[3].filter(([px]) => px >= lo && px <= hi).map(([, u]) => u)).sort((a, b) => a - b);
    const ref97 = vals[Math.floor(vals.length * 0.97)] || 1;
    const y = (px: number) => H - ((px - lo) / (hi - lo)) * H;
    const cw = W / h.snapshots.length;
    h.snapshots.forEach(([, , step, bins], i) => {
      for (const [px, u] of bins) {
        if (px < lo || px > hi) continue;
        const a = Math.min(1, Math.log1p(u) / Math.log1p(ref97));
        ctx.fillStyle = `rgba(${Math.round(40 + 215 * a)},${Math.round(60 + 170 * a)},${Math.round(150 - 70 * a)},${(0.15 + 0.8 * a).toFixed(3)})`;
        ctx.fillRect(i * cw, y(px + step), Math.ceil(cw) + 0.5, Math.max(1, y(px) - y(px + step)));
      }
    });
    ctx.strokeStyle = "rgba(255,255,255,.9)";
    ctx.lineWidth = 1.5 * dpr;
    ctx.beginPath();
    h.snapshots.forEach((s, i) => (i ? ctx.lineTo(i * cw + cw / 2, y(s[1])) : ctx.moveTo(cw / 2, y(s[1]))));
    ctx.stroke();
    ctx.font = `${11 * dpr}px ui-monospace, monospace`;
    ctx.fillStyle = "rgba(238,243,240,.75)";
    ctx.textAlign = "right";
    for (const k of [0.05, 0.5, 0.95]) ctx.fillText(fmt.px(hi - (hi - lo) * k), W - 6 * dpr, y(hi - (hi - lo) * k) + 4 * dpr);
  }, [h]);
  if (h.snapshots.length < 2) {
    return <p className="kb-empty">{h.recorded_coins.length ? `No recorded books yet for this coin. Price Chat records ${h.recorded_coins.join(", ")} every 2 minutes.` : "No recorded books yet."}</p>;
  }
  const s0 = h.snapshots[0][0], s1 = h.snapshots[h.snapshots.length - 1][0];
  return <>
    <canvas ref={ref} className="kbh" aria-label="Resting liquidity over time (brighter = more USD), white line = mid price" />
    <figure className="kbs-spark"><figcaption><span>{utc(s0)}</span><span>{utc(s1)}</span></figcaption></figure>
  </>;
}

export function StructurePanel({ d, on, err }: { d: Structure | null; on: Set<SLayer>; err: string | null }) {
  if (!on.size) return null;
  const L = d?.layers ?? {};
  const fail = (k: SLayer) => { const x = L[k] as Err | undefined; return x && "error" in x ? <p className="kb-warn">Unavailable: {x.error}</p> : null; };
  const oi = ok<OiL>(L.oi), cvd = ok<CvdL>(L.cvd), liq = ok<LiqL>(L.liq), z = ok<ZonesL>(L.zones), b = ok<BookL>(L.book), tw = ok<TwapL>(L.twap),
    heat = ok<HeatL>(L.heat);
  const block = (k: SLayer, title: string, l: Base | null, body: React.ReactNode) => on.has(k) && (
    <div className="kbs-block" key={k}>
      <h4>{title} {l && <Badge l={l} />}</h4>
      {fail(k) ?? (l ? body : <div className="kb-skel" style={{ height: 96 }} />)}
    </div>
  );
  return (
    <section className="kbp-panel kbs" aria-label="Market structure">
      <header className="kbp-ph"><h3>Market structure · {d?.coin ?? ""}</h3><span className="kb-src">public exchange feeds · labelled by basis</span></header>
      {err && <p className="kb-warn">{err}</p>}
      <div className="kbs-grid">
        {block("oi", "Open interest", oi, oi && <>
          <div className="kbs-kpi"><b>{fmt.big(oi.total_usd)}</b><small>now, {oi.venues.join(" + ")}</small></div>
          <ul className="kbs-venues">{Object.entries(oi.by_venue).sort((a, c) => c[1] - a[1]).map(([v, u]) =>
            <li key={v}><span>{v}</span><i style={{ width: `${(u / oi.total_usd) * 100}%` }} /><b>{fmt.big(u)}</b></li>)}</ul>
          <Spark pts={oi.history} />
          <Foot l={oi} extra={`history ${oi.history_venues.join(" + ") || "—"} (${oi.period})`} />
        </>)}
        {block("cvd", "CVD · taker buys − sells", cvd, cvd && <>
          <div className="kbs-kpi"><b className={cvd.delta_usd >= 0 ? "is-up" : "is-down"}>{cvd.delta_usd >= 0 ? "+" : ""}{fmt.big(cvd.delta_usd)}</b>
            <small>{fmt.big(cvd.buy_usd)} bought · {fmt.big(cvd.sell_usd)} sold aggressively ({cvd.tf} bars, perps)</small></div>
          <Spark pts={cvd.points} zero />
          <Foot l={cvd} />
        </>)}
        {block("liq", "Liquidations · reported", liq, liq && <>
          <div className="kbs-kpi"><b>{fmt.big(liq.long_usd + liq.short_usd)}</b>
            <small><span className="is-down">longs {fmt.big(liq.long_usd)}</span> · <span className="is-up">shorts {fmt.big(liq.short_usd)}</span> · {liq.count} fills</small></div>
          {Object.keys(liq.by_venue ?? {}).length > 1 && <ul className="kbs-venues">{Object.entries(liq.by_venue ?? {}).map(([v, x]) =>
            <li key={v}><span>{v}</span><i style={{ width: `${((x.long_usd + x.short_usd) / Math.max(1, liq.long_usd + liq.short_usd)) * 100}%` }} />
              <b>{fmt.big(x.long_usd + x.short_usd)}</b></li>)}</ul>}
          {liq.events.length >= 2 ? <Spark bars={liqBars(liq)} /> : <p className="kb-empty">No liquidations reported in this window.</p>}
          <Foot l={liq} extra={liq.first ? `since ${utc(liq.first)}` : undefined} />
        </>)}
        {block("zones", "Liquidation zones · estimated", z, z && <>
          <ol className="kbs-zones">{z.top.map((x) => <li key={`${x.side}${x.px}`} className={x.side === "long" ? "is-down" : "is-up"}>
            <span>est. {x.side} liq</span><b>{fmt.px(x.px)}–{fmt.px(x.px_hi)}</b><span>{fmt.big(x.usd)}</span>
            <small>{fmt.pct(((x.px - z.price) / z.price) * 100, 1)}</small></li>)}</ol>
          <p className="kb-src kbs-warnline">Model estimate, not confirmed positions. {z.assumptions.slice(1).join(". ")}.</p>
          <Foot l={z} />
        </>)}
        {block("book", "Order book · walls & imbalance", b, b && <>
          {Object.entries(b.imbalance).map(([w, x]) => <div key={w} className="kbf-imb"><small>Imbalance within ±{w} (all venues)</small>
            <div className="kbf-bar"><span className="is-b" style={{ width: `${(x.imbalance + 1) * 50}%` }} /></div>
            <b className={x.imbalance >= 0 ? "is-up" : "is-down"}>{x.imbalance >= 0 ? "bids" : "asks"} {fmt.n(Math.abs(x.imbalance) * 100, 0)}% heavier</b>
            <small>{fmt.big(x.bid_usd)} bid · {fmt.big(x.ask_usd)} ask</small></div>)}
          <ol className="kbs-zones">{b.walls.slice(0, 6).map((w) => <li key={`${w.venue}${w.px}`} className={w.side === "bid" ? "is-up" : "is-down"}>
            <span>{w.venue} {w.side}</span><b>{fmt.px(w.px)}</b><span>{fmt.big(w.usd)}</span><small>{fmt.pct(w.dist_pct, 2)}</small></li>)}</ol>
          {!b.walls.length && <p className="kb-empty">No single level stands out (≥10× the venue's median level).</p>}
          <Foot l={b} />
        </>)}
        {block("twap", "TWAP-like execution · inferred", tw, tw && <>
          {tw.patterns.length ? <ol className="kbs-zones">{tw.patterns.map((p) => <li key={p.start} className={p.side === "buy" ? "is-up" : "is-down"}>
            <span>{p.venue ?? ""} {p.side}s{p.address ? ` · ${p.address}` : ""}</span><b>{p.prints}× every {p.interval_s.toFixed(0)}s</b><span>{fmt.big(p.total_usd)}</span>
            <small>{utc(p.start).slice(6)}–{utc(p.end).slice(6)}</small></li>)}</ol>
            : <p className="kb-empty">No regular same-size slicing in the last {tw.minutes} min ({tw.trades.toLocaleString()} trades checked).</p>}
          <p className="kb-src kbs-warnline">Consistent with time-sliced execution. Price Chat can't tell who placed these orders; an on-chain address is not an identity.</p>
          <Foot l={tw} extra={`${tw.trades.toLocaleString()} trades`} />
        </>)}
        {block("heat", "Liquidity heatmap · recorded", heat, heat && <><RecordedHeat h={heat} /><Foot l={heat} /></>)}
      </div>
    </section>
  );
}

function liqBars(l: LiqL): [number, number, number][] {
  const ev = l.events;
  const t0 = ev[0].t, t1 = ev[ev.length - 1].t;
  const n = 48, w = Math.max(60_000, (t1 - t0) / n);
  const out = new Map<number, [number, number, number]>();
  for (const e of ev) {
    const k = t0 + Math.floor((e.t - t0) / w) * w;
    const b = out.get(k) ?? [k, 0, 0];
    if (e.side === "short") b[1] += e.usd; else b[2] += e.usd;  // shorts squeezed up (green), longs flushed down (red)
    out.set(k, b);
  }
  return [...out.values()].sort((a, b) => a[0] - b[0]);
}
