import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Capability, Draft, DraftType, FeedbackDecision, FeedbackTarget, GuardCheck, Opportunity } from "../api/types";
import { ApiErrorBanner, Badge, Empty, formatTime, label, type Tone } from "./ui";

// --- feedback ------------------------------------------------------------------------------------

const FEEDBACK: { decision: FeedbackDecision; label: string; title: string }[] = [
  { decision: "approve", label: "✓ Approve", title: "Useful: approve it and learn from this decision" },
  { decision: "reject", label: "✕ Reject", title: "Not useful: reject it and learn from this decision" },
  { decision: "important", label: "★ Important", title: "Rank this (and things like it) higher" },
  { decision: "not_important", label: "☆ Not important", title: "Rank this (and things like it) lower" },
];

export interface EditField {
  key: string;
  label: string;
  value: string;
  multiline?: boolean;
}

/** Approve / reject / edit / important / not important. Every decision is recorded and used to rank future suggestions. */
export function FeedbackButtons({
  kind,
  id,
  onDone,
  only,
  edit,
}: {
  kind: FeedbackTarget;
  id: number;
  onDone?: () => void;
  only?: FeedbackDecision[];
  edit?: EditField[]; // fields offered by the Edit button
}) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [editing, setEditing] = useState<Record<string, string> | null>(null);
  async function saveEdit(e: FormEvent) {
    e.preventDefault();
    if (!editing) return;
    setBusy(true);
    try {
      await api.feedback(kind, id, "edit", { edits: editing });
      setDone("Edited");
      setEditing(null);
      setError(null);
      onDone?.();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }
  async function send(decision: FeedbackDecision) {
    setBusy(true);
    try {
      await api.feedback(kind, id, decision);
      setDone(label(decision));
      setError(null);
      onDone?.();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="btn-group feedback">
        {FEEDBACK.filter((f) => !only || only.includes(f.decision)).map((f) => (
          <button key={f.decision} className="btn btn-sm btn-ghost" title={f.title} disabled={busy} onClick={() => void send(f.decision)}>
            {f.label}
          </button>
        ))}
        {edit && !editing && (
          <button className="btn btn-sm btn-ghost" title="Correct it; the correction is remembered" disabled={busy} onClick={() => setEditing(Object.fromEntries(edit.map((f) => [f.key, f.value])))}>
            ✎ Edit
          </button>
        )}
        {done && <span className="muted small">Saved: {done}</span>}
      </div>
      {editing && edit && (
        <form className="form" onSubmit={saveEdit}>
          {edit.map((f) => (
            <label key={f.key} className="field">
              <span className="field-label">{f.label}</span>
              {f.multiline ? (
                <textarea className="input textarea" rows={3} value={editing[f.key] ?? ""} onChange={(e) => setEditing({ ...editing, [f.key]: e.target.value })} />
              ) : (
                <input className="input" value={editing[f.key] ?? ""} onChange={(e) => setEditing({ ...editing, [f.key]: e.target.value })} />
              )}
            </label>
          ))}
          <div className="btn-group">
            <button className="btn btn-sm btn-primary" disabled={busy}>Save</button>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setEditing(null)}>Cancel</button>
          </div>
        </form>
      )}
      <ApiErrorBanner error={error} />
    </>
  );
}

// --- ranking -------------------------------------------------------------------------------------

export const SIGNAL_LABEL: Record<string, string> = {
  community_relevance: "Community relevance",
  x_relevance: "X relevance",
  recency: "Recency",
  volume: "Volume / growth",
  participants: "Participants / authors",
  novelty: "Not covered recently",
  brand_relevance: "Brand relevance",
  content_potential: "Content potential",
  confidence: "Confidence",
  learned_preference: "Your past decisions",
  relevance: "Relevance",
  author_reach: "Author reach",
  engagement: "Engagement",
  community_overlap: "Telegram overlap",
  watchlist: "Watched account",
  mention: "Mentions us",
};

const PRIORITY_TONE: Record<string, Tone> = { high: "warn", medium: "info", low: "neutral" };

export function PriorityBadge({ value }: { value: string }) {
  if (!value) return <Badge title="Not ranked yet">unranked</Badge>;
  return <Badge tone={PRIORITY_TONE[value] ?? "neutral"}>{value} priority</Badge>;
}

/** Each ranking signal on its own (0-100%), never a single opaque score. */
export function SignalBars({ signals }: { signals: Record<string, number> }) {
  const entries = Object.entries(signals);
  if (!entries.length) return null;
  return (
    <div className="signals">
      {entries.map(([k, v]) => (
        <div key={k} className="signal" title={`${SIGNAL_LABEL[k] ?? label(k)}: ${Math.round(v * 100)}%`}>
          <span className="signal-name">{SIGNAL_LABEL[k] ?? label(k)}</span>
          <span className="bar">
            <span className="bar-fill" style={{ width: `${Math.max(0, Math.min(1, v)) * 100}%` }} />
          </span>
          <span className="signal-value">{Math.round(v * 100)}</span>
        </div>
      ))}
    </div>
  );
}

export function Reasons({ reasons }: { reasons: string[] }) {
  if (!reasons.length) return null;
  return (
    <ul className="reasons">
      {reasons.map((r) => (
        <li key={r}>{r}</li>
      ))}
    </ul>
  );
}

export function RankingPanel({ opp }: { opp: Pick<Opportunity, "signals" | "reasons" | "priority" | "priority_label" | "ranked_at"> }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="ranking">
      <div className="row small">
        <PriorityBadge value={opp.priority_label} />
        {opp.reasons.slice(0, 3).map((r) => (
          <span key={r} className="chip">{r}</span>
        ))}
        {Object.keys(opp.signals).length > 0 && (
          <button className="link-btn" onClick={() => setOpen(!open)}>
            {open ? "Hide ranking signals" : "Why this rank?"}
          </button>
        )}
      </div>
      {open && (
        <div className="ranking-detail">
          <SignalBars signals={opp.signals} />
          <Reasons reasons={opp.reasons} />
          <div className="muted small">Score {Math.round(opp.priority * 100)} · ranked {formatTime(opp.ranked_at)}</div>
        </div>
      )}
    </div>
  );
}

// --- drafts --------------------------------------------------------------------------------------

export const DRAFT_TYPES: { key: DraftType; label: string; variants: string }[] = [
  { key: "x_post", label: "X post", variants: "concise · educational · conversational" },
  { key: "x_thread", label: "X thread", variants: "concise · educational · conversational" },
  { key: "telegram_post", label: "Telegram community post", variants: "three tones" },
  { key: "youtube_idea", label: "YouTube video idea", variants: "three angles" },
  { key: "youtube_titles", label: "YouTube title options", variants: "search · curiosity · authority" },
  { key: "youtube_outline", label: "YouTube outline", variants: "three structures" },
  { key: "short_video_idea", label: "Short-form video idea", variants: "three angles" },
  { key: "short_hook", label: "Short-form hook", variants: "curiosity · direct · contrarian" },
  { key: "tiktok_idea", label: "TikTok idea", variants: "three angles" },
];

const X_TYPES: DraftType[] = ["x_post", "x_thread"];

export function DraftGenerator({ opp, onCreated }: { opp: Opportunity; onCreated?: () => void }) {
  const [type, setType] = useState<DraftType>(opp.platforms.includes("x") ? "x_post" : "telegram_post");
  const [busy, setBusy] = useState<number | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [error, setError] = useState<unknown>(null);
  const ready = ["approved", "reviewing", "drafted"].includes(opp.status);

  async function generate(variants: number) {
    setBusy(variants);
    try {
      setDrafts(await api.generateDrafts(opp.id, type, variants));
      setError(null);
      onCreated?.();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }
  return (
    <div className="draft-gen">
      <div className="btn-group">
        <select className="input select select-sm" value={type} onChange={(e) => setType(e.target.value as DraftType)} aria-label="Draft type">
          {DRAFT_TYPES.map((t) => (
            <option key={t.key} value={t.key}>
              {t.label}
            </option>
          ))}
        </select>
        <button className="btn btn-sm" disabled={!ready || busy !== null} onClick={() => void generate(1)} title={ready ? "" : "Approve the opportunity first"}>
          {busy === 1 ? "Writing…" : "Generate draft"}
        </button>
        <button className="btn btn-sm" disabled={!ready || busy !== null} onClick={() => void generate(3)} title={DRAFT_TYPES.find((t) => t.key === type)?.variants}>
          {busy === 3 ? "Writing 3 variants…" : "Generate 3 variants"}
        </button>
        {!ready && <span className="muted small">Approve the opportunity to draft from it.</span>}
      </div>
      <ApiErrorBanner error={error} />
      {drafts.length > 0 && (
        <div className="drafts">
          {drafts.map((d) => (
            <DraftCard key={d.id} draft={d} />
          ))}
        </div>
      )}
    </div>
  );
}

export function DraftCard({ draft }: { draft: Draft }) {
  const [open, setOpen] = useState(false);
  const [queued, setQueued] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const ctx = draft.context as {
    why?: string;
    checks?: { length_ok?: boolean; warnings?: string[] };
    memories_used?: { id: number; title?: string | null; layer?: string }[];
    community_context?: { topics?: string[]; questions?: string[] };
    generated_by?: { model?: string; provider?: string; task?: string };
    variant_notes?: string;
  };
  const community = [...(ctx.community_context?.topics ?? []), ...(ctx.community_context?.questions ?? [])];
  const warnings = ctx.checks?.warnings ?? [];
  async function queue() {
    try {
      const a = await api.approvalFromDraft(draft.id);
      setQueued(`Added to the approval queue as proposed X post #${a.id}. Nothing is posted until you approve it there.`);
      setError(null);
    } catch (e) {
      setError(e);
    }
  }
  return (
    <div className="draft-card">
      <div className="opp-head">
        <Badge tone="info">{DRAFT_TYPES.find((t) => t.key === draft.draft_type)?.label ?? draft.draft_type ?? "draft"}</Badge>
        {draft.variant_label && <Badge>{draft.variant_label}</Badge>}
        {ctx.checks?.length_ok === false && <Badge tone="bad">too long</Badge>}
        {warnings.length > 0 && <Badge tone="warn">{warnings.length} warning(s)</Badge>}
        <span className="muted small">{ctx.generated_by?.model ? `by ${ctx.generated_by.model}` : draft.created_by}</span>
      </div>
      <div className="draft-body">{draft.body}</div>
      {warnings.map((w) => (
        <div key={w} className="banner banner-warn">{w}</div>
      ))}
      <div className="btn-group">
        <button className="link-btn" onClick={() => setOpen(!open)}>
          {open ? "Hide context" : "Show why & context"}
        </button>
        <button className="link-btn" onClick={() => void navigator.clipboard?.writeText(draft.body)}>
          Copy
        </button>
        {draft.draft_type && X_TYPES.includes(draft.draft_type) && !queued && (
          <button className="link-btn" onClick={() => void queue()}>
            Send to approval queue
          </button>
        )}
        <FeedbackButtons kind="draft" id={draft.id} only={["approve", "reject"]} />
      </div>
      {queued && <div className="banner banner-good">{queued} <Link className="link" to="/approvals">Open queue</Link></div>}
      <ApiErrorBanner error={error} />
      {open && (
        <dl className="kv details">
          <div className="kv-row">
            <dt>Why this draft</dt>
            <dd>{ctx.why ?? "-"}</dd>
          </div>
          <div className="kv-row">
            <dt>Brand memory used</dt>
            <dd>
              {ctx.memories_used?.length
                ? ctx.memories_used.map((m) => (
                    <div key={m.id} className="small">
                      k{m.id} · {m.layer ?? "memory"}{m.title ? `: ${m.title}` : ""}
                    </div>
                  ))
                : "none"}
            </dd>
          </div>
          <div className="kv-row">
            <dt>Community context</dt>
            <dd className="small">{community.join(" · ") || "none"}</dd>
          </div>
          <div className="kv-row">
            <dt>Length</dt>
            <dd className="small">
              {draft.body.length} characters{ctx.checks?.length_ok === false ? " (over the platform limit)" : ""}
            </dd>
          </div>
          {ctx.variant_notes && (
            <div className="kv-row">
              <dt>Variant notes</dt>
              <dd className="small">{ctx.variant_notes}</dd>
            </div>
          )}
          <div className="kv-row">
            <dt>Model</dt>
            <dd className="small mono">
              {ctx.generated_by?.provider ?? ""} {ctx.generated_by?.model ?? ""} {ctx.generated_by?.task ? `· ${ctx.generated_by.task}` : ""}
            </dd>
          </div>
        </dl>
      )}
    </div>
  );
}

export function DraftList({ drafts, empty = "No drafts yet." }: { drafts: Draft[]; empty?: string }) {
  if (!drafts.length) return <Empty>{empty}</Empty>;
  return (
    <div className="drafts">
      {drafts.map((d) => (
        <DraftCard key={d.id} draft={d} />
      ))}
    </div>
  );
}

// --- capabilities & guard ------------------------------------------------------------------------

const CAP_TONE: Record<string, Tone> = { available: "good", unavailable: "bad", not_configured: "neutral", disabled: "neutral", error: "bad", unknown: "neutral", no_credit: "warn", rate_limited: "warn", budget: "warn", approval: "info" };
const CAP_TEXT: Record<string, string> = {
  available: "Working",
  unavailable: "Not available",
  not_configured: "Not set up",
  disabled: "Disabled",
  error: "Error",
  unknown: "Not confirmed yet",
  approval: "Approval required",
  no_credit: "No API credit",
  rate_limited: "Rate limited",
  budget: "Budget reached",
};

export function CapabilityBadge({ cap }: { cap: Capability | undefined }) {
  const status = cap?.status ?? "unknown";
  const text = status === "not_configured" && /user-context|account keys/i.test(cap?.detail ?? "") ? "Requires user credentials" : CAP_TEXT[status] ?? status;
  return (
    <Badge tone={CAP_TONE[status]} title={cap?.detail}>
      {text}
    </Badge>
  );
}

export function GuardChecks({ checks }: { checks: GuardCheck[] | undefined }) {
  if (!checks?.length) return null;
  return (
    <ul className="guard-checks">
      {checks.map((c, i) => (
        <li key={`${c.check}-${i}`} className={c.passed ? "tone-good" : "tone-bad"}>
          {c.passed ? "✓" : "✕"} <strong>{label(c.check)}</strong>
          {c.detail ? <span className="muted"> · {c.detail}</span> : null}
        </li>
      ))}
    </ul>
  );
}

export function compact(n: number | null | undefined): string {
  if (n == null) return "n/a";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}
