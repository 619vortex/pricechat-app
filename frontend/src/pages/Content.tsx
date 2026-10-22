import { useCallback, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import type { ContentStatus, OpportunityStatus, Platform } from "../api/types";
import { OpportunityCard } from "../components/community";
import { DraftList } from "../components/social";
import { ContentItemsTable } from "../components/contentItems";
import type { ContentItemRow } from "../api/types";
import { ApiErrorBanner, Badge, Card, Empty, ErrorBanner, Loading, PageHeader, Tabs, formatTime, label, statusTone } from "../components/ui";

// "published" is intentionally absent: only a connector + SafetyGuard may publish.
const EDITABLE_STATUSES: ContentStatus[] = ["draft", "queued", "pending_approval", "approved", "rejected"];

export function ContentPage() {
  const [params, setParams] = useSearchParams();
  const tab = (["opportunities", "drafts", "published"].includes(params.get("tab") ?? "") ? params.get("tab") : "content") as
    | "content"
    | "opportunities"
    | "drafts"
    | "published";
  return (
    <>
      <PageHeader title="Content" subtitle="Ideas, drafts and the review queue. Nothing here is published automatically." />
      <Tabs
        tabs={[
          { key: "content", label: "Drafts & queue" },
          { key: "opportunities", label: "Opportunities" },
          { key: "drafts", label: "AI drafts & variants" },
          { key: "published", label: "Published (YouTube)" },
        ]}
        value={tab}
        onChange={(t) => setParams(t === "content" ? {} : { tab: t })}
      />
      {tab === "opportunities" && <OpportunityQueue />}
      {tab === "drafts" && <AIDrafts />}
      {tab === "content" && <ContentQueue />}
      {tab === "published" && <PublishedContent />}
    </>
  );
}

const OPP_FILTERS: { key: OpportunityStatus | ""; label: string }[] = [
  { key: "detected", label: "Detected" },
  { key: "reviewing", label: "Reviewing" },
  { key: "approved", label: "Approved" },
  { key: "drafted", label: "Drafted" },
  { key: "rejected", label: "Rejected" },
  { key: "", label: "All" },
];

function AIDrafts() {
  const { data, failure } = useApi(() => api.drafts());
  return (
    <Card title="AI drafts & variants">
      <p className="muted small">Drafts written from approved opportunities, with the reason, brand memory and community context behind each one. Use “Send to approval queue” for X drafts; nothing is posted until you approve it there.</p>
      <ApiErrorBanner error={failure} />
      {data ? <DraftList drafts={data} empty="No AI drafts yet. Approve an opportunity and press “Generate draft”." /> : <Loading />}
    </Card>
  );
}

function OpportunityQueue() {
  const [status, setStatus] = useState<OpportunityStatus | "">("detected");
  const fetcher = useCallback(() => api.opportunities(status || undefined), [status]);
  const { data, failure, reload } = useApi(fetcher);
  return (
    <Card
      title="Content opportunities"
      actions={
        <div className="btn-group">
          <button className="btn btn-sm" onClick={() => void api.rankOpportunities().then(() => reload())} title="Recompute ranking signals now">
            Re-rank
          </button>
          {OPP_FILTERS.map((f) => (
            <button key={f.key} className={`btn btn-sm ${status === f.key ? "btn-primary" : ""}`} onClick={() => setStatus(f.key)}>
              {f.label}
            </button>
          ))}
        </div>
      }
    >
      <p className="muted small">
        Ideas detected from community discussion, ranked by separate signals you can inspect (“Why this rank?”). Your approve, reject and importance decisions are remembered and shape future ranking.
        Approve an opportunity to generate drafts or three variants. An X draft can be sent to the approval queue; nothing is posted without your approval.
      </p>
      <ApiErrorBanner error={failure} />
      {!data ? (
        <Loading />
      ) : data.length === 0 ? (
        <Empty>No opportunities with this status.</Empty>
      ) : (
        <div className="stack">
          {data.map((o) => (
            <OpportunityCard key={`${o.id}-${o.status}`} opp={o} onChanged={() => void reload()} />
          ))}
        </div>
      )}
    </Card>
  );
}

function ContentQueue() {
  const { data, error, loading, reload } = useApi(api.content);
  const [body, setBody] = useState("");
  const [title, setTitle] = useState("");
  const [platform, setPlatform] = useState<Platform | "">("");
  const [actionError, setActionError] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>) {
    try {
      await action();
      setActionError(null);
      await reload();
    } catch (e) {
      setActionError((e as Error).message);
    }
  }

  async function create(e: FormEvent) {
    e.preventDefault();
    if (!body.trim()) return;
    await run(() =>
      api.createContent({ body: body.trim(), title: title.trim() || undefined, status: "draft", target_platform: platform || null }),
    );
    setBody("");
    setTitle("");
  }

  return (
    <>
      <ErrorBanner message={error ?? actionError} />

      <Card title="New draft">
        <form className="form" onSubmit={create}>
          <input className="input" placeholder="Title (optional)" value={title} onChange={(e) => setTitle(e.target.value)} />
          <textarea className="input textarea" placeholder="Write a post…" value={body} onChange={(e) => setBody(e.target.value)} rows={4} />
          <div className="row">
            <select className="input select" value={platform} onChange={(e) => setPlatform(e.target.value as Platform | "")}>
              <option value="">No target platform</option>
              <option value="x">X / Twitter</option>
              <option value="telegram">Telegram</option>
            </select>
            <button className="btn btn-primary" disabled={!body.trim()}>Save draft</button>
          </div>
        </form>
      </Card>

      <Card title="All content">
        {loading && !data ? (
          <Loading />
        ) : !data?.length ? (
          <Empty>No content yet.</Empty>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Content</th>
                <th>Platform</th>
                <th>Status</th>
                <th>Created</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.map((c) => (
                <tr key={c.id}>
                  <td>
                    {c.title && <strong>{c.title}<br /></strong>}
                    {c.created_by.startsWith("ai:") && <Badge tone="info" title="Drafted by AI from a content opportunity; review before use">AI draft · {c.created_by.slice(3)}</Badge>}{" "}
                    <span className="clamp">{c.body}</span>
                  </td>
                  <td>{c.target_platform ? label(c.target_platform) : <span className="muted">-</span>}</td>
                  <td>
                    {c.status === "published" ? (
                      <Badge tone={statusTone[c.status]}>Published</Badge>
                    ) : (
                      <select
                        className={`input select select-sm tone-border-${statusTone[c.status]}`}
                        value={c.status}
                        onChange={(e) => void run(() => api.updateContent(c.id, { status: e.target.value as ContentStatus }))}
                      >
                        {EDITABLE_STATUSES.map((s) => (
                          <option key={s} value={s}>{label(s)}</option>
                        ))}
                      </select>
                    )}
                  </td>
                  <td className="muted small nowrap">{formatTime(c.created_at)}</td>
                  <td>
                    <button className="btn btn-ghost btn-sm" onClick={() => void run(() => api.deleteContent(c.id))} aria-label="Delete">✕</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}

/** Content published on YouTube (synced read-only) and what the website shows. */
function PublishedContent() {
  const { data, failure, setData } = useApi(() => api.contentItems(), 60000);
  return (
    <Card title="Published content" actions={<Link className="link small" to="/youtube">YouTube connection →</Link>}>
      <p className="muted small">Synced from the Price Chat YouTube channel. Public videos appear on the website automatically; you can hide or feature any item.</p>
      <ApiErrorBanner error={failure} />
      {!data ? <Loading /> : <ContentItemsTable items={data} onChanged={(row: ContentItemRow) => setData(data.map((i) => (i.id === row.id ? row : i)))} />}
    </Card>
  );
}

