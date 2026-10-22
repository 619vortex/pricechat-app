import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import type { ActionLimit, ActionMode, SafetySettings as SafetySettingsData } from "../api/types";
import { JobsCard } from "../components/JobsCard";
import { ModelStatusCard } from "../components/ModelStatusCard";
import { RoutingCard } from "../components/RoutingCard";
import { PsaConnectionCard } from "./pokemon/psa";
import { Advanced, Badge, Card, ErrorBanner, Loading, PageHeader, Tabs, label } from "../components/ui";

type Tab = "safety" | "jobs" | "ai" | "psa";

export function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const tab = (["safety", "jobs", "ai", "psa"].includes(params.get("tab") ?? "") ? params.get("tab") : "safety") as Tab;
  const { data, error, reload } = useApi(api.safety);
  const [actionError, setActionError] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>) {
    try {
      await action();
      setActionError(null);
      await reload();
    } catch (e) {
      setActionError((e as Error).message);
    }
  }

  return (
    <>
      <PageHeader title="Settings" subtitle="The emergency stop, background jobs and AI models." />
      <Tabs<Tab> value={tab} onChange={(t) => setParams(t === "safety" ? {} : { tab: t })} tabs={[
        { key: "safety", label: "Emergency stop & limits" }, { key: "jobs", label: "Background jobs" }, { key: "ai", label: "AI models" },
        { key: "psa", label: "PSA (Pokémon grading)", more: true },
      ]} />
      {tab === "safety" && (!data ? (error ? <ErrorBanner message={error} /> : <Loading />) : <SafetySettings data={data} run={run} />)}
      {tab === "jobs" && <JobsCard />}
      {tab === "ai" && (
        <>
          <ModelStatusCard editable />
          <Advanced label="Which AI model does which task"><RoutingCard /></Advanced>
        </>
      )}
      {tab === "psa" && <PsaConnectionCard />}
      <ErrorBanner message={actionError} />
    </>
  );
}

function SafetySettings({ data, run }: { data: SafetySettingsData; run: (a: () => Promise<unknown>) => Promise<void> }) {
  const { policy } = data;
  return (
    <>
      <div className="grid-2">
        <Card title="Emergency stop">
          <p className="muted">One click stops every post, reply and other action on social media, whatever else is set.</p>
          <button
            className={`btn btn-kill ${policy.kill_switch ? "engaged" : ""}`}
            onClick={() => void run(() => api.updateSafety({ kill_switch: !policy.kill_switch }))}
          >
            {policy.kill_switch ? "Emergency stop is ON: click to turn off" : "Turn on emergency stop"}
          </button>
        </Card>

        <Card title="How actions go out">
          <div className="row">
            <span className="muted">Ask me first, or go automatically</span>
            <div className="btn-group">
              {(["approval", "automatic"] as ActionMode[]).map((m) => (
                <button key={m} className={`btn btn-sm ${policy.mode === m ? "btn-primary" : ""}`} onClick={() => void run(() => api.updateSafety({ mode: m }))}>
                  {label(m)}
                </button>
              ))}
            </div>
          </div>
          <div className="row">
            <span className="muted">Outbound actions</span>
            <Badge tone={policy.outbound_enabled ? "warn" : "neutral"}>{policy.outbound_enabled ? "Enabled" : "Disabled (not built yet)"}</Badge>
          </div>
          <NumberRow label="Most actions per hour" value={policy.max_actions_per_hour} onSave={(v) => run(() => api.updateSafety({ max_actions_per_hour: v }))} />
          <NumberRow label="Most actions per day" value={policy.max_actions_per_day} onSave={(v) => run(() => api.updateSafety({ max_actions_per_day: v }))} />
        </Card>
      </div>

      <p className="muted small">{data.outbound_note}</p>
      <Advanced label="Limits for each kind of action">
      <Card title="Limits for each action">
        <table className="table">
          <thead>
            <tr><th>Platform</th><th>Action</th><th>Enabled</th><th>Mode</th><th>Per hour</th><th>Per day</th></tr>
          </thead>
          <tbody>
            {data.limits.map((l) => (
              <LimitRow key={l.id} limit={l} onChange={(changes) => run(() => api.updateLimit(l.id, changes))} />
            ))}
          </tbody>
        </table>
      </Card>
      </Advanced>
    </>
  );
}

function NumberRow({ label: text, value, onSave }: { label: string; value: number; onSave: (v: number) => Promise<void> }) {
  const [draft, setDraft] = useState(String(value));
  const dirty = draft !== String(value);
  return (
    <div className="row">
      <span className="muted">{text}</span>
      <div className="btn-group">
        <input className="input input-num" type="number" min={0} value={draft} onChange={(e) => setDraft(e.target.value)} />
        <button className="btn btn-sm" disabled={!dirty || draft === ""} onClick={() => void onSave(Number(draft))}>Save</button>
      </div>
    </div>
  );
}

function LimitRow({ limit, onChange }: { limit: ActionLimit; onChange: (c: Partial<ActionLimit>) => Promise<void> }) {
  const numberCell = (field: "max_per_hour" | "max_per_day") => (
    <input
      className="input input-num"
      type="number"
      min={0}
      defaultValue={limit[field]}
      onBlur={(e) => {
        const v = Number(e.target.value);
        if (e.target.value !== "" && v !== limit[field]) void onChange({ [field]: v });
      }}
    />
  );
  return (
    <tr>
      <td>{label(limit.platform)}</td>
      <td>{label(limit.action)}</td>
      <td>
        <label className="toggle">
          <input type="checkbox" checked={limit.enabled} onChange={(e) => void onChange({ enabled: e.target.checked })} />
          <span />
        </label>
      </td>
      <td>
        <select
          className="input select select-sm"
          value={limit.mode ?? ""}
          onChange={(e) => void onChange({ mode: (e.target.value || null) as ActionMode | null })}
        >
          <option value="">Inherit</option>
          <option value="approval">Approval</option>
          <option value="automatic">Automatic</option>
        </select>
      </td>
      <td>{numberCell("max_per_hour")}</td>
      <td>{numberCell("max_per_day")}</td>
    </tr>
  );
}
