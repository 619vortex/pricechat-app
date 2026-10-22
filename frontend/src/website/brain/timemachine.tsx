import { useEffect, useMemo, useRef, useState } from "react";
import { CandlestickSeries, ColorType, LineSeries, LineStyle, createChart, createSeriesMarkers, type IChartApi, type IPriceLine, type ISeriesApi,
  type ISeriesMarkersPluginApi, type SeriesMarker, type Time, type UTCTimestamp } from "lightweight-charts";
import { emaSeries, fmt, macdHist, rsiLast, usePoll, type Ev, type Replay } from "./api";

/* TIME MACHINE: replay a signal / trade. The chart and the indicators only ever see the candles up to the playhead (no future
   data); logged decisions light up when their time is reached. Missing history is said, not reconstructed. */

type Item = { signal_id: number; number: number; coin: string; side: string; status: string; test: boolean; created_at: string;
  position: { id: number; mode: string; status: string; net: number } | null };
const STAGES: [string, string[]][] = [["Strategy", ["strategy"]], ["Signal", ["signal"]], ["Risk", ["risk"]], ["Order", ["order"]], ["Exchange", ["exchange"]], ["Position", ["position"]]];
const SPEEDS = [1, 2, 5, 10];

function Player({ r }: { r: Replay }) {
  const box = useRef<HTMLDivElement>(null);
  const ch = useRef<{ chart: IChartApi; c: ISeriesApi<"Candlestick">; e20: ISeriesApi<"Line">; e50: ISeriesApi<"Line">; mk: ISeriesMarkersPluginApi<Time>; lines: IPriceLine[] } | null>(null);
  const cs = r.candles;
  const span = cs.length > 1 ? cs[1][0] - cs[0][0] : 60_000;
  const idxAt = (iso: string | null | undefined) => (iso ? Math.max(0, cs.findIndex((c) => c[0] + span > new Date(iso).getTime())) : -1);
  const sigI = idxAt(r.signal.created_at), entI = idxAt(String(r.report.opened_at ?? r.signal.activated_at ?? "")),
    exitI = idxAt(String(r.report.closed_at ?? r.signal.closed_at ?? ""));
  const [i, setI] = useState(Math.max(1, sigI - 30));
  const [play, setPlay] = useState(false);
  const [speed, setSpeed] = useState(2);
  useEffect(() => {
    if (!play) return;
    const id = window.setInterval(() => setI((x) => { if (x >= cs.length - 1) { setPlay(false); return x; } return x + 1; }), 700 / speed);
    return () => window.clearInterval(id);
  }, [play, speed, cs.length]);
  useEffect(() => {
    if (!box.current) return;
    const chart = createChart(box.current, { autoSize: true, layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8b968f", fontSize: 11 },
      grid: { vertLines: { color: "rgba(255,255,255,.05)" }, horzLines: { color: "rgba(255,255,255,.05)" } }, timeScale: { timeVisible: true } });
    const c = chart.addSeries(CandlestickSeries, { upColor: "#00e58f", downColor: "#ff5468", borderVisible: false, wickUpColor: "#00e58f", wickDownColor: "#ff5468" });
    const e20 = chart.addSeries(LineSeries, { color: "#ffc93c", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const e50 = chart.addSeries(LineSeries, { color: "#6ea8ff", lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    ch.current = { chart, c, e20, e50, mk: createSeriesMarkers(c, []), lines: [] };
    return () => { chart.remove(); ch.current = null; };
  }, []);
  const now = cs[Math.min(i, cs.length - 1)];
  const seen = cs.slice(0, i + 1);  // ONLY what existed at the playhead
  const closes = seen.map((k) => k[4]);
  useEffect(() => {
    const h = ch.current;
    if (!h) return;
    const t = (ms: number) => Math.floor(ms / 1000) as UTCTimestamp;
    h.c.setData(seen.map((k) => ({ time: t(k[0]), open: k[1], high: k[2], low: k[3], close: k[4] })));
    const e20 = emaSeries(closes, 20), e50 = emaSeries(closes, 50);
    h.e20.setData(seen.map((k, j) => (e20[j] == null ? { time: t(k[0]) } : { time: t(k[0]), value: e20[j]! })));
    h.e50.setData(seen.map((k, j) => (e50[j] == null ? { time: t(k[0]) } : { time: t(k[0]), value: e50[j]! })));
    const ms: SeriesMarker<Time>[] = [];
    if (sigI >= 0 && i >= sigI) ms.push({ time: t(cs[sigI][0]), position: r.signal.side === "LONG" ? "belowBar" : "aboveBar", color: "#9b5cff",
      shape: r.signal.side === "LONG" ? "arrowUp" : "arrowDown", text: `#${String(r.signal.number).padStart(3, "0")} signal` });
    if (entI >= 0 && i >= entI && r.report.entry) ms.push({ time: t(cs[entI][0]), position: "inBar", color: "#fff", shape: "circle", text: "entry" });
    if (exitI >= 0 && i >= exitI && (r.report.closed_at || r.signal.closed_at)) ms.push({ time: t(cs[exitI][0]), position: "inBar", color: "#ffc93c", shape: "square", text: "exit" });
    h.mk.setMarkers(ms.sort((a, b) => Number(a.time) - Number(b.time)));
    for (const l of h.lines) h.c.removePriceLine(l);
    h.lines = [];
    if (i >= sigI) {
      const add = (price: unknown, color: string, title: string) => { if (typeof price === "number") h.lines.push(h.c.createPriceLine({ price, color, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title })); };
      add(r.report.stop ?? r.signal.stop, "#ff5468", "stop");
      add(r.report.tp1 ?? r.signal.target, "#00e58f", "TP1");
      if (r.report.tp2) add(r.report.tp2, "#00e58f", "TP2");
      if (r.report.entry && i >= entI) add(r.report.entry, "#ffffff", "entry");
    }
  }, [i, cs]);  // eslint-disable-line react-hooks/exhaustive-deps
  const evsNow = r.events.filter((e) => new Date(e.at).getTime() <= now[0] + span);
  const lastBy = (kinds: string[]) => [...evsNow].reverse().find((e) => kinds.includes(e.kind));
  const done = i >= cs.length - 1;
  const rsi = rsiLast(closes), mh = macdHist(closes), e20 = emaSeries(closes, 20).at(-1), e50 = emaSeries(closes, 50).at(-1);
  return (
    <div className="kbtm-player">
      <div className="kbtm-ctl" role="group" aria-label="Replay controls">
        <button onClick={() => { setPlay(false); setI(Math.max(1, sigI - 30)); }} aria-label="Rewind">⏮</button>
        <button onClick={() => setI((x) => Math.max(1, x - 1))} aria-label="Step back">◀</button>
        <button className="is-play" onClick={() => setPlay(!play)} aria-label={play ? "Pause" : "Play"}>{play ? "⏸" : "▶"}</button>
        <button onClick={() => setI((x) => Math.min(cs.length - 1, x + 1))} aria-label="Step forward">▶|</button>
        {SPEEDS.map((s) => <button key={s} aria-pressed={speed === s} onClick={() => setSpeed(s)}>{s}x</button>)}
        <button onClick={() => setI(Math.max(1, sigI))} disabled={sigI < 0}>Signal</button>
        <button onClick={() => setI(Math.max(1, entI))} disabled={entI < 0 || !r.report.entry}>Entry</button>
        <button onClick={() => setI(Math.max(1, exitI))} disabled={exitI < 0}>Exit</button>
        <input type="range" min={1} max={cs.length - 1} value={i} onChange={(e) => setI(Number(e.target.value))} aria-label="Playhead" />
        <span className="kb-src">{fmt.day(now[0])} {fmt.time(now[0])} · {r.interval}</span>
      </div>
      <div className="kbtm-body">
        <div className="kbtm-chart" ref={box} />
        <aside className="kbtm-side">
          <dl className="kb-dl is-dense">
            <div><dt>Price</dt><dd>{fmt.px(now[4])}</dd></div><div><dt>RSI 14</dt><dd>{fmt.n(rsi, 1)}</dd></div>
            <div><dt>MACD hist</dt><dd>{fmt.n(mh, 5)}</dd></div><div><dt>EMA 20 / 50</dt><dd>{fmt.px(e20)} / {fmt.px(e50)}</dd></div>
          </dl>
          <p className="kb-src">Indicators from the {seen.length} candles that existed at this moment.</p>
          <ol className="kbtm-stages">{STAGES.map(([name, kinds]) => { const e = lastBy(kinds); return (
            <li key={name} className={e ? "is-lit" : ""}><b>{name}</b><span>{e ? e.title : "—"}</span>{e && <time>{fmt.time(e.at)}</time>}</li>); })}</ol>
        </aside>
      </div>
      {done && <Report r={r} />}
    </div>
  );
}

function Report({ r }: { r: Replay }) {
  const p = r.report;
  return (
    <section className="kbtm-report" aria-label="Trade report">
      <h4>Trade report · #{String(r.signal.number).padStart(3, "0")} {r.signal.coin} {r.signal.side}</h4>
      <dl className="kb-dl">
        <div><dt>Strategy / version</dt><dd>{String(p.strategy_name)} · {String(p.version)}</dd></div>
        <div><dt>Entry reasoning</dt><dd>{String(p.signal_reason ?? "—")}</dd></div>
        <div><dt>Exit</dt><dd>{String(p.close_reason ?? r.signal.status)}</dd></div>
        <div><dt>Net P&L</dt><dd className={Number(p.net) >= 0 ? "is-up" : "is-down"}>{p.net != null ? fmt.pnl(Number(p.net), 4) : "no position of yours"}</dd></div>
        <div><dt>Fees</dt><dd>{p.fees != null ? fmt.usd(Number(p.fees), 4) : "—"}</dd></div>
        <div><dt>Funding</dt><dd>{p.funding != null ? fmt.usd(Number(p.funding), 4) : p.mode === "PAPER" ? "n/a (paper)" : "—"}</dd></div>
        <div><dt>Max adverse (MAE)</dt><dd className="is-down">{p.mae_pct != null ? `${fmt.pct(Number(p.mae_pct), 2)} · ${fmt.usd(Number(p.mae_usd), 4)}` : "—"}</dd></div>
        <div><dt>Max favorable (MFE)</dt><dd className="is-up">{p.mfe_pct != null ? `${fmt.pct(Number(p.mfe_pct), 2)} · ${fmt.usd(Number(p.mfe_usd), 4)}` : "—"}</dd></div>
        <div><dt>Execution quality</dt><dd>{p.entry_vs_zone_bps != null ? `entry ${fmt.n(Number(p.entry_vs_zone_bps), 1)} bps ${Number(p.entry_vs_zone_bps) > 0 ? "worse" : "better"} than the zone middle` : "—"}</dd></div>
        <div><dt>Signal result (model)</dt><dd>{p.signal_result_pct != null ? `${fmt.pct(Number(p.signal_result_pct))} unlevered, after fees` : "still open or expired"}</dd></div>
      </dl>
      {p.mae_note && <p className="kb-src">MAE/MFE {String(p.mae_note)}.</p>}
    </section>
  );
}

export function TimeMachine({ start, onClear }: { start: number | null; onClear: () => void }) {
  const list = usePoll<{ items: Item[] }>("/members/kiwi-brain/replays", 120_000);
  const [sel, setSel] = useState<number | null>(start);
  useEffect(() => { if (start) setSel(start); }, [start]);
  const r = usePoll<Replay>(sel ? `/members/kiwi-brain/replay?signal_id=${sel}` : null, 3_600_000);
  const items = list.data?.items ?? [];
  const cur = useMemo(() => (r.data && r.data.signal.id === sel ? r.data : null), [r.data, sel]);
  return (
    <div className="kbtm">
      <aside className="kbp-panel kbtm-list">
        <header className="kbp-ph"><h3>Signals & trades</h3></header>
        <ol>{items.map((x) => <li key={x.signal_id}><button aria-pressed={sel === x.signal_id} onClick={() => { setSel(x.signal_id); onClear(); }}>
          <b>#{String(x.number).padStart(3, "0")} {x.coin} {x.side}</b>{x.test && <small> manual</small>}
          <span>{x.status} · {fmt.day(x.created_at)}{x.position ? ` · yours ${x.position.mode.toLowerCase()} ${fmt.pnl(x.position.net)}` : ""}</span></button></li>)}</ol>
        {!items.length && <p className="kb-empty">No published signals yet.</p>}
      </aside>
      <section className="kbp-panel kbtm-main">
        {!sel ? <p className="kb-empty">Pick a signal to replay what the Kiwi saw and did, step by step.</p>
          : r.err ? <p className="kb-warn">{r.err}</p> : !cur ? <div className="kb-skel" style={{ height: 380 }} /> : <>
            <header className="kbp-ph"><h3>Replay · #{String(cur.signal.number).padStart(3, "0")} {cur.signal.coin} {cur.signal.side}</h3><span className="kb-src">{cur.source}</span></header>
            {cur.gap && <p className="kb-warn">{cur.gap}</p>}
            <Player key={cur.signal.id} r={cur} />
            <details className="kbtm-evs"><summary>All {cur.events.length} logged events for this signal</summary>
              <ol>{cur.events.map((e: Ev) => <li key={e.id}><time>{fmt.day(e.at)} {fmt.time(e.at)}</time> <b>{e.kind}</b> {e.title}</li>)}</ol></details>
          </>}
      </section>
    </div>
  );
}
