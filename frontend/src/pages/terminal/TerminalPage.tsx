import { useEffect, useMemo, useRef, useState } from "react";
import { request } from "../../api/client";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, Loading, PageHeader, relativeTime, type Tone } from "../../components/ui";
import "./terminal.css";

/** Admin › Price Chat Terminal (owner, 2026-11-18): every story both desks see, with viral potential and evidence kept apart, the rumour
 *  watch, the disruptions radar, the timeline and the reasons, and one-click edit / publish / reject. The server decides what is allowed:
 *  publishing goes through Trending's gates, stops and the one-story rule. */

type Ev = { score: number; level: string; rumor: boolean; reasons: string[]; outlets: number; official: boolean; mainstream: boolean };
type Vi = { score: number; parts: Record<string, number>; drivers: string[]; measured: string[]; words?: string[] };
type Link = { outlet: string; title: string; url: string; source: string; reliable: boolean; official: boolean; at: string | null };
type Post = { id: number; status: string; text: string; hold_reason: string | null; note: string | null; x_id: string | null; url: string | null; published_at: string | null };
export type Story = { id: number; title: string; category: string; topic_status: string; lane: string; trend_score: number; peak_score: number; viral: Vi | null;
  evidence: Ev | null; rumor: boolean; ahead_of_mainstream: boolean; fast: boolean; fast_why: string | null; breaking_ok: boolean; first_report: string | null;
  detected: string | null; last_update: string | null; age_hours: number | null; stale: string | null; why: string | null;
  incident: { label?: string; major?: boolean; verified?: boolean; evidence?: string } | null; sources: Link[]; decision: { status: string; reason: string }; post: Post | null };
type Hook = { kind: string; text: string; writer: string; ok: boolean; reason: string };
type Detail = Story & { timeline: { at: string | null; seen: string | null; source: string; outlet: string; title: string; url: string; reliable: boolean; official: boolean }[];
  posts: Post[]; hooks: Hook[]; ledger: Record<string, unknown> | null };
type DeskEvent = { id: number; title: string; headline: string; status: string; x_status: string; topic: string; why: string; first_seen: string | null; released_at: string | null;
  sources: { name: string; url: string }[] };
type LedgerRow = { topic_id: number; title: string; kind: string; first_report: string | null; status: string; reason: string; via: string | null;
  latency_min: { detect: number | null; verify: number | null; publish: number | null } };
type Source = { key: string; name: string; status: string; problem: string | null; last_scan: string | null; items: number | null; note: string };
type Controls = { newsdesk_mode: string; newsdesk_stop: boolean; trending_mode: string; trending_live: boolean; trending_stop: boolean; x_live: boolean; blocked: string | null;
  fast_lane: { evidence_min: number; max_age_min: number; extra_per_day: number; gap_min: number } };
type Overview = { now: string; counts: Record<string, number>; stories: Story[]; total: number; controls: Controls; desk_events: DeskEvent[];
  published: { lane: string; id: number; text: string; status: string; at: string | null; x_id: string | null }[];
  incidents: { topic_id: number; title: string; label: string; verified: boolean; status: string; reason: string; event_time: string }[];
  ledger: { events: number; detect: Stat; verify: Stat; publish: Stat } | null; ledger_rows: LedgerRow[]; sources: Source[]; last_scan: string | null;
  alerts: { topic_id: number; kind: string; title: string; at: string; sent: boolean }[] };
type Stat = { median: number; max: number; n: number } | null;

const LANES: { key: string; label: string; tone: Tone; about: string }[] = [
  { key: "breaking", label: "Breaking", tone: "bad", about: "Verified, fresh, high-impact: the fast lane" },
  { key: "disruption", label: "Disruptions", tone: "warn", about: "Outages, groundings, disasters, official alerts" },
  { key: "emerging", label: "Emerging", tone: "info", about: "Growing interest or spreading before the big outlets" },
  { key: "rumor", label: "Rumor watch", tone: "warn", about: "Unverified or hedged: human review only, never automatic" },
  { key: "watch", label: "Watch", tone: "neutral", about: "Everything else on the radar" },
  { key: "published", label: "Published", tone: "good", about: "Sent (or recorded in dry run)" },
  { key: "rejected", label: "Rejected", tone: "neutral", about: "Dismissed by you or refused" },
];
const LANE = Object.fromEntries(LANES.map((l) => [l.key, l]));
const CATS = ["", "crypto", "stocks", "markets", "geopolitics", "technology", "world"];
const utc = (iso: string | null | undefined) => (iso ? `${iso.slice(5, 10)} ${iso.slice(11, 16)}Z` : "-");
const mins = (n: number | null | undefined) => (n === null || n === undefined ? "-" : n >= 90 ? `${(n / 60).toFixed(1)} h` : `${n} min`);
const tone = (n: number): Tone => (n >= 70 ? "good" : n >= 45 ? "warn" : "neutral");

function Meter({ label, value, kind }: { label: string; value: number; kind: "viral" | "evidence" }) {
  return (
    <div className={`pt-meter pt-${kind}`} title={`${label} ${value.toFixed(0)}/100`}>
      <span className="pt-meter-label">{label}</span>
      <span className="pt-meter-bar"><span style={{ width: `${Math.max(2, Math.min(100, value))}%` }} /></span>
      <span className="pt-meter-num">{value.toFixed(0)}</span>
    </div>
  );
}

export function TerminalPage() {
  const [q, setQ] = useState("");
  const [lane, setLane] = useState("");
  const [cat, setCat] = useState("");
  const [minViral, setMinViral] = useState(0);
  const [minEv, setMinEv] = useState(0);
  const [hours, setHours] = useState(24);
  const [open, setOpen] = useState<number | null>(null);
  const qs = useMemo(() => new URLSearchParams({ q, lane, category: cat, min_viral: String(minViral), min_evidence: String(minEv), hours: String(hours) }).toString(), [q, lane, cat, minViral, minEv, hours]);
  const { data, error, loading, reload } = useApi(() => request<Overview>(`/terminal?${qs}`), 30_000);
  const [msg, setMsg] = useState<string | null>(null);
  const first = useRef(true);
  useEffect(() => {  // a new filter asks the server now, not at the next 30-second refresh
    if (first.current) { first.current = false; return; }
    const t = window.setTimeout(() => void reload(), q ? 250 : 0);
    return () => window.clearTimeout(t);
  }, [qs]);  // eslint-disable-line react-hooks/exhaustive-deps

  async function stop(which: "trending" | "newsdesk", on: boolean) {
    if (on && !window.confirm(`Turn on the ${which === "newsdesk" ? "News Desk emergency stop (stops ALL X posting)" : "Trending stop"}?`)) return;
    try { await request("/terminal/stop", { method: "POST", body: JSON.stringify({ which, on }) }); setMsg(null); reload(); } catch (e) { setMsg(String((e as Error).message)); }
  }

  if (loading && !data) return <Loading />;
  const c = data?.controls;
  return (
    <div className="pt">
      <PageHeader title="PRICE CHAT TERMINAL" subtitle="Live breaking news, emerging narratives, rumours and disruptions from every source both desks read. Viral potential and evidence are scored separately." />
      <ApiErrorBanner error={error} />
      {msg && <div className="banner banner-error" role="alert">{msg}</div>}
      {data && c && (
        <>
          <div className="pt-status">
            <Badge tone={c.x_live ? "good" : "neutral"}>{c.x_live ? "X LIVE" : "X dry run"}</Badge>
            <Badge tone={c.newsdesk_stop ? "bad" : "good"}>News Desk {c.newsdesk_stop ? "STOPPED" : c.newsdesk_mode}</Badge>
            <Badge tone={c.trending_stop ? "bad" : "good"}>Trending {c.trending_stop ? "STOPPED" : c.trending_mode}</Badge>
            {c.blocked && <Badge tone="bad">{c.blocked}</Badge>}
            <span className="small muted">scan {relativeTime(data.last_scan)} · refreshes every 30 s</span>
            <span className="pt-grow" />
            <button className={c.trending_stop ? "btn btn-sm" : "btn btn-sm btn-danger"} onClick={() => stop("trending", !c.trending_stop)}>{c.trending_stop ? "Resume Trending" : "Stop Trending"}</button>
            <button className={c.newsdesk_stop ? "btn btn-sm" : "btn btn-sm btn-danger"} onClick={() => stop("newsdesk", !c.newsdesk_stop)}>{c.newsdesk_stop ? "Lift emergency stop" : "Emergency stop"}</button>
          </div>
          <div className="pt-lanes">
            {LANES.map((l) => (
              <button key={l.key} className={`pt-lane ${lane === l.key ? "on" : ""}`} onClick={() => setLane(lane === l.key ? "" : l.key)} title={l.about}>
                <span className="pt-lane-n">{data.counts[l.key] ?? 0}</span><span>{l.label}</span>
              </button>
            ))}
          </div>
          <div className="pt-filters">
            <input className="input" placeholder="Search headlines and outlets" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search" />
            <select className="input" value={cat} onChange={(e) => setCat(e.target.value)} aria-label="Category">{CATS.map((x) => <option key={x} value={x}>{x || "All categories"}</option>)}</select>
            <label className="small">Viral ≥ <input className="input pt-num" type="number" min={0} max={100} value={minViral} onChange={(e) => setMinViral(Number(e.target.value) || 0)} /></label>
            <label className="small">Evidence ≥ <input className="input pt-num" type="number" min={0} max={100} value={minEv} onChange={(e) => setMinEv(Number(e.target.value) || 0)} /></label>
            <select className="input" value={hours} onChange={(e) => setHours(Number(e.target.value))} aria-label="Window">{[6, 24, 48, 72].map((h) => <option key={h} value={h}>Last {h} h</option>)}</select>
          </div>
          <div className="pt-grid">
            <div className="pt-main">
              <Card title={`${lane ? LANE[lane].label : "Live feed"} (${data.total})`}>
                {data.stories.length === 0 ? <Empty>No story matches.</Empty> : data.stories.map((s) => (
                  <StoryRow key={s.id} s={s} open={open === s.id} onToggle={() => setOpen(open === s.id ? null : s.id)} onChanged={reload} />
                ))}
              </Card>
            </div>
            <aside className="pt-side">
              <Card title="Disruptions radar">
                {data.incidents.length === 0 ? <Empty>No major outage, grounding or disaster in 72 h.</Empty> : (
                  <ul className="pt-list">{data.incidents.slice(0, 8).map((i) => (
                    <li key={i.topic_id}><Badge tone={i.verified ? "good" : "warn"}>{i.verified ? "verified" : "unverified"}</Badge> {i.title}
                      <div className="small muted">{i.label} · {utc(i.event_time)} · {i.status}: {i.reason}</div></li>))}</ul>)}
              </Card>
              <Card title="Published (24 h)">
                {data.published.length === 0 ? <Empty>Nothing sent in 24 h.</Empty> : (
                  <ul className="pt-list">{data.published.slice(0, 10).map((p) => (
                    <li key={`${p.lane}${p.id}`}><Badge tone={p.lane === "fast" ? "bad" : "info"}>{p.lane}</Badge> <span className="small muted">{utc(p.at)} · {p.status}</span>
                      <div className="small">{p.text.split("\n")[0]}</div>{p.x_id && <a className="link small" href={`https://x.com/i/web/status/${p.x_id}`} target="_blank" rel="noreferrer">on X</a>}</li>))}</ul>)}
              </Card>
              <Card title="News Desk decisions (24 h)">
                {data.desk_events.length === 0 ? <Empty>No News Desk events in 24 h.</Empty> : (
                  <ul className="pt-list">{data.desk_events.slice(0, 12).map((e) => (
                    <li key={e.id}><Badge tone={e.status === "released" ? "good" : e.status === "candidate" ? "info" : "neutral"}>{e.status}</Badge> {e.headline || e.title}
                      <div className="small muted">{e.why}</div></li>))}</ul>)}
              </Card>
              <Card title="Detection and publication latency">
                {data.ledger && <p className="small">Median from first report: detected {mins(data.ledger.detect?.median)} · verified {mins(data.ledger.verify?.median)} · published {mins(data.ledger.publish?.median)} ({data.ledger.events} events, 7 days)</p>}
                <ul className="pt-list">{data.ledger_rows.slice(0, 8).map((r) => (
                  <li key={r.topic_id}>{r.title}<div className="small muted">report {utc(r.first_report)} · detect +{mins(r.latency_min.detect)} · verify {mins(r.latency_min.verify)} · publish {mins(r.latency_min.publish)} · {r.status}{r.via ? ` via ${r.via}` : ""}</div></li>))}</ul>
              </Card>
              <Card title="Sources and social signals">
                <ul className="pt-list">{data.sources.map((s) => (
                  <li key={s.key}><Badge tone={s.status === "ok" ? "good" : s.status === "error" ? "bad" : s.status === "off" || s.status === "unavailable" ? "warn" : "neutral"}>{s.status}</Badge> <strong>{s.name}</strong>
                    <div className="small muted">{s.problem ? `${s.problem} · ` : ""}{s.note}</div></li>))}</ul>
              </Card>
              {data.alerts.length > 0 && <Card title="Alerts sent to you">
                <ul className="pt-list">{data.alerts.map((a) => <li key={`${a.topic_id}${a.kind}`}><Badge tone={a.kind === "fast" ? "bad" : "warn"}>{a.kind}</Badge> {a.title}<div className="small muted">{utc(a.at)}{a.sent ? "" : " · not delivered"}</div></li>)}</ul>
              </Card>}
            </aside>
          </div>
          <p className="small muted">Fast lane: verified (evidence ≥ {c.fast_lane.evidence_min}, never a rumour), first reported ≤ {c.fast_lane.max_age_min} min ago, high-impact. It skips the momentum line and routine slots, may add {c.fast_lane.extra_per_day} posts a day, {c.fast_lane.gap_min} min apart. Every gate, the shared cap and both stops still apply.</p>
        </>
      )}
    </div>
  );
}

function StoryRow({ s, open, onToggle, onChanged }: { s: Story; open: boolean; onToggle: () => void; onChanged: () => void }) {
  const l = LANE[s.lane] ?? LANE.watch;
  const ev = s.evidence?.score ?? 0;
  const vi = s.viral?.score ?? 0;
  return (
    <div className={`pt-story ${open ? "open" : ""}`}>
      <button className="pt-story-head" onClick={onToggle} aria-expanded={open}>
        <span className="pt-story-top">
          <Badge tone={l.tone}>{l.label}</Badge>
          {s.fast && <Badge tone="bad">FAST LANE</Badge>}
          {s.rumor && <Badge tone="warn">unverified</Badge>}
          {s.ahead_of_mainstream && <Badge tone="info">ahead of mainstream</Badge>}
          <span className="small muted">{s.category} · first report {utc(s.first_report)} · {s.sources.length} source{s.sources.length === 1 ? "" : "s"}</span>
        </span>
        <span className="pt-story-title">{s.title}</span>
        <span className="pt-meters"><Meter label="Viral" value={vi} kind="viral" /><Meter label="Evidence" value={ev} kind="evidence" /></span>
        <span className="small muted pt-decision">{s.decision.status}{s.decision.reason ? `: ${s.decision.reason}` : ""}</span>
      </button>
      {open && <StoryDetail id={s.id} onChanged={onChanged} />}
    </div>
  );
}

function StoryDetail({ id, onChanged }: { id: number; onChanged: () => void }) {
  const { data, error, loading, reload } = useApi(() => request<Detail>(`/terminal/stories/${id}`));
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  async function act(path: string, body: unknown, done: string) {
    setBusy(true); setNote(null);
    try { await request(`/terminal/stories/${id}/${path}`, { method: "POST", body: JSON.stringify(body) }); setNote({ ok: true, text: done }); reload(); onChanged(); }
    catch (e) { setNote({ ok: false, text: String((e as Error).message) }); }
    finally { setBusy(false); }
  }

  if (loading && !data) return <Loading />;
  if (!data) return <ApiErrorBanner error={error} />;
  const draft = text ?? data.post?.text ?? "";
  const sent = data.post && ["published", "dry_run", "uncertain", "publishing"].includes(data.post.status);
  return (
    <div className="pt-detail">
      <div className="pt-cols">
        <div>
          <h4>Why it was detected</h4>
          <ul className="small pt-why">
            {data.fast_why && <li><strong>Fast lane:</strong> {data.fast_why}</li>}
            {data.why && <li><strong>Trend score {data.trend_score.toFixed(0)}:</strong> {data.why}</li>}
            {data.viral && <li><strong>Viral {data.viral.score.toFixed(0)}:</strong> {data.viral.drivers.join(", ") || "no strong driver"}{data.viral.measured.length ? ` · measured: ${data.viral.measured.join("; ")}` : " · no social measurement"}</li>}
            {data.evidence && <li><strong>Evidence {data.evidence.score.toFixed(0)} ({data.evidence.level}):</strong> {data.evidence.reasons.join("; ")}</li>}
            <li><strong>Decision:</strong> {data.decision.status}{data.decision.reason ? ` (${data.decision.reason})` : ""}</li>
          </ul>
          {data.viral && <div className="pt-parts">{Object.entries(data.viral.parts).map(([k, v]) => <span key={k} className="pt-chip">{k} {v}</span>)}</div>}
          <h4>Timeline</h4>
          <ol className="pt-timeline">{data.timeline.map((t, i) => (
            <li key={i}><span className="small muted">{utc(t.at)}</span> <Badge tone={t.official ? "good" : t.reliable ? "info" : "neutral"}>{t.official ? "official" : t.reliable ? t.outlet : t.source}</Badge>{" "}
              {t.url ? <a className="link" href={t.url} target="_blank" rel="noreferrer">{t.title}</a> : t.title}
              {t.seen && t.seen !== t.at && <span className="small muted"> · seen {utc(t.seen)}</span>}</li>))}</ol>
        </div>
        <div>
          <h4>Hooks</h4>
          {data.rumor && <p className="small warn-text">Unverified: these can't be published as fact. Wait for a reliable outlet or an official source.</p>}
          <ul className="pt-hooks">{data.hooks.map((h, i) => (
            <li key={i} className={h.ok ? "" : "bad"}>
              <span className="small muted">{h.kind} · {h.writer}</span>
              <div className="pt-hook-text">{h.text}</div>
              {h.ok ? <button className="btn btn-sm" onClick={() => setText(h.text)} disabled={!!sent}>Use</button> : <span className="small bad-text">Blocked: {h.reason}</span>}
            </li>))}</ul>
          <button className="btn btn-sm" disabled={busy} onClick={() => act("hooks", {}, "The model wrote three more hooks.")}>Write hooks with the model</button>
          <h4>Post</h4>
          {data.post?.hold_reason && <p className="small warn-text">Held: {data.post.hold_reason}</p>}
          {sent ? (
            <p className="small">Sent ({data.post?.status}) {utc(data.post?.published_at)} {data.post?.x_id && <a className="link" href={`https://x.com/i/web/status/${data.post.x_id}`} target="_blank" rel="noreferrer">on X</a>}<br />{data.post?.text}</p>
          ) : (
            <>
              <textarea className="input pt-text" rows={5} maxLength={280} value={draft} placeholder="Write or pick a hook" onChange={(e) => setText(e.target.value)} />
              <div className="small muted">{draft.length}/280</div>
              <div className="pt-actions">
                <button className="btn btn-sm" disabled={busy} onClick={() => act("draft", { text: draft || null }, "Saved and checked.")}>Save draft</button>
                <button className="btn btn-sm btn-primary" disabled={busy || data.rumor} onClick={() => window.confirm("Publish this to X now (dry run if X isn't live)?") && act("publish", { text: draft || null }, "Published.")}>Publish now</button>
                <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => act("reject", { reason: "" }, "Rejected.")}>Reject</button>
              </div>
            </>
          )}
          {note && <p className={`small ${note.ok ? "good-text" : "bad-text"}`}>{note.text}</p>}
          <h4>Original sources</h4>
          <ul className="small pt-list">{data.sources.map((x, i) => (
            <li key={i}>{x.url ? <a className="link" href={x.url} target="_blank" rel="noreferrer">{x.outlet}</a> : x.outlet} {x.official ? <Badge tone="good">official</Badge> : x.reliable ? <Badge tone="info">reliable</Badge> : <Badge tone="neutral">signal</Badge>} {x.title}</li>))}</ul>
        </div>
      </div>
    </div>
  );
}

export const terminalTone = tone;
