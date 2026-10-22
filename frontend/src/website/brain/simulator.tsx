import { useEffect, useMemo, useState } from "react";
import { fmt, usePoll } from "./api";

/* WHAT IF? A calculator with Hyperliquid's real contract specs. It never places an order (there is no code path here that
   could). Liquidation is an ESTIMATE (isolated margin, the exchange's maintenance margin approximated as 1 / (2 x max leverage)). */

type Contract = { coin: string; mark: number; max_leverage: number; sz_decimals: number; funding_h: number; taker_fee: number; min_notional: number; fee_note: string };
type Inputs = { side: "LONG" | "SHORT"; entry: number; stop: number; tp: number; size: number; lev: number; slip_bps: number; fee_bps: number };
type Saved = Inputs & { coin: string; at: number; name: string };
const KEY = "pc-kb-scenarios";

export function simulate(x: Inputs, c: Pick<Contract, "max_leverage" | "funding_h">, equity: number | null) {
  const sg = x.side === "LONG" ? 1 : -1;
  const qty = x.entry > 0 ? x.size / x.entry : 0;
  const fee = x.fee_bps / 1e4, slip = x.slip_bps / 1e4;
  const exitCost = (px: number) => qty * px * (fee + slip);
  const entryCost = x.size * (fee + slip);
  const lossAtStop = qty * Math.abs(x.entry - x.stop) + entryCost + exitCost(x.stop);
  const profitAtTp = qty * (x.tp - x.entry) * sg - entryCost - exitCost(x.tp);
  const mm = 1 / (2 * c.max_leverage);
  const liq = x.lev > 0 ? x.entry * (1 - sg * (1 / x.lev - mm)) : null;
  const stopBeyondLiq = liq != null && (sg > 0 ? x.stop <= liq : x.stop >= liq);
  return {
    qty, lossAtStop, profitAtTp, rr: lossAtStop > 0 ? profitAtTp / lossAtStop : null, margin: x.lev > 0 ? x.size / x.lev : null, liq, stopBeyondLiq,
    fees: x.size * fee + qty * x.tp * fee, fundingDay: x.size * c.funding_h * 24 * sg, exposurePct: equity ? (x.size / equity) * 100 : null,
    marginPct: equity && x.lev ? (x.size / x.lev / equity) * 100 : null, valid: x.entry > 0 && x.size > 0 && (sg > 0 ? x.stop < x.entry && x.tp > x.entry : x.stop > x.entry && x.tp < x.entry),
  };
}

function Payoff({ x, r }: { x: Inputs; r: ReturnType<typeof simulate> }) {
  const sg = x.side === "LONG" ? 1 : -1;
  const lo = Math.min(x.stop, x.tp, x.entry) * 0.995, hi = Math.max(x.stop, x.tp, x.entry) * 1.005;
  const W = 600, H = 150, pts = 60;
  const pnl = (p: number) => r.qty * (p - x.entry) * sg;
  const vals = Array.from({ length: pts + 1 }, (_, i) => lo + ((hi - lo) * i) / pts);
  const ys = vals.map(pnl), ymin = Math.min(...ys, -r.lossAtStop), ymax = Math.max(...ys, r.profitAtTp);
  const X = (p: number) => ((p - lo) / (hi - lo)) * W, Y = (v: number) => H - ((v - ymin) / (ymax - ymin || 1)) * (H - 8) - 4;
  return (
    <svg className="kbsim-pay" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Profit and loss at exit price, before costs">
      <line x1={0} x2={W} y1={Y(0)} y2={Y(0)} stroke="rgba(255,255,255,.15)" />
      <polyline fill="none" stroke="#9b5cff" strokeWidth={2} points={vals.map((p) => `${X(p)},${Y(pnl(p))}`).join(" ")} />
      {[[x.stop, "#ff5468", "stop"], [x.entry, "#ffffff", "entry"], [x.tp, "#00e58f", "target"]].map(([p, col, t]) =>
        <g key={String(t)}><line x1={X(Number(p))} x2={X(Number(p))} y1={0} y2={H} stroke={String(col)} strokeDasharray="3 3" opacity={0.6} />
          <text x={X(Number(p)) + 4} y={12} fill={String(col)} fontSize={11}>{String(t)}</text></g>)}
    </svg>
  );
}

function load(): Saved[] {
  try { return JSON.parse(localStorage.getItem(KEY) ?? "[]") as Saved[]; } catch { return []; }
}

export function Simulator({ coin, equity }: { coin: string; equity: number | null }) {
  const ct = usePoll<Contract>(`/members/kiwi-brain/contract?coin=${coin}`, 60_000);
  const c = ct.data?.coin === coin ? ct.data : null;
  const [x, setX] = useState<Inputs>({ side: "LONG", entry: 0, stop: 0, tp: 0, size: 100, lev: 3, slip_bps: 5, fee_bps: 4.5 });
  const [saved, setSaved] = useState<Saved[]>(load);
  useEffect(() => {
    if (!c) return;
    const m = c.mark;
    setX((cur) => cur.entry && Math.abs(cur.entry / m - 1) < 0.2 ? cur : { ...cur, entry: +m.toPrecision(6), stop: +(m * 0.98).toPrecision(6), tp: +(m * 1.04).toPrecision(6),
      side: "LONG", lev: Math.min(cur.lev, c.max_leverage) });
  }, [c]);
  const r = useMemo(() => simulate(x, c ?? { max_leverage: 20, funding_h: 0 }, equity), [x, c, equity]);
  const set = (k: keyof Inputs) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setX((cur) => ({ ...cur, [k]: k === "side" ? e.target.value : Number(e.target.value) }));
  const save = () => {
    const n = [{ ...x, coin, at: Date.now(), name: `${coin} ${x.side} ${fmt.px(x.entry)}` }, ...saved].slice(0, 12);
    setSaved(n);
    try { localStorage.setItem(KEY, JSON.stringify(n)); } catch { /* blocked */ }
  };
  return (
    <section className="kbp-panel kbsim" aria-label="Trade simulator">
      <header className="kbp-ph"><h3>What if? · trade simulator</h3><span className="kb-pill is-warn">SIMULATION · NEVER PLACES ORDERS</span></header>
      <div className="kbsim-grid">
        <div className="kbsim-in">
          <label>Side<select value={x.side} onChange={set("side")}><option>LONG</option><option>SHORT</option></select></label>
          <label>Entry<input type="number" step="any" value={x.entry} onChange={set("entry")} /></label>
          <label>Stop-loss<input type="number" step="any" value={x.stop} onChange={set("stop")} /></label>
          <label>Take-profit<input type="number" step="any" value={x.tp} onChange={set("tp")} /></label>
          <label>Size (USD)<input type="number" step="any" min={0} value={x.size} onChange={set("size")} /></label>
          <label>Leverage<input type="number" min={1} max={c?.max_leverage ?? 50} value={x.lev} onChange={set("lev")} /></label>
          <label>Slippage (bps/side)<input type="number" step="any" min={0} value={x.slip_bps} onChange={set("slip_bps")} /></label>
          <label>Fee (bps/side)<input type="number" step="any" min={0} value={x.fee_bps} onChange={set("fee_bps")} /></label>
        </div>
        <div className="kbsim-out">
          {!r.valid && <p className="kb-warn">For a {x.side.toLowerCase()}, the stop must be {x.side === "LONG" ? "below" : "above"} the entry and the target on the other side.</p>}
          <dl className="kb-dl">
            <div><dt>Max loss at stop</dt><dd className="is-down">{fmt.usd(-r.lossAtStop)}</dd></div>
            <div><dt>Profit at target</dt><dd className="is-up">{fmt.usd(r.profitAtTp)}</dd></div>
            <div><dt>Risk / reward</dt><dd>{r.rr != null ? `1 : ${fmt.n(r.rr, 2)}` : "—"}</dd></div>
            <div><dt>Margin</dt><dd>{fmt.usd(r.margin)}{r.marginPct != null ? ` · ${fmt.n(r.marginPct, 1)}% of equity` : ""}</dd></div>
            <div><dt>Est. liquidation</dt><dd className={r.stopBeyondLiq ? "is-down" : ""}>{fmt.px(r.liq)}{r.stopBeyondLiq ? " ⚠ before your stop" : ""}</dd></div>
            <div><dt>Est. fees (in + out)</dt><dd>{fmt.usd(r.fees)}</dd></div>
            <div><dt>Funding / day now</dt><dd>{fmt.usd(-r.fundingDay)}</dd></div>
            <div><dt>Exposure</dt><dd>{r.exposurePct != null ? `${fmt.n(r.exposurePct, 1)}% of equity` : "equity unknown"}</dd></div>
            <div><dt>Quantity</dt><dd>{fmt.n(r.qty, c?.sz_decimals ?? 4)} {coin}</dd></div>
          </dl>
          {x.size > 0 && c && x.size < c.min_notional && <p className="kb-warn">Below Hyperliquid's ${c.min_notional} minimum order.</p>}
          {r.valid && <Payoff x={x} r={r} />}
          <button className="kb-btn" onClick={save} disabled={!r.valid}>Save scenario</button>
        </div>
      </div>
      <p className="kb-src">{c ? `${c.coin} mark ${fmt.px(c.mark)} · max ${c.max_leverage}x · size step ${10 ** -c.sz_decimals} · funding ${(c.funding_h * 100).toFixed(4)}%/h now (it changes hourly). ${c.fee_note}` : "Loading contract specs…"}
        {" "}Liquidation is an estimate for isolated margin; on cross margin it depends on your whole account.</p>
      {saved.length > 0 && <div className="kbsim-saved"><h4>Saved scenarios (this browser)</h4>
        <table><thead><tr><th>Scenario</th><th>Loss at stop</th><th>Profit at TP</th><th>R/R</th><th>Liq.</th><th /></tr></thead>
          <tbody>{saved.map((s) => { const q = simulate(s, c && c.coin === s.coin ? c : { max_leverage: 20, funding_h: 0 }, equity); return (
            <tr key={s.at}><td>{s.name} · {s.lev}x · {fmt.usd(s.size, 0)}</td><td className="is-down">{fmt.usd(-q.lossAtStop)}</td><td className="is-up">{fmt.usd(q.profitAtTp)}</td>
              <td>{q.rr != null ? fmt.n(q.rr, 2) : "—"}</td><td>{fmt.px(q.liq)}</td>
              <td><button className="kb-link" onClick={() => setX({ side: s.side, entry: s.entry, stop: s.stop, tp: s.tp, size: s.size, lev: s.lev, slip_bps: s.slip_bps, fee_bps: s.fee_bps })}>load</button></td></tr>); })}</tbody></table></div>}
    </section>
  );
}
