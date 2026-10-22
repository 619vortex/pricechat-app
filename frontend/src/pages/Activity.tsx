import { useState } from "react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import type { Activity, ActivityCategory } from "../api/types";
import { ApiErrorBanner, Badge, Card, Empty, Loading, PageHeader, formatTime, type Tone } from "../components/ui";

type Filter = ActivityCategory | "all" | "errors";
const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "Everything" },
  { key: "ai", label: "AI" },
  { key: "memory", label: "Memory" },
  { key: "community", label: "Community" },
  { key: "social", label: "X" },
  { key: "task", label: "Tasks" },
  { key: "config", label: "Configuration" },
  { key: "safety", label: "Safety & audit" },
  { key: "content", label: "Content" },
  { key: "errors", label: "Problems" },
];

function tone(a: Activity): Tone {
  if (a.level === "error") return "bad";
  if (a.level === "warning") return "warn";
  if (a.event === "provider_recovery") return "good";
  return a.category === "ai" ? "info" : "neutral";
}

export function ActivityPage() {
  const [filter, setFilter] = useState<Filter>("all");
  return (
    <>
      <PageHeader
        title="Activity"
        subtitle="Everything Price Chat does, in plain English, plus the safety audit trail"
        actions={
          <div className="btn-group">
            {FILTERS.map((f) => (
              <button key={f.key} className={`btn btn-sm ${filter === f.key ? "btn-primary" : ""}`} onClick={() => setFilter(f.key)}>
                {f.label}
              </button>
            ))}
          </div>
        }
      />
      <LiveNow />
      {/* Keyed so a filter change remounts and refetches. */}
      <ActivityTable key={filter} filter={filter} />
    </>
  );
}

/** What is running right now (polled every few seconds; not stored). */
function LiveNow() {
  const { data } = useApi(api.processing, 3000);
  if (!data) return null;
  const g = data.gate;
  if (!g.background_task && data.running_jobs.length === 0) return <div className="muted small">Nothing running right now.</div>;
  return (
    <div className="banner banner-info">
      <strong>Running now:</strong> {data.running_jobs.map((j) => j.label).join(", ") || "background work"}
      {g.background_task ? ` · model working on ${g.background_task}${g.background_seconds ? ` for ${Math.round(g.background_seconds)} s` : ""}` : ""}
      {g.interactive_active ? " · your chat has priority" : ""}
    </div>
  );
}

function ActivityTable({ filter }: { filter: Filter }) {
  const { data, failure, loading } = useApi(
    () =>
      api.activity(
        filter === "all" ? {} : filter === "errors" ? { level: "error" } : { category: filter },
      ),
    10000,
  );
  const [open, setOpen] = useState<number | null>(null);

  return (
    <>
      <ApiErrorBanner error={failure} />
      <Card>
        {loading && !data ? (
          <Loading />
        ) : !data?.length ? (
          <Empty>Nothing here yet.</Empty>
        ) : (
          <table className="table activity-table">
            <thead>
              <tr>
                <th>When</th>
                <th>What</th>
                <th>Details</th>
                <th>By</th>
              </tr>
            </thead>
            <tbody>
              {data.map((a) => (
                <tr key={a.id} onClick={() => setOpen(open === a.id ? null : a.id)} className="clickable">
                  <td className="muted small nowrap">{formatTime(a.created_at)}</td>
                  <td className="nowrap">
                    <Badge tone={tone(a)}>{a.label}</Badge>
                  </td>
                  <td>
                    {a.message}
                    {open === a.id && Object.keys(a.details).length > 0 && <Details details={a.details} />}
                  </td>
                  <td className="muted small">{a.actor}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}

function Details({ details }: { details: Record<string, unknown> }) {
  return (
    <dl className="kv details">
      {Object.entries(details).map(([k, v]) => (
        <div key={k} className="kv-row">
          <dt>{k.replace(/_/g, " ")}</dt>
          <dd className="mono small">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}
