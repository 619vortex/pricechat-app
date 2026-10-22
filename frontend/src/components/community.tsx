import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type {
  CommunityQuestion,
  CommunitySummary,
  ContentChannel,
  Opportunity,
  OpportunityStatus,
  SummaryPoint,
  TelegramMessage,
  Topic,
  TopicTrend,
} from "../api/types";
import { DraftGenerator, FeedbackButtons, RankingPanel } from "./social";
import { ApiErrorBanner, Badge, Empty, Loading, formatTime, label, ms, relativeTime, type Tone } from "./ui";

export const TREND: Record<TopicTrend, { label: string; tone: Tone }> = {
  new: { label: "New", tone: "info" },
  rising: { label: "Rising ↑", tone: "good" },
  steady: { label: "Stable", tone: "neutral" },
  falling: { label: "Cooling ↓", tone: "warn" },
  insufficient_data: { label: "Not enough data", tone: "neutral" },
};

export const CHANNEL_LABEL: Record<ContentChannel, string> = {
  x: "X",
  youtube: "YouTube",
  youtube_shorts: "YouTube Shorts",
  tiktok: "TikTok",
  telegram: "Telegram",
};

export function TrendBadge({ trend }: { trend: TopicTrend }) {
  const t = TREND[trend];
  return (
    <Badge tone={t.tone} title={trend === "insufficient_data" ? "Too few messages to compare with the previous day" : undefined}>
      {t.label}
    </Badge>
  );
}

// --- messages --------------------------------------------------------------------------------

export function MessageItem({ m, onDelete, onTrace }: { m: TelegramMessage; onDelete?: (m: TelegramMessage) => void; onTrace?: (m: TelegramMessage) => void }) {
  return (
    <li className="tg-msg">
      <div className="tg-msg-head">
        <strong>{m.author_name ?? "Unknown member"}</strong>
        <span className="muted small">{formatTime(m.sent_at)}</span>
        {m.category && m.category !== "information" && <Badge tone={m.category === "concern" ? "warn" : m.category === "noise" ? "neutral" : "info"}>{m.category}</Badge>}
        {m.reply_to_message_id && <span className="muted small">↩ reply</span>}
        {m.forward_from && <span className="muted small">forwarded from {m.forward_from}</span>}
        {m.media_type && <Badge>{m.media_type}</Badge>}
        {onTrace && (
          <button className="link-btn small push-right" title="Show what Price Chat derived from this message" onClick={() => onTrace(m)}>
            Trace
          </button>
        )}
        {onDelete && (
          <button className={`btn btn-ghost btn-sm ${onTrace ? "" : "push-right"}`} title="Delete this message from Price Chat" onClick={() => onDelete(m)}>
            ✕
          </button>
        )}
      </div>
      <div className="tg-msg-text">{m.text || <span className="muted">[{m.media_type ?? "no text"}]</span>}</div>
    </li>
  );
}

export function MessageList({ messages, onDeleted, onTrace, empty = "No messages." }: { messages: TelegramMessage[]; onDeleted?: () => void; onTrace?: (m: TelegramMessage) => void; empty?: string }) {
  const [error, setError] = useState<unknown>(null);
  if (!messages.length) return <Empty>{empty}</Empty>;
  async function remove(m: TelegramMessage) {
    if (!window.confirm("Delete this message from Price Chat? Anything learned only from it is removed too.")) return;
    try {
      await api.deleteTelegramMessage(m.id);
      onDeleted?.();
    } catch (e) {
      setError(e);
    }
  }
  return (
    <>
      <ApiErrorBanner error={error} />
      <ul className="tg-msgs">
        {messages.map((m) => (
          <MessageItem key={m.id} m={m} onDelete={onDeleted ? remove : undefined} onTrace={onTrace} />
        ))}
      </ul>
    </>
  );
}

/** Expandable list of the Telegram messages behind a derived record. */
export function Evidence({ kind, id, label: text = "Show evidence" }: { kind: string; id: number; label?: string }) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<TelegramMessage[] | null>(null);
  const [generated, setGenerated] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<unknown>(null);
  async function toggle() {
    setOpen(!open);
    if (!messages) {
      try {
        const p = await api.provenance(kind, id);
        setMessages(p.messages);
        setGenerated(p.generated);
      } catch (e) {
        setError(e);
      }
    }
  }
  return (
    <div className="evidence">
      <button className="link-btn small" onClick={() => void toggle()}>
        {open ? "Hide evidence" : text}
      </button>
      {open && (
        <div className="evidence-body">
          <ApiErrorBanner error={error} />
          {generated && (
            <div className="muted small">
              {Object.entries(generated)
                .filter(([, v]) => v !== null && typeof v !== "object")
                .map(([k, v]) => `${label(k)}: ${String(v)}`)
                .join(" · ")}
              {typeof generated.generated_by === "object" && generated.generated_by && ` · model: ${(generated.generated_by as { model: string }).model}`}
            </div>
          )}
          {messages ? <MessageList messages={messages} empty="No supporting messages remain." /> : !error && <Loading />}
        </div>
      )}
    </div>
  );
}

// --- topics ------------------------------------------------------------------------------------

export function TopicTable({ topics, compact = false }: { topics: Topic[]; compact?: boolean }) {
  if (!topics.length) return <Empty>No topics yet. Topics appear once several members discuss the same thing.</Empty>;
  return (
    <table className="table">
      <thead>
        <tr>
          <th>Topic</th>
          <th>Trend</th>
          <th>Messages</th>
          {!compact && <th>24h / prev 24h</th>}
          <th>People</th>
          {!compact && <th>Last seen</th>}
          {!compact && <th>Confidence</th>}
        </tr>
      </thead>
      <tbody>
        {topics.map((t) => (
          <tr key={t.id}>
            <td>
              <Link to={`/telegram/topics/${t.id}`} className="link">
                {t.name}
              </Link>
              {!compact && t.description && <div className="muted small clamp">{t.description}</div>}
            </td>
            <td>
              <TrendBadge trend={t.trend} />
            </td>
            <td>{t.message_count}</td>
            {!compact && (
              <td>
                {t.count_24h} / {t.count_prev_24h}
              </td>
            )}
            <td>{t.participant_count}</td>
            {!compact && <td className="muted small nowrap">{relativeTime(t.last_seen_at)}</td>}
            {!compact && <td className="small">{Math.round(t.confidence * 100)}%</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// --- questions -----------------------------------------------------------------------------------

export function QuestionList({ questions, onChanged }: { questions: CommunityQuestion[]; onChanged?: () => void }) {
  if (!questions.length) return <Empty>No recurring questions yet. A question shows here once two or more members ask it.</Empty>;
  return (
    <ul className="question-list">
      {questions.map((q) => (
        <QuestionItem key={q.id} q={q} onChanged={onChanged} />
      ))}
    </ul>
  );
}

function QuestionItem({ q, onChanged }: { q: CommunityQuestion; onChanged?: () => void }) {
  const [open, setOpen] = useState<"messages" | "faq" | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [messages, setMessages] = useState<TelegramMessage[] | null>(null);
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState<unknown>(null);

  async function showMessages() {
    setOpen(open === "messages" ? null : "messages");
    if (!messages) setMessages(await api.questionMessages(q.id).catch((e) => (setError(e), [])));
  }
  async function faq(e: FormEvent) {
    e.preventDefault();
    try {
      await api.questionFaq(q.id, answer.trim());
      setOpen(null);
      onChanged?.();
    } catch (err) {
      setError(err);
    }
  }
  async function dismiss() {
    try {
      await api.updateQuestion(q.id, { status: "dismissed" });
      onChanged?.();
    } catch (err) {
      setError(err);
    }
  }

  return (
    <li className="question">
      <div className="question-head">
        <span className="question-text">“{q.text}”</span>
        {q.status === "answered" && <Badge tone="good">FAQ added</Badge>}
      </div>
      <div className="muted small">
        Asked <strong>{q.ask_count}</strong> times by {q.asker_count} {q.asker_count === 1 ? "member" : "members"} · last asked {relativeTime(q.last_asked_at)}
      </div>
      {onChanged && (
        <div className="btn-group question-actions">
          <button className="link-btn small" onClick={() => void showMessages()}>
            {open === "messages" ? "Hide messages" : "Show messages"}
          </button>
          {q.status === "open" && (
            <>
              <button className="link-btn small" onClick={() => setOpen(open === "faq" ? null : "faq")}>
                Write FAQ answer
              </button>
              <button
                className="link-btn small"
                onClick={() =>
                  void api.questionToOpportunity(q.id).then(
                    (r) => setNote(r.created ? "Content opportunity created: see Content › Opportunities." : "This question already has an opportunity."),
                    setError,
                  )
                }
              >
                Create content opportunity
              </button>
              <button className="link-btn small muted" onClick={() => void dismiss()}>
                Ignore
              </button>
            </>
          )}
          <FeedbackButtons kind="question" id={q.id} only={["important", "not_important"]} onDone={onChanged} edit={[{ key: "text", label: "Question", value: q.text }]} />
        </div>
      )}
      {note && <div className="muted small">{note}</div>}
      <ApiErrorBanner error={error} />
      {open === "messages" && (messages ? <MessageList messages={messages} /> : <Loading />)}
      {open === "faq" && (
        <form className="chat-input" onSubmit={faq}>
          <input className="input" placeholder="Your answer. It becomes an approved brand FAQ the agent can use." value={answer} onChange={(e) => setAnswer(e.target.value)} maxLength={4000} />
          <button className="btn btn-primary" disabled={answer.trim().length < 3}>
            Save FAQ
          </button>
        </form>
      )}
    </li>
  );
}

// --- summaries -------------------------------------------------------------------------------------

const SECTIONS: [string, string][] = [
  ["themes", "What the community is talking about"],
  ["emerging", "Becoming important"],
  ["questions", "Questions that keep appearing"],
  ["concerns", "Recurring concerns"],
  ["ideas", "Interesting ideas"],
  ["content_suggestions", "Possible content"],
];

export function SummaryView({ summary }: { summary: CommunitySummary }) {
  return (
    <div className="summary">
      <div className="summary-head">
        <strong>{summary.headline}</strong>
        <Badge tone="info">{label(summary.kind)}</Badge>
      </div>
      <div className="muted small">
        AI-generated by {summary.model} · {formatTime(summary.period_start)} → {formatTime(summary.period_end)} · {summary.message_count} messages from{" "}
        {summary.participant_count} members{summary.duration_ms ? ` · ${ms(summary.duration_ms)}` : ""}
      </div>
      {typeof summary.data.overview === "string" && <p>{summary.data.overview}</p>}
      {SECTIONS.map(([key, title]) => {
        const points = summary.data[key] as SummaryPoint[] | undefined;
        if (!Array.isArray(points) || !points.length) return null;
        return (
          <div key={key} className="summary-section">
            <div className="field-label">{title}</div>
            <ul>
              {points.map((p, i) => (
                <li key={i}>
                  {p.text} {p.message_ids.length > 0 && <span className="muted small">({p.message_ids.length} source{p.message_ids.length > 1 ? "s" : ""})</span>}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
      <Evidence kind="summary" id={summary.id} label={`Show the ${summary.message_count} source messages`} />
    </div>
  );
}

// --- opportunities ----------------------------------------------------------------------------------

const NEXT_ACTIONS: Partial<Record<OpportunityStatus, { status: OpportunityStatus; label: string }[]>> = {
  detected: [
    { status: "reviewing", label: "Start review" },
    { status: "approved", label: "Approve" },
    { status: "rejected", label: "Reject" },
  ],
  reviewing: [
    { status: "approved", label: "Approve" },
    { status: "rejected", label: "Reject" },
  ],
  approved: [{ status: "rejected", label: "Reject" }],
  rejected: [{ status: "detected", label: "Restore" }],
};

export const OPP_TONE: Record<OpportunityStatus, Tone> = {
  detected: "info",
  reviewing: "warn",
  approved: "good",
  drafted: "good",
  rejected: "neutral",
  published: "good",
};

export function OpportunityCard({ opp, onChanged }: { opp: Opportunity; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const ev = opp.evidence as Record<string, unknown>;

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
      setError(null);
      onChanged();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`opp ${opp.status === "rejected" ? "muted-card" : ""}`}>
      <div className="opp-head">
        <strong>{opp.title}</strong>
        <Badge tone={OPP_TONE[opp.status]}>{label(opp.status)}</Badge>
        <Badge tone={opp.urgency === "high" ? "warn" : "neutral"}>{opp.urgency} urgency</Badge>
        <span className="muted small">confidence {Math.round(opp.confidence * 100)}%</span>
        {opp.user_importance != null && <Badge tone={opp.user_importance > 0.5 ? "warn" : "neutral"}>{opp.user_importance > 0.5 ? "★ marked important" : "marked not important"}</Badge>}
      </div>
      <RankingPanel opp={opp} />
      <div className="opp-grid">
        <div>
          <div className="field-label">Why it matters</div>
          <p>{opp.why}</p>
        </div>
        <div>
          <div className="field-label">Suggested angle</div>
          <p>{opp.angle}</p>
        </div>
      </div>
      <div className="muted small">
        Community signal:{" "}
        {ev.signal === "topic"
          ? `${ev.messages} messages from ${ev.participants} members (${ev.messages_24h} in the last 24h, trend ${String(ev.trend).replace("_", " ")})`
          : `asked ${ev.asked} times by ${ev.askers} members`}
        {" · "}platforms: {opp.platforms.map((p) => CHANNEL_LABEL[p] ?? p).join(", ")} · {opp.model ? `idea by ${opp.model}` : "manual"}
        {opp.notes && ` · ${opp.notes}`}
      </div>
      <Evidence kind="opportunity" id={opp.id} />
      <ApiErrorBanner error={error} />
      <div className="btn-group opp-actions">
        {(NEXT_ACTIONS[opp.status] ?? []).map((a) => (
          <button
            key={a.status}
            className={`btn btn-sm ${a.status === "approved" ? "btn-primary" : ""}`}
            disabled={busy}
            title={a.status === "approved" || a.status === "rejected" ? "Your decision is remembered and used to rank future opportunities" : undefined}
            onClick={() =>
              void act(() =>
                // approve / reject go through feedback so Price Chat learns from them
                a.status === "approved" ? api.feedback("opportunity", opp.id, "approve") : a.status === "rejected" ? api.feedback("opportunity", opp.id, "reject") : api.updateOpportunity(opp.id, { status: a.status }),
              )
            }
          >
            {a.label}
          </button>
        ))}
        <FeedbackButtons
          kind="opportunity"
          id={opp.id}
          only={["important", "not_important"]}
          onDone={onChanged}
          edit={[
            { key: "title", label: "Title", value: opp.title },
            { key: "angle", label: "Suggested angle", value: opp.angle, multiline: true },
          ]}
        />
        {opp.content_id && (
          <Link className="link small" to="/content">
            View draft in Content →
          </Link>
        )}
      </div>
      {opp.status !== "rejected" && <DraftGenerator opp={opp} onCreated={onChanged} />}
    </div>
  );
}
