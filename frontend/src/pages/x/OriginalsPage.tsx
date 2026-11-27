import { useEffect, useRef, useState } from "react";
import { request } from "../../api/client";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, Loading, PageHeader, type Tone } from "../../components/ui";

/** Admin › Publishing › X › Originals (Growth Engine G1): drafts of ORIGINAL findings computed from Market Memory, each with its graphic,
 *  the tweet text, sources, the calculation, score and format. Approve only MARKS a draft approved: nothing is posted automatically. The
 *  owner copies the text and downloads the image and posts by hand. */

export type OriginalSource = { name: string; label: string; attribution: string; terms_url: string; commercial_ok: boolean };
export type OriginalDraft = {
  id: number; finding_key: string; format: string; detector: string; status: "pending" | "approved" | "rejected" | "regenerating";
  text: string; chars: number; original_text: string; writer: string; score: number; as_of: string | null; headline: string; calculation: string;
  sources: OriginalSource[]; attribution: string[]; window: string; rarity: number | null; novelty: number | null; notes: string[];
  chart: { kind: string; title?: string; variant_of?: string }; chart_kinds?: string[];
  render_info?: Record<string, { template?: string; version?: string }>; version: number; created_at: string | null; decided_at: string | null;
  decided_by: string; reject_reason: string; writer_pending: boolean; gate_problems: string[];
  verification?: { ok: boolean; at: string; values_checked: number; problems: string[]; series: Record<string, { source?: string }> } | null;
  generator?: string;
};
export type OriginalsList = {
  drafts: OriginalDraft[]; counts: Record<string, number>; publishing: string;
  switches: { growth_enabled: boolean; growth_collect_enabled: boolean; intel_enabled: boolean };
};

const TABS: { key: string; label: string }[] = [
  { key: "pending", label: "To review" }, { key: "approved", label: "Approved" }, { key: "rejected", label: "Rejected" },
];
const FORMAT_LABEL: Record<string, string> = {
  extremes: "Market extremes", history: "Historical comparison", breadth: "Market breadth", crowded: "Crowded trades", relative: "Relative performance",
};
const STATUS_TONE: Record<string, Tone> = { pending: "info", approved: "good", rejected: "neutral", regenerating: "warn" };
const WRITER: Record<string, string> = { template: "Template", pc: "PC model", cloud: "Claude", owner: "You" };
const imageUrl = (id: number, fmt: "x" | "portrait", v: number) => `/api/originals/drafts/${id}/image.png?fmt=${fmt}&v=${v}`;

function Graphic({ d }: { d: OriginalDraft }) {
  const [broken, setBroken] = useState(false);
  if (broken)
    return <div className="muted small" style={{ padding: 16, border: "1px dashed var(--border, #ccc)", borderRadius: 8 }}>
      Graphic not available yet ({d.chart.kind}): it could not be drawn. Reload, or try Other chart.
    </div>;
  return <img src={imageUrl(d.id, "x", d.version)} alt={d.chart.title || d.headline} onError={() => setBroken(true)}
    style={{ width: "100%", maxWidth: 640, borderRadius: 8, display: "block" }} />;
}

function DraftCard({ d, onChanged }: { d: OriginalDraft; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(d.text);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [copied, setCopied] = useState(false);

  async function act(path: string, body?: unknown) {
    setBusy(true);
    setError(null);
    try {
      await request(`/originals/drafts/${d.id}/${path}`, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
      setEditing(false);
      onChanged();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(d.text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  const over = (editing ? text.length : d.chars) > 280;
  return (
    <Card title={<span>{FORMAT_LABEL[d.format] ?? d.format} <span className="muted small">· {d.detector}</span></span>}
      actions={<Badge tone={STATUS_TONE[d.status] ?? "neutral"}>{d.status}</Badge>}>
      <div className="stack">
        <Graphic key={d.version} d={d} />
        {editing ? (
          <textarea aria-label="Tweet text" value={text} rows={6} onChange={(e) => setText(e.target.value)} style={{ width: "100%" }} />
        ) : (
          <p data-testid="tweet-text" style={{ whiteSpace: "pre-wrap", fontSize: "1.05em" }}>{d.text}</p>
        )}
        <div className="small">
          <Badge tone={over ? "bad" : "good"}>{editing ? text.length : d.chars}/280</Badge>{" "}
          <span className="muted">Score {d.score.toFixed(0)} · as of {d.as_of ?? "—"} · {d.window} · words by {WRITER[d.writer] ?? d.writer}
            {d.writer_pending ? " (AI wording on its way)" : ""}</span>
        </div>
        {d.gate_problems.length > 0 && <div className="small" style={{ color: "var(--bad, #b00)" }}>Figures gate: {d.gate_problems.join("; ")}</div>}
        {d.verification ? (
          <div className="small" data-testid="verification">
            <Badge tone={d.verification.ok ? "good" : "bad"}>{d.verification.ok ? "Data verified" : "Not verified"}</Badge>{" "}
            <span className="muted">{d.verification.values_checked.toLocaleString()} stored values re-read from{" "}
              {[...new Set(Object.values(d.verification.series).map((x) => x.source).filter(Boolean))].join(", ") || "the source"}
              {" "}at {d.verification.at.slice(0, 16).replace("T", " ")} UTC{d.verification.ok ? "; every one matched" : `; mismatches: ${d.verification.problems.join("; ")}`}</span>
          </div>
        ) : <div className="small muted">Data not re-read from the source (drafted before verification existed)</div>}
        <div className="small"><b>Sources:</b> {d.sources.map((s) => s.label).join(", ")}
          {d.attribution.length ? <span className="muted"> · on the graphic: {d.attribution.join(" · ")}</span> : null}</div>
        <details>
          <summary className="small">How it was calculated</summary>
          <p className="small muted" style={{ whiteSpace: "pre-wrap" }}>{d.calculation}</p>
          {d.notes.length > 0 && <p className="small muted">{d.notes.join(" · ")}</p>}
        </details>
        {d.status === "rejected" && d.reject_reason && <div className="small muted">Rejected: {d.reject_reason}</div>}
        <ApiErrorBanner error={error} />
        <div className="row" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {d.status === "pending" && !editing && <>
            <button className="btn primary" disabled={busy} onClick={() => act("approve")}>Approve</button>
            <button className="btn" disabled={busy} onClick={() => { setText(d.text); setEditing(true); }}>Edit</button>
            <button className="btn" disabled={busy} onClick={() => act("regenerate", { chart_variant: false })}>Regenerate</button>
            <button className="btn" disabled={busy} onClick={() => act("regenerate", { chart_variant: true })}
              title={d.chart_kinds && d.chart_kinds.length > 1 ? `Chart types: ${d.chart_kinds.join(", ")}` : "This finding has one chart type"}>Other chart</button>
            <button className="btn" disabled={busy} onClick={() => act("reject", { reason: window.prompt("Why reject it? (optional)") ?? "" })}>Reject</button>
          </>}
          {editing && <>
            <button className="btn primary" disabled={busy || !text.trim()} onClick={() => act("edit", { text })}>Save</button>
            <button className="btn" disabled={busy} onClick={() => setEditing(false)}>Cancel</button>
          </>}
          {d.status === "approved" && <>
            <button className="btn primary" onClick={copy}>{copied ? "Copied" : "Copy text"}</button>
            <a className="btn" href={imageUrl(d.id, "x", d.version)} download={`original-${d.id}.png`}>Download image</a>
            <a className="btn" href={imageUrl(d.id, "portrait", d.version)} download={`original-${d.id}-portrait.png`}>Portrait image</a>
            <button className="btn" disabled={busy} onClick={() => { setText(d.text); setEditing(true); }}>Edit</button>
          </>}
          {d.status === "regenerating" && <span className="muted small">Writing a new version…</span>}
          {d.status === "rejected" && <button className="btn" disabled={busy} onClick={() => act("regenerate", { chart_variant: false })}>Bring back with new wording</button>}
        </div>
      </div>
    </Card>
  );
}

export function OriginalsPage() {
  const [tab, setTab] = useState("pending");
  const [genBusy, setGenBusy] = useState(false);
  const [genMsg, setGenMsg] = useState<string>("");
  const [genErr, setGenErr] = useState<unknown>(null);
  const { data, failure, loading, reload } = useApi(() => request<OriginalsList>(`/originals/drafts?status=${tab}&limit=100`), 60_000);
  const first = useRef(true);
  useEffect(() => {  // useApi fetched on mount; a new tab fetches again
    if (first.current) { first.current = false; return; }
    void reload();
  }, [tab, reload]);

  async function generate() {
    setGenBusy(true);
    setGenErr(null);
    try {
      const out = await request<{ drafted: number; found: number; errors: string[] }>("/originals/generate", { method: "POST" });
      setGenMsg(`${out.drafted} new draft${out.drafted === 1 ? "" : "s"} from ${out.found} findings${out.errors.length ? ` (${out.errors.join("; ")})` : ""}`);
      await reload();
    } catch (e) {
      setGenErr(e);
    } finally {
      setGenBusy(false);
    }
  }

  const header = (
    <PageHeader title="Originals" subtitle="Findings only Price Chat publishes, computed from our own Market Memory. Approve marks a draft approved; you post it yourself."
      actions={<button className="btn" disabled={genBusy} onClick={generate}>{genBusy ? "Generating…" : "Generate now"}</button>} />
  );
  const counts = data?.counts ?? {};
  return (
    <div className="stack">
      {header}
      <div className="small muted" role="note">Nothing here is posted automatically. Approved drafts offer Copy text and Download image.</div>
      {genMsg && <div className="small">{genMsg}</div>}
      <ApiErrorBanner error={genErr} />
      <div role="tablist" style={{ display: "flex", gap: 8 }}>
        {TABS.map((t) => (
          <button key={t.key} role="tab" aria-selected={tab === t.key} className={`btn ${tab === t.key ? "primary" : ""}`} onClick={() => setTab(t.key)}>
            {t.label} {counts[t.key] ? `(${counts[t.key]})` : ""}
          </button>
        ))}
      </div>
      {!data ? (loading ? <Loading /> : <ApiErrorBanner error={failure} />) : (
        <>
          {failure ? <ApiErrorBanner error={failure} /> : null}
          {!data.switches.growth_enabled && <div className="small muted">The daily generation job is off (GROWTH_ENABLED); Generate now still works.</div>}
          {data.drafts.length === 0 ? <Empty>{tab === "pending" ? "No drafts to review. Try Generate now." : "Nothing here yet."}</Empty> :
            data.drafts.map((d) => <DraftCard key={`${d.id}-${d.version}-${d.status}`} d={d} onChanged={reload} />)}
        </>
      )}
    </div>
  );
}
