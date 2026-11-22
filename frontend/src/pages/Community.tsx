import { useCallback, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import type { CommunitySearch, SummaryKind, TelegramStatus } from "../api/types";
import { MessageList, QuestionList, SummaryView, TopicTable, TrendBadge } from "../components/community";
import { MergesTab, PilotTab, TelegramWizard, TraceView } from "./community/Pilot";
import { AccountSetup, HistoryImport, TelegramStatusCard } from "./community/Connection";
import { ApiErrorBanner, Badge, Card, Empty, Loading, PageHeader, Stat, Tabs, formatTime, label, relativeTime } from "../components/ui";
import { Fold } from "../components/hub";

type Tab = "overview" | "pilot" | "topics" | "merges" | "questions" | "summaries" | "search" | "messages" | "setup";
// Admin redesign (owner, 2026-11-22): six real tabs, no "More" menu; connecting Telegram, the pilot and topic merging fold away
// below them (?tab=setup|pilot|merges opens the Overview with that fold open).
const TABS: { key: Tab; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "questions", label: "Questions" },
  { key: "topics", label: "Topics" },
  { key: "messages", label: "Messages" },
  { key: "summaries", label: "Daily summaries" },
  { key: "search", label: "Search" },
];
const FOLDS: { key: Tab; label: string; summary: string }[] = [
  { key: "setup", label: "Connect Telegram", summary: "bot or account, chats to read" },
  { key: "pilot", label: "Pilot", summary: "statistics and message tracing" },
  { key: "merges", label: "Similar topics to merge", summary: "topics that look like one" },
];

const STATUS_TEXT: Record<TelegramStatus["status"], string> = {
  not_configured: "Not configured",
  disconnected: "Waiting for first sync",
  connected: "Connected (read-only)",
  error: "Connection problem",
};
const STATUS_TONE: Record<TelegramStatus["status"], "good" | "bad" | "warn" | "neutral"> = {
  not_configured: "neutral",
  disconnected: "warn",
  connected: "good",
  error: "bad",
};

export function CommunityPage() {
  const asked = new URLSearchParams(window.location.search).get("tab") as Tab | null;
  const [tab, setTab] = useState<Tab>(() => TABS.find((t) => t.key === asked)?.key ?? "overview");
  const [fold, setFold] = useState<Tab | undefined>(() => FOLDS.find((f) => f.key === asked)?.key);
  const go = (t: Tab) => { if (FOLDS.some((f) => f.key === t)) setFold(t); else setTab(t); };
  return (
    <>
      <PageHeader title="Telegram community" subtitle="What people are talking about in your Telegram chats. Read-only." />
      <Tabs tabs={TABS} value={tab} onChange={go} />
      {tab === "overview" && <OverviewTab go={go} />}
      {tab === "topics" && <TopicsTab />}
      {tab === "questions" && <QuestionsTab />}
      {tab === "summaries" && <SummariesTab />}
      {tab === "search" && <SearchTab />}
      {tab === "messages" && <MessagesTab />}
      {FOLDS.map((f) => (
        <Fold key={f.key} id={`tg-${f.key}`} title={f.label} summary={f.summary} focus={fold === f.key}>
          {f.key === "setup" && <SetupTab />}
          {f.key === "pilot" && <PilotTab />}
          {f.key === "merges" && <MergesTab />}
        </Fold>
      ))}
    </>
  );
}

function ConnectionCard({ status, onSynced }: { status: TelegramStatus; onSynced: () => void }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  async function sync() {
    setBusy(true);
    try {
      const r = await api.telegramSync();
      setResult(r.status === "success" ? `Synced: ${r.result.stored ?? 0} new message(s), ${r.result.ignored_unauthorised ?? 0} ignored from unauthorised chats.` : `${label(r.status)}: ${r.error ?? ""}`);
      setError(null);
      onSynced();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card
      title="Telegram connection"
      actions={
        status.configured && (
          <button className="btn btn-sm" onClick={() => void sync()} disabled={busy}>
            {busy ? "Syncing…" : "Sync now"}
          </button>
        )
      }
    >
      <dl className="kv">
        <dt>Status</dt>
        <dd>
          <Badge tone={STATUS_TONE[status.status]}>{STATUS_TEXT[status.status]}</Badge>
        </dd>
        <dt>Bot</dt>
        <dd>{status.bot_username ? `@${status.bot_username}` : "-"}{status.bot_name ? ` (${status.bot_name})` : ""}</dd>
        <dt>Authorised chats</dt>
        <dd>
          {status.sources.filter((s) => s.enabled).length} of {status.sources.length}
        </dd>
        <dt>Last successful sync</dt>
        <dd title={status.last_success_at ?? ""}>{relativeTime(status.last_success_at)}</dd>
        <dt>Messages ingested</dt>
        <dd>{status.messages_ingested}</dd>
        <dt>Ignored (not authorised)</dt>
        <dd>{status.ignored_updates}</dd>
        <dt>Last error</dt>
        <dd className={status.last_error ? "tone-bad" : "muted"}>
          {status.last_error ? `${status.last_error} (${relativeTime(status.last_error_at)})` : "none"}
        </dd>
      </dl>
      {!status.configured && <div className="banner banner-info">Telegram isn't set up yet. See the “Setup & chats” tab: paste the bot token and authorise a chat.</div>}
      {result && <div className="banner banner-good">{result}</div>}
      <ApiErrorBanner error={error} />
    </Card>
  );
}

function OverviewTab({ go }: { go: (t: Tab) => void }) {
  const { data, failure, reload } = useApi(api.communityOverview, 20000);
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  return (
    <>
      <div className="stats">
        <Stat label="Messages ingested" value={data.messages_total} hint={data.unprocessed ? `${data.unprocessed} waiting to be processed` : "all processed"} />
        <Stat label="Messages today" value={data.messages_today} />
        <Stat label="Active members today" value={data.active_members_today} hint={`${data.active_members_7d} in the last 7 days`} />
        <Stat label="Topics" value={data.top_topics.length} hint={`${data.trending_topics.length} trending`} />
        <Stat label="Memories to review" value={data.pending_memories} tone={data.pending_memories ? "warn" : "neutral"} hint={<Link className="link" to="/knowledge?tab=community">Community brain</Link>} />
      </div>
      <div className="grid-2">
        <ConnectionCard status={data.telegram} onSynced={() => void reload()} />
        <Card title="Trending now" actions={<button className="link-btn" onClick={() => go("topics")}>All topics</button>}>
          {data.trending_topics.length === 0 ? (
            <Empty>Nothing is trending. Topics are marked new or rising only when there is enough activity to compare.</Empty>
          ) : (
            <ul className="feed">
              {data.trending_topics.map((t) => (
                <li key={t.id}>
                  <TrendBadge trend={t.trend} />
                  <div className="feed-body">
                    <Link to={`/telegram/topics/${t.id}`} className="link">
                      {t.name}
                    </Link>
                    <span className="muted small">
                      {t.count_24h} messages in 24h (previous 24h: {t.count_prev_24h}) · {t.participant_count} members
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
      <div className="grid-2">
        <Card title="Top topics" actions={<button className="link-btn" onClick={() => go("topics")}>Details</button>}>
          <TopicTable topics={data.top_topics} compact />
        </Card>
        <Card title="Community questions" actions={<button className="link-btn" onClick={() => go("questions")}>All questions</button>}>
          <QuestionList questions={data.recurring_questions} />
        </Card>
      </div>
      <div className="grid-2">
        <Card title="Latest community summary" actions={<button className="link-btn" onClick={() => go("summaries")}>All summaries</button>}>
          {data.latest_summary ? <SummaryView summary={data.latest_summary} /> : <Empty>No summary yet. Summaries are generated every few hours once there is enough discussion.</Empty>}
        </Card>
        <div className="stack">
          <Card title="Content opportunities" actions={<Link className="link" to="/content?tab=opportunities">Review</Link>}>
            {data.opportunities.length === 0 ? (
              <Empty>No open opportunities.</Empty>
            ) : (
              <ul className="feed">
                {data.opportunities.map((o) => (
                  <li key={o.id}>
                    <Badge tone={o.urgency === "high" ? "warn" : "neutral"}>{o.urgency}</Badge>
                    <div className="feed-body">
                      <span>{o.title}</span>
                      <span className="muted small clamp">{o.angle}</span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Recent ingestion activity">
            {data.recent_activity.length === 0 ? (
              <Empty>No community activity yet.</Empty>
            ) : (
              <ul className="feed">
                {data.recent_activity.map((a) => (
                  <li key={a.id}>
                    <Badge tone={a.level === "error" ? "bad" : "neutral"}>{a.label}</Badge>
                    <div className="feed-body">
                      <span>{a.message}</span>
                      <span className="muted small">{formatTime(a.created_at)}</span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}

function TopicsTab() {
  const [sort, setSort] = useState<"importance" | "volume" | "recent" | "trending">("importance");
  const [status, setStatus] = useState("active");
  const fetcher = useCallback(() => api.topics(sort, status), [sort, status]);
  const { data, failure } = useApi(fetcher);
  return (
    <Card
      title="Topics"
      actions={
        <div className="btn-group">
          <select className="input select select-sm" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            <option value="active">Active</option>
            <option value="candidate">Emerging (too small yet)</option>
            <option value="archived">Archived</option>
          </select>
          <select className="input select select-sm" value={sort} onChange={(e) => setSort(e.target.value as typeof sort)} aria-label="Sort">
            <option value="importance">Most important</option>
            <option value="trending">Most active (24h)</option>
            <option value="volume">Most messages</option>
            <option value="recent">Most recent</option>
          </select>
        </div>
      }
    >
      <p className="muted small">
        Topics are groups of messages about the same thing, found by meaning. A topic becomes active once several members discuss it. Trends compare the last 24 hours with the 24 hours before and are shown only when there is enough activity.
      </p>
      <ApiErrorBanner error={failure} />
      {data ? <TopicTable topics={data} /> : <Loading />}
    </Card>
  );
}

function QuestionsTab() {
  const [all, setAll] = useState(false);
  const fetcher = useCallback(() => api.questions(!all), [all]);
  const { data, failure, reload } = useApi(fetcher);
  return (
    <Card
      title="Community questions"
      actions={
        <label className="row small">
          <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> include one-off questions
        </label>
      }
    >
      <p className="muted small">
        Questions are grouped by meaning, so different wordings count as the same question. Recurring questions are candidates for FAQ entries and content. Writing an FAQ answer adds an approved brand fact the agent can use.
      </p>
      <ApiErrorBanner error={failure} />
      {data ? <QuestionList questions={data} onChanged={() => void reload()} /> : <Loading />}
    </Card>
  );
}

function SummariesTab() {
  const [kind, setKind] = useState<SummaryKind | "">("");
  const fetcher = useCallback(() => api.summaries(kind || undefined), [kind]);
  const { data, failure, reload } = useApi(fetcher);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function generate(k: SummaryKind, hours: number) {
    setBusy(true);
    try {
      await api.createSummary({ kind: k, hours });
      setError(null);
      await reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card
      title="Community summaries"
      actions={
        <div className="btn-group">
          <select className="input select select-sm" value={kind} onChange={(e) => setKind(e.target.value as SummaryKind | "")} aria-label="Kind">
            <option value="">All</option>
            <option value="recent">Recent activity</option>
            <option value="daily">Daily</option>
            <option value="topic">Topic</option>
          </select>
          <button className="btn btn-sm" disabled={busy} onClick={() => void generate("recent", 6)}>
            {busy ? "Generating…" : "Summarise last 6h"}
          </button>
          <button className="btn btn-sm" disabled={busy} onClick={() => void generate("daily", 24)}>
            Summarise last 24h
          </button>
        </div>
      }
    >
      <p className="muted small">AI-generated interpretations of the discussion, with links to every source message. They are never quotes. Recent summaries use the fast model; daily and topic summaries use the strong model.</p>
      <ApiErrorBanner error={failure ?? error} />
      {!data ? (
        <Loading />
      ) : data.length === 0 ? (
        <Empty>No summaries yet.</Empty>
      ) : (
        <div className="stack">
          {data.map((s) => (
            <div key={s.id} className="summary-card">
              <SummaryView summary={s} />
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function SearchTab() {
  const [q, setQ] = useState("");
  const [answer, setAnswer] = useState(true);
  const [result, setResult] = useState<CommunitySearch | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  async function search(e: FormEvent) {
    e.preventDefault();
    if (q.trim().length < 2) return;
    setBusy(true);
    try {
      setResult(await api.communitySearch(q.trim(), answer));
      setError(null);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }
  const examples = ["What has the community said about Bitcoin this week?", "What questions are people asking about Price Chat?", "What were the biggest topics yesterday?"];
  return (
    <Card title="Search community knowledge">
      <form className="chat-input" onSubmit={search}>
        <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about what the community has discussed…" maxLength={500} />
        <label className="row small nowrap">
          <input type="checkbox" checked={answer} onChange={(e) => setAnswer(e.target.checked)} /> AI answer
        </label>
        <button className="btn btn-primary" disabled={busy || q.trim().length < 2}>
          {busy ? "Searching…" : "Search"}
        </button>
      </form>
      <div className="chips">
        {examples.map((x) => (
          <button key={x} className="chip chip-btn" onClick={() => setQ(x)}>
            {x}
          </button>
        ))}
      </div>
      <ApiErrorBanner error={error} />
      {result && (
        <div className="stack search-results">
          {result.window.label && <div className="muted small">Time range understood as: {result.window.label}</div>}
          {result.notes.map((n) => (
            <div key={n} className="banner banner-warn">{n}</div>
          ))}
          {result.answer && (
            <div className={`banner ${result.answer.sufficient ? "banner-info" : "banner-warn"}`}>
              <strong>AI answer (based only on the evidence below):</strong> {result.answer.answer}
              <div className="muted small">
                {result.answer.model ? `by ${result.answer.model} · ` : ""}cites {result.answer.cited.length} item(s): {result.answer.cited.join(", ") || "none"}
              </div>
            </div>
          )}
          {result.topics.length > 0 && (
            <div>
              <div className="field-label">Topics</div>
              <ul className="feed">
                {result.topics.map(({ topic, score }) => (
                  <li key={topic.id}>
                    <Badge>t{topic.id}</Badge>
                    <div className="feed-body">
                      <Link to={`/telegram/topics/${topic.id}`} className="link">{topic.name}</Link>
                      <span className="muted small">{topic.message_count} messages · match {score}</span>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {result.memories.length > 0 && (
            <div>
              <div className="field-label">Community memories</div>
              <ul className="feed">
                {result.memories.map(({ memory, score }) => (
                  <li key={memory.id}>
                    <Badge tone={memory.review_status === "approved" ? "good" : "warn"}>k{memory.id} · {memory.review_status}</Badge>
                    <div className="feed-body">
                      <span>{memory.title ? <strong>{memory.title}: </strong> : null}{memory.content}</span>
                      <span className="muted small">match {score}</span>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {result.summaries.length > 0 && (
            <div>
              <div className="field-label">Summaries</div>
              <ul className="feed">
                {result.summaries.map((s) => (
                  <li key={s.id}>
                    <Badge tone="info">s{s.id}</Badge>
                    <div className="feed-body">
                      <span>{s.headline}</span>
                      <span className="muted small">{formatTime(s.period_start)} → {formatTime(s.period_end)} · AI summary by {s.model}</span>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div>
            <div className="field-label">Messages ({result.messages.length})</div>
            {result.messages.length === 0 ? (
              <Empty>No matching messages.</Empty>
            ) : (
              <ul className="tg-msgs">
                {result.messages.map((h) => (
                  <li key={h.message.id} className="tg-msg">
                    <div className="tg-msg-head">
                      <Badge>m{h.message.id}</Badge>
                      <strong>{h.message.author_name ?? "member"}</strong>
                      <span className="muted small">{formatTime(h.message.sent_at)} · {h.source_name} · {h.reason}</span>
                    </div>
                    <div className="tg-msg-text">{h.message.text}</div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

function MessagesTab() {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [questionsOnly, setQuestionsOnly] = useState(false);
  const fetcher = useCallback(() => api.telegramMessages({ q: query || undefined, questions_only: questionsOnly }), [query, questionsOnly]);
  const { data, failure, reload } = useApi(fetcher);
  const [trace, setTrace] = useState<number | null>(null);
  return (
    <>
    {trace !== null && <TraceView messageId={trace} onClose={() => setTrace(null)} />}
    <Card
      title="Raw messages"
      actions={
        <form
          className="btn-group"
          onSubmit={(e) => {
            e.preventDefault();
            setQuery(q.trim());
          }}
        >
          <label className="row small">
            <input type="checkbox" checked={questionsOnly} onChange={(e) => setQuestionsOnly(e.target.checked)} /> questions only
          </label>
          <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter text" />
          <button className="btn btn-sm">Filter</button>
        </form>
      }
    >
      <p className="muted small">
        Messages from authorised chats exactly as received (newest first). Raw messages are kept separate from the agent's memory. Deleting a message also removes anything learned only from it.
      </p>
      <ApiErrorBanner error={failure} />
      {data ? <MessageList messages={data} onDeleted={() => void reload()} onTrace={(m) => setTrace(m.id)} empty="No messages yet." /> : <Loading />}
    </Card>
    </>
  );
}

function SetupTab() {
  const { data, failure, reload } = useApi(api.telegramStatus, 15000);
  const conn = useApi(api.telegramConnection, 15000);
  const [mode, setMode] = useState<"bot" | "account" | null>(null);
  const shown = mode ?? (conn.data?.account.configured && !conn.data?.bot.configured ? "account" : "bot");
  const [chatId, setChatId] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function run(fn: () => Promise<unknown>, success?: string) {
    try {
      await fn();
      setError(null);
      if (success) setNotice(success);
      await reload();
    } catch (e) {
      setError(e);
    }
  }
  async function add(e: FormEvent) {
    e.preventDefault();
    const id = Number(chatId.trim());
    if (!Number.isInteger(id)) return setError(new Error("Chat id must be a whole number, e.g. -1001234567890"));
    await run(() => api.addTelegramSource(id, name.trim()), "Chat authorised. New messages from it will be ingested.");
    setChatId("");
    setName("");
  }

  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  return (
    <>
      <ApiErrorBanner error={error} />
      {notice && <div className="banner banner-good">{notice}</div>}
      {conn.data && <TelegramStatusCard data={conn.data} />}
      <div className="row" style={{ justifyContent: "flex-start" }}>
        <span className="field-label">Connection type</span>
        <div className="btn-group">
          <button className={`btn btn-sm ${shown === "bot" ? "btn-primary" : ""}`} onClick={() => setMode("bot")}>
            Bot
          </button>
          <button className={`btn btn-sm ${shown === "account" ? "btn-primary" : ""}`} onClick={() => setMode("account")}>
            Telegram account
          </button>
        </div>
        <span className="muted small">
          {shown === "bot"
            ? "A bot you add to the group. Simple; only sees messages sent after it joins."
            : "Log in with a Telegram account that is already in the Price Chat groups. Can import recent history."}
        </span>
      </div>
      {shown === "bot" ? (
        <>
          <TelegramWizard onChanged={() => { void reload(); void conn.reload(); }} />
          <ConnectionCard status={data} onSynced={() => { void reload(); void conn.reload(); }} />
        </>
      ) : (
        <AccountSetup onChanged={() => { void reload(); void conn.reload(); }} />
      )}

      <Card title="Authorised chats (allow-list)">
        <p className="muted small">Price Chat processes messages <strong>only</strong> from chats on this list. Messages from any other chat are discarded on arrival.</p>
        {data.sources.length === 0 ? (
          <Empty>No chats authorised yet.</Empty>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Chat</th>
                <th>Via</th>
                <th>Chat id</th>
                <th>Enabled</th>
                <th>Ingest</th>
                <th>Added</th>
                <th>Last sync</th>
                <th>Messages</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.sources.map((s) => (
                <tr key={s.id}>
                  <td>
                    {s.display_name}
                    <div className="muted small">{s.chat_type ?? ""}{s.last_error ? ` · ${s.last_error}` : ""}</div>
                    {s.via === "account" && s.enabled && <HistoryImport source={s} onDone={() => void reload()} />}
                  </td>
                  <td>
                    <Badge>{s.via === "account" ? "account" : "bot"}</Badge>
                  </td>
                  <td className="mono small">{s.chat_id}</td>
                  <td>
                    <label className="toggle">
                      <input type="checkbox" checked={s.enabled} onChange={(e) => void run(() => api.updateTelegramSource(s.id, { enabled: e.target.checked }))} />
                      <span />
                    </label>
                  </td>
                  <td>
                    <label className="toggle">
                      <input type="checkbox" checked={s.ingestion_enabled} onChange={(e) => void run(() => api.updateTelegramSource(s.id, { ingestion_enabled: e.target.checked }))} />
                      <span />
                    </label>
                  </td>
                  <td className="muted small nowrap">{formatTime(s.created_at)}</td>
                  <td className="muted small nowrap">{relativeTime(s.last_sync_at)}</td>
                  <td>{s.message_count}</td>
                  <td>
                    <button
                      className="btn btn-ghost btn-sm tone-bad"
                      onClick={() => {
                        if (window.confirm(`Remove "${s.display_name}" and permanently delete ALL its messages, summaries, topics and memories learned only from it?`))
                          void run(async () => {
                            const r = await api.deleteTelegramSource(s.id);
                            setNotice(`Removed. Deleted ${r.messages} messages, ${r.summaries} summaries, ${r.memories} memories and ${r.opportunities} opportunities.`);
                          });
                      }}
                    >
                      Remove & delete data
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {shown === "bot" && (
        <form className="goal-add" onSubmit={add}>
          <input className="input mono" placeholder="Chat id, e.g. -1001234567890" value={chatId} onChange={(e) => setChatId(e.target.value)} />
          <input className="input" placeholder="Display name (optional)" value={name} onChange={(e) => setName(e.target.value)} maxLength={256} />
          <span />
          <button className="btn btn-primary" disabled={!chatId.trim()}>
            Authorise chat
          </button>
        </form>
        )}
      </Card>

      {shown === "bot" && (
      <Card title="Chats the bot can see (not authorised)">
        <p className="muted small">Chats where the bot received messages since the backend started. Their messages were discarded; only the chat name and id are shown so you can decide whether to authorise them.</p>
        {data.discovered_chats.length === 0 ? (
          <Empty>None seen yet. Add the bot to a group and send a message there.</Empty>
        ) : (
          <ul className="feed">
            {data.discovered_chats.map((d) => (
              <li key={d.chat_id}>
                <Badge>{d.chat_type}</Badge>
                <div className="feed-body">
                  <span>
                    {d.title} <span className="mono small muted">{d.chat_id}</span>
                  </span>
                  <span className="muted small">
                    {d.ignored_messages} message(s) discarded · seen {relativeTime(d.last_seen_at)}
                  </span>
                </div>
                <button className="btn btn-sm" onClick={() => void run(() => api.addTelegramSource(d.chat_id, d.title), `Authorised "${d.title}".`)}>
                  Authorise
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>
      )}
    </>
  );
}
