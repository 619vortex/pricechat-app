import { useState } from "react";
import { api } from "../../api/client";
import type { AgentConfig } from "../../api/types";
import { ApiErrorBanner, Card, Field, ListEditor } from "../../components/ui";

export function IdentityRules({ config, onSaved }: { config: AgentConfig; onSaved: () => void }) {
  const [identity, setIdentity] = useState(config.identity);
  const [rules, setRules] = useState(config.behavior_rules);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const dirty = identity !== config.identity || JSON.stringify(rules) !== JSON.stringify(config.behavior_rules);

  async function save() {
    try {
      await api.updateAgent({ identity: identity.trim(), behavior_rules: rules });
      setError(null);
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
      onSaved();
    } catch (e) {
      setError(e);
    }
  }

  return (
    <Card
      title="Identity & behaviour rules"
      actions={
        <div className="btn-group">
          {saved && <span className="tone-good small">Saved</span>}
          <button className="btn btn-primary btn-sm" disabled={!dirty || !identity.trim()} onClick={() => void save()}>
            Save
          </button>
        </div>
      }
    >
      <ApiErrorBanner error={error} />
      <div className="form">
        <Field label="Identity" hint="Who the agent is and what it does. Opens the system prompt.">
          <textarea className="input textarea" rows={4} value={identity} onChange={(e) => setIdentity(e.target.value)} maxLength={4000} />
        </Field>
        <Field label="Behaviour rules" hint="How the agent should behave. One rule per line.">
          <ListEditor value={rules} onChange={setRules} rows={7} />
        </Field>
        <p className="muted small">
          Hard limits (no publishing, no inventing facts, no revealing credentials) are always appended by the system and can't be removed here.
        </p>
      </div>
    </Card>
  );
}
