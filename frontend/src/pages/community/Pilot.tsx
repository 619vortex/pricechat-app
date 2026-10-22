import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { useApi } from "../../api/useApi";
import type { MessageTrace, TelegramSetup } from "../../api/types";
import { MessageItem } from "../../components/community";
import { ApiErrorBanner, Badge, Card, Empty, Loading, Stat, label, relativeTime } from "../../components/ui";

const JOB_LABEL: Record<string, string> = {
  "telegram.ingest": "Receive messages",
  "community.process": "Process messages",
  "community.topics": "Update topics",
  "community.summary.recent": "Recent summary",
  "community.memories": "Learn community memories",
  "content.opportunities": "Find content opportunities",
};

/** One-page Telegram setup: paste the token, authorise a chat, start receiving. */
export function TelegramWizard({ onChanged }: { onChanged?: () => void }) {
  const { data, failure, reload, setData } = useApi(api.telegramSetup, 15000);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function run(fn: () => Promise<TelegramSetup | unknown>, success: string) {
    setBusy(true);
    try {
      const r = await fn();
      if (r && typeof r === "object" && "steps" in r) setData(r as TelegramSetup);
      else await reload();
      setNotice(success);
      setError(null);
      onChanged?.();
    } catch (e) {
      setError(e);
      setNotice(null);
    } finally {
      setBusy(false);
    }
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    const value = token.trim();
    setToken(""); // never keep the token in the page longer than needed
    await run(() => api.saveTelegramToken(value), "Token verified with Telegram and saved encrypted on this computer.");
  }

  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  return (
    <Card
      title="Connect Telegram (step by step)"
      actions={
        data.token.configured && (
          <div className="btn-group">
            <button className="btn btn-sm" disabled={busy} onClick={() => void run(() => api.telegramVerify(), "Telegram accepted the bot token (connection tested).")}>
              Test connection
            </button>
            <button className="btn btn-sm" disabled={busy} onClick={() => void run(() => api.refreshTelegramChats(), "Checked Telegram for chats the bot is in.")}>
              Refresh chats
            </button>
            {data.ingestion_enabled ? (
              <button className="btn btn-sm" disabled={busy} onClick={() => void run(() => api.setTelegramIngestion(false), "Ingestion paused. Nothing new will be read until you start it again.")}>
                Pause ingestion
              </button>
            ) : (
              <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void run(() => api.setTelegramIngestion(true), "Ingestion started.")}>
                Start ingestion
              </button>
            )}
          </div>
        )
      }
    >
      <ol className="wizard">
        {data.steps.map((s, i) => (
          <li key={s.key}>
            <span className={`wizard-dot ${s.done ? "done" : s.done === null ? "unknown" : ""}`} title={s.done === null ? "Can't be checked automatically" : s.done ? "Done" : "To do"}>
              {s.done ? "✓" : i + 1}
            </span>
            <div>
              <strong>{s.title}</strong>
              <div className="muted small">{s.detail}</div>
              {s.key === "token" && (
                <div className="stack" style={{ marginTop: 8 }}>
                  {data.token.configured ? (
                    <div className="row small">
                      <Badge tone="good">Token saved</Badge>
                      <span className="muted">
                        {data.bot_username ? `bot @${data.bot_username} · ` : ""}
                        {data.token.source === "env" ? "from .env" : `saved ${relativeTime(data.token.saved_at)}`} · the token is never shown again
                      </span>
                      {data.token.source === "dashboard" && (
                        <button
                          className="link-btn tone-bad"
                          disabled={busy}
                          onClick={() => {
                            if (window.confirm("Remove the saved bot token? Price Chat will stop receiving Telegram messages.")) void run(() => api.removeTelegramToken(), "Token removed.");
                          }}
                        >
                          Remove token
                        </button>
                      )}
                    </div>
                  ) : null}
                  <form className="row" onSubmit={save}>
                    <input
                      className="input secret-input"
                      type="password"
                      autoComplete="off"
                      spellCheck={false}
                      placeholder={data.token.configured ? "Paste a new token to replace it" : "Paste the token from @BotFather, e.g. 123456789:AA…"}
                      value={token}
                      onChange={(e) => setToken(e.target.value)}
                    />
                    <button className="btn btn-primary" disabled={busy || token.trim().length < 20}>
                      {busy ? "Checking…" : "Save & test"}
                    </button>
                  </form>
                </div>
              )}
              {s.key === "privacy" && data.privacy_disabled === false && (
                <div className="banner banner-warn">Privacy mode is still on: the bot will only see commands and replies to it. Turn it off in @BotFather, then remove and re-add the bot to the group.</div>
              )}
            </div>
          </li>
        ))}
      </ol>
      {notice && <div className="banner banner-good">{notice}</div>}
      <ApiErrorBanner error={error} />
      <p className="muted small">
        The token is stored encrypted with Windows (DPAPI) on this computer. It is never sent back to the browser, written to logs or given to the AI. Adding the bot to a group does nothing by itself: only chats you authorise below are read.
      </p>
    </Card>
  );
}

export function PilotTab() {
  const { data, failure, reload } = useApi(api.pilot, 10000);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function runJob(name: string) {
    setBusy(name);
    try {
      const r = await api.runJob(name);
      setResult(`${JOB_LABEL[name] ?? name}: ${label(r.status)}${r.error ? ` (${r.error})` : ""}`);
      setError(null);
      await reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  async function setIngestion(enabled: boolean) {
    setBusy("ingestion");
    try {
      await api.setTelegramIngestion(enabled);
      setResult(enabled ? "Ingestion started." : "Ingestion paused. Nothing new is read until you start it again.");
      setError(null);
      await reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const connText: Record<string, [string, "good" | "warn" | "bad" | "neutral"]> = {
    connected: ["Connected (read-only)", "good"],
    disconnected: ["Waiting for first sync", "warn"],
    error: ["Telegram API error", "bad"],
    not_configured: ["Not set up", "neutral"],
  };
  const [ct, ctone] = connText[data.telegram_status] ?? [data.telegram_status, "neutral"];
  return (
    <>
      {data.telegram_error && data.telegram_status === "error" && <div className="banner banner-error">Telegram API error: {data.telegram_error}</div>}
      {!data.ingestion_enabled && <div className="banner banner-warn">Ingestion is paused.</div>}
      <Card
        title="Live status"
        actions={
          <div className="btn-group">
            {data.ingestion_enabled ? (
              <button className="btn btn-sm" disabled={busy !== null} onClick={() => void setIngestion(false)}>Pause ingestion</button>
            ) : (
              <button className="btn btn-sm btn-primary" disabled={busy !== null} onClick={() => void setIngestion(true)}>Start ingestion</button>
            )}
            <button className="btn btn-sm" disabled={busy !== null} onClick={() => void runJob("community.process")}>
              {busy === "community.process" ? "Processing…" : "Process now"}
            </button>
          </div>
        }
      >
        <dl className="kv">
          <dt>Connection</dt>
          <dd><Badge tone={ctone}>{ct}</Badge></dd>
          <dt>Authorised chats</dt>
          <dd>{data.authorised_chats}</dd>
          <dt>Last message received</dt>
          <dd>{relativeTime(data.last_message_at)}</dd>
          <dt>Last processing run</dt>
          <dd>{relativeTime(data.last_processed_at)}</dd>
        </dl>
      </Card>
      <div className="stats">
        <Stat label="Messages today" value={data.messages_today} hint={`${data.messages_total} in total`} />
        <Stat label="Processed" value={data.messages_processed} hint={data.messages_unprocessed ? `${data.messages_unprocessed} waiting` : "all processed"} tone={data.messages_unprocessed ? "warn" : "neutral"} />
        <Stat label="Ignored" value={data.messages_ignored} hint="from chats you have not authorised (discarded)" />
        <Stat label="Topics" value={data.topics} hint={`${data.questions} questions`} />
        <Stat label="Community memories" value={data.community_memories} hint={<Link className="link" to="/knowledge?tab=community">{data.pending_memories} to review</Link>} tone={data.pending_memories ? "warn" : "neutral"} />
        <Stat label="Open opportunities" value={data.opportunities} hint={<Link className="link" to="/content?tab=opportunities">review</Link>} />
      </div>
      <Card title="Processing pipeline">
        <p className="muted small">These run automatically in the background. Use “Run now” to process immediately; background work always waits for your chats with the agent.</p>
        {result && <div className="banner banner-info">{result}</div>}
        <ApiErrorBanner error={error} />
        <table className="table">
          <thead>
            <tr>
              <th>Step</th>
              <th>Status</th>
              <th>Last run</th>
              <th>Next run</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.jobs.map((j) => (
              <tr key={j.name}>
                <td>
                  {JOB_LABEL[j.name] ?? j.name}
                  <div className="muted small mono">{j.name}</div>
                  {j.last_error && <div className="tone-bad small">{j.last_error}</div>}
                </td>
                <td>
                  <Badge tone={!j.enabled ? "neutral" : j.status === "failed" ? "bad" : j.status === "running" ? "info" : j.status === "success" ? "good" : "neutral"}>
                    {!j.enabled ? "paused" : j.status ?? "not run yet"}
                  </Badge>
                </td>
                <td className="muted small nowrap">{relativeTime(j.last_run_at)}</td>
                <td className="muted small nowrap">{j.enabled ? relativeTime(j.next_run_at).replace(" ago", "") : "-"}</td>
                <td>
                  <button className="btn btn-sm" disabled={busy !== null} onClick={() => void runJob(j.name)}>
                    {busy === j.name ? "Running…" : "Run now"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  );
}

/** raw message → classification → topic/question → summaries, memories and opportunities derived from it. */
export function TraceView({ messageId, onClose }: { messageId: number; onClose: () => void }) {
  const [trace, setTrace] = useState<MessageTrace | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    setTrace(null);
    setError(null);
    api.messageTrace(messageId).then(setTrace, setError);
  }, [messageId]);
  return (
    <Card title={`How message m${messageId} was used`} actions={<button className="link-btn" onClick={onClose}>Close</button>}>
      <ApiErrorBanner error={error} />
      {!trace ? (
        !error && <Loading />
      ) : (
        <dl className="kv">
          <dt>Message</dt>
          <dd>
            <ul className="tg-msgs">
              <MessageItem m={trace.message} />
            </ul>
            <span className="muted small">from {trace.source_name ?? "unknown chat"}</span>
          </dd>
          <dt>Classified as</dt>
          <dd>{trace.message.category ?? <span className="muted">not processed yet</span>}</dd>
          <dt>Topic</dt>
          <dd>{trace.topic ? <Link className="link" to={`/telegram/topics/${trace.topic.id}`}>{trace.topic.name}</Link> : <span className="muted">none</span>}</dd>
          <dt>Question</dt>
          <dd>{trace.question ? `“${trace.question.text}” (asked ${trace.question.ask_count ?? "?"} times)` : <span className="muted">none</span>}</dd>
          <dt>Summaries</dt>
          <dd>{trace.summaries.length ? trace.summaries.map((s) => <div key={s.id} className="small">s{s.id} · {s.kind} · {s.headline} <span className="muted">({s.model})</span></div>) : <span className="muted">none</span>}</dd>
          <dt>Memories</dt>
          <dd>{trace.memories.length ? trace.memories.map((m) => <div key={m.id} className="small">k{m.id} · {m.review_status} · {m.content}</div>) : <span className="muted">none</span>}</dd>
          <dt>Opportunities</dt>
          <dd>{trace.opportunities.length ? trace.opportunities.map((o) => <div key={o.id} className="small">#{o.id} · {o.status} · {o.title}</div>) : <span className="muted">none</span>}</dd>
        </dl>
      )}
    </Card>
  );
}

export function MergesTab() {
  const { data, failure, reload } = useApi(() => api.mergeSuggestions());
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  async function act(fn: () => Promise<unknown>, msg: string) {
    try {
      await fn();
      setNotice(msg);
      setError(null);
      await reload();
    } catch (e) {
      setError(e);
    }
  }
  return (
    <Card
      title="Possible duplicate topics"
      actions={
        <button className="btn btn-sm" onClick={() => void act(() => api.runJob("topic.merges"), "Checked for similar topics.")}>
          Check now
        </button>
      }
    >
      <p className="muted small">
        Price Chat suggests topics that look like the same thing (for example “BTC” and “Bitcoin price”). Nothing is merged unless you say so. Merging keeps every message; the other name is kept as an alias.
      </p>
      {notice && <div className="banner banner-good">{notice}</div>}
      <ApiErrorBanner error={failure ?? error} />
      {!data ? (
        <Loading />
      ) : data.length === 0 ? (
        <Empty>No merge suggestions.</Empty>
      ) : (
        <div className="stack">
          {data.map((s) => (
            <div key={s.id} className="opp">
              <div className="opp-head">
                <Link className="link" to={`/telegram/topics/${s.topic_a.id}`}>{s.topic_a.name}</Link>
                <span className="muted">({s.topic_a.message_count} msgs)</span>
                <span>↔</span>
                <Link className="link" to={`/telegram/topics/${s.topic_b.id}`}>{s.topic_b.name}</Link>
                <span className="muted">({s.topic_b.message_count} msgs)</span>
                <Badge tone={s.verdict === "same" ? "warn" : "neutral"}>{s.verdict === "same" ? "likely the same" : s.verdict}</Badge>
              </div>
              <div className="muted small">
                {s.reason} · similarity {Math.round(s.similarity * 100)}% · {s.shared_participants} shared members{s.judged_by ? ` · judged by ${s.judged_by}` : ""}
              </div>
              <div className="btn-group">
                <button className="btn btn-sm btn-primary" onClick={() => void act(() => api.mergeTopics(s.id, "larger"), "Topics merged.")}>
                  Merge (keep larger)
                </button>
                <button className="btn btn-sm" onClick={() => void act(() => api.mergeTopics(s.id, "a"), `Merged into “${s.topic_a.name}”.`)}>
                  Keep “{s.topic_a.name}”
                </button>
                <button className="btn btn-sm" onClick={() => void act(() => api.mergeTopics(s.id, "b"), `Merged into “${s.topic_b.name}”.`)}>
                  Keep “{s.topic_b.name}”
                </button>
                <button className="btn btn-sm btn-ghost" onClick={() => void act(() => api.keepTopicsSeparate(s.id), "Kept separate. This pair won't be suggested again.")}>
                  Keep separate
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
