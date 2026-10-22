import { useEffect, useRef } from "react";
import { CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, createChart, type IChartApi, type IPriceLine, type ISeriesApi,
  type AutoscaleInfo, type UTCTimestamp } from "lightweight-charts";
import type { Candle } from "./api";

/** Candles + volume from executed PAPER trades only, and the real-card reference as a separate dashed line.
 *  TradingView Lightweight Charts (Apache-2.0). */
export function PriceChart({ candles, reference, refNow }: { candles: Candle[]; reference: { t: number; v: number }[]; refNow?: number | null }) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<{ c: ISeriesApi<"Candlestick">; v: ISeriesApi<"Histogram">; r: ISeriesApi<"Line"> } | null>(null);
  const refLine = useRef<IPriceLine | null>(null);
  const refVal = useRef<number | null>(null);  // the price scale always includes the reference

  useEffect(() => {
    if (!box.current) return;
    const ch = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8fa39a", fontFamily: "inherit" },
      grid: { vertLines: { color: "rgba(255,255,255,.04)" }, horzLines: { color: "rgba(255,255,255,.05)" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "rgba(255,255,255,.08)" },
      timeScale: { borderColor: "rgba(255,255,255,.08)", timeVisible: true, secondsVisible: false },
    });
    const c = ch.addSeries(CandlestickSeries, {
      upColor: "#00e58f", downColor: "#ff4d6a", wickUpColor: "#00e58f", wickDownColor: "#ff4d6a", borderVisible: false,
      autoscaleInfoProvider: (base: () => AutoscaleInfo | null) => {
        const r = base(), v = refVal.current;
        if (v == null) return r;
        const lo = Math.min(r?.priceRange?.minValue ?? v, v), hi = Math.max(r?.priceRange?.maxValue ?? v, v);
        const pad = (hi - lo) * 0.08 || v * 0.01;
        return { ...(r ?? {}), priceRange: { minValue: lo - pad, maxValue: hi + pad } };
      },
    });
    const v = ch.addSeries(HistogramSeries, { priceScaleId: "", priceFormat: { type: "volume" } });
    v.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const r = ch.addSeries(LineSeries, { color: "#ffd24d", lineWidth: 1, lineStyle: LineStyle.Dashed, priceLineVisible: false, lastValueVisible: false });
    chart.current = ch;
    series.current = { c, v, r };
    return () => { ch.remove(); chart.current = null; series.current = null; };
  }, []);

  useEffect(() => {
    const s = series.current;
    if (!s) return;
    s.c.setData(candles.map((k) => ({ time: k.t as UTCTimestamp, open: k.o / 100, high: k.h / 100, low: k.l / 100, close: k.c / 100 })));
    s.v.setData(candles.map((k) => ({ time: k.t as UTCTimestamp, value: k.v, color: k.c >= k.o ? "rgba(0,229,143,.35)" : "rgba(255,77,106,.35)" })));
    s.r.setData(reference.map((p) => ({ time: p.t as UTCTimestamp, value: p.v / 100 })));
    chart.current?.timeScale().fitContent();
  }, [candles, reference]);

  // the current real-card reference, always visible as a dashed gold line (even before there's any history)
  useEffect(() => {
    const s = series.current;
    if (!s) return;
    if (refLine.current) { s.c.removePriceLine(refLine.current); refLine.current = null; }
    refVal.current = refNow ? refNow / 100 : null;
    if (refNow) refLine.current = s.c.createPriceLine({ price: refNow / 100, color: "#ffd24d", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "Reference" });
  }, [refNow]);

  return (
    <div className="ex-chart-wrap">
      <div ref={box} className="ex-chart" />
      {candles.length === 0 && <p className="ex-chart-empty">No paper trades in this period yet: candles appear only when trades execute.</p>}
    </div>
  );
}
