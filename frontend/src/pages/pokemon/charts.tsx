/** Interactive charts (TradingView Lightweight Charts): crosshair, tooltips, wheel / drag zoom, reset.
 * Honesty rules: the line is Price Chat's ESTIMATED valuation (one point per day with a verified sale, carried forward);
 * completed sales are separate markers at their exact price; volume bars exist only on days with verified sales.
 * Nothing is interpolated into fake trades, and no OHLC is drawn. */
import { useEffect, useRef, useState } from "react";
import {
  AreaSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, createChart,
  type IChartApi, type ISeriesApi, type LineData, type SeriesType, type Time, type UTCTimestamp,
} from "lightweight-charts";
import type { ChartResp, CompareResp } from "./api";
import { day, num, sourceLabel, usd } from "./format";
import { useMarketTheme } from "./mx";

// colours are theme variables (market.css: --chart-*), resolved against the chart's own element when it is drawn
const NEON = "var(--chart-line)";
const SALE = "var(--chart-sale)";
const OTHER = "var(--chart-other)";
const BLUE = "var(--chart-blue)";
const PALETTE = [NEON, BLUE, "var(--chart-yellow)", "var(--chart-pink)"];
const FALLBACK: Record<string, string> = { "--chart-line": "#39ff88", "--chart-sale": "#f5f7fa", "--chart-other": "#5b6472", "--chart-blue": "#3b9eff",
  "--chart-yellow": "#f5c542", "--chart-pink": "#ff7a90", "--chart-grid": "rgba(255,255,255,0.05)" };

/** "var(--chart-line)" -> the colour it has on `el` (theme-aware); plain colours pass through. */
function col(c: string, el: HTMLElement | null): string {
  const m = /^var\((--[\w-]+)\)$/.exec(c);
  return m ? css(m[1], FALLBACK[m[1]] ?? "#888888", el) : c;
}

/** A hex colour with alpha. */
function alpha(hex: string, a: number): string {
  const h = hex.replace("#", "");
  if (!/^[0-9a-f]{6}$/i.test(h)) return hex;
  return `rgba(${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)},${a})`;
}

function css(name: string, fallback: string, el: Element | null = null): string {
  if (typeof window === "undefined") return fallback;
  return getComputedStyle(el ?? document.documentElement).getPropertyValue(name).trim() || fallback;
}

const sec = (iso: string) => Math.floor(new Date(iso).getTime() / 1000) as UTCTimestamp;

/** Lightweight Charts needs strictly increasing times: keep the last value per second. */
function uniq<T extends { time: UTCTimestamp }>(rows: T[]): T[] {
  const m = new Map<number, T>();
  for (const r of [...rows].sort((a, b) => a.time - b.time)) m.set(r.time, r);
  return [...m.values()];
}

/** Several sales can share a timestamp: nudge duplicates by a second so each stays its own marker. */
function spread<T extends { time: UTCTimestamp }>(rows: T[]): T[] {
  const out: T[] = [];
  let last = -Infinity;
  for (const r of [...rows].sort((a, b) => a.time - b.time)) {
    const t = r.time <= last ? ((last + 1) as UTCTimestamp) : r.time;
    out.push({ ...r, time: t });
    last = t;
  }
  return out;
}

const dollars = (cents: number) => cents / 100;
const compactUsd = (v: number) => usd(Math.round(v * 100), true);

function baseChart(el: HTMLElement, formatter: (v: number) => string): IChartApi {
  const muted = css("--muted", "#8a93a3", el);
  const border = css("--border", "#232831", el);
  const grid = css("--chart-grid", FALLBACK["--chart-grid"], el);
  const label = css("--surface-2", "#232831", el);
  return createChart(el, {
    autoSize: true,
    layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: muted, fontSize: 12,
              fontFamily: css("--font", "Inter, system-ui, sans-serif"), attributionLogo: false, panes: { separatorColor: border } },
    grid: { vertLines: { color: "transparent" }, horzLines: { color: grid } },
    crosshair: { mode: CrosshairMode.Normal, vertLine: { color: muted, style: LineStyle.Dashed, labelBackgroundColor: label },
                 horzLine: { color: muted, style: LineStyle.Dashed, labelBackgroundColor: label } },
    rightPriceScale: { borderColor: border, scaleMargins: { top: 0.12, bottom: 0.08 } },
    timeScale: { borderColor: border, rightOffset: 4, timeVisible: false, fixLeftEdge: false },
    localization: { priceFormatter: formatter },
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
    handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
  });
}

type Tip = { x: number; rows: [string, string][] } | null;

function TipBox({ tip }: { tip: Tip }) {
  if (!tip) return null;
  return (
    <div className="mx-tip" role="status" aria-live="polite">
      {tip.rows.map(([k, v]) => <div key={k}><span>{k}</span><b>{v}</b></div>)}
    </div>
  );
}

export type ChartMode = "price" | "cap";
export type ChartStyle = "line" | "area";

/** The card page's main chart for one grade. */
export function MarketChart({ data, mode, style, onReset }: { data: ChartResp | null; mode: ChartMode; style: ChartStyle; onReset?: (fn: () => void) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<Tip>(null);
  const theme = useMarketTheme();
  // no verified-sales history: the provider's published daily valuations (Scrydex), labelled as such
  const providerOnly = !!data && mode === "price" && data.line.length === 0 && (data.provider_line?.length ?? 0) > 0;
  const line = data ? (mode === "cap" ? data.cap_line : providerOnly ? data.provider_line ?? [] : data.line) : [];
  const sales = data && mode === "price" ? data.sales : [];
  // a chart needs at least two points; a single recorded value is stated in words, not drawn as a flat block
  const empty = !data || (line.length < 2 && sales.length === 0);
  const single = data && line.length === 1 && sales.length === 0 ? line[0] : null;

  useEffect(() => {
    if (!box.current || !data || empty) return;
    const chart = baseChart(box.current, compactUsd);
    const el = box.current;
    const lineCol = col(NEON, el);
    chart.applyOptions({ timeScale: { timeVisible: false } });
    const pts = uniq(line.map((p) => ({ time: sec(p.t), value: dollars(p.v), kind: p.kind, population: p.population })));
    const opts = { lineWidth: 2 as const, priceLineVisible: false, lastValueVisible: true, crosshairMarkerRadius: 4 };
    const main: ISeriesApi<SeriesType> = style === "area"
      ? chart.addSeries(AreaSeries, { ...opts, lineColor: lineCol, topColor: alpha(lineCol, 0.26), bottomColor: alpha(lineCol, 0.02) })
      : chart.addSeries(LineSeries, { ...opts, color: lineCol });
    main.setData(pts.map((p, i) => {
      const next = pts[i + 1];
      // the segment that starts at a carried estimate (no sale that day) is drawn muted
      const d: LineData = { time: p.time, value: p.value };
      if (p.kind === "carried" && next) d.color = alpha(lineCol, 0.45);
      return d;
    }));

    const ok = spread(sales.filter((s) => s.verified && !s.excluded).map((s) => ({ time: sec(s.t), value: dollars(s.v), sale: s })));
    const other = spread(sales.filter((s) => !s.verified || s.excluded).map((s) => ({ time: sec(s.t), value: dollars(s.v), sale: s })));
    const dots = { lineVisible: false, pointMarkersVisible: true, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
    const saleSeries = ok.length ? chart.addSeries(LineSeries, { ...dots, color: col(SALE, el), pointMarkersRadius: 4 }) : null;
    saleSeries?.setData(ok.map(({ time, value }) => ({ time, value })));
    const otherSeries = other.length ? chart.addSeries(LineSeries, { ...dots, color: col(OTHER, el), pointMarkersRadius: 3 }) : null;
    otherSeries?.setData(other.map(({ time, value }) => ({ time, value })));

    const vol = uniq(data.volume.map((v) => ({ time: sec(v.t), value: dollars(v.usd), count: v.count })));
    let volSeries: ISeriesApi<"Histogram"> | null = null;
    if (vol.length) {
      volSeries = chart.addSeries(HistogramSeries, { color: alpha(lineCol, 0.45), priceLineVisible: false, lastValueVisible: false,
                                                    priceFormat: { type: "custom", formatter: compactUsd } }, 1);
      volSeries.setData(vol.map(({ time, value }) => ({ time, value })));
      chart.panes()[1]?.setStretchFactor(0.22);
      chart.panes()[0]?.setStretchFactor(0.78);
    }
    chart.timeScale().fitContent();
    onReset?.(() => chart.timeScale().fitContent());

    const byTime = <T extends { time: UTCTimestamp }>(rows: T[], t: number): T[] => rows.filter((r) => r.time === t);
    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.point) {
        setTip(null);
        return;
      }
      const t = param.time as number;
      const rows: [string, string][] = [["Date", day(new Date(t * 1000).toISOString())]];
      const est = param.seriesData.get(main) as { value?: number } | undefined;
      const pt = pts.find((p) => p.time === t);
      if (est?.value !== undefined) {
        const capFromProvider = mode === "cap" && pt?.kind === "provider";
        rows.push([mode === "cap" ? "Est. market cap" : providerOnly ? "Scrydex market price" : "Estimated price", `${usd(Math.round(est.value * 100))} USD`]);
        rows.push(["Source", mode === "cap" ? (capFromProvider ? "Scrydex price × population" : "Verified-sales price × population")
                                             : providerOnly ? "Scrydex (provider estimate)" : "Price Chat (verified sales)"]);
        if (pt?.kind === "carried") rows.push(["Estimate", "carried (no sale that day)"]);
        if (pt?.population) rows.push(["Population then", num(pt.population)]);
      }
      for (const s of byTime(ok, t)) rows.push([`Verified sale · ${sourceLabel(s.sale.source)}`, `${usd(s.sale.v)} USD`]);
      for (const s of byTime(other, t)) rows.push([s.sale.excluded ? "Excluded sale" : "Unverified sale", `${usd(s.sale.v)} USD`]);
      const v = vol.find((x) => x.time === t);
      if (v) rows.push(["Verified volume", `${usd(Math.round(v.value * 100))} USD · ${v.count} sale${v.count === 1 ? "" : "s"}`]);
      setTip(rows.length > 1 ? { x: param.point.x, rows } : null);
    });
    return () => chart.remove();
  }, [data, mode, style, empty, providerOnly, theme]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="mx-chart" aria-label={data ? `${data.grade} ${mode === "cap" ? "estimated market cap" : "price"} chart, ${data.sales.length} sales in range` : "Chart loading"}>
      <div ref={box} className="mx-chart-canvas" />
      <TipBox tip={tip} />
      {!data && <div className="mx-chart-empty"><div>Loading chart…</div></div>}
      {data && empty && (
        <div className="mx-chart-empty">
          <div>
            {single ? (
              <>
                <b>{mode === "cap" ? "Market cap" : `${data.grade} value`} {usd(single.v)} on {day(single.t)}</b>
                {data.summary.price_source === "pricecharting"
                  ? "PriceCharting's API has no price history, so Price Chat records its value every day from this date. The chart fills in as days are recorded."
                  : "Only one value on record so far. The chart fills in as more are recorded."}
              </>
            ) : <b>{mode === "cap" ? "No market cap history" : `No ${data.grade} price history in this range`}</b>}
            {single ? null : mode === "cap"
              ? "A market cap needs both a verified price and a population on record for this grade at the same time."
              : "The chart is built only from verified completed sales. It fills in when sales for this grade are imported or synced."}
          </div>
        </div>
      )}
    </div>
  );
}

type Series = { name: string; points: { t: string; v: number }[]; color?: string; scale?: "left" | "right"; format?: (v: number) => string; dashed?: boolean };

/** Several lines on one time axis (grade comparison, price vs population). `v` is already in display units. */
export function MultiLineChart({ series, empty, small = false }: { series: Series[]; empty: string; small?: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<Tip>(null);
  const theme = useMarketTheme();
  const has = series.some((s) => s.points.length > 0);
  useEffect(() => {
    if (!box.current || !has) return;
    const chart = baseChart(box.current, compactUsd);
    const handles: [Series, ISeriesApi<"Line">][] = [];
    series.forEach((s, i) => {
      if (!s.points.length) return;
      const left = s.scale === "left";
      if (left) chart.applyOptions({ leftPriceScale: { visible: true, borderColor: css("--border", "#232831", box.current) } });
      const h = chart.addSeries(LineSeries, {
        color: col(s.color ?? PALETTE[i % PALETTE.length], box.current), lineWidth: 2, priceLineVisible: false, priceScaleId: left ? "left" : "right",
        lineStyle: s.dashed ? LineStyle.Dashed : LineStyle.Solid, title: s.name, pointMarkersVisible: s.points.length < 40,
        ...(s.format ? { priceFormat: { type: "custom" as const, formatter: s.format } } : {}),
      });
      h.setData(uniq(s.points.map((p) => ({ time: sec(p.t), value: p.v }))));
      handles.push([s, h]);
    });
    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.point) return setTip(null);
      const rows: [string, string][] = [["Date", day(new Date((param.time as number) * 1000).toISOString())]];
      for (const [s, h] of handles) {
        const d = param.seriesData.get(h) as { value?: number } | undefined;
        if (d?.value !== undefined) rows.push([s.name, (s.format ?? compactUsd)(d.value)]);
      }
      setTip(rows.length > 1 ? { x: param.point.x, rows } : null);
    });
    return () => chart.remove();
  }, [JSON.stringify(series), theme]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className={`mx-chart${small ? " mx-chart-sm" : ""}`}>
      <div ref={box} className="mx-chart-canvas" />
      <TipBox tip={tip} />
      {!has && <div className="mx-chart-empty"><div>{empty}</div></div>}
    </div>
  );
}

export function compareSeries(c: CompareResp | null): Series[] {
  return (c?.series ?? []).map((s, i) => ({ name: s.source === "scrydex" ? `${s.grade} (Scrydex est.)` : s.grade, color: PALETTE[i % PALETTE.length],
                                            points: s.line.map((p) => ({ t: p.t, v: dollars(p.v) })) }));
}

export function pricePopSeries(d: ChartResp | null): Series[] {
  if (!d) return [];
  return [
    { name: `${d.grade} est. price`, color: NEON, points: d.line.map((p) => ({ t: p.t, v: dollars(p.v) })) },
    { name: `${d.grade} population`, color: BLUE, scale: "left", dashed: true, format: (v: number) => num(Math.round(v)),
      points: d.population_history.map((p) => ({ t: p.t, v: p.count })) },
  ];
}

/** Re-exported for tests / callers that only need a type. */
export type { Time };
