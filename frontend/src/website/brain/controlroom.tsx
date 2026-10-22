import { useEffect, useState } from "react";
import { STRAT, fmt, post, usePoll, type ConfigState, type Impact, type KiwiConfig, type Overview } from "./api";

/* CONTROL ROOM: Teach Your Kiwi (versioned filters on the published signals, applied to AUTO entries) and the account's risk
   limits (the existing validated settings). Every change shows its impact first and needs an explicit save. */

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function Diff({ a, b }: { a: KiwiConfig; b: KiwiConfig }) {
  const ks = (Object.keys(b) as (keyof KiwiConfig)[]).filter((k) => JSON.stringify(a[k]) !== JSON.stringify(b[k]));
  if (!ks.length) return <p className="kb-src">No changes.</p>;
  return <ul className="kbcr-diff">{ks.map((k) => <li key={k}><code>{k}</code>: <s>{JSON.stringify(a[k])}</s> → <b>{JSON.stringify(b[k])}</b></li>)}</ul>;
}

export function ControlRoom({ o, open, onClose, onChanged }: { o: Overview; open: boolean; onClose: () => void; onChanged: () => void }) {
  const st = usePoll<ConfigState>(open ? "/members/kiwi-brain/config" : null, 120_000);
  const [draft, setDraft] = useState<KiwiConfig | null>(null);
  const [impact, setImpact] = useState<Impact | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [nl, setNl] = useState("");
  const [interp, setInterp] = useState<{ ok: boolean; changes?: Record<string, unknown>; unsupported: string[]; explanation: string; config?: KiwiConfig; error?: string } | null>(null);
  const [lim, setLim] = useState(o.limits);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (st.data) setDraft(st.data.config); }, [st.data]);
  useEffect(() => {
    if (!draft || !st.data) return;
    const id = window.setTimeout(() => {
      post<{ impact: Impact }>("/members/kiwi-brain/config/preview", { config: draft }).then((r) => { setImpact(r.impact); setErr(null); })
        .catch((e: unknown) => setErr(e instanceof Error ? e.message : "Invalid"));
    }, 300);
    return () => window.clearTimeout(id);
  }, [draft, st.data]);
  if (!open) return null;
  const cur = st.data?.config;
  const set = <K extends keyof KiwiConfig>(k: K, v: KiwiConfig[K]) => setDraft((d) => (d ? { ...d, [k]: v } : d));
  async function run(fn: () => Promise<unknown>, ok: string) {
    setBusy(true); setErr(null); setMsg(null);
    try { await fn(); setMsg(ok); st.reload(); onChanged(); } catch (e) { setErr(e instanceof Error ? e.message : "Refused"); } finally { setBusy(false); }
  }
  async function interpret() {
    setBusy(true); setErr(null); setInterp(null);
    try { setInterp(await post("/members/kiwi-brain/config/interpret", { text: nl })); } catch (e) { setErr(e instanceof Error ? e.message : "Unavailable"); } finally { setBusy(false); }
  }
  return (
    <div className="kbcr-veil" role="dialog" aria-modal="true" aria-label="Control room">
      <div className="kbcr">
        <header className="kbp-ph"><h3>⚙ Control room</h3><button className="kb-x" onClick={onClose} aria-label="Close">✕</button></header>
        {msg && <p className="kb-ok" role="status">{msg}</p>}
        {err && <p className="kb-warn" role="alert">{err}</p>}
        <div className="kbcr-cols">
          <section>
            <h4>Teach your Kiwi <small>v{st.data?.version ?? 0}{st.data?.at ? ` · ${fmt.ago(st.data.at)}` : ""}</small></h4>
            <p className="kb-src">Filters on the bot's published signals for your AUTO entries. They can only skip trades: the risk engine still checks everything.</p>
            <div className="kbcr-nl">
              <textarea value={nl} onChange={(e) => setNl(e.target.value)} maxLength={600} rows={2} placeholder='e.g. "Only take BTC breakouts when funding is negative"' aria-label="Describe a rule" />
              <button className="kb-btn" disabled={busy || nl.trim().length < 3} onClick={() => void interpret()}>Interpret</button>
            </div>
            {interp && <div className="kbcr-interp">
              <p><b>Kiwi understood:</b> {interp.explanation || "—"}</p>
              {interp.changes && Object.keys(interp.changes).length > 0 && cur && interp.config && <Diff a={cur} b={interp.config} />}
              {interp.unsupported.length > 0 && <p className="kb-warn">Not supported (ignored): {interp.unsupported.join(" · ")}</p>}
              {interp.error && <p className="kb-warn">{interp.error}</p>}
              {interp.ok && interp.config && <button className="kb-btn" onClick={() => { setDraft(interp.config!); setInterp(null); }}>Use these rules (review below)</button>}
            </div>}
            {draft && <div className="kbcr-form">
              <fieldset><legend>Strategies</legend>{Object.keys(draft.strategies).map((k) => <label key={k} className="kb-check">
                <input type="checkbox" checked={draft.strategies[k]} onChange={(e) => set("strategies", { ...draft.strategies, [k]: e.target.checked })} /> {STRAT[k] ?? k}</label>)}</fieldset>
              <label>Direction<select value={draft.sides} onChange={(e) => set("sides", e.target.value)}><option value="both">Long and short</option><option value="long">Long only</option><option value="short">Short only</option></select></label>
              <label>Pairs (empty = any)<input value={draft.coins.join(", ")} onChange={(e) => set("coins", e.target.value.split(/[ ,]+/).filter(Boolean).map((x) => x.toUpperCase()))} /></label>
              <fieldset><legend>Timeframes</legend>{["1h", "4h"].map((t) => <label key={t} className="kb-check"><input type="checkbox" checked={draft.timeframes.includes(t)}
                onChange={(e) => set("timeframes", e.target.checked ? [...draft.timeframes, t] : draft.timeframes.filter((x) => x !== t))} /> {t}</label>)}</fieldset>
              <label>Min. risk/reward<input type="number" step="0.1" min={1} max={5} value={draft.min_rr ?? ""} onChange={(e) => set("min_rr", e.target.value ? Number(e.target.value) : null)} placeholder="none" /></label>
              <label>Funding<select value={draft.funding} onChange={(e) => set("funding", e.target.value)}><option value="any">Any</option><option value="negative">Negative only</option><option value="positive">Positive only</option></select></label>
              <label>Max volatility (ATR %)<input type="number" step="0.1" min={0.1} max={20} value={draft.max_volatility_pct ?? ""} onChange={(e) => set("max_volatility_pct", e.target.value ? Number(e.target.value) : null)} placeholder="none" /></label>
              <fieldset><legend>Schedule (UTC)</legend>
                <label className="kb-check"><input type="checkbox" checked={!!draft.schedule} onChange={(e) => set("schedule", e.target.checked ? { days: [0, 1, 2, 3, 4], from_h: 8, to_h: 20 } : null)} /> only at set times</label>
                {draft.schedule && <div className="kbcr-sched">{DAYS.map((d, i) => <label key={d} className="kb-check"><input type="checkbox" checked={draft.schedule!.days.includes(i)}
                  onChange={(e) => set("schedule", { ...draft.schedule!, days: e.target.checked ? [...draft.schedule!.days, i].sort() : draft.schedule!.days.filter((x) => x !== i) })} />{d}</label>)}
                  <input type="number" min={0} max={23} value={draft.schedule.from_h} onChange={(e) => set("schedule", { ...draft.schedule!, from_h: Number(e.target.value) })} aria-label="From hour" />–
                  <input type="number" min={1} max={24} value={draft.schedule.to_h} onChange={(e) => set("schedule", { ...draft.schedule!, to_h: Number(e.target.value) })} aria-label="To hour" /></div>}
              </fieldset>
              {cur && <div className="kbcr-impact"><h5>Impact before saving</h5><Diff a={cur} b={draft} />
                {impact && <p className="kb-src">Of the last {impact.signals} published signals, {impact.allowed_before} passed your filters; with this change {impact.allowed_after} would.
                  {impact.removed.length ? ` Now skipped: ${impact.removed.join(", ")}.` : ""}{impact.added.length ? ` Now allowed: ${impact.added.join(", ")}.` : ""}</p>}</div>}
              <button className="kb-btn" disabled={busy || !cur || JSON.stringify(cur) === JSON.stringify(draft)}
                onClick={() => void run(() => post("/members/kiwi-brain/config", { config: draft, note: "Control room" }, "PUT"), "Saved as a new version.")}>Save as new version</button>
            </div>}
            {st.data && st.data.versions.length > 0 && <details className="kbcr-ver"><summary>Versions ({st.data.versions.length})</summary>
              <ol>{st.data.versions.map((v) => <li key={v.version}><b>v{v.version}</b> {fmt.ago(v.at)} · {v.note ?? ""}
                {v.version !== st.data!.version && <button className="kb-link" onClick={() => void run(() => post("/members/kiwi-brain/config/rollback", { version: v.version }), `Restored v${v.version}.`)}>restore</button>}</li>)}</ol></details>}
          </section>
          <section>
            <h4>Risk limits <small>{o.mode}</small></h4>
            <p className="kb-src">The same limits as the terminal's settings, checked by the same validation and hard caps (max 5x, max 3 positions).</p>
            <div className="kbcr-form">
              <label>Max leverage<input type="number" min={1} max={5} value={lim.max_leverage} onChange={(e) => setLim({ ...lim, max_leverage: Number(e.target.value) })} /></label>
              <label>Max loss per trade (USD)<input type="number" min={1} step="0.5" value={lim.max_loss_per_trade_usd} onChange={(e) => setLim({ ...lim, max_loss_per_trade_usd: Number(e.target.value) })} /></label>
              <label>Max open positions<input type="number" min={1} max={3} value={lim.max_positions} onChange={(e) => setLim({ ...lim, max_positions: Number(e.target.value) })} /></label>
              <label>Daily loss limit (USD)<input type="number" min={1} step="1" value={lim.daily_loss_limit_usd} onChange={(e) => setLim({ ...lim, daily_loss_limit_usd: Number(e.target.value) })} /></label>
              <label>Allowed pairs (empty = any)<input value={lim.allowed_coins} onChange={(e) => setLim({ ...lim, allowed_coins: e.target.value.toUpperCase() })} /></label>
              <p className="kb-src">Loss at your stops now: {fmt.usd(o.exposure.loss_at_stop)}. New entries: {o.paused ? "PAUSED" : "allowed"}. Mode: {o.mode}{o.mode !== "LIVE" ? " (live is enabled from the terminal with its checks)" : ""}.</p>
              <button className="kb-btn" disabled={busy || JSON.stringify(lim) === JSON.stringify(o.limits)}
                onClick={() => void run(() => post("/members/trading/settings", { max_leverage: lim.max_leverage, max_loss_per_trade_usd: lim.max_loss_per_trade_usd,
                  max_positions: lim.max_positions, daily_loss_limit_usd: lim.daily_loss_limit_usd, allowed_coins: lim.allowed_coins }, "PUT"), "Risk limits saved.")}>Save limits</button>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
