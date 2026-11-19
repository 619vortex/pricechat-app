import { lazy, Suspense, useEffect, useMemo, useState, type ReactNode } from "react";
import { request } from "../../api/client";
import { useApi } from "../../api/useApi";
import type { Story } from "./classic";
import "./t2.css";

/** PRICE CHAT TERMINAL 2.0 (owner, 2026-11-18): one page. A top bar with one automation switch and the emergency stop, three counters, the
 *  single most important story, one deduplicated live feed (Trending, News Desk, X Intelligence, disruptions and rumour watch together),
 *  a story panel with hooks and X / Telegram publishing, and every older control in a Settings drawer. The server decides what may go out:
 *  each button calls the same checked endpoints as before (verification, the one-story rule, caps, stops, budgets). */

const Classic = lazy(() => import("./classic").then((m) => ({ default: m.TerminalClassic })));
const XIntel = lazy(() => import("./XIntelPage").then((m) => ({ default: m.XIntelPage })));
const NewsDesk = lazy(() => import("../x/NewsDesk").then((m) => ({ default: m.NewsDesk })));
const Trending = lazy(() => import("../x/TrendingTopics").then((m) => ({ default: m.TrendingTopics })));
const Validation = lazy(() => import("./ValidationTab").then((m) => ({ default: m.ValidationTab })));
const WorldMonitor = lazy(() => import("../WorldMonitorPage").then((m) => ({ default: m.WorldMonitorPage })));

type DeskInfo = { id: number; status: string; headline: string; why: string; x_status: string; tg_status: string; released_at: string | null; x_note?: string; tg_note?: string };
export type T2Story = Story & { desk?: DeskInfo; merged?: number[]; telegram?: { at: string; message_id: number; chat: string } | null; sensitive?: unknown };
type DeskRow = { id: number; title: string; headline: string; status: string; category?: string; why: string; first_seen: string | null; released_at: string | null; lane: string;
  evidence?: string; sources: { name: string; url: string }[]; desk: DeskInfo };
type Automation = { status: "running" | "paused" | "attention"; on: boolean; stopped: boolean; paused_at: string | null; attention: string[]; trending_auto: boolean };
export type T2Overview = { now: string; counts: Record<string, number>; stories: T2Story[]; total: number; desk_only: DeskRow[]; automation: Automation;
  today: { x: number; live: number; telegram: number }; last_scan: string | null;
  controls: { newsdesk_stop: boolean; trending_stop: boolean; x_live: boolean; newsdesk_mode: string; trending_mode: string } };
type Hook = { kind: string; text: string; writer: string; ok: boolean; reason: string };
type Detail = T2Story & { timeline: { at: string | null; source: string; outlet: string; title: string; url: string; reliable: boolean; official: boolean }[]; hooks: Hook[] };

type Verdict = "verified" | "developing" | "unverified";
type Item = { key: string; id: number; title: string; category: string; lane: string; at: string | null; verdict: Verdict; level: 1 | 2 | 3; sources: number; x: boolean;
  priority: boolean; story?: T2Story; desk?: DeskRow };

const VERDICT_TEXT: Record<Verdict, string> = { verified: "Verified", developing: "Developing", unverified: "Unverified" };
const LANE_TEXT: Record<string, string> = { breaking: "Breaking", disruption: "Disruption", emerging: "Emerging", rumor: "Rumor watch", watch: "News", published: "Published", rejected: "Skipped" };
const HOOK_TEXT: Record<string, string> = { breaking: "Breaking", viral: "Viral", bold: "Viral", curiosity: "Curiosity" };
const FILTERS = [{ key: "all", label: "All" }, { key: "breaking", label: "Breaking" }, { key: "emerging", label: "Emerging" }, { key: "rumor", label: "Rumors" },
  { key: "published", label: "Published" }] as const;
type Filter = (typeof FILTERS)[number]["key"];

function verdictOf(s: T2Story): Verdict {
  if (s.rumor || s.evidence?.rumor) return "unverified";
  return s.evidence && ["confirmed", "corroborated"].includes(s.evidence.level) ? "verified" : "developing";
}
const timeOf = (s: T2Story) => s.first_report || s.detected || s.last_update;
const ms = (iso: string | null | undefined) => (iso ? Date.parse(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`) : NaN);

export function ago(iso: string | null | undefined, now = Date.now()): string {
  const t = ms(iso);
  if (!Number.isFinite(t)) return "";
  const m = Math.max(0, Math.round((now - t) / 60_000));
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  return h < 24 ? `${h}h ago` : `${Math.floor(h / 24)}d ago`;
}

function levelOf(s: T2Story): 1 | 2 | 3 {
  const v = s.viral?.score ?? 0;
  if (s.lane === "breaking" || (s.lane === "disruption" && v >= 50)) return 3;
  if (s.lane === "disruption" || s.lane === "emerging" || v >= 60) return 2;
  return 1;
}

/** The featured story: urgency (lane, freshness), relevance and evidence together with viral potential. Published and skipped ones never. */
export function featuredOf(stories: T2Story[], now = Date.now()): T2Story | null {
  const lane: Record<string, number> = { breaking: 40, disruption: 30, emerging: 18, rumor: 4, watch: 0 };
  let best: T2Story | null = null;
  let top = -Infinity;
  for (const s of stories) {
    if (!(s.lane in lane)) continue;
    const age = (now - ms(timeOf(s))) / 3_600_000;
    const fresh = Number.isFinite(age) ? (age < 1 ? 15 : age < 3 ? 8 : age < 8 ? 2 : -10) : 0;
    const score = lane[s.lane] + 0.35 * (s.viral?.score ?? 0) + 0.35 * (s.evidence?.score ?? 0) + fresh + Math.min(6, s.sources.length);
    if (score > top) { top = score; best = s; }
  }
  return best;
}

export function feedOf(data: T2Overview, now = Date.now()): Item[] {
  const items: Item[] = data.stories.map((s) => ({
    key: `t${s.id}`, id: s.id, title: s.title, category: s.category, lane: s.lane, at: timeOf(s), verdict: verdictOf(s), level: levelOf(s), sources: s.sources.length,
    x: s.sources.some((x) => x.source === "x_accounts"), priority: false, story: s,
  }));
  for (const d of data.desk_only ?? []) {
    items.push({ key: `d${d.id}`, id: d.id, title: d.headline || d.title, category: d.category || "news", lane: d.lane, at: d.released_at || d.first_seen,
      verdict: d.status === "released" ? "verified" : "developing", level: d.status === "released" ? 2 : 1, sources: d.sources.length, x: false, priority: false, desk: d });
  }
  for (const it of items) {
    const age = (now - ms(it.at)) / 3_600_000;
    it.priority = ["breaking", "disruption", "emerging"].includes(it.lane) && (!Number.isFinite(age) || age < 3);
  }
  return items.sort((a, b) => Number(b.priority) - Number(a.priority) || (ms(b.at) || 0) - (ms(a.at) || 0));
}

async function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body) });
}
const errText = (e: unknown) => String((e as Error)?.message ?? e).replace(/^\d{3}:?\s*/, "");

// --- the page --------------------------------------------------------------------------------------------------------------------------

export function TerminalPage() {
  const { data, error, loading, reload } = useApi(() => request<T2Overview>("/terminal?hours=24"), 20_000);
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Item | null>(null);
  const [settings, setSettings] = useState(false);
  const [toast, setToast] = useState<{ ok: boolean; text: string } | null>(null);
  const [quick, setQuick] = useState<T2Story | null>(null);
  const [, tick] = useState(0);
  useEffect(() => { const id = window.setInterval(() => tick((n) => n + 1), 30_000); return () => window.clearInterval(id); }, []);
  useEffect(() => { if (!toast) return; const id = window.setTimeout(() => setToast(null), 5000); return () => window.clearTimeout(id); }, [toast]);

  const feed = useMemo(() => (data ? feedOf(data) : []), [data]);
  const featured = useMemo(() => (data ? featuredOf(data.stories) : null), [data]);
  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return feed.filter((i) => {
      if (filter === "all" ? i.lane === "rejected" : filter === "breaking" ? !["breaking", "disruption"].includes(i.lane) : i.lane !== filter) return false;
      return !needle || i.title.toLowerCase().includes(needle) || (i.story?.sources ?? []).some((x) => (x.outlet ?? "").toLowerCase().includes(needle));
    }).filter((i) => !featured || filter !== "all" || i.key !== `t${featured.id}`);
  }, [feed, filter, q, featured]);

  async function run(fn: () => Promise<unknown>, ok: string) {
    try { await fn(); setToast({ ok: true, text: ok }); await reload(); } catch (e) { setToast({ ok: false, text: errText(e) }); }
  }

  if (loading && !data) return <div className="t2"><div className="t2-boot"><span className="t2-dot" /> Connecting to the live desk…</div></div>;
  const c = data?.counts ?? {};
  const a = data?.automation;
  return (
    <div className="t2">
      <TopBar data={data} onSettings={() => setSettings(true)} run={run} />
      {error && !data && <div className="t2-alert bad" role="alert">Can't reach the desk: {error}</div>}
      {data && a && (a.attention.length > 0) && (
        <div className={`t2-alert ${a.stopped ? "bad" : "warn"}`} role="status">
          <strong>{a.stopped ? "Emergency stop is on" : "Needs attention"}</strong>
          <span>{a.attention.filter((x) => !(a.stopped && /emergency stop|stop is on/i.test(x))).join(" · ") || "Nothing posts automatically until you lift it."}</span>
          {a.stopped && <button className="t2-btn sm" onClick={() => void liftStop(data, run)}>Lift stop</button>}
        </div>
      )}
      {data && (
        <>
          <div className="t2-counters">
            <Counter label="Breaking" value={(c.breaking ?? 0) + (c.disruption ?? 0)} tone="hot" active={filter === "breaking"} onClick={() => setFilter(filter === "breaking" ? "all" : "breaking")} />
            <Counter label="Emerging" value={(c.emerging ?? 0) + (c.rumor ?? 0)} tone="cool" active={filter === "emerging"} onClick={() => setFilter(filter === "emerging" ? "all" : "emerging")} />
            <Counter label="Published today" value={data.today.x + data.today.telegram} tone="ok" active={filter === "published"} onClick={() => setFilter(filter === "published" ? "all" : "published")}
              hint={`${data.today.x} X${data.controls.x_live ? "" : " (dry run)"} · ${data.today.telegram} Telegram`} />
          </div>
          {featured ? (
            <Featured s={featured} onOpen={() => setOpen({ ...feedOf({ ...data, stories: [featured], desk_only: [] })[0] })} onPublish={() => setQuick(featured)}
              onSkip={() => void run(() => post(`/terminal/stories/${featured.id}/reject`, { reason: "Skipped from the Terminal" }), "Skipped.")} />
          ) : <div className="t2-featured empty"><span className="t2-dot" /> Quiet right now. New stories appear here the moment they break.</div>}
          <section className="t2-feed" aria-label="Live feed">
            <div className="t2-feed-head">
              <h2>Live feed</h2>
              <div className="t2-chips" role="tablist">{FILTERS.map((f) => (
                <button key={f.key} role="tab" aria-selected={filter === f.key} className={`t2-chip ${filter === f.key ? "on" : ""}`} onClick={() => setFilter(f.key)}>{f.label}</button>))}</div>
              <input className="t2-search" type="search" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search stories" />
            </div>
            {shown.length === 0 ? <p className="t2-empty">Nothing here right now.</p> : (
              <ul className="t2-list">{shown.map((i, n) => <FeedCard key={i.key} i={i} n={n} onOpen={() => setOpen(i)} />)}</ul>
            )}
          </section>
        </>
      )}
      {open && <StoryPanel item={open} onClose={() => setOpen(null)} onChanged={() => void reload()} />}
      {quick && <QuickPublish s={quick} xLive={!!data?.controls.x_live} onClose={() => setQuick(null)} onDone={(text) => { setQuick(null); setToast({ ok: true, text }); void reload(); }} />}
      {settings && <SettingsDrawer data={data} onClose={() => { setSettings(false); void reload(); }} run={run} />}
      {toast && <div className={`t2-toast ${toast.ok ? "ok" : "bad"}`} role="status">{toast.text}</div>}
    </div>
  );
}

async function liftStop(data: T2Overview, run: (fn: () => Promise<unknown>, ok: string) => Promise<void>) {
  if (!window.confirm("Lift the emergency stop? Automatic posting resumes within its normal limits.")) return;
  await run(async () => {
    if (data.controls.newsdesk_stop) await post("/terminal/stop", { which: "newsdesk", on: false });
    if (data.controls.trending_stop) await post("/terminal/stop", { which: "trending", on: false });
  }, "Emergency stop lifted.");
}

function TopBar({ data, onSettings, run }: { data: T2Overview | null; onSettings: () => void; run: (fn: () => Promise<unknown>, ok: string) => Promise<void> }) {
  const a = data?.automation;
  const status = a?.status ?? "running";
  const word = { running: "Running", paused: "Paused", attention: "Needs attention" }[status];
  async function toggle() {
    if (!a) return;
    if (a.on) {
      if (!window.confirm("Pause automation? Nothing posts on its own until you turn it back on. Scanning, the feed and your own publishing keep working.")) return;
      await run(() => post("/terminal/automation", { on: false }), "Automation paused.");
    } else {
      if (!window.confirm("Turn automation back on? Stories post on their own again, within every limit and check.")) return;
      await run(() => post("/terminal/automation", { on: true }), "Automation is running.");
    }
  }
  async function stopAll() {
    if (!window.confirm("EMERGENCY STOP: stop all automatic X posting from both desks now?\n\nNothing is deleted. You can lift it from this page.")) return;
    await run(() => post("/terminal/stop", { which: "newsdesk", on: true }), "Emergency stop is on. Nothing posts.");
  }
  return (
    <header className="t2-top">
      <div className="t2-brand">
        <span className="t2-logo">PRICE CHAT</span>
        <span className="t2-live"><span className="t2-dot" />LIVE</span>
      </div>
      <div className={`t2-status s-${status}`} title={a?.attention.join("\n") || undefined}><span className="t2-pip" />{word}</div>
      <span className="t2-updated">{data ? `Updated ${ago(data.now)}` : ""}</span>
      <span className="t2-grow" />
      {a && (
        <label className="t2-switch" title={a.on ? "Automatic publishing is on" : "Automatic publishing is paused"}>
          <span>Automation</span>
          <button role="switch" aria-checked={a.on} aria-label="Automation" className={`t2-toggle ${a.on ? "on" : ""}`} onClick={() => void toggle()} disabled={a.stopped}>
            <span className="t2-knob" />
          </button>
          <span className="t2-onoff">{a.on ? "ON" : "OFF"}</span>
        </label>
      )}
      {data && !data.automation.stopped && <button className="t2-btn danger sm" onClick={() => void stopAll()}>Emergency stop</button>}
      <button className="t2-icon" aria-label="Settings" title="Settings" onClick={onSettings}>
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path fill="currentColor" d="M19.4 13a7.6 7.6 0 0 0 0-2l2-1.6-2-3.4-2.4 1a7.4 7.4 0 0 0-1.7-1L15 3.5h-4l-.4 2.5a7.4 7.4 0 0 0-1.7 1l-2.4-1-2 3.4 2 1.6a7.6 7.6 0 0 0 0 2l-2 1.6 2 3.4 2.4-1c.5.4 1.1.7 1.7 1l.4 2.5h4l.4-2.5c.6-.3 1.2-.6 1.7-1l2.4 1 2-3.4-2-1.6ZM13 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7Z" transform="translate(-1 0)" /></svg>
      </button>
    </header>
  );
}

function Counter({ label, value, tone, active, onClick, hint }: { label: string; value: number; tone: string; active: boolean; onClick: () => void; hint?: string }) {
  return (
    <button className={`t2-counter ${tone} ${active ? "on" : ""}`} onClick={onClick} aria-pressed={active} title={hint}>
      <span className="t2-counter-n">{value}</span>
      <span className="t2-counter-l">{label}</span>
    </button>
  );
}

function VerdictChip({ v }: { v: Verdict }) {
  return <span className={`t2-verdict v-${v}`}>{v === "verified" ? "✓ " : v === "unverified" ? "! " : ""}{VERDICT_TEXT[v]}</span>;
}

function Importance({ level }: { level: 1 | 2 | 3 }) {
  return <span className={`t2-imp l${level}`} aria-label={`Importance ${level} of 3`} title={["", "Normal", "High", "Top"][level]}><i /><i /><i /></span>;
}

function Featured({ s, onOpen, onPublish, onSkip }: { s: T2Story; onOpen: () => void; onPublish: () => void; onSkip: () => void }) {
  const v = verdictOf(s);
  const context = s.fast_why ? null : s.sources.find((x) => x.title && x.title !== s.title)?.title;
  const outlets = [...new Set(s.sources.filter((x) => x.reliable || x.official).map((x) => x.outlet))].slice(0, 3);
  const sent = s.lane === "published";
  return (
    <article className={`t2-featured lane-${s.lane}`}>
      <div className="t2-featured-top">
        <span className={`t2-lane lane-${s.lane}`}>{s.lane === "breaking" ? "● BREAKING" : LANE_TEXT[s.lane] ?? s.lane}</span>
        <VerdictChip v={v} />
        <span className="t2-meta">{s.category} · {ago(timeOf(s))} · {s.sources.length} source{s.sources.length === 1 ? "" : "s"}</span>
      </div>
      <h1 className="t2-featured-title" onClick={onOpen}>{s.title}</h1>
      {v === "unverified" && <p className="t2-warnline">Unconfirmed claim. Not reported by a reliable outlet or an official source yet, so it can't be published as fact.</p>}
      {context && <p className="t2-context">{context}</p>}
      <div className="t2-confidence">
        <Meter label="Confidence" value={s.evidence?.score ?? 0} kind="ev" />
        <Meter label="Viral potential" value={s.viral?.score ?? 0} kind="vi" />
        {outlets.length > 0 && <span className="t2-meta">via {outlets.join(", ")}</span>}
      </div>
      <div className="t2-actions">
        <button className="t2-btn primary" onClick={onPublish} disabled={v === "unverified" || sent} title={v === "unverified" ? "Unverified: can't be published" : undefined}>Publish</button>
        <button className="t2-btn" onClick={onOpen}>Edit</button>
        <button className="t2-btn ghost" onClick={onSkip}>Skip</button>
      </div>
    </article>
  );
}

function Meter({ label, value, kind }: { label: string; value: number; kind: "ev" | "vi" }) {
  const v = Math.max(0, Math.min(100, Math.round(value)));
  return (
    <div className={`t2-meter ${kind}`} title={`${label} ${v}/100`}>
      <span className="t2-meter-l">{label}</span>
      <span className="t2-meter-bar"><span style={{ width: `${Math.max(3, v)}%` }} /></span>
      <span className="t2-meter-n">{v}</span>
    </div>
  );
}

function FeedCard({ i, n, onOpen }: { i: Item; n: number; onOpen: () => void }) {
  return (
    <li className={`t2-card lane-${i.lane} ${i.priority ? "priority" : ""}`} style={{ animationDelay: `${Math.min(n, 12) * 25}ms` }}>
      <button className="t2-card-btn" onClick={onOpen}>
        <span className="t2-card-top">
          <span className={`t2-lane lane-${i.lane}`}>{LANE_TEXT[i.lane] ?? i.lane}</span>
          <VerdictChip v={i.verdict} />
          <span className="t2-meta">{i.category} · {ago(i.at)}</span>
          <span className="t2-grow" />
          <Importance level={i.level} />
        </span>
        <span className="t2-card-title">{i.title}</span>
        <span className="t2-card-foot">
          <span className="t2-meta">{i.sources} source{i.sources === 1 ? "" : "s"}{i.x ? " · on X" : ""}{i.desk ? " · News Desk" : ""}</span>
          <span className="t2-open">Open ›</span>
        </span>
      </button>
    </li>
  );
}

// --- publish ---------------------------------------------------------------------------------------------------------------------------

function useDetail(id: number) {
  return useApi(() => request<Detail>(`/terminal/stories/${id}`));
}

function bestHook(d: Detail): string {
  return d.post?.text || d.hooks.find((h) => h.ok)?.text || "";
}

function QuickPublish({ s, xLive, onClose, onDone }: { s: T2Story; xLive: boolean; onClose: () => void; onDone: (text: string) => void }) {
  const { data } = useDetail(s.id);
  const [x, setX] = useState(true);
  const [tg, setTg] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const text = data ? bestHook(data) : "";
  async function go() {
    setBusy(true); setProblem(null);
    const done: string[] = [];
    try {
      if (x) { await post(`/terminal/stories/${s.id}/publish`, { text: text || null }); done.push(xLive ? "X" : "X (dry run)"); }
      if (tg) { await post(`/terminal/stories/${s.id}/telegram`, { text: text || null }); done.push("Telegram"); }
      onDone(`Published to ${done.join(" and ")}.`);
    } catch (e) {
      setProblem((done.length ? `Published to ${done.join(" and ")}. ` : "") + errText(e));
    } finally { setBusy(false); }
  }
  return (
    <Modal title="Publish" onClose={onClose}>
      {!data ? <p className="t2-meta">Preparing the post…</p> : (
        <>
          <div className="t2-preview">{text || "No checked wording yet. Open the story to write one."}</div>
          <div className="t2-targets">
            <label><input type="checkbox" checked={x} onChange={(e) => setX(e.target.checked)} /> X{xLive ? "" : " (dry run)"}</label>
            <label><input type="checkbox" checked={tg} onChange={(e) => setTg(e.target.checked)} disabled={!!data.telegram} /> Telegram{data.telegram ? " (sent)" : ""}</label>
          </div>
          {problem && <p className="t2-problem">{problem}</p>}
          <div className="t2-actions">
            <button className="t2-btn primary" disabled={busy || !text || (!x && !tg)} onClick={() => void go()}>{busy ? "Publishing…" : "Publish now"}</button>
            <button className="t2-btn ghost" onClick={onClose}>Cancel</button>
          </div>
          <p className="t2-fine">Every check still runs: facts, sources, duplicates, limits and stops.</p>
        </>
      )}
    </Modal>
  );
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEscape(onClose);
  return (
    <div className="t2-scrim" onClick={onClose}>
      <div className="t2-modal" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="t2-sheet-head"><h3>{title}</h3><button className="t2-icon" aria-label="Close" onClick={onClose}>✕</button></div>
        {children}
      </div>
    </div>
  );
}

function useEscape(fn: () => void) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") fn(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [fn]);
}

// --- the story panel -------------------------------------------------------------------------------------------------------------------

function Panel({ label, onClose, children, wide }: { label: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEscape(onClose);
  return (
    <div className="t2-scrim" onClick={onClose}>
      <aside className={`t2-panel ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={label} onClick={(e) => e.stopPropagation()}>
        <span className="t2-grab" aria-hidden="true" />
        {children}
      </aside>
    </div>
  );
}

function StoryPanel({ item, onClose, onChanged }: { item: Item; onClose: () => void; onChanged: () => void }) {
  return (
    <Panel label="Story" onClose={onClose}>
      <div className="t2-sheet-head">
        <span className="t2-card-top"><span className={`t2-lane lane-${item.lane}`}>{LANE_TEXT[item.lane] ?? item.lane}</span><VerdictChip v={item.verdict} /><span className="t2-meta">{item.category} · {ago(item.at)}</span></span>
        <button className="t2-icon" aria-label="Close" onClick={onClose}>✕</button>
      </div>
      {item.story ? <StoryBody id={item.story.id} onChanged={onChanged} onClose={onClose} /> : item.desk ? <DeskBody d={item.desk} /> : null}
    </Panel>
  );
}

function DeskBody({ d }: { d: DeskRow }) {
  const sent = [d.desk.x_status, d.desk.tg_status];
  return (
    <div className="t2-body">
      <h2 className="t2-panel-title">{d.headline || d.title}</h2>
      {d.headline && d.headline !== d.title && <p className="t2-context">{d.title}</p>}
      <Section title="Status">
        <p>{d.status === "released" ? "Released by the News Desk." : d.status === "candidate" ? "The News Desk is still deciding." : `News Desk: ${d.status}.`}</p>
        {d.why && <p className="t2-reason">{d.why}</p>}
        <p className="t2-meta">X: {sent[0]}{d.desk.x_note ? ` (${d.desk.x_note})` : ""} · Telegram: {sent[1]}{d.desk.tg_note ? ` (${d.desk.tg_note})` : ""}</p>
      </Section>
      <Section title="Sources">
        <ul className="t2-sources">{d.sources.map((x, i) => <li key={i}>{x.url ? <a href={x.url} target="_blank" rel="noreferrer">{x.name}</a> : x.name}</li>)}</ul>
      </Section>
      <p className="t2-fine">The News Desk publishes this one itself. Its controls are in Settings › News Desk.</p>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <section className="t2-section"><h4>{title}</h4>{children}</section>;
}

function StoryBody({ id, onChanged, onClose }: { id: number; onChanged: () => void; onClose: () => void }) {
  const { data, error, reload } = useDetail(id);
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  if (!data) return <div className="t2-body">{error ? <p className="t2-problem">{error}</p> : <p className="t2-meta">Loading…</p>}</div>;
  const v = verdictOf(data);
  const draft = text ?? bestHook(data);
  const xSent = data.post && ["published", "dry_run", "uncertain", "publishing"].includes(data.post.status);
  const held = data.post?.hold_reason || (["held", "rejected", "dismissed"].includes(data.decision.status) ? data.decision.reason : "");
  async function act(path: string, body: unknown, ok: string, after?: () => void) {
    setBusy(true); setNote(null);
    try { await post(`/terminal/stories/${id}/${path}`, body); setNote({ ok: true, text: ok }); await reload(); onChanged(); after?.(); }
    catch (e) { setNote({ ok: false, text: errText(e) }); }
    finally { setBusy(false); }
  }
  const summary = data.fast_why || data.claim?.original?.title && data.claim.original.title !== data.title && data.claim.original.title || data.sources.find((x) => x.title !== data.title)?.title;
  return (
    <div className="t2-body">
      <h2 className="t2-panel-title">{data.title}</h2>
      {v === "unverified" && <p className="t2-warnline">Unconfirmed claim. It can't be published as fact until a reliable outlet or official source reports it.</p>}
      {data.claim?.status === "disputed" && <p className="t2-warnline">Disputed: {data.claim.disputes.map((d) => d.outlet).join(", ")}.</p>}
      {summary && <p className="t2-context">{summary}</p>}
      {(held || data.desk) && (
        <div className="t2-why">
          {held && <p><strong>{data.decision.status === "rejected" || data.decision.status === "dismissed" ? "Rejected" : "Held"}:</strong> {held}</p>}
          {data.desk && <p><strong>News Desk:</strong> {data.desk.status}{data.desk.why ? `. ${data.desk.why}` : ""}</p>}
        </div>
      )}
      <div className="t2-scores">
        <Meter label="Evidence" value={data.evidence?.score ?? 0} kind="ev" />
        <Meter label="Viral" value={data.viral?.score ?? 0} kind="vi" />
      </div>
      {data.evidence?.reasons?.length ? <p className="t2-fine">{data.evidence.reasons.slice(0, 2).join(" · ")}</p> : null}

      <Section title="Headlines">
        <div className="t2-hooks">{data.hooks.slice(0, 6).map((h, i) => (
          <button key={i} className={`t2-hook ${draft === h.text ? "on" : ""}`} disabled={!h.ok || !!xSent} onClick={() => setText(h.text)} title={h.ok ? "Use this" : h.reason}>
            <span className="t2-hook-k">{HOOK_TEXT[h.kind] ?? h.kind}{h.writer === "model" ? " · AI" : ""}</span>
            <span className="t2-hook-t">{h.text.split("\n")[0]}</span>
            {!h.ok && <span className="t2-hook-no">Blocked: {h.reason}</span>}
          </button>))}</div>
        <button className="t2-link" disabled={busy} onClick={() => void act("hooks", {}, "Three new headlines written.")}>Write new options with AI</button>
      </Section>

      <Section title="Publish">
        {xSent ? (
          <p className="t2-sent">On X{data.post?.status === "dry_run" ? " (dry run)" : ""} {ago(data.post?.published_at)}{data.post?.x_id ? <> · <a href={`https://x.com/i/web/status/${data.post.x_id}`} target="_blank" rel="noreferrer">view</a></> : null}</p>
        ) : null}
        {data.telegram && <p className="t2-sent">On Telegram {ago(data.telegram.at)}</p>}
        <textarea className="t2-text" rows={5} maxLength={280} value={draft} onChange={(e) => setText(e.target.value)} aria-label="Post text" disabled={!!xSent && !!data.telegram} />
        <div className="t2-count">{draft.length}/280</div>
        <div className="t2-actions">
          <button className="t2-btn primary" disabled={busy || v === "unverified" || !!xSent || !draft}
            onClick={() => window.confirm("Publish this to X now?") && void act("publish", { text: draft }, "Published to X.")}>Publish to X</button>
          <button className="t2-btn" disabled={busy || v === "unverified" || !!data.telegram || !draft}
            onClick={() => window.confirm("Send this to the Telegram channel now?") && void act("telegram", { text: draft }, "Sent to Telegram.")}>Publish to Telegram</button>
          <button className="t2-btn ghost" disabled={busy || !!xSent} onClick={() => void act("draft", { text: draft }, "Saved and checked.")}>Save</button>
          {!xSent && data.lane !== "rejected" && <button className="t2-btn ghost" disabled={busy} onClick={() => void act("reject", { reason: "Skipped from the Terminal" }, "Skipped.", onClose)}>Skip</button>}
        </div>
        {note && <p className={note.ok ? "t2-okline" : "t2-problem"}>{note.text}</p>}
      </Section>

      <Section title="Timeline">
        <ol className="t2-timeline">{data.timeline.slice(-12).map((t, i) => (
          <li key={i}>
            <span className="t2-tl-time">{t.at ? new Date(ms(t.at)).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}</span>
            <span className={`t2-src ${t.source === "x_accounts" ? "x" : t.official ? "official" : t.reliable ? "reliable" : ""}`}>{t.source === "x_accounts" ? "X" : t.official ? "Official" : t.outlet}</span>
            {t.url ? <a href={t.url} target="_blank" rel="noreferrer">{t.title}</a> : <span>{t.title}</span>}
          </li>))}</ol>
      </Section>

      <Section title="Sources">
        <ul className="t2-sources">{data.sources.map((x, i) => (
          <li key={i}>
            <span className={`t2-src ${x.source === "x_accounts" ? "x" : x.official ? "official" : x.reliable ? "reliable" : ""}`}>{x.source === "x_accounts" ? "X post" : x.official ? "Official" : x.reliable ? "Reliable" : "Signal"}</span>
            {x.url ? <a href={x.url} target="_blank" rel="noreferrer">{x.outlet}</a> : x.outlet}
            <span className="t2-meta"> {x.title}</span>
          </li>))}</ul>
      </Section>
    </div>
  );
}

// --- settings --------------------------------------------------------------------------------------------------------------------------

const TABS = [{ key: "general", label: "General" }, { key: "newsdesk", label: "News Desk" }, { key: "trending", label: "Trending" },
  { key: "x", label: "X Intelligence" }, { key: "monitor", label: "World Monitor" }, { key: "validation", label: "Validation" }, { key: "diagnostics", label: "Diagnostics" }] as const;
type TabKey = (typeof TABS)[number]["key"];

function SettingsDrawer({ data, onClose, run }: { data: T2Overview | null; onClose: () => void; run: (fn: () => Promise<unknown>, ok: string) => Promise<void> }) {
  const [tab, setTab] = useState<TabKey>("general");
  const c = data?.controls;
  async function stop(which: "newsdesk" | "trending", on: boolean) {
    const what = which === "newsdesk" ? "the News Desk emergency stop (stops ALL X posting)" : "the Trending stop";
    if (!window.confirm(`${on ? "Turn on" : "Lift"} ${what}?`)) return;
    await run(() => post("/terminal/stop", { which, on }), on ? "Stop is on." : "Stop lifted.");
  }
  return (
    <Panel label="Settings" onClose={onClose} wide>
      <div className="t2-sheet-head"><h3>Settings</h3><button className="t2-icon" aria-label="Close" onClick={onClose}>✕</button></div>
      <div className="t2-tabs" role="tablist">{TABS.map((t) => (
        <button key={t.key} role="tab" aria-selected={tab === t.key} className={`t2-chip ${tab === t.key ? "on" : ""}`} onClick={() => setTab(t.key)}>{t.label}</button>))}</div>
      <div className="t2-settings">
        {tab === "general" && c && (
          <div className="t2-body">
            <Section title="Publishing">
              <Row label="X posting" value={c.x_live ? "Live" : "Dry run (recorded, not sent)"} />
              <Row label="News Desk" value={c.newsdesk_stop ? "Emergency stop on" : data?.automation.on ? "Automatic" : "Paused"} />
              <Row label="Trending" value={c.trending_stop ? "Stopped" : data?.automation.trending_auto ? "Automatic" : "Approval only"} />
              <p className="t2-fine">Live or dry run is changed only in Settings › News Desk.</p>
            </Section>
            <Section title="Emergency stops">
              <div className="t2-actions">
                <button className={`t2-btn ${c.newsdesk_stop ? "" : "danger"}`} onClick={() => void stop("newsdesk", !c.newsdesk_stop)}>{c.newsdesk_stop ? "Lift News Desk stop" : "Stop all X posting"}</button>
                <button className={`t2-btn ${c.trending_stop ? "" : "danger"}`} onClick={() => void stop("trending", !c.trending_stop)}>{c.trending_stop ? "Lift Trending stop" : "Stop Trending only"}</button>
              </div>
            </Section>
            <Section title="Safeguards (always on)">
              <ul className="t2-plain">
                <li>Unverified claims never publish automatically.</li>
                <li>Every post passes the facts, sources and duplicate checks.</li>
                <li>Daily caps, spacing and X spending limits apply.</li>
              </ul>
            </Section>
          </div>
        )}
        <Suspense fallback={<p className="t2-meta">Loading…</p>}>
          {tab === "newsdesk" && <NewsDesk />}
          {tab === "trending" && <Trending />}
          {tab === "x" && <XIntel />}
          {tab === "monitor" && <WorldMonitor />}
          {tab === "diagnostics" && <Classic />}
          {tab === "validation" && <Validation />}
        </Suspense>
      </div>
    </Panel>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return <div className="t2-row"><span>{label}</span><strong>{value}</strong></div>;
}
