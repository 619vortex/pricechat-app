import { useEffect, useMemo, useRef, useState } from "react";
import { CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, createChart, createSeriesMarkers,
  type IChartApi, type IPriceLine, type ISeriesApi, type ISeriesMarkersPluginApi, type SeriesMarker, type Time, type UTCTimestamp } from "lightweight-charts";
import { bollinger, emaSeries, fmt, usePoll, vwapSeries, type Candles, type Check, type Ev, type Flow, type Overview, type Snap } from "./api";
import { Simulator } from "./simulator";
import { ok, SLAYERS, StructurePanel, type BookL, type SLayer, type Structure, type TwapL, type ZonesL } from "./structure";

/* BATTLEFIELD: the interactive chart (real Hyperliquid candles; overlays computed from those candles), the member's levels,
   signal markers synced with the decision log, and the order-flow recorder (liquidity heatmap, trade flow) showing only what
   was actually recorded. */

const C = { up: "#00e58f", down: "#ff5468", grid: "rgba(255,255,255,0.05)", text: "#8b968f", purple: "#9b5cff", amber: "#ffc93c", blue: "#6ea8ff" };
type Overlay = "ema" | "vwap" | "bb" | "levels";

type Marks = { zones: ZonesL | null; book: BookL | null; twap: TwapL | null };

function Chart({ data, o, focus, overlays, onMarker, marks }: { data: Candles; o: Overview; focus: Ev | null; overlays: Set<Overlay>;
  onMarker: (signalId: number) => void; marks: Marks }) {
  const box = useRef<HTMLDivElement>(null);
  const ref = useRef<{ ch: IChartApi; c: ISeriesApi<"Candlestick">; v: ISeriesApi<"Histogram">; e20: ISeriesApi<"Line">; e50: ISeriesApi<"Line">;
    vw: ISeriesApi<"Line">; bu: ISeriesApi<"Line">; bl: ISeriesApi<"Line">; mk: ISeriesMarkersPluginApi<Time>; lines: IPriceLine[] } | null>(null);
  const markersRef = useRef(data.markers);
  markersRef.current = data.markers;
  const onMarkerRef = useRef(onMarker);
  onMarkerRef.current = onMarker;
  useEffect(() => {
    if (!box.current) return;
    const ch = createChart(box.current, {
      autoSize: true, layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: C.text, fontSize: 11 },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } }, crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "rgba(255,255,255,.08)" }, timeScale: { borderColor: "rgba(255,255,255,.08)", timeVisible: true, secondsVisible: false },
    });
    const c = ch.addSeries(CandlestickSeries, { upColor: C.up, downColor: C.down, borderVisible: false, wickUpColor: C.up, wickDownColor: C.down });
    const v = ch.addSeries(HistogramSeries, { priceScaleId: "", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    v.priceScale().applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
    const line = (color: string, style = LineStyle.Solid) => ch.addSeries(LineSeries, { color, lineWidth: 1, lineStyle: style, priceLineVisible: false,
      lastValueVisible: false, crosshairMarkerVisible: false });
    const e20 = line(C.amber), e50 = line(C.blue), vw = line(C.purple, LineStyle.Dashed), bu = line("rgba(255,255,255,.28)", LineStyle.Dotted),
      bl = line("rgba(255,255,255,.28)", LineStyle.Dotted);
    const mk = createSeriesMarkers(c, []);
    ch.subscribeClick((p) => {  // a click near a signal marker opens its decisions
      if (p.time == null) return;
      const t = Number(p.time);
      const hit = markersRef.current.map((m) => ({ m, d: Math.abs(new Date(m.at).getTime() / 1000 - t) })).sort((a, b) => a.d - b.d)[0];
      if (hit && hit.d < 6 * 3600) onMarkerRef.current(hit.m.signal_id);
    });
    ref.current = { ch, c, v, e20, e50, vw, bu, bl, mk, lines: [] };
    return () => { ch.remove(); ref.current = null; };
  }, []);
  const cs = data.candles;
  useEffect(() => {
    const r = ref.current;
    if (!r || !cs.length) return;
    const t = (ms: number) => Math.floor(ms / 1000) as UTCTimestamp;
    r.c.setData(cs.map((k) => ({ time: t(k[0]), open: k[1], high: k[2], low: k[3], close: k[4] })));
    r.v.setData(cs.map((k) => ({ time: t(k[0]), value: k[5] * k[4], color: k[4] >= k[1] ? "rgba(0,229,143,.28)" : "rgba(255,84,104,.28)" })));
    const closes = cs.map((k) => k[4]);
    const set = (s: ISeriesApi<"Line">, vals: (number | null)[], on: boolean) =>
      s.setData(on ? vals.map((x, i) => (x == null ? { time: t(cs[i][0]) } : { time: t(cs[i][0]), value: x })) : []);
    set(r.e20, emaSeries(closes, 20), overlays.has("ema"));
    set(r.e50, emaSeries(closes, 50), overlays.has("ema"));
    set(r.vw, vwapSeries(cs), overlays.has("vwap"));
    const bb = bollinger(closes);
    set(r.bu, bb.up, overlays.has("bb"));
    set(r.bl, bb.lo, overlays.has("bb"));
    const span = cs.length > 1 ? (cs[1][0] - cs[0][0]) / 1000 : 3600;
    const snapTo = (iso: string) => {
      const s = new Date(iso).getTime() / 1000;
      let k: (typeof cs)[number] | undefined;
      for (const c of cs) if (c[0] / 1000 <= s) k = c;
      return k ? t(k[0]) : null;
    };
    const ms: SeriesMarker<Time>[] = [];
    for (const m of data.markers) {
      const at = snapTo(m.at);
      if (at == null) continue;
      const long = m.side === "LONG";
      ms.push({ time: at, position: long ? "belowBar" : "aboveBar", color: C.purple, shape: long ? "arrowUp" : "arrowDown",
        text: `#${String(m.number).padStart(3, "0")} ${m.side}${m.test ? " (manual)" : ""}` });
    }
    for (const p of o.positions.filter((x) => x.coin === data.coin && x.opened_at)) {
      const at = snapTo(p.opened_at!);
      if (at != null) ms.push({ time: at, position: "inBar", color: "#fff", shape: "circle", text: "entry" });
    }
    for (const p of marks.twap?.patterns ?? []) {  // inferred: labelled as such on the chart
      const at = snapTo(new Date(p.start).toISOString());
      if (at != null) ms.push({ time: at, position: p.side === "buy" ? "belowBar" : "aboveBar", color: "#be78ff", shape: "circle",
        text: `TWAP-like ${p.side}s (inferred) ${p.prints}×${p.interval_s.toFixed(0)}s` });
    }
    if (focus) {
      const at = snapTo(focus.at);
      if (at != null) {
        ms.push({ time: at, position: "aboveBar", color: C.amber, shape: "square", text: `◆ ${focus.kind}` });
        const i = cs.findIndex((c) => t(c[0]) === at);
        if (i >= 0) r.ch.timeScale().setVisibleLogicalRange({ from: Math.max(0, i - 40), to: Math.min(cs.length + 5, i + 25) });
      }
    }
    ms.sort((a, b) => Number(a.time) - Number(b.time));
    r.mk.setMarkers(ms);
    void span;
    for (const l of r.lines) r.c.removePriceLine(l);
    r.lines = [];
    if (overlays.has("levels")) {
      for (const p of o.positions.filter((x) => x.coin === data.coin)) {
        const add = (price: number | null | undefined, color: string, title: string, style = LineStyle.Dashed) => {
          if (price) r.lines.push(r.c.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title }));
        };
        add(p.entry_px, "#ffffff", `entry ${p.side}`, LineStyle.Solid);
        add(p.stop, C.down, "stop");
        add(p.tp1, C.up, "TP1");
        add(p.tp2, C.up, "TP2");
        add(p.liq_px, "#ff9a3c", "liquidation", LineStyle.Dotted);
      }
    }
    for (const z of marks.zones?.top ?? []) {  // a model's output: dotted, titled "est."
      r.lines.push(r.c.createPriceLine({ price: (z.px + z.px_hi) / 2, color: z.side === "long" ? "rgba(255,84,104,.7)" : "rgba(0,229,143,.7)",
        lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false, title: `est. ${z.side} liq ${fmt.big(z.usd)}` }));
    }
    for (const w of (marks.book?.walls ?? []).slice(0, 4)) {
      r.lines.push(r.c.createPriceLine({ price: w.px, color: C.amber, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true,
        title: `${w.venue} ${w.side} wall ${fmt.big(w.usd)}` }));
    }
    if (!focus) r.ch.timeScale().fitContent();
  }, [cs, data.markers, data.coin, o.positions, focus, overlays, marks]);
  return <div className="kbc-chart" ref={box} role="img" aria-label={`${data.coin} ${data.timeframe} candles from Hyperliquid`} />;
}

function Heatmap({ f }: { f: Flow }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current;
    if (!cv || !f.heat.length) return;
    const W = cv.width = cv.clientWidth * (window.devicePixelRatio || 1);
    const H = cv.height = cv.clientHeight * (window.devicePixelRatio || 1);
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, W, H);
    const all = f.heat.flatMap((s) => [...s.bids, ...s.asks].map((l) => l[0]));
    const lo = Math.min(...all), hi = Math.max(...all);
    const maxUsd = Math.max(...f.heat.flatMap((s) => [...s.bids, ...s.asks].map((l) => l[0] * l[1])), 1);
    const y = (px: number) => H - ((px - lo) / (hi - lo || 1)) * H;
    const cw = W / f.heat.length;
    const rowH = Math.max(2, H / 42);
    f.heat.forEach((s, i) => {
      for (const [side, levels] of [["b", s.bids], ["a", s.asks]] as const) {
        for (const [px, sz] of levels) {
          const a = Math.log1p(px * sz) / Math.log1p(maxUsd);
          ctx.fillStyle = side === "b" ? `rgba(0,229,143,${(a * 0.95).toFixed(3)})` : `rgba(255,84,104,${(a * 0.95).toFixed(3)})`;
          ctx.fillRect(i * cw, y(px) - rowH / 2, Math.ceil(cw) + 0.5, rowH);
        }
      }
    });
    ctx.strokeStyle = "rgba(255,255,255,.85)";
    ctx.lineWidth = 1.5 * (window.devicePixelRatio || 1);
    ctx.beginPath();
    f.heat.forEach((s, i) => (i ? ctx.lineTo(i * cw + cw / 2, y(s.mid)) : ctx.moveTo(cw / 2, y(s.mid))));
    ctx.stroke();
    const dpr = window.devicePixelRatio || 1;
    ctx.font = `${11 * dpr}px ui-monospace, monospace`;
    ctx.fillStyle = "rgba(238,243,240,.75)";
    ctx.textAlign = "right";
    for (const f2 of [0.03, 0.5, 0.97]) {
      const px = hi - (hi - lo) * f2;
      ctx.fillText(px.toLocaleString("en-US", { maximumFractionDigits: px >= 1000 ? 1 : 5 }), W - 6 * dpr, y(px) + 4 * dpr);
    }
  }, [f]);
  const mins = (f.now - f.recording_since) / 60;
  return (
    <section className="kbp-panel" aria-label="Liquidity heatmap">
      <header className="kbp-ph"><h3>Liquidity heatmap · {f.coin}</h3>
        <span className="kb-src">{f.samples} order-book snapshots · recording {mins < 1 ? "<1" : Math.round(mins)} min</span></header>
      {f.heat.length < 2 ? <p className="kb-empty">Recording started: the heatmap builds up while this page is open (every {f.sample_every_s}s). No past liquidity is reconstructed.</p>
        : <canvas ref={ref} className="kbh" aria-label="Resting bid (green) and ask (red) liquidity over time, white line = mid price" />}
      <p className="kb-src">Top 20 levels each side from {f.source}. Brighter = more USD resting. Only the history recorded since this page opened is shown.</p>
    </section>
  );
}

function Bars({ f }: { f: Flow }) {
  const v = f.volume.slice(-90);
  if (!v.length) return <p className="kb-empty">No trades recorded yet.</p>;
  const max = Math.max(...v.map((x) => Math.max(x.buy, x.sell)), 1);
  const cvd = v.map((x) => x.cvd), cmin = Math.min(...cvd, 0), cmax = Math.max(...cvd, 0);
  const W = 600, H = 90, w = W / v.length;
  return (
    <div className="kbf-charts">
      <figure><figcaption>Buy vs sell volume (10 s bars, USD)</figcaption>
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Buy and sell volume">
          <line x1={0} x2={W} y1={H / 2} y2={H / 2} stroke="rgba(255,255,255,.12)" />
          {v.map((x, i) => <g key={x.t}><rect x={i * w + 1} width={Math.max(1, w - 2)} y={H / 2 - (x.buy / max) * (H / 2 - 2)} height={(x.buy / max) * (H / 2 - 2)} rx={1} fill={C.up} />
            <rect x={i * w + 1} width={Math.max(1, w - 2)} y={H / 2} height={(x.sell / max) * (H / 2 - 2)} rx={1} fill={C.down} /></g>)}
        </svg></figure>
      <figure><figcaption>Cumulative volume delta (since recording began): {fmt.big(cvd[cvd.length - 1])}</figcaption>
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Cumulative volume delta">
          <line x1={0} x2={W} y1={H - ((0 - cmin) / (cmax - cmin || 1)) * H} y2={H - ((0 - cmin) / (cmax - cmin || 1)) * H} stroke="rgba(255,255,255,.12)" />
          <polyline fill="none" stroke={C.purple} strokeWidth={2} points={cvd.map((c, i) => `${i * w + w / 2},${H - ((c - cmin) / (cmax - cmin || 1)) * (H - 4) - 2}`).join(" ")} />
        </svg></figure>
    </div>
  );
}

function OrderFlow({ f }: { f: Flow }) {
  const prof = f.profile, pmax = Math.max(...prof.map((x) => x.usd), 1);
  const hist = [...f.ctx_history, ...f.ctx.map((x) => ({ t: x.t, oi_usd: x.oi_usd, funding_h: x.funding_h, mark: x.mark }))];
  const oi0 = hist[0]?.oi_usd, oi1 = hist[hist.length - 1]?.oi_usd;
  const imb = f.imbalance;
  return (
    <section className="kbp-panel" aria-label="Order flow">
      <header className="kbp-ph"><h3>Order flow · {f.coin}</h3><span className="kb-src">{f.trades_recorded} trades recorded</span></header>
      <div className="kbf-top">
        <div className="kbf-imb"><small>Book imbalance (top 20 levels)</small>
          <div className="kbf-bar"><span className="is-b" style={{ width: `${imb == null ? 50 : (imb + 1) * 50}%` }} /></div>
          <b className={imb == null ? "" : imb >= 0 ? "is-up" : "is-down"}>{imb == null ? "—" : `${imb >= 0 ? "bids" : "asks"} ${fmt.n(Math.abs(imb) * 100, 0)}% heavier`}</b>
          <small>{fmt.big(f.depth_top20.bid_usd)} bid · {fmt.big(f.depth_top20.ask_usd)} ask</small></div>
        <div><small>Spread</small><b>{f.book ? `${fmt.px(f.book.asks[0]?.[0] - f.book.bids[0]?.[0])} (${fmt.n(((f.book.asks[0]?.[0] - f.book.bids[0]?.[0]) / f.book.mid) * 1e4, 2)} bps)` : "—"}</b></div>
        <div><small>Open interest change</small><b>{oi0 && oi1 ? fmt.pct((oi1 / oi0 - 1) * 100) : "—"}</b>
          <small>{hist.length > 1 ? `since ${fmt.ago(hist[0].t * 1000)}` : "needs more samples"}</small></div>
        <div><small>Funding now</small><b>{hist.length ? `${(hist[hist.length - 1].funding_h * 100).toFixed(4)}%/h` : "—"}</b></div>
      </div>
      <Bars f={f} />
      <div className="kbf-grid">
        <div><h4>Large trades</h4>
          <ol className="kbf-big">{f.large_trades.slice(0, 8).map((x) => <li key={`${x.t}${x.px}${x.sz}`} className={x.side === "buy" ? "is-up" : "is-down"}>
            <span>{x.side.toUpperCase()}</span><b>{fmt.big(x.usd)}</b><span>@ {fmt.px(x.px)}</span><time>{fmt.time(x.t)}</time></li>)}</ol>
          {!f.large_trades.length && <p className="kb-empty">None yet.</p>}</div>
        <div><h4>Volume profile (recorded trades)</h4>
          <ol className="kbf-prof">{[...prof].reverse().map((x) => <li key={x.px}><span>{fmt.px(x.px)}</span><i style={{ width: `${(x.usd / pmax) * 100}%` }} /></li>)}</ol>
          {!prof.length && <p className="kb-empty">None yet.</p>}</div>
      </div>
      <p className="kb-src">Liquidations: {f.liquidations.why}</p>
    </section>
  );
}

function FocusPanel({ e, onClose }: { e: Ev; onClose: () => void }) {
  const d = e.data as Record<string, unknown>;
  const ind = (d.indicators as Record<string, unknown> | undefined) ?? {};
  const checks = (d.checks as Check[] | undefined) ?? [];
  return (
    <section className="kbp-panel kbfocus" aria-label="Decision at this moment">
      <header className="kbp-ph"><h3>◆ {e.kind} · {fmt.day(e.at)} {fmt.time(e.at)}</h3><button className="kb-x" onClick={onClose} aria-label="Close">✕</button></header>
      <p><b>{e.title}</b></p>
      {Object.keys(ind).length > 0 && <dl className="kb-dl is-dense">{Object.entries(ind).filter(([, v]) => typeof v === "number" || typeof v === "string").slice(0, 16)
        .map(([k, v]) => <div key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{typeof v === "number" ? fmt.px(v) : String(v)}</dd></div>)}</dl>}
      {checks.length > 0 && <ul className="kbr-list">{checks.map((c, i) => <li key={i} className={`kbr is-${c.ok ? "ok" : c.ok === false ? "bad" : "na"}`}>
        <span>{c.ok ? "✓" : c.ok === false ? "✗" : "·"}</span> {c.rule} <small>· {c.value}</small></li>)}</ul>}
      {!checks.length && Array.isArray(d.reasons) && <ul className="kbr-list">{(d.reasons as string[]).map((r) => <li key={r} className="kbr is-bad"><span>✗</span> {r}</li>)}</ul>}
      {typeof d.decision === "string" && <p>Decision: <b>{d.decision}</b>{d.reason ? ` · ${String(d.reason)}` : ""}</p>}
      <p className="kb-src">{e.source} · values logged at decision time</p>
    </section>
  );
}

export function Battlefield({ o, coins, focus, setFocus, onSignal }: { o: Overview; coins: string[]; focus: Ev | null; setFocus: (e: Ev | null) => void;
  onSignal: (signalId: number) => void }) {
  const [coin, setCoin] = useState(focus?.coin ?? coins[0] ?? "BTC");
  const [tf, setTf] = useState("1h");
  const [ov, setOv] = useState<Set<Overlay>>(new Set(["ema", "levels"]));
  useEffect(() => { if (focus?.coin) { setCoin(focus.coin); setTf("1h"); } }, [focus]);
  const cand = usePoll<Candles>(`/members/kiwi-brain/candles?coin=${coin}&timeframe=${tf}&n=400`, 60_000);
  const snap = usePoll<Snap>(`/members/kiwi-brain/market?coin=${coin}&timeframe=${tf === "5m" ? "15m" : tf}`, 20_000);
  const flow = usePoll<Flow>(`/members/kiwi-brain/flow?coin=${coin}`, 5_000);
  const [sl, setSl] = useState<Set<SLayer>>(new Set());
  const st = usePoll<Structure>(sl.size ? `/members/kiwi-brain/structure?coin=${coin}&timeframe=${tf}&layers=${[...sl].sort().join(",")}` : null, 30_000);
  const sd = st.data && st.data.coin === coin && st.data.timeframe === tf ? st.data : null;
  const marks = useMemo<Marks>(() => ({ zones: sl.has("zones") ? ok<ZonesL>(sd?.layers.zones) : null, book: sl.has("book") ? ok<BookL>(sd?.layers.book) : null,
    twap: sl.has("twap") ? ok<TwapL>(sd?.layers.twap) : null }), [sd, sl]);
  const toggleS = (k: SLayer) => setSl((cur) => { const n = new Set(cur); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  const s = snap.data?.coin === coin ? snap.data : null;
  const i = s?.indicators ?? {};
  const toggle = (k: Overlay) => setOv((cur) => { const n = new Set(cur); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  const chg = s ? (s.market.mark / s.market.prev_day - 1) * 100 : null;
  const c = cand.data && cand.data.coin === coin && cand.data.timeframe === tf ? cand.data : null;
  const all = useMemo(() => Array.from(new Set([...coins, coin])), [coins, coin]);
  return (
    <div className="kbb">
      <section className="kbp-panel kbb-chart">
        <header className="kbp-ph kbb-head">
          <div className="kb-tabs" role="tablist">{all.map((x) => <button key={x} role="tab" aria-selected={x === coin} onClick={() => { setCoin(x); setFocus(null); }}>{x}</button>)}</div>
          <div className="kb-tabs" role="tablist">{["5m", "15m", "1h", "4h"].map((x) => <button key={x} role="tab" aria-selected={x === tf} onClick={() => setTf(x)}>{x}</button>)}</div>
          <div className="kb-chips">{(["ema", "vwap", "bb", "levels"] as Overlay[]).map((k) => <button key={k} aria-pressed={ov.has(k)} onClick={() => toggle(k)}>
            {k === "ema" ? "EMA 20/50" : k === "vwap" ? "VWAP" : k === "bb" ? "Bollinger" : "My levels"}</button>)}</div>
          <div className="kb-chips kbs-chips" aria-label="Market structure layers">{SLAYERS.map(({ k, label }) =>
            <button key={k} aria-pressed={sl.has(k)} onClick={() => toggleS(k)}>{label}</button>)}</div>
        </header>
        {s && <div className="kbb-quote"><b>{coin} {fmt.px(s.market.mark)}</b><span className={chg != null && chg >= 0 ? "is-up" : "is-down"}>{fmt.pct(chg)} 24h</span>
          <span>RSI {fmt.n(i.rsi14, 1)}</span><span>MACD {fmt.n(i.macd_hist, 4)}</span><span>ATR {fmt.px(i.atr14)}</span>
          <span>Funding {(s.market.funding_h * 100).toFixed(4)}%/h</span><span>OI {fmt.big(s.market.open_interest_usd)}</span>
          <span>S/R {fmt.px(i.support)} / {fmt.px(i.resistance)}</span>{s.stale && <span className="kb-pill is-warn">STALE</span>}</div>}
        {cand.err && <p className="kb-warn">Candles unavailable: {cand.err}</p>}
        {c ? <Chart data={c} o={o} focus={focus && focus.coin === coin ? focus : null} overlays={ov} onMarker={onSignal} marks={marks} /> : <div className="kb-skel kbc-chart" />}
        <p className="kb-src">{c?.source ?? "hyperliquid:info"} · drag to pan, scroll to zoom · click a signal marker to open its decisions · overlays are computed from these candles.</p>
      </section>
      <StructurePanel d={sd} on={sl} err={st.err} />
      {focus && <FocusPanel e={focus} onClose={() => setFocus(null)} />}
      <div className="kbb-grid">
        {flow.data && flow.data.coin === coin ? <><Heatmap f={flow.data} /><OrderFlow f={flow.data} /></> : <div className="kb-skel" style={{ height: 260 }} />}
      </div>
      <Simulator coin={coin} equity={o.exposure.equity} />
    </div>
  );
}
