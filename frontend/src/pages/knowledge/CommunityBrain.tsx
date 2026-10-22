import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { useApi } from "../../api/useApi";
import type { Memory, ReviewStatus } from "../../api/types";
import { Evidence, OpportunityCard, QuestionList, SummaryView, TopicTable } from "../../components/community";
import { ApiErrorBanner, Badge, Card, Empty, Loading, relativeTime } from "../../components/ui";

const STATUS_TONE: Record<ReviewStatus, "good" | "warn" | "neutral"> = { approved: "good", pending: "warn", rejected: "neutral" };

export function CommunityBrain() {
  const [filter, setFilter] = useState<ReviewStatus | "">("pending");
  const [refresh, setRefresh] = useState(0);
  const memoriesFetcher = useCallback(
    () => api.knowledge({ layer: "community", review_status: filter }),
    [filter, refresh], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const memories = useApi(memoriesFetcher);
  const terminology = useApi(useCallback(() => api.knowledge({ layer: "community", tag: "terminology" }), [refresh])); // eslint-disable-line react-hooks/exhaustive-deps
  const questions = useApi(useCallback(() => api.questions(true), []));
  const topics = useApi(useCallback(() => api.topics("importance"), []));
  const opportunities = useApi(useCallback(() => api.opportunities("detected"), []));
  const summaries = useApi(useCallback(() => api.summaries(), []));

  const changed = () => setRefresh((n) => n + 1);

  return (
    <>
      <div className="banner banner-info">
        The community brain is what Price Chat has learned from authorised Telegram chats. AI-proposed memories start as <strong>pending</strong>; the
        agent only uses <strong>approved</strong> ones, and treats them as community observations, not brand facts.
      </div>
      <Card
        title="Community memories"
        actions={
          <select className="input select select-sm" value={filter} onChange={(e) => setFilter(e.target.value as ReviewStatus | "")} aria-label="Review status">
            <option value="pending">Pending review</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
            <option value="">All</option>
          </select>
        }
      >
        <ApiErrorBanner error={memories.failure} />
        {!memories.data ? (
          <Loading />
        ) : memories.data.length === 0 ? (
          <Empty>{filter === "pending" ? "Nothing waiting for review." : "No memories here."}</Empty>
        ) : (
          <div className="review-list">
            {memories.data.map((m) => (
              <ReviewItem key={`${m.id}-${m.updated_at}`} memory={m} onChanged={changed} />
            ))}
          </div>
        )}
      </Card>

      <div className="grid-2">
        <Card title="Learned terminology">
          {!terminology.data ? (
            <Loading />
          ) : terminology.data.length === 0 ? (
            <Empty>No community terms learned yet.</Empty>
          ) : (
            <ul className="feed">
              {terminology.data.map((m) => (
                <li key={m.id}>
                  <Badge tone={STATUS_TONE[m.review_status]}>{m.review_status}</Badge>
                  <div className="feed-body">
                    <span>
                      <strong>{m.title}</strong> {m.content}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Recurring questions" actions={<Link className="link" to="/telegram">Community</Link>}>
          {questions.data ? <QuestionList questions={questions.data.slice(0, 8)} onChanged={() => void questions.reload()} /> : <Loading />}
        </Card>
      </div>
      <div className="grid-2">
        <Card title="Important topics">{topics.data ? <TopicTable topics={topics.data.slice(0, 8)} compact /> : <Loading />}</Card>
        <Card title="Recent summaries">
          {!summaries.data ? <Loading /> : summaries.data.length === 0 ? <Empty>No summaries yet.</Empty> : summaries.data.slice(0, 2).map((s) => <SummaryView key={s.id} summary={s} />)}
        </Card>
      </div>
      <Card title="New content opportunities" actions={<Link className="link" to="/content?tab=opportunities">All opportunities</Link>}>
        {!opportunities.data ? (
          <Loading />
        ) : opportunities.data.length === 0 ? (
          <Empty>No new opportunities.</Empty>
        ) : (
          <div className="stack">
            {opportunities.data.slice(0, 3).map((o) => (
              <OpportunityCard key={o.id} opp={o} onChanged={() => void opportunities.reload()} />
            ))}
          </div>
        )}
      </Card>
    </>
  );
}

function ReviewItem({ memory, onChanged }: { memory: Memory; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(memory.title ?? "");
  const [content, setContent] = useState(memory.content);
  const [error, setError] = useState<unknown>(null);
  const gen = memory.extra.generated_by;

  async function update(changes: Parameters<typeof api.updateKnowledge>[1]) {
    try {
      await api.updateKnowledge(memory.id, changes);
      // Text corrections are also recorded as feedback (the update above re-indexes the memory).
      if (changes.content !== undefined || changes.title !== undefined) {
        await api.feedback("memory", memory.id, "edit", { edits: { title: changes.title ?? "", content: changes.content ?? "" } });
      }
      setError(null);
      setEditing(false);
      onChanged();
    } catch (e) {
      setError(e);
    }
  }
  // approve / reject go through feedback, so the decision is remembered and used for ranking
  async function decide(decision: "approve" | "reject") {
    try {
      await api.feedback("memory", memory.id, decision);
      onChanged();
    } catch (e) {
      setError(e);
    }
  }
  async function remove() {
    if (!window.confirm("Delete this memory?")) return;
    try {
      await api.deleteKnowledge(memory.id);
      onChanged();
    } catch (e) {
      setError(e);
    }
  }
  const importance = Math.round(memory.importance * 10) / 10;

  return (
    <div className="review-item">
      <div className="review-head">
        <Badge tone={STATUS_TONE[memory.review_status]}>{memory.review_status}</Badge>
        {memory.extra.insight_type && <Badge>{memory.extra.insight_type.replace(/_/g, " ")}</Badge>}
        <span className="muted small">
          {gen ? `proposed by ${gen.model}` : "added manually"} · {relativeTime(memory.created_at)}
          {memory.extra.confidence != null && ` · confidence ${Math.round(memory.extra.confidence * 100)}%`}
        </span>
      </div>
      {editing ? (
        <div className="form">
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={256} />
          <textarea className="input textarea" rows={3} value={content} onChange={(e) => setContent(e.target.value)} maxLength={20000} />
        </div>
      ) : (
        <div>
          {memory.title && <strong>{memory.title}: </strong>}
          {memory.content}
        </div>
      )}
      <ApiErrorBanner error={error} />
      <div className="btn-group review-actions">
        {editing ? (
          <>
            <button className="btn btn-sm btn-primary" disabled={!content.trim()} onClick={() => void update({ title: title.trim(), content: content.trim() })}>
              Save
            </button>
            <button className="btn btn-sm btn-ghost" onClick={() => setEditing(false)}>
              Cancel
            </button>
          </>
        ) : (
          <>
            {memory.review_status !== "approved" && (
              <button className="btn btn-sm btn-primary" onClick={() => void decide("approve")}>
                Approve
              </button>
            )}
            {memory.review_status !== "rejected" && (
              <button className="btn btn-sm" onClick={() => void decide("reject")}>
                Reject
              </button>
            )}
            <button className="btn btn-sm" onClick={() => setEditing(true)}>
              Edit
            </button>
            <span className="importance">
              Importance
              <button className="btn btn-sm btn-ghost" aria-label="Decrease importance" disabled={importance <= 0} onClick={() => void update({ importance: Math.max(0, importance - 0.1) })}>
                −
              </button>
              <strong>{Math.round(memory.importance * 100)}%</strong>
              <button className="btn btn-sm btn-ghost" aria-label="Increase importance" disabled={importance >= 1} onClick={() => void update({ importance: Math.min(1, importance + 0.1) })}>
                +
              </button>
            </span>
            <button className="btn btn-sm btn-ghost" onClick={() => void remove()} aria-label="Delete memory">
              ✕
            </button>
          </>
        )}
      </div>
      {memory.source_type === "telegram" && <Evidence kind="memory" id={memory.id} label="Show supporting messages" />}
    </div>
  );
}
