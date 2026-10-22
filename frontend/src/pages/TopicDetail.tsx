import { useCallback, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import type { TelegramMessage } from "../api/types";
import { MessageList, OpportunityCard, QuestionList, SummaryView, TrendBadge } from "../components/community";
import { FeedbackButtons } from "../components/social";
import { ApiErrorBanner, Badge, Card, Empty, Loading, PageHeader, Stat, formatTime, relativeTime } from "../components/ui";

export function TopicDetailPage() {
  const { id } = useParams();
  const topicId = Number(id);
  const fetcher = useCallback(() => api.topic(topicId), [topicId]);
  const { data, failure, reload } = useApi(fetcher);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const t = data.topic;
  const maxDay = Math.max(1, ...data.volume_by_day.map((d) => d.messages));

  async function summarise() {
    setBusy(true);
    try {
      await api.createSummary({ kind: "topic", topic_id: t.id });
      setError(null);
      await reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title={t.name}
        subtitle={t.description || "Topic detected from community discussion"}
        actions={
          <div className="btn-group">
            <Link className="link" to="/telegram">
              ← Community
            </Link>
            <button className="btn btn-sm" disabled={busy} onClick={() => void summarise()}>
              {busy ? "Summarising…" : "Summarise this topic"}
            </button>
          </div>
        }
      />
      <ApiErrorBanner error={error} />
      {t.status === "merged" && (
        <div className="banner banner-info">
          This topic was merged{t.canonical_id ? <> into <Link className="link" to={`/telegram/topics/${t.canonical_id}`}>topic #{t.canonical_id}</Link></> : null}. Its messages now live there.
        </div>
      )}
      <div className="row">
        <Badge tone={t.review_status === "approved" ? "good" : t.review_status === "rejected" ? "bad" : "neutral"}>review: {t.review_status}</Badge>
        {t.user_importance != null && <Badge tone="warn">{t.user_importance > 0.5 ? "★ marked important" : "marked not important"}</Badge>}
        <FeedbackButtons
          kind="topic"
          id={t.id}
          onDone={() => void reload()}
          edit={[
            { key: "name", label: "Topic name", value: t.name },
            { key: "description", label: "Description", value: t.description, multiline: true },
          ]}
        />
      </div>
      <div className="stats">
        <Stat label="Trend" value={<TrendBadge trend={t.trend} />} hint={`${t.count_24h} in last 24h · ${t.count_prev_24h} the 24h before`} />
        <Stat label="Messages" value={t.message_count} hint={`${t.participant_count} members`} />
        <Stat label="First seen" value={<span className="small">{formatTime(t.first_seen_at)}</span>} hint={`last seen ${relativeTime(t.last_seen_at)}`} />
        <Stat label="Confidence" value={`${Math.round(t.confidence * 100)}%`} hint="how tightly the messages hang together" />
        <Stat label="Importance" value={`${Math.round(t.importance * 100)}%`} hint={t.named_by_model ? `named by ${t.named_by_model}` : "not named yet"} />
      </div>
      {t.aliases.length > 0 && (
        <div className="muted small">Also known as (merged): {t.aliases.join(", ")}</div>
      )}
      {t.keywords.length > 0 && (
        <div className="chips">
          {t.keywords.map((k) => (
            <span key={k} className="chip">
              {k}
            </span>
          ))}
        </div>
      )}

      <div className="grid-2">
        <div className="stack">
          <Card title="Most representative messages">
            <MessageList messages={data.representative} empty="Not enough data yet." />
          </Card>
          <Card title="Activity by day">
            {data.volume_by_day.length === 0 ? (
              <Empty>No data.</Empty>
            ) : (
              <div className="bars" role="img" aria-label="Messages per day">
                {data.volume_by_day.map((d) => (
                  <div key={d.day} className="bar-row">
                    <span className="muted small mono">{d.day}</span>
                    <span className="bar" style={{ width: `${(100 * d.messages) / maxDay}%` }} />
                    <span className="small">{d.messages}</span>
                  </div>
                ))}
              </div>
            )}
          </Card>
          <Card title="Questions in this topic">
            <QuestionList questions={data.questions} onChanged={() => void reload()} />
          </Card>
        </div>
        <div className="stack">
          <Card title="Topic summaries">
            {data.summaries.length === 0 ? <Empty>No topic summary yet. Use “Summarise this topic”.</Empty> : data.summaries.map((s) => <SummaryView key={s.id} summary={s} />)}
          </Card>
          <Card title="Related memories">
            {data.memories.length === 0 ? (
              <Empty>No memories learned from this topic yet.</Empty>
            ) : (
              <ul className="feed">
                {data.memories.map((m) => (
                  <li key={m.id}>
                    <Badge tone={m.review_status === "approved" ? "good" : "warn"}>{m.review_status}</Badge>
                    <div className="feed-body">
                      <span>
                        {m.title ? <strong>{m.title}: </strong> : null}
                        {m.content}
                      </span>
                      <FeedbackButtons kind="memory" id={m.id} onDone={() => void reload()} />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Content opportunities">
            {data.opportunities.length === 0 ? <Empty>None yet.</Empty> : data.opportunities.map((o) => <OpportunityCard key={o.id} opp={o} onChanged={() => void reload()} />)}
          </Card>
        </div>
      </div>
      <Card title={`All messages in this topic (latest ${data.recent.length})`}>
        <MessageList messages={data.recent} onDeleted={() => void reload()} />
      </Card>
      {t.status !== "merged" && data.recent.length > 1 && <SplitCard topicId={t.id} messages={data.recent} />}
    </>
  );
}

/** Move selected messages into a new topic. Nothing is deleted; the new topic links back to this one. */
function SplitCard({ topicId, messages }: { topicId: number; messages: TelegramMessage[] }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [name, setName] = useState("");
  const [error, setError] = useState<unknown>(null);
  function toggle(id: number) {
    const next = new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setPicked(next);
  }
  async function split(e: FormEvent) {
    e.preventDefault();
    try {
      const created = await api.splitTopic(topicId, [...picked], name.trim());
      navigate(`/telegram/topics/${created.id}`);
    } catch (err) {
      setError(err);
    }
  }
  return (
    <Card title="Split this topic" actions={<button className="link-btn" onClick={() => setOpen(!open)}>{open ? "Cancel" : "Split off some messages…"}</button>}>
      <p className="muted small">If this topic mixes two different discussions, tick the messages that belong elsewhere and give them a name. They move to a new topic; nothing is deleted.</p>
      {open && (
        <form className="stack" onSubmit={split}>
          <ul className="tg-msgs">
            {messages.map((m) => (
              <li key={m.id} className="tg-msg">
                <label className="row small">
                  <input type="checkbox" checked={picked.has(m.id)} onChange={() => toggle(m.id)} />
                  <strong>{m.author_name ?? "member"}</strong>
                  <span className="muted">{formatTime(m.sent_at)}</span>
                </label>
                <div className="tg-msg-text">{m.text}</div>
              </li>
            ))}
          </ul>
          <div className="row">
            <input className="input" placeholder="Name for the new topic" value={name} onChange={(e) => setName(e.target.value)} maxLength={160} />
            <button className="btn btn-primary" disabled={picked.size === 0 || picked.size === messages.length || name.trim().length < 2}>
              Split {picked.size} message(s)
            </button>
          </div>
          <ApiErrorBanner error={error} />
        </form>
      )}
    </Card>
  );
}
