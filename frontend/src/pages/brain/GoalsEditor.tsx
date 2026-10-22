import { useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { useApi } from "../../api/useApi";
import type { Goal } from "../../api/types";
import { ApiErrorBanner, Card, Empty, Loading } from "../../components/ui";

const PRIORITIES = [1, 2, 3, 4, 5];

export function GoalsEditor({ onChanged }: { onChanged: () => void }) {
  const goals = useApi(api.goals);
  const [error, setError] = useState<unknown>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState(3);

  async function run(action: () => Promise<unknown>) {
    try {
      await action();
      setError(null);
      await goals.reload();
      onChanged();
    } catch (e) {
      setError(e);
    }
  }

  async function add(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    await run(() => api.createGoal({ name: name.trim(), description: description.trim(), priority, enabled: true }));
    setName("");
    setDescription("");
    setPriority(3);
  }

  return (
    <Card title="Agent goals">
      <p className="muted small">Enabled goals are given to the agent in priority order (P1 = most important). Disabled goals are kept but ignored.</p>
      <ApiErrorBanner error={goals.failure ?? error} />
      {!goals.data ? (
        <Loading />
      ) : goals.data.length === 0 ? (
        <Empty>No goals yet.</Empty>
      ) : (
        <div className="goal-table">
          {goals.data.map((g) => (
            <GoalRow key={`${g.id}-${g.updated_at}`} goal={g} onSave={(c) => run(() => api.updateGoal(g.id, c))} onDelete={() => run(() => api.deleteGoal(g.id))} />
          ))}
        </div>
      )}
      <form className="goal-add" onSubmit={add}>
        <input className="input" placeholder="New goal name" value={name} onChange={(e) => setName(e.target.value)} maxLength={160} />
        <input className="input" placeholder="Description (optional)" value={description} onChange={(e) => setDescription(e.target.value)} maxLength={2000} />
        <select className="input select" value={priority} onChange={(e) => setPriority(Number(e.target.value))} aria-label="Priority">
          {PRIORITIES.map((p) => (
            <option key={p} value={p}>
              P{p}
            </option>
          ))}
        </select>
        <button className="btn btn-primary" disabled={!name.trim()}>
          Add goal
        </button>
      </form>
    </Card>
  );
}

function GoalRow({ goal, onSave, onDelete }: { goal: Goal; onSave: (c: Partial<Goal>) => Promise<void>; onDelete: () => Promise<void> }) {
  const [name, setName] = useState(goal.name);
  const [description, setDescription] = useState(goal.description);
  const dirty = name !== goal.name || description !== goal.description;

  return (
    <div className={`goal-row ${goal.enabled ? "" : "disabled"}`}>
      <label className="toggle" title={goal.enabled ? "Enabled" : "Disabled"}>
        <input type="checkbox" checked={goal.enabled} onChange={(e) => void onSave({ enabled: e.target.checked })} />
        <span />
      </label>
      <select className="input select select-sm" value={goal.priority} onChange={(e) => void onSave({ priority: Number(e.target.value) })} aria-label="Priority">
        {PRIORITIES.map((p) => (
          <option key={p} value={p}>
            P{p}
          </option>
        ))}
      </select>
      <div className="goal-fields">
        <input className="input goal-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={160} />
        <input className="input" value={description} placeholder="Description" onChange={(e) => setDescription(e.target.value)} maxLength={2000} />
      </div>
      <div className="btn-group">
        <button className="btn btn-sm" disabled={!dirty || !name.trim()} onClick={() => void onSave({ name: name.trim(), description: description.trim() })}>
          Save
        </button>
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => {
            if (window.confirm(`Delete goal "${goal.name}"?`)) void onDelete();
          }}
          aria-label="Delete goal"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
