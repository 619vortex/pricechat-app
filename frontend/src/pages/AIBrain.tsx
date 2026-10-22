import { useState } from "react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import type { AgentStatus } from "../api/types";
import { ModelStatusCard } from "../components/ModelStatusCard";
import { ApiErrorBanner, Badge, Card, Loading, PageHeader, Stat, Tabs, statusTone } from "../components/ui";
import { BrandProfileEditor } from "./brain/BrandProfileEditor";
import { CompiledPromptView } from "./brain/CompiledPromptView";
import { GoalsEditor } from "./brain/GoalsEditor";
import { IdentityRules } from "./brain/IdentityRules";
import { TestChat } from "./brain/TestChat";

type Tab = "chat" | "brand" | "goals" | "identity" | "prompt";
const TABS: { key: Tab; label: string }[] = [
  { key: "chat", label: "Test chat" },
  { key: "brand", label: "Brand profile" },
  { key: "goals", label: "Goals" },
  { key: "identity", label: "Identity & rules" },
  { key: "prompt", label: "Compiled prompt" },
];

export function AIBrain() {
  const brain = useApi(api.agent);
  const [tab, setTab] = useState<Tab>("chat");
  const [actionError, setActionError] = useState<unknown>(null);
  const b = brain.data;

  async function setStatus(status: AgentStatus) {
    try {
      await api.updateAgent({ status });
      setActionError(null);
      await brain.reload();
    } catch (e) {
      setActionError(e);
    }
  }

  return (
    <>
      <PageHeader title="AI Brain" subtitle="Price Chat's model, brand identity, goals and memory" />
      <ApiErrorBanner error={brain.failure ?? actionError} />
      {!b ? (
        !brain.failure && <Loading />
      ) : (
        <>
          <div className="stats">
            <Stat
              label="Agent status"
              value={<Badge tone={statusTone[b.config.status]}>{b.config.status}</Badge>}
              hint={
                <button className="btn btn-sm" onClick={() => void setStatus(b.config.status === "paused" ? "active" : "paused")}>
                  {b.config.status === "paused" ? "Activate" : "Pause"}
                </button>
              }
            />
            <Stat label="Model" value={<span className="mono">{b.effective_model}</span>} hint={`providers: ${b.available_providers.join(", ")}`} />
            <Stat
              label="Memory"
              value={b.memory.total}
              hint={`${Object.entries(b.memory.by_layer).map(([k, v]) => `${v} ${k}`).join(" · ") || "no knowledge yet"}`}
            />
            <Stat
              label="Search index"
              value={`${b.memory.embedded}/${b.memory.total}`}
              hint={b.memory.embedding_model ? `meaning search · ${b.memory.embedding_model}` : "keyword search only"}
              tone={b.memory.embedded < b.memory.total ? "warn" : "neutral"}
            />
            <Stat label="Conversations" value={b.memory.conversations} hint="logged" />
            <Stat
              label="Brand profile"
              value={`${b.brand_completeness}%`}
              hint="complete"
              tone={b.brand_completeness < 60 ? "warn" : "good"}
            />
          </div>

          <div className="grid-2 brain-top">
            <ModelStatusCard onChange={() => void brain.reload()} />
            <Card title={`Active goals (${b.active_goals.length})`} actions={<button className="link-btn" onClick={() => setTab("goals")}>Edit</button>}>
              {b.active_goals.length === 0 ? (
                <p className="empty">No active goals. Add some on the Goals tab.</p>
              ) : (
                <ol className="goal-list">
                  {b.active_goals.map((g) => (
                    <li key={g.id}>
                      <Badge tone={g.priority <= 1 ? "warn" : "neutral"}>P{g.priority}</Badge>
                      <div>
                        <strong>{g.name}</strong>
                        {g.description && <div className="muted small">{g.description}</div>}
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </Card>
          </div>

          <Tabs tabs={TABS} value={tab} onChange={setTab} />
          {tab === "chat" && <TestChat paused={b.config.status === "paused"} onActivate={() => void setStatus("active")} />}
          {tab === "brand" && <BrandProfileEditor onSaved={() => void brain.reload()} />}
          {tab === "goals" && <GoalsEditor onChanged={() => void brain.reload()} />}
          {tab === "identity" && <IdentityRules config={b.config} onSaved={() => void brain.reload()} />}
          {tab === "prompt" && <CompiledPromptView />}
        </>
      )}
    </>
  );
}
