import { useCallback, useState } from "react";
import { api } from "../../api/client";
import { useApi } from "../../api/useApi";
import type { AgentTask, PromptPlatform } from "../../api/types";
import { ApiErrorBanner, Badge, Card, Loading, label, ms, statusTone } from "../../components/ui";

const TASKS: AgentTask[] = ["chat", "draft_post", "draft_reply", "summarize"];
const PLATFORMS: PromptPlatform[] = ["dashboard", "x", "telegram"];

export function CompiledPromptView() {
  const [task, setTask] = useState<AgentTask>("chat");
  const [platform, setPlatform] = useState<PromptPlatform>("dashboard");
  const [queryDraft, setQueryDraft] = useState("");
  const [query, setQuery] = useState("");
  const fetcher = useCallback(() => api.prompt(task, platform, query || undefined), [task, platform, query]);

  return (
    <Card title="Compiled system prompt (read-only)">
      <p className="muted small">
        This is exactly what the agent receives as its system prompt, built from the brand profile, goals, rules, task, platform and the memories
        retrieved for a message. Edit the sources on the other tabs, not this text.
      </p>
      <div className="prompt-controls">
        <select className="input select" value={task} onChange={(e) => setTask(e.target.value as AgentTask)} aria-label="Task">
          {TASKS.map((t) => (
            <option key={t} value={t}>
              Task: {label(t)}
            </option>
          ))}
        </select>
        <select className="input select" value={platform} onChange={(e) => setPlatform(e.target.value as PromptPlatform)} aria-label="Platform">
          {PLATFORMS.map((p) => (
            <option key={p} value={p}>
              Platform: {label(p)}
            </option>
          ))}
        </select>
        <form
          className="prompt-query"
          onSubmit={(e) => {
            e.preventDefault();
            setQuery(queryDraft.trim());
          }}
        >
          <input className="input" placeholder="Sample message, to preview memory retrieval" value={queryDraft} onChange={(e) => setQueryDraft(e.target.value)} />
          <button className="btn">Preview</button>
        </form>
      </div>
      <PromptBody key={`${task}-${platform}-${query}`} fetcher={fetcher} />
    </Card>
  );
}

function PromptBody({ fetcher }: { fetcher: () => ReturnType<typeof api.prompt> }) {
  const { data, failure } = useApi(fetcher);
  if (failure) return <ApiErrorBanner error={failure} />;
  if (!data) return <Loading />;
  return (
    <>
      <div className="chips">
        <span className="chip">
          {data.chars.toLocaleString()} chars · ~{data.approx_tokens.toLocaleString()} tokens
        </span>
        {data.sections.map((s) => (
          <span key={s.key} className="chip">
            {s.title ?? label(s.key)} · {s.chars}
          </span>
        ))}
      </div>
      {data.retrieval && (
        <div className="muted small retrieval-line">
          Retrieval ({data.retrieval.strategy}, {ms(data.retrieval.duration_ms)}):{" "}
          {data.retrieval.memories.length === 0
            ? "no memories matched"
            : data.retrieval.memories.map((m) => (
                <span key={m.memory.id} className="retrieved">
                  <Badge tone={statusTone[m.memory.layer]}>{m.memory.layer}</Badge> {m.memory.title ?? m.memory.content.slice(0, 40)} ({m.score.toFixed(2)})
                </span>
              ))}
        </div>
      )}
      <pre className="prompt">{data.text}</pre>
    </>
  );
}
