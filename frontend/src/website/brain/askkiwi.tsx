import { useState } from "react";
import { site } from "../websiteData";
import { fmt, post, type Ev, type Impact, type KiwiConfig } from "./api";

/* ASK KIWI: answers come from the member's own logged evidence (the backend drops any citation that isn't in it). A requested
   change comes back as a PROPOSAL: nothing is applied until the member confirms, and it goes through the normal validated
   settings endpoints. */

type Proposal = { kind: "config"; config: KiwiConfig; changes: Record<string, unknown>; summary: string; impact: Impact }
  | { kind: "limits"; changes: Record<string, unknown>; summary: string } | { kind: "invalid"; summary: string };
type Answer = { answer: string; cited: string[]; sufficient: boolean; proposal: Proposal | null; events: Ev[]; model: string };
type Msg = { who: "me" | "kiwi"; text: string; a?: Answer; err?: boolean };

const SUGGEST = ["Why didn't you short BTC?", "What would make you go long ETH?", "What is my current risk exposure?", "Explain the last five rejected signals.",
  "How much did I pay in funding?", "Why are the strategies switched off?"];

function ProposalCard({ p, onDone }: { p: Proposal; onDone: (msg: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (p.kind === "invalid") return <p className="kb-warn">{p.summary}</p>;
  const changes = p.changes;
  async function apply() {
    setBusy(true);
    setErr(null);
    try {
      if (p.kind === "config") {
        const r = await post<{ version: number }>("/members/kiwi-brain/config", { config: p.config, note: `Ask Kiwi: ${p.summary}` }, "PUT");
        onDone(`Saved as Kiwi config v${r.version}. You can roll it back in the Control room.`);
      } else {
        await post("/members/trading/settings", changes, "PUT");
        onDone("Risk limits updated (checked by the same validation as the settings page).");
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Refused");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="kba-prop">
      <p><b>Proposed change</b> · {p.summary}</p>
      <ul>{Object.entries(p.changes).map(([k, v]) => <li key={k}><code>{k}</code> → <code>{JSON.stringify(v)}</code></li>)}</ul>
      {p.kind === "config" && <p className="kb-src">Over the last {p.impact.signals} signals: {p.impact.allowed_before} → {p.impact.allowed_after} would have passed your filters.</p>}
      <p className="kb-src">Nothing changes until you confirm. Filters can only skip trades; the risk engine still checks every entry.</p>
      {err && <p className="kb-warn">{err}</p>}
      <button className="kb-btn" disabled={busy} onClick={() => void apply()}>Confirm & apply</button>
    </div>
  );
}

export function AskKiwi({ open, onClose, onJump }: { open: boolean; onClose: () => void; onJump: (e: Ev) => void }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  if (!open) return null;
  async function ask(text: string) {
    if (!text.trim() || busy) return;
    setMsgs((m) => [...m, { who: "me", text }]);
    setQ("");
    setBusy(true);
    try {
      const a = await post<Answer>("/members/kiwi-brain/ask", { question: text });
      setMsgs((m) => [...m, { who: "kiwi", text: a.answer, a }]);
    } catch (e) {
      setMsgs((m) => [...m, { who: "kiwi", text: e instanceof Error ? e.message : "Kiwi couldn't answer.", err: true }]);
    } finally {
      setBusy(false);
    }
  }
  return (
    <aside className="kba" role="dialog" aria-label="Ask Kiwi">
      <header className="kba-h"><img src={site.kiwi.src} alt="" width={30} height={25} /><b>ASK KIWI 🥝</b><span className="kb-src">answers only from your records</span>
        <button className="kb-x" onClick={onClose} aria-label="Close">✕</button></header>
      <div className="kba-body">
        {!msgs.length && <div className="kba-sug">{SUGGEST.map((s) => <button key={s} onClick={() => void ask(s)}>{s}</button>)}</div>}
        {msgs.map((m, i) => (
          <div key={i} className={`kba-msg is-${m.who}${m.err ? " is-err" : ""}`}>
            <p>{m.text}</p>
            {m.a && !m.a.sufficient && <p className="kb-src">⚠ Not enough recorded evidence to answer fully.</p>}
            {m.a && m.a.events.length > 0 && <div className="kba-cites">{m.a.events.map((e) => <button key={e.id} onClick={() => onJump(e)} title={e.title}>
              E{e.id} · {e.kind} · {fmt.time(e.at)}</button>)}</div>}
            {m.a && m.a.cited.filter((c) => !c.startsWith("E")).length > 0 && <p className="kb-src">Also from: {m.a.cited.filter((c) => !c.startsWith("E")).join(", ")}</p>}
            {m.a?.proposal && <ProposalCard p={m.a.proposal} onDone={(t) => setMsgs((x) => [...x, { who: "kiwi", text: t }])} />}
          </div>
        ))}
        {busy && <div className="kba-msg is-kiwi"><p className="kba-typing"><i /><i /><i /></p></div>}
      </div>
      <form className="kba-in" onSubmit={(e) => { e.preventDefault(); void ask(q); }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about your trades, signals, risk…" maxLength={500} aria-label="Your question" />
        <button className="kb-btn" disabled={busy || q.trim().length < 2}>Ask</button>
      </form>
    </aside>
  );
}
