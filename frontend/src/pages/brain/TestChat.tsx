import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, explainError } from "../../api/client";
import type { AgentTask, ChatReply, PromptPlatform } from "../../api/types";
import { Badge, Card, Empty, ms, statusTone } from "../../components/ui";

const STORAGE_KEY = "pricechat.conversation";

const MODES: { key: string; label: string; task: AgentTask; platform: PromptPlatform }[] = [
  { key: "chat", label: "Chat with the team", task: "chat", platform: "dashboard" },
  { key: "x_post", label: "Draft an X post", task: "draft_post", platform: "x" },
  { key: "x_reply", label: "Draft an X reply", task: "draft_reply", platform: "x" },
  { key: "tg_post", label: "Draft a Telegram message", task: "draft_post", platform: "telegram" },
  { key: "summary", label: "Summarise text", task: "summarize", platform: "dashboard" },
];

interface Line {
  role: "user" | "assistant" | "error";
  text: string;
  reply?: ChatReply;
  meta?: string;
  error?: ReturnType<typeof explainError>;
}

function newConversationId(): string {
  return `chat-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

function loadConversationId(): string {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && /^[A-Za-z0-9_-]{1,64}$/.test(saved)) return saved;
  } catch {
    // storage unavailable: fall through
  }
  return newConversationId();
}

function saveConversationId(id: string) {
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // non-critical
  }
}

export function TestChat({ paused, onActivate }: { paused: boolean; onActivate: () => void }) {
  const [conversationId, setConversationId] = useState(loadConversationId);
  const [lines, setLines] = useState<Line[]>([]);
  const [input, setInput] = useState("");
  const [mode, setMode] = useState(MODES[0].key);
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const endRef = useRef<HTMLDivElement>(null);

  // Restore the conversation from the backend log.
  useEffect(() => {
    saveConversationId(conversationId);
    let cancelled = false;
    api
      .conversation(conversationId)
      .then((messages) => {
        if (cancelled) return;
        setLines(
          messages.map((m) => ({
            role: m.role,
            text: m.content,
            meta:
              m.role === "assistant"
                ? [m.model, ms(m.latency_ms), m.completion_tokens != null ? `${m.completion_tokens} tokens` : null].filter(Boolean).join(" · ")
                : undefined,
          })),
        );
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  // braces: newer browsers return a Promise from scrollIntoView, and an effect must return nothing or a cleanup function
  // (returning it crashed the AI Brain page with "destroy is not a function", found 2026-11-22)
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, [lines, busy]);

  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    const id = window.setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 500);
    return () => window.clearInterval(id);
  }, [busy]);

  async function send(e: FormEvent) {
    e.preventDefault();
    const message = input.trim();
    if (!message || busy) return;
    const m = MODES.find((x) => x.key === mode) ?? MODES[0];
    setInput("");
    setLines((l) => [...l, { role: "user", text: message }]);
    setBusy(true);
    setElapsed(0);
    try {
      const reply = await api.chat(message, conversationId, m.task, m.platform);
      setLines((l) => [...l, { role: "assistant", text: reply.reply, reply }]);
    } catch (err) {
      setLines((l) => [...l, { role: "error", text: "", error: explainError(err) }]);
    } finally {
      setBusy(false);
    }
  }

  function startNew() {
    setConversationId(newConversationId());
    setLines([]);
  }

  return (
    <Card
      title="Test chat"
      actions={
        <div className="btn-group">
          <span className="muted small mono" title="Conversation id">
            {conversationId}
          </span>
          <button className="btn btn-ghost btn-sm" onClick={startNew}>
            New conversation
          </button>
        </div>
      }
    >
      {paused && (
        <div className="banner banner-warn">
          The agent is paused, so it won't answer.{" "}
          <button className="link-btn" onClick={onActivate}>
            Activate it
          </button>
        </div>
      )}
      <div className="chat-log tall">
        {lines.length === 0 && <Empty>Ask Price Chat something. This chat is private: nothing here is ever posted.</Empty>}
        {lines.map((l, i) =>
          l.role === "error" && l.error ? (
            <div key={i} className={`bubble bubble-error kind-${l.error.kind}`}>
              <strong>{l.error.title}</strong>
              <div>{l.error.message}</div>
              <div className="bubble-meta">{ERROR_HELP[l.error.kind] ?? ""}</div>
            </div>
          ) : (
            <div key={i} className={`bubble bubble-${l.role}`}>
              <div className="bubble-text">{l.text}</div>
              {l.reply ? <ReplyDetails reply={l.reply} /> : l.meta && <div className="bubble-meta">{l.meta}</div>}
            </div>
          ),
        )}
        {busy && (
          <div className="bubble bubble-assistant typing">
            Thinking… {elapsed > 3 && <span className="muted">{elapsed}s{elapsed > 10 ? " (the model may be loading into GPU memory)" : ""}</span>}
          </div>
        )}
        <div ref={endRef} />
      </div>
      <form className="chat-input" onSubmit={send}>
        <select className="input select" value={mode} onChange={(e) => setMode(e.target.value)} aria-label="Mode">
          {MODES.map((m) => (
            <option key={m.key} value={m.key}>
              {m.label}
            </option>
          ))}
        </select>
        <input className="input" value={input} onChange={(e) => setInput(e.target.value)} placeholder="Message Price Chat…" disabled={busy} maxLength={8000} />
        <button className="btn btn-primary" disabled={busy || !input.trim()}>
          Send
        </button>
      </form>
    </Card>
  );
}

const ERROR_HELP: Record<string, string> = {
  backend_unavailable: "Start the backend: scripts\\run-backend.ps1",
  ai_unavailable: "The model service isn't reachable. For Ollama, make sure the Ollama app is running.",
  model_unavailable: "Pick an installed model in Settings, or install this one.",
  timeout: "The first request after a model switch can be slow while it loads. Try again.",
  paused: "Activate the agent at the top of this page.",
};

function ReplyDetails({ reply }: { reply: ChatReply }) {
  const [open, setOpen] = useState(false);
  const mems = reply.retrieval.memories;
  const tokens =
    reply.prompt_tokens != null || reply.completion_tokens != null
      ? `${reply.prompt_tokens ?? "?"} in / ${reply.completion_tokens ?? "?"} out tokens`
      : null;
  return (
    <div className="bubble-meta">
      <span className="mono">{reply.model}</span> · {ms(reply.latency_ms)}
      {tokens && ` · ${tokens}`}
      {reply.tokens_per_second ? ` · ${reply.tokens_per_second} tok/s` : ""} ·{" "}
      <button className="link-btn small" onClick={() => setOpen(!open)}>
        {mems.length} {mems.length === 1 ? "memory" : "memories"} used
      </button>
      {open && (
        <div className="used-memories">
          <div className="muted">
            Search: {reply.retrieval.strategy} in {ms(reply.retrieval.duration_ms)}
            {reply.retrieval.notes.map((n) => ` · ${n}`)}
          </div>
          {mems.length === 0 && <div className="muted">No stored knowledge matched this message.</div>}
          {mems.map((m) => (
            <div key={m.memory.id} className="used-memory">
              <Badge tone={statusTone[m.memory.layer]}>{m.memory.layer}</Badge> <strong>{m.memory.title ?? m.memory.content.slice(0, 60)}</strong>{" "}
              <span className="muted">
                score {m.score.toFixed(2)} · {m.reasons.join(" · ")}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
