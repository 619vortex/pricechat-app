import { useMemo, useState, type ReactNode } from "react";
import { request } from "../../api/client";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, Stat, Tabs, relativeTime } from "../../components/ui";

/** Admin › X › News Desk: World Monitor (owner, 2026-11-11). "Scanning": every story the desk read from its feeds and how it judged
 * each one (verified, held, rejected, why, which outlets confirm it, World Monitor outlets marked). "Up next": what the desk is about to
 * post, exactly as Telegram and X will show it. Reading changes nothing. */

type Ev = { id: number; status: string; score: number; telegram: string; x: string; why: string } | null;
export type ScanRow = { story: number; title: string; link: string; source: string; world_monitor: boolean; published_at: string | null; category: string | null;
  state: "verified" | "held" | "rejected"; why: string; evidence: string; confirmed_by: string[]; world_monitor_confirms: string[]; important: boolean;
  roundup: boolean; event: Ev };
type Feed = { url: string; name?: string; items?: number; newest?: string | null; problem?: string | null; world_monitor: boolean; new?: number };
export type Scan = { at: string; hours: number; counts: Record<string, number>; world_monitor_found: number; world_monitor_confirmed: number; next_slot_dubai: string;
  stories: ScanRow[]; feeds: Feed[]; feeds_checked_at: string | null; note: string };
type Link = { name: string; url: string; kind: string };
type Pkg = { ok: boolean; final: boolean; x_text?: string; telegram_html?: string; links?: Link[]; line?: string; copy?: { writer: string; rejected?: string[] }; error?: string;
  checks?: { check: string; passed: boolean; detail: string; platform: string }[] };
type Upcoming = { at: string; next_slot_dubai: string; x_links: boolean; simultaneous: string;
  posts: { event: number; when: string; will_post: boolean; status: string; score: number; image: string; package: Pkg }[] };

const STATE_TONE = { verified: "good", held: "warn", rejected: "neutral" } as const;
const WRITER: Record<string, string> = { local: "written on your PC (local model, fact-checked)", model: "written by Claude", desk: "the outlets' own words" };
type View = "scan" | "next" | "app";
export type AppSignal = { id: string; layer: string; severity: "critical" | "high" | "elevated" | "low"; title: string; detail: string; at: string | null; url: string;
  lat: number | null; lon: number | null };
export type AppSnap = { connected: boolean; seen: boolean; age_seconds: number | null; note: string; app_version?: string; bridge_version?: string; received_at?: string;
  feeds: Record<string, { ok: boolean; count: number; note: string; ms: number }>; signals: AppSignal[];
  summary: { "macro-signals"?: { verdict?: string; fear_greed?: number; fear_greed_label?: string };
    "theater-posture"?: { theaters?: { theater: string; level: string; aircraft: number; vessels: number }[] } } };
const SEV_TONE = { critical: "bad", high: "warn", elevated: "info", low: "neutral" } as const;
const LAYER: Record<string, string> = { earthquake: "Earthquakes", military: "Military activity", instability: "Country instability", aviation: "Aviation (FAA)",
  climate: "Climate", cyber: "Cyber threats", other: "Other" };
type Filter = "all" | "verified" | "world";

/** Telegram HTML as the channel shows it: only bold, italics, line breaks and https links survive; everything else is plain text. */
export function TelegramText({ html }: { html: string }) {
  const nodes = useMemo(() => {
    const doc = new DOMParser().parseFromString(`<div>${html}</div>`, "text/html");
    let k = 0;
    const walk = (n: Node): ReactNode => {
      if (n.nodeType === Node.TEXT_NODE) {
        const parts = (n.textContent ?? "").split("\n");
        return parts.flatMap((p, i) => (i ? [<br key={`br${k++}`} />, p] : [p]));
      }
      if (!(n instanceof HTMLElement)) return null;
      const kids = Array.from(n.childNodes).map(walk);
      const tag = n.tagName.toLowerCase();
      if (tag === "b") return <b key={k++}>{kids}</b>;
      if (tag === "i") return <i key={k++}>{kids}</i>;
      if (tag === "a") {
        const href = n.getAttribute("href") ?? "";
        return href.startsWith("https://") ? <a key={k++} href={href} target="_blank" rel="noreferrer">{kids}</a> : <span key={k++}>{kids}</span>;
      }
      return <span key={k++}>{kids}</span>;
    };
    return walk(doc.body.firstChild as Node);
  }, [html]);
  return <div className="small" style={{ lineHeight: 1.45, overflowWrap: "anywhere" }}>{nodes}</div>;
}

/** X counts every link as 23 characters. */
export function xLength(text: string): number {
  return text.split(/(https?:\/\/\S+)/).reduce((n, part) => n + (/^https?:\/\//.test(part) ? 23 : Array.from(part).length), 0);
}

function ScanView({ data }: { data: Scan }) {
  const [filter, setFilter] = useState<Filter>("all");
  const rows = data.stories.filter((r) => filter === "all" || (filter === "verified" ? r.state === "verified" : r.world_monitor || r.world_monitor_confirms.length > 0));
  const wm = data.feeds.filter((f) => f.world_monitor);
  const down = data.feeds.filter((f) => f.problem);
  return (
    <>
      <div className="stat-grid">
        <Stat label={`Read, last ${data.hours} h`} value={String(data.stories.length)} />
        <Stat label="Verified" value={String(data.counts.verified ?? 0)} tone="good" />
        <Stat label="Waiting for a 2nd outlet" value={String(data.counts.held ?? 0)} />
        <Stat label="World Monitor finds / verified" value={`${data.world_monitor_found} / ${data.world_monitor_confirmed}`} />
      </div>
      <p className="small muted">Feeds checked {data.feeds_checked_at ? relativeTime(data.feeds_checked_at) : "not yet"} · {wm.length} World Monitor outlets
        {down.length ? ` · not answering: ${down.map((f) => f.name).join(", ")}` : " · all answering"} · next slot {data.next_slot_dubai} Dubai. {data.note}</p>
      <div className="row" style={{ gap: 6, flexWrap: "wrap", margin: "8px 0" }}>
        {(["all", "verified", "world"] as Filter[]).map((f) => (
          <button key={f} className={`btn btn-sm${filter === f ? " btn-primary" : ""}`} onClick={() => setFilter(f)}>
            {f === "all" ? "Everything" : f === "verified" ? "Verified only" : "World Monitor outlets"}
          </button>
        ))}
      </div>
      {rows.length === 0 ? <Empty>Nothing here yet.</Empty> : (
        <div style={{ overflowX: "auto" }}>
          <table className="table small">
            <thead><tr><th>When</th><th>Story</th><th>Verdict</th><th>Confirmed by</th><th>Desk</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.story}>
                  <td style={{ whiteSpace: "nowrap" }}>{r.published_at ? relativeTime(r.published_at) : "-"}</td>
                  <td style={{ minWidth: 260 }}>
                    <a href={r.link} target="_blank" rel="noreferrer">{r.title}</a>
                    <div className="muted">{r.source}{r.world_monitor ? " · World Monitor" : ""}{r.important ? " · world news" : ""}</div>
                  </td>
                  <td><Badge tone={STATE_TONE[r.state]}>{r.state === "verified" ? `Verified (${r.evidence})` : r.state === "held" ? "Held" : "Rejected"}</Badge>
                    {r.why ? <div className="muted">{r.why}</div> : null}</td>
                  <td>{r.confirmed_by.length ? r.confirmed_by.map((o) => (r.world_monitor_confirms.includes(o) ? `${o} (WM)` : o)).join(", ") : "-"}</td>
                  <td>{r.event ? <><Badge tone={r.event.status === "released" ? "good" : "info"}>{r.event.status === "released" ? "Posted" : r.event.status}</Badge>
                    <div className="muted">score {r.event.score}{r.event.status === "released" ? ` · Telegram ${r.event.telegram} · X ${r.event.x}` : ""}</div></> : <span className="muted">-</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function AppView({ data }: { data: AppSnap }) {
  const [layer, setLayer] = useState<string>("all");
  if (!data.seen) return <Empty>{data.note} It runs on your PC as the "Price Chat World Monitor" task and reports every 5 minutes.</Empty>;
  const layers = Array.from(new Set(data.signals.map((s) => s.layer)));
  const rows = data.signals.filter((s) => layer === "all" || s.layer === layer);
  const feeds = Object.entries(data.feeds);
  const macro = data.summary["macro-signals"];
  const theaters = data.summary["theater-posture"]?.theaters ?? [];
  const urgent = data.signals.filter((s) => s.severity === "critical" || s.severity === "high").length;
  return (
    <>
      <div className="stat-grid">
        <Stat label="Desktop app" value={data.connected ? "Connected" : "Offline"} tone={data.connected ? "good" : "bad"} />
        <Stat label="Signals" value={String(data.signals.length)} />
        <Stat label="Critical / high" value={String(urgent)} tone={urgent ? "warn" : undefined} />
        <Stat label="Fear & greed" value={macro?.fear_greed != null ? `${macro.fear_greed} ${macro.fear_greed_label ?? ""}` : "-"} />
      </div>
      <p className="small muted">World Monitor {data.app_version || "?"} on your PC · last report {data.received_at ? relativeTime(data.received_at) : "never"} ·
        {" "}{feeds.filter(([, f]) => f.ok).length}/{feeds.length} feeds working
        {feeds.some(([, f]) => !f.ok) ? ` · not working: ${feeds.filter(([, f]) => !f.ok).map(([k, f]) => `${k} (${f.note || "down"})`).join(", ")}` : ""}.
        {" "}Signals to watch; the desk doesn't post from them. {data.note}</p>
      {theaters.length ? <p className="small muted">Military air activity: {theaters.map((t) => `${t.theater} ${t.level}`).join(" · ")}</p> : null}
      <div className="row" style={{ gap: 6, flexWrap: "wrap", margin: "8px 0" }}>
        {["all", ...layers].map((l) => (
          <button key={l} className={`btn btn-sm${layer === l ? " btn-primary" : ""}`} onClick={() => setLayer(l)}>{l === "all" ? "Everything" : LAYER[l] ?? l}</button>
        ))}
      </div>
      {rows.length === 0 ? <Empty>No signals right now.</Empty> : (
        <div style={{ overflowX: "auto" }}>
          <table className="table small">
            <thead><tr><th>Level</th><th>Signal</th><th>Layer</th><th>When</th></tr></thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.id}>
                  <td><Badge tone={SEV_TONE[s.severity] ?? "neutral"}>{s.severity}</Badge></td>
                  <td style={{ minWidth: 260 }}>{s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.title}</a> : s.title}
                    {s.detail ? <div className="muted">{s.detail}</div> : null}</td>
                  <td>{LAYER[s.layer] ?? s.layer}</td>
                  <td style={{ whiteSpace: "nowrap" }}>{s.at ? relativeTime(s.at) : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function NextView({ data }: { data: Upcoming }) {
  if (data.posts.length === 0) return <Empty>Nothing lined up for the {data.next_slot_dubai} Dubai slot yet. An empty slot stays empty rather than posting filler.</Empty>;
  return (
    <>
      <p className="small muted">{data.simultaneous} Links on X are {data.x_links ? "on" : "off"}.</p>
      {data.posts.map((p) => {
        const pkg = p.package;
        const failed = (pkg.checks ?? []).filter((c) => !c.passed);
        return (
          <div key={p.event} className="feed-item" style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", marginBottom: 20 }}>
            <div style={{ minWidth: 0 }}>
              <img src={p.image} alt={`Picture for event ${p.event}`} loading="lazy" style={{ width: "100%", aspectRatio: "16 / 9", borderRadius: 6, background: "#111" }} />
              <div className="row" style={{ gap: 6, flexWrap: "wrap", marginTop: 6 }}>
                <Badge tone={p.will_post ? "good" : "neutral"}>{p.will_post ? `Posts: ${p.when}` : "Won't post next"}</Badge>
                <span className="small muted">score {p.score}{pkg.copy ? ` · ${WRITER[pkg.copy.writer] ?? pkg.copy.writer}` : ""}{pkg.final ? " · final" : " · draft"}</span>
              </div>
              {!p.will_post ? <p className="small muted">{p.status}</p> : null}
            </div>
            <div style={{ minWidth: 0 }}>
              <p className="small muted" style={{ margin: "0 0 4px" }}>Telegram</p>
              {pkg.ok && pkg.telegram_html ? <TelegramText html={pkg.telegram_html} /> : <p className="small">Older format (headline and source): {pkg.error ?? failed.map((c) => c.detail).join("; ")}</p>}
            </div>
            <div style={{ minWidth: 0 }}>
              <p className="small muted" style={{ margin: "0 0 4px" }}>X {pkg.x_text ? `· ${xLength(pkg.x_text)}/280` : ""}</p>
              {pkg.ok && pkg.x_text ? <pre className="small" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", margin: 0, fontFamily: "inherit" }}>{pkg.x_text}</pre> : <p className="small">-</p>}
              {pkg.links && pkg.links.length ? <p className="small muted">Links: {pkg.links.map((l) => l.name).join(", ")}</p> : null}
            </div>
          </div>
        );
      })}
    </>
  );
}

export function WorldMonitor() {
  const [view, setView] = useState<View>("scan");
  const scan = useApi(() => request<Scan>("/x/newsdesk/editorial/monitor/scan"), 30000);
  const next = useApi(() => request<Upcoming>("/x/newsdesk/editorial/monitor/upcoming"), 60000);
  const app = useApi(() => request<AppSnap>("/x/newsdesk/editorial/monitor/app"), 60000);
  const [busy, setBusy] = useState(false);
  const [found, setFound] = useState<string>("");
  const [error, setError] = useState<unknown>(null);
  const scanNow = async () => {
    setBusy(true); setError(null); setFound("");
    try {
      const got = await request<{ new_stories: number }>("/x/newsdesk/editorial/monitor/scan-now", { method: "POST" });
      setFound(`${got.new_stories} new ${got.new_stories === 1 ? "story" : "stories"} found`);
      await Promise.all([scan.reload(), next.reload()]);
    } catch (e) { setError(e); } finally { setBusy(false); }
  };
  const s = scan.data && Array.isArray(scan.data.stories) ? scan.data : null;
  const n = next.data && Array.isArray(next.data.posts) ? next.data : null;
  const a = app.data && Array.isArray(app.data.signals) && typeof app.data.connected === "boolean" ? app.data : null;
  return (
    <Card title="World Monitor" actions={<button className="btn btn-sm" disabled={busy} onClick={() => void scanNow()}>{busy ? "Scanning feeds…" : "Scan now"}</button>}>
      {error ? <ApiErrorBanner error={error} /> : null}
      {found ? <p className="small muted">{found}. The desk decides what to post on its own schedule; scanning never posts.</p> : null}
      <Tabs tabs={[{ key: "scan", label: "Scanning" }, { key: "next", label: "Up next" },
        { key: "app", label: a ? `Desktop app ${a.connected ? "●" : "○"}` : "Desktop app" }]} value={view} onChange={setView} />
      {view === "scan" ? (scan.failure ? <ApiErrorBanner error={scan.failure} /> : s ? <ScanView data={s} /> : <p className="muted">Scanning…</p>)
        : view === "next" ? (next.failure ? <ApiErrorBanner error={next.failure} /> : n ? <NextView data={n} /> : <p className="muted">Writing the next posts…</p>)
        : (app.failure ? <ApiErrorBanner error={app.failure} /> : a ? <AppView data={a} /> : <p className="muted">Asking your PC…</p>)}
    </Card>
  );
}
