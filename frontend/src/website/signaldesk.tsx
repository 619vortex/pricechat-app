import { useEffect, useRef, useState, type ReactNode } from "react";
import { CandlestickSeries, ColorType, createChart, CrosshairMode, LineStyle, type AutoscaleInfo, type IChartApi, type IPriceLine, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import { memberApi } from "./members";
import { site } from "./websiteData";
import { CrowdLine, RecordLine, type Sig } from "./signalProof";

/** SIGNAL DESK: one signal on one screen. The live chart with its levels, a discussion with Kiwi about this signal, and the same
 *  ENTER TRADE flow as the card (preview, then confirm), so nobody leaves the chart to trade. Kiwi answers from the records and
 *  doesn't tell anyone to enter or skip. */

type Candle = [number, number, number, number, number, number];
type Msg = { who: "me" | "kiwi"; text: string; err?: boolean };

const SUGGEST = ["Explain this chart in simple words", "What would prove this signal wrong?", "How has this setup done so far?", "What could I lose on this trade?"];
const UP = "#00e58f", DOWN = "#ff5468";

function DeskChart({ sig }: { sig: Sig }) {
  const box = useRef<HTMLDivElement>(null);
  const ref = useRef<{ ch: IChartApi; c: ISeriesApi<"Candlestick">; lines: IPriceLine[] } | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const tf = sig.timeframe ?? "1h";
  const levelsRef = useRef<number[]>([]);
  levelsRef.current = [...sig.entry, sig.stop, sig.target, ...(sig.tp2 ? [sig.tp2] : [])];

  useEffect(() => {
    let stop = false;
    const tick = () => memberApi<{ candles: Candle[] }>(`/members/kiwi-brain/candles?coin=${encodeURIComponent(sig.coin)}&timeframe=${tf}&n=200`)
      .then((d) => { if (!stop) { setCandles(d.candles); setErr(null); } })
      .catch((e) => { if (!stop) setErr(e instanceof Error ? e.message : "Chart unavailable"); });
    void tick();
    const id = window.setInterval(() => { if (document.visibilityState === "visible") void tick(); }, 60_000);
    return () => { stop = true; window.clearInterval(id); };
  }, [sig.coin, tf]);

  useEffect(() => {
    if (!box.current) return;
    const ch = createChart(box.current, {
      autoSize: true, layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#8b968f", fontSize: 11 },
      grid: { vertLines: { color: "rgba(255,255,255,0.05)" }, horzLines: { color: "rgba(255,255,255,0.05)" } }, crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "rgba(255,255,255,.08)" }, timeScale: { borderColor: "rgba(255,255,255,.08)", timeVisible: true, secondsVisible: false },
    });
    const c = ch.addSeries(CandlestickSeries, { upColor: UP, downColor: DOWN, borderVisible: false, wickUpColor: UP, wickDownColor: DOWN,
      autoscaleInfoProvider: (base: () => AutoscaleInfo | null) => {  // the stop and targets are always on screen, however far from price
        const info = base();
        if (!info?.priceRange) return info;
        const levels = levelsRef.current;
        return { ...info, priceRange: { minValue: Math.min(info.priceRange.minValue, ...levels), maxValue: Math.max(info.priceRange.maxValue, ...levels) } };
      } });
    ref.current = { ch, c, lines: [] };
    return () => { ch.remove(); ref.current = null; };
  }, []);

  useEffect(() => {
    const r = ref.current;
    if (!r || !candles.length) return;
    r.c.setData(candles.map((k) => ({ time: Math.floor(k[0] / 1000) as UTCTimestamp, open: k[1], high: k[2], low: k[3], close: k[4] })));
    for (const l of r.lines) r.c.removePriceLine(l);
    const line = (price: number | null | undefined, color: string, title: string) => (price
      ? [r.c.createPriceLine({ price, color, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title })] : []);
    r.lines = [...line(sig.entry[0], "#ffffff", "entry"), ...line(sig.entry[1], "#ffffff", "entry"), ...line(sig.stop, DOWN, "stop"),
      ...line(sig.target, UP, "TP1"), ...line(sig.tp2, UP, "TP2")];
  }, [candles, sig.entry, sig.stop, sig.target, sig.tp2]);

  return (
    <div className="sd-chart-wrap">
      <div className="sd-chart" ref={box} role="img" aria-label={`${sig.coin} ${tf} chart with the signal's entry, stop and targets`} />
      {err && <p className="site-error" role="alert">{err}</p>}
    </div>
  );
}

function DeskChat({ sig }: { sig: Sig }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView?.({ block: "nearest" }); }, [msgs, busy]);

  async function ask(text: string) {
    const question = text.trim();
    if (question.length < 2 || busy) return;
    const history = msgs.filter((m) => !m.err).slice(-6).map(({ who, text: t }) => ({ who, text: t.slice(0, 2000) }));
    setMsgs((m) => [...m, { who: "me", text: question }]);
    setQ("");
    setBusy(true);
    try {
      const a = await memberApi<{ answer: string }>("/members/kiwi-brain/ask", { method: "POST", body: JSON.stringify({ question, signal_id: sig.id, history }) });
      setMsgs((m) => [...m, { who: "kiwi", text: a.answer }]);
    } catch (e) {
      setMsgs((m) => [...m, { who: "kiwi", text: e instanceof Error ? e.message : "Kiwi couldn't answer.", err: true }]);
    } finally { setBusy(false); }
  }

  return (
    <section className="sd-chat" aria-label={`Discuss ${sig.coin} with Kiwi`}>
      <div className="sd-msgs" aria-live="polite">
        {!msgs.length && (
          <>
            <p className="sd-intro"><img className="kt-av" src={site.kiwi.src} width={site.kiwi.width} height={site.kiwi.height} alt="" /> Ask me anything about this {sig.coin} chart. The decision stays yours.</p>
            <div className="sd-sug">{SUGGEST.map((s) => <button key={s} onClick={() => void ask(s)}>{s}</button>)}</div>
          </>
        )}
        {msgs.map((m, i) => <p key={i} className={`sd-msg is-${m.who}${m.err ? " is-err" : ""}`}>{m.text}</p>)}
        {busy && <p className="sd-msg is-kiwi">Kiwi is reading the chart…</p>}
        <div ref={end} />
      </div>
      <form className="sd-in" onSubmit={(e) => { e.preventDefault(); void ask(q); }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Ask about ${sig.coin}…`} maxLength={500} aria-label="Your question" />
        <button className="kt-btn is-small" disabled={busy || q.trim().length < 2}>Ask</button>
      </form>
    </section>
  );
}

export default function SignalDesk({ sig, modeTag, actions, error, notice, onClose }: {
  sig: Sig; modeTag: string; actions: ReactNode; error: string | null; notice: string | null; onClose: () => void;
}) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="sd" role="dialog" aria-modal="true" aria-label={`${sig.coin} ${sig.side} chart, discussion and trade`}>
      <header className="sd-head">
        <b className={`sd-title is-${sig.side.toLowerCase()}`}>{sig.coin} {sig.side}</b>
        <span className="kt-tag">{sig.timeframe ?? "1h"}</span>
        <span className="sd-mode">{modeTag}</span>
        <button className="tt-x sd-x" aria-label="Close signal desk" onClick={onClose}>✕</button>
      </header>
      <div className="sd-main">
        <DeskChart sig={sig} />
        <DeskChat sig={sig} />
      </div>
      <footer className="sd-foot">
        <div className="sd-proof"><RecordLine record={sig.record} strategy={sig.strategy} /><CrowdLine x={sig} /></div>
        {error && <p className="site-error" role="alert">{error}</p>}
        {notice && <p className="kt-restricted" role="alert">{notice}</p>}
        <div className="kt-actions">{actions}</div>
      </footer>
    </div>
  );
}
