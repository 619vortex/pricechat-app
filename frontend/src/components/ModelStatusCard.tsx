import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import type { ModelTestResult } from "../api/types";
import { ApiErrorBanner, Badge, Card, Loading, modelStateLabel, ms, relativeTime, statusTone } from "./ui";

/** Model status, Test Model button and (optionally) model selection. Provider-neutral. */
export function ModelStatusCard({ editable = false, onChange }: { editable?: boolean; onChange?: () => void }) {
  const status = useApi(api.model, 15000);
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<ModelTestResult | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [modelDraft, setModelDraft] = useState("");
  const [tempDraft, setTempDraft] = useState("");

  const s = status.data;
  useEffect(() => {
    if (s) {
      setModelDraft(s.model);
      setTempDraft(String(s.temperature));
    }
  }, [s?.model, s?.temperature]);

  async function runTest() {
    setTesting(true);
    setTest(null);
    setActionError(null);
    try {
      setTest(await api.testModel());
    } catch (e) {
      setActionError(e);
    } finally {
      setTesting(false);
      await status.reload();
    }
  }

  async function save() {
    setActionError(null);
    try {
      const temperature = Number(tempDraft);
      status.setData(
        await api.updateModel({
          model_name: modelDraft === s?.default_model ? "" : modelDraft,
          temperature: Number.isFinite(temperature) ? temperature : undefined,
        }),
      );
      setTest(null);
      onChange?.();
    } catch (e) {
      setActionError(e);
    }
  }

  if (!s) {
    return (
      <Card title="Model status">
        {status.failure ? <ApiErrorBanner error={status.failure} /> : <Loading />}
      </Card>
    );
  }

  const dirty = modelDraft !== s.model || tempDraft !== String(s.temperature);
  const options = Array.from(new Set([s.default_model, s.model, ...s.installed_models.filter((m) => !m.includes("embed"))]));

  return (
    <Card
      title="Model status"
      actions={
        <button className="btn btn-primary" onClick={() => void runTest()} disabled={testing}>
          {testing ? "Testing…" : "Test Model"}
        </button>
      }
    >
      <ApiErrorBanner error={status.failure} />
      <dl className="kv">
        <dt>Provider</dt>
        <dd>{label(s.provider)}</dd>
        <dt>Model</dt>
        <dd className="mono">
          {s.model} {s.model_overridden && <span className="muted small">(override; default {s.default_model})</span>}
        </dd>
        <dt>Status</dt>
        <dd>
          <Badge tone={statusTone[s.status]}>{modelStateLabel[s.status]}</Badge>
        </dd>
        <dt>Endpoint</dt>
        <dd className="mono small">{s.endpoint ?? "-"}</dd>
        <dt>Latency</dt>
        <dd>
          {ms(s.last_inference_latency_ms)} <span className="muted small">last answer</span> · {ms(s.health_latency_ms)}{" "}
          <span className="muted small">status check</span>
        </dd>
        <dt>Last successful inference</dt>
        <dd title={s.last_success_at ?? ""}>
          {relativeTime(s.last_success_at)}
          {s.last_success_model && <span className="muted small"> · {s.last_success_model}</span>}
        </dd>
        <dt>Memory search</dt>
        <dd className="small">
          {s.embedding_model ? (
            <>
              meaning-based via <span className="mono">{s.embedding_model}</span>
            </>
          ) : (
            "keywords only"
          )}
        </dd>
      </dl>

      {s.status !== "online" && s.detail && <div className="banner banner-warn">{s.detail}</div>}
      <ApiErrorBanner error={actionError} />
      {test && (
        <div className="banner banner-good">
          <strong>Model test passed.</strong> {test.model} answered “{test.reply}” in {ms(test.latency_ms)}
          {test.tokens_per_second ? ` (${test.tokens_per_second} tokens/s)` : ""}.
        </div>
      )}

      {editable && (
        <div className="model-edit">
          <label className="field">
            <span className="field-label">Model</span>
            <select className="input select mono" value={modelDraft} onChange={(e) => setModelDraft(e.target.value)}>
              {options.map((m) => (
                <option key={m} value={m}>
                  {m}
                  {m === s.default_model ? " (default)" : ""}
                  {!s.installed_models.includes(m) && !s.installed_models.includes(`${m}:latest`) ? " (not installed)" : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Temperature</span>
            <input className="input input-num" type="number" min={0} max={2} step={0.1} value={tempDraft} onChange={(e) => setTempDraft(e.target.value)} />
          </label>
          <button className="btn" disabled={!dirty} onClick={() => void save()}>
            Save
          </button>
        </div>
      )}
      {editable && (
        <p className="muted small">
          Models listed are those installed in the provider. Install more with your provider's tools (for Ollama:{" "}
          <code>ollama pull &lt;model&gt;</code>). The default model and provider come from <code>.env</code>.
        </p>
      )}
    </Card>
  );
}

function label(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
