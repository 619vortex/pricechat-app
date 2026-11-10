import { useState } from "react";
import { request } from "../../api/client";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, Loading, Stat, relativeTime, type Tone } from "../../components/ui";
import { TelegramLane } from "./TelegramLane";

/** Admin › X › News Desk: Price Chat's automatic news account. The server decides everything; this page shows it and holds the switches. */

type Source = { name: string; url: string; at: string | null };
type Check = { check: string; passed: boolean; detail: string };
export type DeskPost = { id: number; kind: string; category: string; priority: string; status: string; text: string; edited: boolean; score: number; sources: Source[]; checks: Check[];
  error: string | null; url: string | null; metrics: Record<string, number | string>; created_at: string | null; published_at: string | null; expires_at: string | null; facts: Record<string, unknown> };
type Feed = { name: string; url: string; tier: number; category: string | null };
type Watch = { symbol: string; name: string; group: string; category: string };
export type DeskSettings = { automation: boolean; mode: "dry_run" | "live"; kill_switch: boolean; paused_until: string | null; pause_reason: string | null; target_per_day: number; max_breaking_per_day: number;
  breaking_gap_minutes: number; sensitivity: string; categories: Record<string, boolean>; feeds: Feed[]; watchlist: Watch[] };
export type Desk = { settings: DeskSettings; blocked: string | null; last_run: (Record<string, unknown> & { at?: string }) | null; feed_problems: Record<string, string>;
  x: { configured: boolean; can_write: boolean; status: string; account: string | null; blocked_until: string | null }; writer: "model" | "headlines";
  today: { published: number; breaking: number; target: number }; cost: { per_post_usd: number; posted_30d: number; estimate_30d_usd: number; budget: { daily_usd: number; monthly_usd: number }; x_spend_today_usd: number; x_spend_month_usd: number };
  queue: DeskPost[]; published: DeskPost[]; problems: DeskPost[];
  feed_health?: { at?: string; feeds?: Record<string, { name: string; tier: number; items: number; new?: number; newest: string | null; problem: string | null }> };
  desk?: { schedule?: Schedule }; x_lane?: XLane };
export type SlotRow = { at: string; dubai: string; state: "filled" | "open" | "missed" | "before"; event: number | null; kind: string | null; headline: string | null; x: string | null; telegram: string | null;
  telegram_message_id: number | null };
export type Schedule = { schedule: string; slot_hours_dubai: number[]; next_slot: string; next_slot_dubai: string; slots: SlotRow[];
  last_telegram: { event: number; at: string | null; message_id: number | null } | null; last_x: { event: number; at: string | null; status: string } | null;
  failures: { event: number; dubai: string; x: string | null; telegram: string | null }[] };

export type XLaneEvent = { id: number; headline: string; released_at: string | null; telegram: string | null; telegram_message_id: number | null; x: string | null; note: string; post_id: number | null;
  x_id: string | null; url: string | null; post_status: string | null };
export type XLane = { mode: string; live: boolean; paused: string | null; last_ok: { at: string | null; x_id: string; url: string; post_id: number } | null;
  last_error: { at: string; post_id: number; event_id: number | null; outcome: string; stage: string; message: string } | null; events: XLaneEvent[] };
type TestPreview = { event_id: number; headline: string; text: string; ok: boolean; reason: string; x_status: string | null; image: string };

const SLOT_TONE: Record<string, Tone> = { filled: "good", open: "info", missed: "bad" };

/** The six Dubai slots: what filled each, each platform's delivery, and the feeds' health (2026-11-09). */
export function SchedulePanel({ schedule, feeds }: { schedule: Schedule; feeds?: Desk["feed_health"] }) {
  const rows = Object.entries(feeds?.feeds ?? {});
  return (
    <Card title={`Schedule: next post ${schedule.next_slot_dubai} Dubai`}>
      <p className="small">Six posts a day at {schedule.slot_hours_dubai.map((h) => `${String(h).padStart(2, "0")}:00`).join(", ")} Dubai. Last Telegram post:{" "}
        {schedule.last_telegram?.message_id ? <a href={`https://t.me/Pricechatupdates/${schedule.last_telegram.message_id}`} target="_blank" rel="noreferrer">#{schedule.last_telegram.message_id}</a> : "none"}
        {schedule.last_telegram?.at ? ` (${new Date(schedule.last_telegram.at).toLocaleString()})` : ""}. Last X: {schedule.last_x ? `${schedule.last_x.status}${schedule.last_x.at ? ` (${new Date(schedule.last_x.at).toLocaleString()})` : ""}` : "none"}.</p>
      {schedule.failures.length > 0 && <p className="small banner banner-warn">Not delivered: {schedule.failures.map((f) => `${f.dubai} (Telegram ${f.telegram ?? "-"}, X ${f.x ?? "-"})`).join(", ")}</p>}
      <table className="table small"><thead><tr><th>Dubai</th><th>Slot</th><th>Post</th><th>Telegram</th><th>X</th></tr></thead>
        <tbody>{schedule.slots.slice().reverse().map((r) => (
          <tr key={r.at}><td>{r.dubai}</td><td><Badge tone={SLOT_TONE[r.state] ?? "neutral"}>{r.state}</Badge></td><td>{r.kind === "recap" ? "Market check" : r.headline ?? "-"}</td>
            <td>{r.telegram ?? "-"}{r.telegram_message_id ? ` #${r.telegram_message_id}` : ""}</td><td>{r.x ?? "-"}</td></tr>))}</tbody></table>
      {rows.length > 0 && <details><summary>Feed health ({rows.filter(([, f]) => f.problem).length} with problems of {rows.length})</summary>
        <table className="table small"><thead><tr><th>Feed</th><th>Tier</th><th>Items</th><th>New</th><th>Newest</th><th>Problem</th></tr></thead>
          <tbody>{rows.map(([u, f]) => <tr key={u}><td>{f.name}</td><td>{f.tier}</td><td>{f.items}</td><td>{f.new ?? 0}</td><td>{f.newest ? new Date(f.newest).toLocaleString() : "-"}</td><td>{f.problem ?? ""}</td></tr>)}</tbody></table></details>}
    </Card>
  );
}

const body = (method: string, value: unknown): RequestInit => ({ method, body: JSON.stringify(value), headers: { "Content-Type": "application/json" } });
const CATEGORY_NAME: Record<string, string> = { crypto: "Crypto", stocks: "Stocks", commodities: "Commodities", politics: "Politics", economics: "Economics", viral: "Viral market stories" };
const STATUS_TONE: Record<string, Tone> = { published: "good", dry_run: "info", queued: "neutral", failed: "bad", uncertain: "warn" };
const STATUS_TEXT: Record<string, string> = { dry_run: "Dry run: not sent", uncertain: "Check X", published: "Published", failed: "Failed", queued: "Queued" };

/** X publishing at a glance (2026-11-10): live or not, the last post X confirmed, the last error in X's words, each event's X outcome, and the owner's one test post. */
export function XPublishing({ lane, busy, act }: { lane: XLane; busy: boolean; act: (fn: () => Promise<unknown>) => Promise<void> }) {
  const [preview, setPreview] = useState<TestPreview | null>(null);
  const [sent, setSent] = useState<DeskPost | null>(null);
  return (
    <Card title="X publishing">
      <p className="small"><Badge tone={lane.paused ? "warn" : lane.live ? "good" : "info"}>{lane.paused ? "Paused" : lane.live ? "Live" : "Dry run"}</Badge>{" "}
        Last post X confirmed: {lane.last_ok ? <a className="link" href={lane.last_ok.url} target="_blank" rel="noopener noreferrer">{lane.last_ok.at ? new Date(lane.last_ok.at).toLocaleString() : lane.last_ok.x_id}</a> : "none yet"}.</p>
      {lane.paused && <p className="small banner banner-warn" role="status">{lane.paused}</p>}
      {lane.last_error && <p className="small banner banner-warn" role="alert">Last X error ({new Date(lane.last_error.at).toLocaleString()}, post {lane.last_error.post_id}, {lane.last_error.outcome}): {lane.last_error.message}</p>}
      {lane.events.length > 0 && <table className="table small"><thead><tr><th>Event</th><th>Telegram</th><th>X</th><th>Note</th></tr></thead>
        <tbody>{lane.events.map((e) => <tr key={e.id}><td>{e.headline}</td><td>{e.telegram ?? "-"}{e.telegram_message_id ? ` #${e.telegram_message_id}` : ""}</td>
          <td>{e.url ? <a className="link" href={e.url} target="_blank" rel="noopener noreferrer">{e.x ?? "published"}</a> : e.x ?? "-"}</td><td>{e.note}</td></tr>)}</tbody></table>}
      <div className="row" style={{ gap: 6, marginTop: 8 }}>
        <button className="btn btn-sm" disabled={busy} onClick={() => void act(async () => { setSent(null); setPreview(await request<TestPreview>("/x/newsdesk/test-post")); })}>Preview a test post</button>
      </div>
      {preview && <div style={{ marginTop: 8 }}>
        <p className="small muted">Test post for event {preview.event_id} ({preview.headline}). Nothing is sent until you confirm.</p>
        <p style={{ whiteSpace: "pre-wrap" }}>{preview.text}</p>
        {preview.image && <img src={preview.image} alt="The picture the test post would carry" style={{ maxWidth: 360, width: "100%" }} />}
        {!preview.ok && <p className="small" role="alert">The text fails the checks: {preview.reason}</p>}
        <button className="btn btn-sm btn-primary" disabled={busy || !preview.ok || preview.x_status === "published" || preview.x_status === "uncertain"}
                onClick={() => { if (window.confirm("Send this one test post to X now, with its picture? It goes out even in dry run.")) void act(async () => { setSent(await request<DeskPost>("/x/newsdesk/test-post", body("POST", { event_id: preview.event_id, confirm: true }))); setPreview(null); }); }}>Send this test post to X</button>
      </div>}
      {sent && <p className="small" role="status">Test post: {STATUS_TEXT[sent.status] ?? sent.status}{sent.url ? <> (<a className="link" href={sent.url} target="_blank" rel="noopener noreferrer">open on X</a>)</> : null}{sent.error ? `: ${sent.error}` : ""}</p>}
    </Card>
  );
}

export function NewsDesk() {
  const { data, failure, reload } = useApi(() => request<Desk>("/x/newsdesk"), 30000);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (failure) return <ApiErrorBanner error={failure} />;
  if (!data) return <Loading />;
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(null);
    try { await fn(); await reload(); } catch (e) { setError(e); } finally { setBusy(false); }
  };
  const save = (change: Partial<DeskSettings> & { resume?: boolean }) => act(() => request("/x/newsdesk/settings", body("PUT", change)));
  const s = data.settings;
  const live = s.mode === "live";
  return (
    <div className="stack">
      {error ? <ApiErrorBanner error={error} /> : null}
      <Card title="News Desk">
        <p className="muted">Reads news and prices around the clock, verifies them, and posts to X without you. Runs on the server, so this page can be closed.</p>
        <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <Badge tone={s.kill_switch ? "bad" : data.blocked ? "warn" : live ? "good" : "info"}>{s.kill_switch ? "Emergency stop" : data.blocked ? "Paused" : live ? "LIVE: posting to X" : "Dry run: nothing goes to X"}</Badge>
          <button className="btn btn-sm" disabled={busy} onClick={() => void save({ automation: !s.automation })}>{s.automation ? "Turn automation off" : "Turn automation on"}</button>
          {live ? <button className="btn btn-sm" disabled={busy} onClick={() => void save({ mode: "dry_run" })}>Back to dry run</button>
            : <button className="btn btn-sm btn-primary" disabled={busy || !data.x.can_write} title={data.x.can_write ? "" : "X isn't connected with posting access"}
                      onClick={() => { if (window.confirm(`Go live? From now on the desk posts to @${data.x.account ?? "your account"} on its own, up to ${s.target_per_day + s.max_breaking_per_day} times a day, at about $${data.cost.per_post_usd} a post.`)) void save({ mode: "live" }); }}>Go live</button>}
          {s.kill_switch ? <button className="btn btn-sm" disabled={busy} onClick={() => void save({ kill_switch: false })}>Release emergency stop</button>
            : <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => void save({ kill_switch: true })}>Emergency stop</button>}
          {s.pause_reason && <button className="btn btn-sm" disabled={busy} onClick={() => void save({ resume: true })}>Resume</button>}
        </div>
        {data.blocked && <p className="banner banner-warn" role="status">{data.blocked}</p>}
        {!data.x.configured && <p className="banner banner-warn" role="status">X isn't connected: dry run works, but it can't go live. Connect it in the Connect X tab.</p>}
        {data.x.configured && !data.x.can_write && <p className="banner banner-warn" role="status">X is connected without posting access: add the Access Token and Secret in Connect X.</p>}
        {data.writer === "headlines" && <p className="muted small">Writing: attributed headlines. Add an Anthropic key in API keys for posts in Price Chat's own words.</p>}
        <div className="stat-grid">
          <Stat label="Last 24 hours" value={`${data.today.published} / ${data.today.target}`} />
          <Stat label="Breaking, 24 hours" value={`${data.today.breaking} / ${s.max_breaking_per_day}`} />
          <Stat label="X spend this month" value={`$${data.cost.x_spend_month_usd.toFixed(2)} / $${data.cost.budget.monthly_usd}`} />
          <Stat label="Last run" value={data.last_run?.at ? relativeTime(data.last_run.at) : "Not yet"} />
        </div>
      </Card>

      {data.x_lane && <XPublishing lane={data.x_lane} busy={busy} act={act} />}

      <TelegramLane />

      <Card title="Settings">
        <div className="row" style={{ gap: 16, flexWrap: "wrap" }}>
          <label>Posts a day <input className="input" type="number" min={1} max={48} defaultValue={s.target_per_day} key={`t${s.target_per_day}`} onBlur={(e) => { const n = Number(e.target.value); if (n !== s.target_per_day && n >= 1) void save({ target_per_day: n }); }} /></label>
          <label>Breaking a day <input className="input" type="number" min={0} max={24} defaultValue={s.max_breaking_per_day} key={`b${s.max_breaking_per_day}`} onBlur={(e) => { const n = Number(e.target.value); if (n !== s.max_breaking_per_day && n >= 0) void save({ max_breaking_per_day: n }); }} /></label>
          <label>Breaking sensitivity <select className="input" value={s.sensitivity} disabled={busy} onChange={(e) => void save({ sensitivity: e.target.value })}>{["low", "medium", "high"].map((x) => <option key={x}>{x}</option>)}</select></label>
        </div>
        <div className="row" style={{ gap: 12, flexWrap: "wrap", marginTop: 12 }}>
          {Object.entries(CATEGORY_NAME).map(([k, name]) => (
            <label key={k}><input type="checkbox" checked={s.categories[k] !== false} disabled={busy} onChange={(e) => void save({ categories: { ...s.categories, [k]: e.target.checked } })} /> {name}</label>
          ))}
        </div>
        <details style={{ marginTop: 12 }}>
          <summary>Sources ({s.feeds.length}) and tickers ({s.watchlist.length})</summary>
          <p className="small"><b>Feeds:</b> {s.feeds.map((f) => f.name).join(", ")}</p>
          <p className="small"><b>Tickers:</b> {s.watchlist.map((w) => `${w.name} (${w.symbol})`).join(", ")}</p>
          <JsonEditor label="Feeds" value={s.feeds} disabled={busy} onSave={(feeds) => save({ feeds: feeds as Feed[] })} />
          <JsonEditor label="Tickers" value={s.watchlist} disabled={busy} onSave={(watchlist) => save({ watchlist: watchlist as Watch[] })} />
          {Object.keys(data.feed_problems).length > 0 && <p className="small banner banner-warn">Feeds not answering: {Object.entries(data.feed_problems).map(([u, p]) => `${new URL(u).hostname} (${p})`).join(", ")}</p>}
        </details>
      </Card>

      {data.desk?.schedule && <SchedulePanel schedule={data.desk.schedule} feeds={data.feed_health} />}

      <NewPost busy={busy} onAdd={(text) => act(() => request("/x/newsdesk/posts", body("POST", { text })))} />

      <Card title={`Upcoming (${data.queue.length})`}>
        {data.queue.length === 0 ? <Empty>Nothing queued.</Empty> : data.queue.map((p) => <PostRow key={p.id} p={p} busy={busy} act={act} />)}
      </Card>
      {data.problems.length > 0 && <Card title="Problems">{data.problems.map((p) => <PostRow key={p.id} p={p} busy={busy} act={act} />)}</Card>}
      <Card title="Published">
        {data.published.length === 0 ? <Empty>Nothing yet.</Empty> : data.published.map((p) => <PostRow key={p.id} p={p} busy={busy} act={act} />)}
      </Card>
    </div>
  );
}

function JsonEditor({ label, value, disabled, onSave }: { label: string; value: unknown; disabled: boolean; onSave: (v: unknown) => Promise<unknown> }) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 1));
  const [bad, setBad] = useState(false);
  return (
    <label className="small" style={{ display: "block", marginTop: 8 }}>{label}
      <textarea className="input" rows={6} style={{ width: "100%", fontFamily: "monospace" }} value={text} onChange={(e) => { setText(e.target.value); setBad(false); }} />
      <button className="btn btn-sm" type="button" disabled={disabled} onClick={() => { try { void onSave(JSON.parse(text)); } catch { setBad(true); } }}>Save {label.toLowerCase()}</button>
      {bad && <span role="alert"> That isn't valid JSON.</span>}
    </label>
  );
}

function NewPost({ busy, onAdd }: { busy: boolean; onAdd: (text: string) => Promise<unknown> }) {
  const [text, setText] = useState("");
  return (
    <Card title="Write one yourself">
      <textarea className="input" rows={3} maxLength={280} style={{ width: "100%" }} value={text} placeholder="Goes to the front of the queue. No links: a link makes a post cost $0.20." onChange={(e) => setText(e.target.value)} />
      <button className="btn btn-sm" disabled={busy || !text.trim()} onClick={() => void onAdd(text).then(() => setText(""))}>Add to queue</button>
    </Card>
  );
}

function PostRow({ p, busy, act }: { p: DeskPost; busy: boolean; act: (fn: () => Promise<unknown>) => Promise<void> }) {
  const [editing, setEditing] = useState<string | null>(null);
  const m = p.metrics;
  return (
    <div className="list-row" style={{ padding: "10px 0", borderBottom: "1px solid var(--line, rgba(255,255,255,.1))" }}>
      <div className="row" style={{ gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <Badge tone={STATUS_TONE[p.status] ?? "neutral"}>{STATUS_TEXT[p.status] ?? p.status}</Badge>
        {p.priority === "breaking" && <Badge tone="bad">Breaking</Badge>}
        <Badge>{CATEGORY_NAME[p.category] ?? p.category}</Badge>
        {p.edited && <Badge>Edited</Badge>}
        <span className="muted small">{relativeTime(p.published_at ?? p.created_at ?? "")}</span>
        {p.url && <a className="link small" href={p.url} target="_blank" rel="noopener noreferrer">Open on X</a>}
      </div>
      {editing !== null ? (
        <>
          <textarea className="input" rows={4} maxLength={280} style={{ width: "100%" }} value={editing} onChange={(e) => setEditing(e.target.value)} />
          <button className="btn btn-sm" disabled={busy} onClick={() => void act(() => request(`/x/newsdesk/posts/${p.id}`, body("PUT", { text: editing }))).then(() => setEditing(null))}>Save</button>{" "}
          <button className="btn btn-sm" disabled={busy} onClick={() => setEditing(null)}>Cancel</button>
        </>
      ) : <p style={{ whiteSpace: "pre-wrap", margin: "6px 0" }}>{p.text}</p>}
      {p.error && <p className="small" role="alert">{p.error}</p>}
      {p.sources.length > 0 && <p className="muted small">Sources: {p.sources.map((x, i) => <span key={x.url + i}>{i > 0 && ", "}<a className="link" href={x.url} target="_blank" rel="noopener noreferrer">{x.name}</a></span>)}</p>}
      {m.impression_count !== undefined && <p className="muted small">{String(m.impression_count)} views · {String(m.like_count ?? 0)} likes · {String(m.retweet_count ?? 0)} reposts · {String(m.reply_count ?? 0)} replies</p>}
      {p.status === "queued" && editing === null && (
        <div className="row" style={{ gap: 6 }}>
          <button className="btn btn-sm" disabled={busy} onClick={() => setEditing(p.text)}>Edit</button>
          <button className="btn btn-sm" disabled={busy} onClick={() => void act(() => request(`/x/newsdesk/posts/${p.id}/publish`, { method: "POST" }))}>Publish now</button>
          <button className="btn btn-sm" disabled={busy} onClick={() => void act(() => request(`/x/newsdesk/posts/${p.id}/reject`, { method: "POST" }))}>Reject</button>
        </div>
      )}
    </div>
  );
}
