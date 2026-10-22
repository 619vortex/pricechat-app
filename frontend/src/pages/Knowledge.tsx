import { useCallback, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import type { Memory, MemoryKind, MemoryLayer, RetrievedMemory } from "../api/types";
import { ApiErrorBanner, Badge, Card, Empty, Field, Loading, PageHeader, Stat, Tabs, formatTime, label, ms, statusTone } from "../components/ui";
import { CommunityBrain } from "./knowledge/CommunityBrain";

const INPUT_KINDS: { kind: MemoryKind; label: string; hint: string }[] = [
  { kind: "text", label: "Plain text", hint: "General information about Price Chat" },
  { kind: "note", label: "Note", hint: "Internal notes, decisions, reminders" },
  { kind: "brand_fact", label: "Brand fact", hint: "A verified fact the agent may state" },
  { kind: "community_insight", label: "Community insight", hint: "Something learned about the community" },
];
const LAYERS: MemoryLayer[] = ["brand", "community", "social", "conversation"];
const IMPORTANCE = [
  { value: 0.3, label: "Low" },
  { value: 0.5, label: "Normal" },
  { value: 0.8, label: "High" },
  { value: 1, label: "Critical" },
];

function importanceLabel(v: number): string {
  return [...IMPORTANCE].reverse().find((i) => v >= i.value - 0.05)?.label ?? "Low";
}

export function KnowledgePage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "community" ? "community" : "library";
  return (
    <>
      <PageHeader title="Knowledge" subtitle="What Price Chat knows. The agent recalls the most relevant approved items for each request." />
      <Tabs
        tabs={[
          { key: "library", label: "Knowledge library" },
          { key: "community", label: "Community brain" },
        ]}
        value={tab}
        onChange={(t) => setParams(t === "community" ? { tab: "community" } : {})}
      />
      {tab === "community" ? <CommunityBrain /> : <KnowledgeLibrary />}
    </>
  );
}

function KnowledgeLibrary() {
  const [layer, setLayer] = useState<MemoryLayer | "">("");
  const [refresh, setRefresh] = useState(0);
  const stats = useApi(api.knowledgeStats);
  const [editing, setEditing] = useState<Memory | null>(null);

  const changed = () => {
    setRefresh((n) => n + 1);
    void stats.reload();
  };

  return (
    <>
      {stats.data && (
        <div className="stats">
          <Stat label="Total items" value={stats.data.total} />
          {LAYERS.map((l) => (
            <Stat key={l} label={`${label(l)} knowledge`} value={stats.data!.by_layer[l] ?? 0} />
          ))}
          <Stat
            label="Indexed for meaning search"
            value={`${stats.data.embedded}/${stats.data.total}`}
            hint={stats.data.embedding_model ?? "keyword search only"}
            tone={stats.data.embedded < stats.data.total ? "warn" : "neutral"}
          />
        </div>
      )}

      <div className="grid-2">
        <KnowledgeForm key={editing?.id ?? "new"} editing={editing} onDone={() => { setEditing(null); changed(); }} onCancel={() => setEditing(null)} />
        <SearchPanel refresh={refresh} />
      </div>

      <Card
        title="All knowledge"
        actions={
          <div className="btn-group">
            {stats.data && stats.data.embedded < stats.data.total && <ReindexButton onDone={changed} />}
            <select className="input select select-sm" value={layer} onChange={(e) => setLayer(e.target.value as MemoryLayer | "")} aria-label="Filter by layer">
              <option value="">All layers</option>
              {LAYERS.map((l) => (
                <option key={l} value={l}>
                  {label(l)}
                </option>
              ))}
            </select>
          </div>
        }
      >
        <KnowledgeList key={`${layer}-${refresh}`} layer={layer} onEdit={setEditing} onChanged={changed} />
      </Card>

      <SourcesCard />
    </>
  );
}

function KnowledgeForm({ editing, onDone, onCancel }: { editing: Memory | null; onDone: () => void; onCancel: () => void }) {
  const [title, setTitle] = useState(editing?.title ?? "");
  const [content, setContent] = useState(editing?.content ?? "");
  const [kind, setKind] = useState<MemoryKind>((INPUT_KINDS.some((k) => k.kind === editing?.kind) ? editing?.kind : "text") as MemoryKind);
  const [tags, setTags] = useState(editing?.tags.join(", ") ?? "");
  const [source, setSource] = useState(editing?.source ?? "");
  const [importance, setImportance] = useState(editing?.importance ?? 0.5);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!content.trim()) return;
    setSaving(true);
    setError(null);
    const payload = {
      title: title.trim() || undefined,
      content: content.trim(),
      kind,
      tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
      source: source.trim() || undefined,
      importance,
    };
    try {
      const saved = editing ? await api.updateKnowledge(editing.id, payload) : await api.addKnowledge(payload);
      setNotice(
        `${editing ? "Updated" : "Saved"} to ${saved.memory.layer} knowledge.` +
          (saved.indexed ? "" : " Meaning-based search will index it once the embedding model is available."),
      );
      if (!editing) {
        setTitle("");
        setContent("");
        setTags("");
      }
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  }

  const hint = INPUT_KINDS.find((k) => k.kind === kind)?.hint;
  return (
    <Card title={editing ? `Edit knowledge #${editing.id}` : "Add knowledge"}>
      <ApiErrorBanner error={error} />
      {notice && <div className="banner banner-good">{notice}</div>}
      <form className="form" onSubmit={submit}>
        <div className="row-fields">
          <Field label="Type" hint={hint}>
            <select className="input select" value={kind} onChange={(e) => setKind(e.target.value as MemoryKind)}>
              {INPUT_KINDS.map((k) => (
                <option key={k.kind} value={k.kind}>
                  {k.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Importance">
            <select className="input select" value={importance} onChange={(e) => setImportance(Number(e.target.value))}>
              {IMPORTANCE.map((i) => (
                <option key={i.value} value={i.value}>
                  {i.label}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Field label="Title">
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={256} placeholder="Short label, e.g. “Weekly AMA”" />
        </Field>
        <Field label="Content">
          <textarea className="input textarea" rows={5} value={content} onChange={(e) => setContent(e.target.value)} maxLength={20000} placeholder="The information itself. Be specific and factual." />
        </Field>
        <div className="row-fields">
          <Field label="Tags" hint="Comma separated">
            <input className="input" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="events, community" />
          </Field>
          <Field label="Source" hint="Where this came from">
            <input className="input" value={source} onChange={(e) => setSource(e.target.value)} maxLength={256} placeholder="e.g. Team call 3 Oct" />
          </Field>
        </div>
        <div className="btn-group">
          <button className="btn btn-primary" disabled={!content.trim() || saving}>
            {saving ? "Saving…" : editing ? "Save changes" : "Add to brain"}
          </button>
          {editing && (
            <button type="button" className="btn btn-ghost" onClick={onCancel}>
              Cancel
            </button>
          )}
        </div>
      </form>
    </Card>
  );
}

function SearchPanel({ refresh }: { refresh: number }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<{ items: RetrievedMemory[]; strategy: string; duration: number; notes: string[] } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function search(e: FormEvent) {
    e.preventDefault();
    if (!q.trim()) return;
    setBusy(true);
    try {
      const r = await api.searchKnowledge(q.trim());
      setResults({ items: r.results, strategy: r.strategy, duration: r.duration_ms, notes: r.notes });
      setError(null);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Test recall">
      <p className="muted small">Search the way the agent does: by meaning and by keywords. Use it to check the agent will find what you added.</p>
      <form className="chat-input" onSubmit={search}>
        <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. when do we meet each week?" maxLength={500} />
        <button className="btn" disabled={busy || !q.trim()}>
          {busy ? "…" : "Search"}
        </button>
      </form>
      <ApiErrorBanner error={error} />
      {results && (
        <>
          <div className="muted small">
            {results.items.length} result{results.items.length === 1 ? "" : "s"} · {results.strategy} search in {ms(results.duration)}
            {results.notes.map((n) => ` · ${n}`)}
            {refresh > 0 && " · list changed since this search"}
          </div>
          {results.items.length === 0 ? (
            <Empty>Nothing relevant found. The agent would answer without stored knowledge.</Empty>
          ) : (
            <ul className="feed">
              {results.items.map((r) => (
                <li key={r.memory.id}>
                  <Badge tone={statusTone[r.memory.layer]}>{r.memory.layer}</Badge>
                  <div className="feed-body">
                    <span>
                      <strong>{r.memory.title ?? ""}</strong> {r.memory.title ? "· " : ""}
                      <span className="clamp">{r.memory.content}</span>
                    </span>
                    <span className="muted small">
                      score {r.score.toFixed(2)} · {r.reasons.join(" · ")}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Card>
  );
}

function KnowledgeList({ layer, onEdit, onChanged }: { layer: MemoryLayer | ""; onEdit: (m: Memory) => void; onChanged: () => void }) {
  const fetcher = useCallback(() => api.knowledge({ layer }), [layer]);
  const { data, failure, reload } = useApi(fetcher);
  const [error, setError] = useState<unknown>(null);

  async function remove(m: Memory) {
    if (!window.confirm(`Remove "${m.title ?? m.content.slice(0, 40)}" from the brain?`)) return;
    try {
      await api.deleteKnowledge(m.id);
      await reload();
      onChanged();
    } catch (e) {
      setError(e);
    }
  }

  if (failure) return <ApiErrorBanner error={failure} />;
  if (!data) return <Loading />;
  if (!data.length) return <Empty>No knowledge yet. Add brand facts, notes and community insights above.</Empty>;
  return (
    <>
      <ApiErrorBanner error={error} />
      <table className="table">
        <thead>
          <tr>
            <th>Knowledge</th>
            <th>Layer / type</th>
            <th>Tags</th>
            <th>Source</th>
            <th>Importance</th>
            <th>Updated</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {data.map((m) => (
            <tr key={m.id}>
              <td>
                {m.title && (
                  <strong>
                    {m.title}
                    <br />
                  </strong>
                )}
                <span className="clamp">{m.content}</span>
              </td>
              <td>
                <Badge tone={statusTone[m.layer]}>{m.layer}</Badge>
                <div className="muted small">{label(m.kind)}</div>
                {m.review_status !== "approved" && <Badge tone="warn" title="The agent does not use this until it is approved">{m.review_status}</Badge>}
              </td>
              <td className="small">{m.tags.join(", ") || <span className="muted">-</span>}</td>
              <td className="small">
                {m.source ?? "-"}
                <div className="muted">{label(m.source_type)}{m.author ? ` · ${m.author}` : ""}</div>
              </td>
              <td className="small">{importanceLabel(m.importance)}</td>
              <td className="muted small nowrap">{formatTime(m.updated_at)}</td>
              <td className="nowrap">
                <button className="btn btn-ghost btn-sm" onClick={() => onEdit(m)}>
                  Edit
                </button>
                <button className="btn btn-ghost btn-sm" onClick={() => void remove(m)} aria-label="Delete">
                  ✕
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function ReindexButton({ onDone }: { onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  return (
    <>
      <button
        className="btn btn-sm"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            await api.reindexKnowledge();
            setError(null);
            onDone();
          } catch (e) {
            setError(e);
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Indexing…" : "Index for meaning search"}
      </button>
      {error ? <ApiErrorBanner error={error} /> : null}
    </>
  );
}

function SourcesCard() {
  const { data } = useApi(api.knowledgeSources);
  return (
    <Card title="Automatic knowledge sources">
      <p className="muted small">
        These will feed the brain automatically in later phases, through the same storage and search as manual knowledge. None are connected yet.
      </p>
      <div className="sources">
        {(data ?? []).map((s) => (
          <div key={s.key} className="source">
            <div className="source-head">
              <strong>{s.display_name}</strong>
              <Badge>{s.available ? "Available" : "Not connected"}</Badge>
            </div>
            <p className="muted small">{s.description}</p>
            <span className="muted small">→ {label(s.layer)} knowledge</span>
          </div>
        ))}
      </div>
    </Card>
  );
}
