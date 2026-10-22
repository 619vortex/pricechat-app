import { Component, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { usePoll, type Ev, type Overview } from "./brain/api";
import { AskKiwi } from "./brain/askkiwi";
import { Battlefield } from "./brain/battlefield";
import { ControlRoom } from "./brain/controlroom";
import { LiveBrain, useEvents } from "./brain/livebrain";
import { KiwiMood, moodOf } from "./brain/mood";
import { Performance } from "./brain/performance";
import { StatusBar } from "./brain/statusbar";
import { TimeMachine } from "./brain/timemachine";
import { SignalToasts } from "./brain/toasts";
import "@fontsource-variable/archivo/wdth.css";
import "./brain/brain.css";

/* KIWI BRAIN: the secret trading command center (/earn/brain). Four tabs over one live state; every number is a real record
   or real Hyperliquid data, and anything unavailable says so. Nothing here can place an order. A panel that crashes shows an
   error in place (the rest keeps working; trade protection lives on the exchange and the backend, never in this page). */

const TABS = [["live", "LIVE BRAIN"], ["battlefield", "BATTLEFIELD"], ["performance", "PERFORMANCE"], ["timemachine", "TIME MACHINE"]] as const;
type Tab = (typeof TABS)[number][0];

function hashTab(): Tab {
  const h = typeof location !== "undefined" ? location.hash.replace("#", "") : "";
  return (TABS.find(([k]) => k === h)?.[0] ?? "live") as Tab;
}

class Boundary extends Component<{ children: ReactNode; name: string }, { err: string | null }> {
  state = { err: null as string | null };
  static getDerivedStateFromError(e: Error) { return { err: e.message || "error" }; }
  render() {
    return this.state.err ? <section className="kbp-panel"><p className="kb-warn">The {this.props.name} panel failed to draw ({this.state.err}).
      Your positions and their exchange-side protection are not affected.</p><button className="kb-btn" onClick={() => this.setState({ err: null })}>Retry</button></section>
      : this.props.children;
  }
}

export function KiwiBrainPage() {
  const ov = usePoll<Overview>("/members/kiwi-brain/overview", 10_000);
  const events = useEvents();
  const [tab, setTabState] = useState<Tab>(hashTab);
  const [focus, setFocus] = useState<Ev | null>(null);
  const [replay, setReplay] = useState<number | null>(null);
  const [ask, setAsk] = useState(false);
  const [control, setControl] = useState(false);
  useEffect(() => { document.title = "Kiwi Brain · Price Chat"; }, []);
  useEffect(() => {
    const on = () => setTabState(hashTab());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const setTab = useCallback((t: Tab) => {
    setTabState(t);
    try { history.replaceState(null, "", `#${t}`); } catch { /* sandboxed */ }
  }, []);
  const o = ov.data;
  const coins = useMemo(() => Array.from(new Set([...(o?.positions.map((p) => p.coin) ?? []), "BTC", "ETH", "SOL", "HYPE"])), [o]);
  const mood = moodOf(events.evs);
  const jump = useCallback((e: Ev) => { setFocus(e); setTab("battlefield"); setAsk(false); }, [setTab]);
  const openSignal = useCallback((id: number) => { setReplay(id); setTab("timemachine"); }, [setTab]);

  return (
    <div className="kbx">
      <header className="kbx-top">
        <a className="kbx-back" href="/earn">← Terminal</a>
        <h1>KIWI BRAIN <small>SECRET TRADING LAB</small></h1>
        <div className="kbx-mood" aria-live="polite"><KiwiMood mood={mood} /><span>{mood.line}</span></div>
      </header>
      {ov.err === "signin" ? (
        <section className="kbp-panel kbx-gate"><p>The Kiwi Brain shows your own bot, so sign in first.</p><a className="kb-btn" href="/passport">Sign in with Kiwi Passport</a></section>
      ) : ov.err && !o ? <p className="kb-warn kbx-gate">{ov.err}</p> : !o ? <div className="kb-skel kbx-gate" style={{ height: 300 }} /> : (
        <>
          <StatusBar o={o} onChanged={ov.reload} onAsk={() => setAsk(true)} onControl={() => setControl(true)} />
          {ov.err && <p className="kb-warn">Status refresh failed ({ov.err}); showing the last good read from {new Date(ov.at ?? 0).toLocaleTimeString()}.</p>}
          <nav className="kbx-tabs" role="tablist" aria-label="Kiwi Brain">
            {TABS.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>{l}</button>)}
          </nav>
          <main className="kbx-main">
            <Boundary key={tab} name={TABS.find(([k]) => k === tab)![1]}>
              {tab === "live" && <LiveBrain o={o} coins={coins} onJump={jump} onReplay={openSignal} ev={events} />}
              {tab === "battlefield" && <Battlefield o={o} coins={coins} focus={focus} setFocus={setFocus} onSignal={openSignal} />}
              {tab === "performance" && <Performance o={o} />}
              {tab === "timemachine" && <TimeMachine start={replay} onClear={() => setReplay(null)} />}
            </Boundary>
          </main>
          <Boundary name="Ask Kiwi"><AskKiwi open={ask} onClose={() => setAsk(false)} onJump={jump} /></Boundary>
          <Boundary name="Control room"><ControlRoom o={o} open={control} onClose={() => setControl(false)} onChanged={ov.reload} /></Boundary>
        </>
      )}
      <SignalToasts onOpen={(e) => { if (e.signal_id) openSignal(e.signal_id); }} />
      <footer className="kbx-foot">Every number is a real record or live Hyperliquid data; PAPER is simulated, backtests are hypothetical, and anything unavailable
        says so. Nothing on this page places orders. Trading is risky and not financial advice.</footer>
    </div>
  );
}
