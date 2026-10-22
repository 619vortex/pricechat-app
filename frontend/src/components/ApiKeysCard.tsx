import { Fragment, useState } from "react";
import { request } from "../api/client";
import { useApi } from "../api/useApi";
import { ApiErrorBanner, Badge, Card, Loading, relativeTime } from "./ui";

interface ApiKey {
  name: string;
  env_name: string;
  provider: string;
  label: string;
  hint: string;
  configured: boolean;
  source: "dashboard" | "env" | null;
  saved_at: string | null;
  in_env_file: boolean;
  test_key: string | null;
}
type ApiKeys = { keys: ApiKey[]; env_file: string; env_writable: boolean };

const keysApi = {
  list: () => request<ApiKeys>("/settings/api-keys"),
  writeEnv: (name: string, value: string) =>
    request<ApiKeys & { written: string }>("/settings/api-keys/env", { method: "PUT", body: JSON.stringify({ name, value }) }),
  save: (name: string, value: string) => request<ApiKeys>(`/settings/api-keys/${name}`, { method: "PUT", body: JSON.stringify({ value }) }),
  remove: (name: string) => request<ApiKeys>(`/settings/api-keys/${name}`, { method: "DELETE" }),
  test: (key: string) => request<{ ok: boolean; message: string }>(`/pokemon/sources/${key}/test`, { method: "POST" }),
};

/** Pokémon data provider keys. Saved encrypted on the backend (priority over .env); values are never shown again. */
export function ApiKeysCard() {
  const { data, failure, setData } = useApi(keysApi.list);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [tests, setTests] = useState<Record<string, { ok: boolean; message: string } | "running">>({});

  async function test(key: string) {
    setTests((t) => ({ ...t, [key]: "running" }));
    try {
      const r = await keysApi.test(key);
      setTests((t) => ({ ...t, [key]: r }));
    } catch (e) {
      setTests((t) => ({ ...t, [key]: { ok: false, message: e instanceof Error ? e.message : "The test request failed." } }));
    }
  }

  async function run(name: string, action: () => Promise<ApiKeys>) {
    setBusy(name);
    try {
      setData(await action());
      setValues((v) => ({ ...v, [name]: "" }));
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  if (!data) return <Card title="Pokémon API keys">{failure ? <ApiErrorBanner error={failure} /> : <Loading />}</Card>;
  return (
    <Card title="Pokémon API keys">
      <p className="muted small">
        Keys for the Pokémon market data sources. Saved keys are encrypted on this server, take priority over <code>.env</code> and are never shown
        again. Test a source with its Test connection button (the check runs on the server; keys never reach this page).
      </p>
      <ApiErrorBanner error={error} />
      <table className="table">
        <thead>
          <tr>
            <th>Key</th>
            <th>Status</th>
            <th>New value</th>
          </tr>
        </thead>
        <tbody>
          {data.keys.map((k, i) => (
            <Fragment key={k.name}>
            <tr>
              <td>
                <strong>{k.provider}</strong> · {k.label}
                <div className="muted small">
                  <span className="mono">{k.env_name}</span> · {k.hint}
                </div>
              </td>
              <td>
                {!k.configured && k.in_env_file ? (
                  <Badge tone="warn" title="Written to .env: restart the backend to use it">IN .ENV · RESTART</Badge>
                ) : !k.configured ? (
                  <Badge>NOT SET</Badge>
                ) : k.source === "env" ? (
                  <Badge tone="good" title="Read from .env">FROM .ENV</Badge>
                ) : (
                  <Badge tone="good" title={k.saved_at ? `Saved ${relativeTime(k.saved_at)}` : undefined}>SAVED</Badge>
                )}
              </td>
              <td>
                <form
                  className="btn-group"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void run(k.name, () => keysApi.save(k.name, values[k.name] ?? ""));
                  }}
                >
                  <input
                    className="input"
                    type="password"
                    autoComplete="off"
                    aria-label={`${k.provider} ${k.label}`}
                    placeholder={k.configured ? "•••••• paste to replace" : "paste key"}
                    value={values[k.name] ?? ""}
                    onChange={(e) => setValues({ ...values, [k.name]: e.target.value })}
                  />
                  <button type="submit" className="btn btn-sm btn-primary" disabled={busy !== null || !(values[k.name] ?? "").trim()}>
                    Save
                  </button>
                  {k.source === "dashboard" && (
                    <button
                      type="button"
                      className="btn btn-sm btn-ghost"
                      disabled={busy !== null}
                      onClick={() => {
                        if (confirm(`Remove the saved ${k.provider} ${k.label}?`)) void run(k.name, () => keysApi.remove(k.name));
                      }}
                    >
                      Remove
                    </button>
                  )}
                </form>
              </td>
            </tr>
            {k.test_key && data.keys[i + 1]?.provider !== k.provider && (
              <tr className="api-key-test">
                <td colSpan={3}>
                  <div className="btn-group" style={{ alignItems: "center" }}>
                    <button type="button" className="btn btn-sm" disabled={tests[k.test_key] === "running"} onClick={() => void test(k.test_key!)}>
                      {tests[k.test_key] === "running" ? "Testing…" : `Test ${k.provider} connection`}
                    </button>
                    {tests[k.test_key] && tests[k.test_key] !== "running" && (() => {
                      const r = tests[k.test_key!] as { ok: boolean; message: string };
                      return <span role="status"><Badge tone={r.ok ? "good" : "warn"}>{r.ok ? "PASS" : "FAIL"}</Badge> <span className="small">{r.message}</span></span>;
                    })()}
                  </div>
                </td>
              </tr>
            )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

const OTHER = "__other__";

/** Write NAME=value straight into the project's .env (local only). The backend reads it on its next start. */
export function EnvKeyCard() {
  const { data, failure, setData } = useApi(keysApi.list);
  const [choice, setChoice] = useState("");
  const [custom, setCustom] = useState("");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState<string | null>(null);

  if (!data) return <Card title="Add an API key to .env">{failure ? <ApiErrorBanner error={failure} /> : <Loading />}</Card>;
  if (!data.env_writable)
    return (
      <Card title="Add an API key to .env">
        <p className="muted small">This server runs in production, where .env isn't used: set variables on the host (Railway) or save keys above.</p>
      </Card>
    );

  const name = (choice === OTHER ? custom : choice).trim().toUpperCase();
  async function submit() {
    setBusy(true);
    setDone(null);
    try {
      const r = await keysApi.writeEnv(name, value);
      setData(r);
      setValue("");
      setError(null);
      setDone(r.written);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Add an API key to .env">
      <p className="muted small">
        Writes <span className="mono">NAME=value</span> into <span className="mono">{data.env_file}</span>. If the name is already there, its value is
        replaced. The backend reads .env when it starts, so <strong>restart the backend</strong> afterwards (stop + start Price Chat). A key saved in the
        table above takes priority over .env.
      </p>
      <ApiErrorBanner error={error} />
      {done && (
        <div className="banner banner-good" role="status">
          ✓ {done} written to .env. Restart the backend to use it.
        </div>
      )}
      <form
        className="btn-group"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <select className="input select" value={choice} onChange={(e) => setChoice(e.target.value)} aria-label="Which key">
          <option value="">Choose a key…</option>
          {data.keys.map((k) => (
            <option key={k.name} value={k.env_name}>
              {k.provider} · {k.label} ({k.env_name})
            </option>
          ))}
          <option value={OTHER}>Other (type a name)…</option>
        </select>
        {choice === OTHER && (
          <input className="input mono" placeholder="MY_API_KEY" value={custom} onChange={(e) => setCustom(e.target.value)} aria-label="Variable name" />
        )}
        <input
          className="input"
          type="password"
          autoComplete="off"
          placeholder="paste the key"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          aria-label="Key value"
        />
        <button type="submit" className="btn btn-primary" disabled={busy || !name || !value.trim()}>
          Add to .env
        </button>
      </form>
    </Card>
  );
}
