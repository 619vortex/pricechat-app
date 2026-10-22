import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import type { Platform } from "../api/types";
import { Badge, Card, ErrorBanner, Loading, PageHeader, label, statusTone } from "../components/ui";

const COPY: Record<Platform, { title: string; purpose: string; envVars: string[] }> = {
  x: {
    title: "X / Twitter",
    purpose:
      "Read posts, find relevant conversations, draft replies, schedule Price Chat posts and report analytics. Likes, replies and posts will only run when authorized, within platform rules and configured limits.",
    envVars: ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET", "X_BEARER_TOKEN"],
  },
  telegram: {
    title: "Telegram",
    purpose:
      "Ingest messages from an authorized Price Chat Telegram community and use them as one source of brand context.",
    envVars: ["TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_IDS"],
  },
};

export function ConnectorPage({ platform }: { platform: Platform }) {
  const connectors = useApi(api.connectors);
  const safety = useApi(api.safety);
  const copy = COPY[platform];
  const connector = connectors.data?.find((c) => c.platform === platform);
  const limits = safety.data?.limits.filter((l) => l.platform === platform) ?? [];

  return (
    <>
      <PageHeader
        title={copy.title}
        actions={connector && <Badge tone={statusTone[connector.status]}>{label(connector.status)}</Badge>}
      />
      <ErrorBanner message={connectors.error ?? safety.error} />
      {!connector ? (
        <Loading />
      ) : (
        <div className="grid-2">
          <Card title="Connection">
            <div className="connector-empty">
              <div className="connector-icon">{platform === "x" ? "𝕏" : "➤"}</div>
              <h3>Not connected</h3>
              <p className="muted">{connector.detail}</p>
              <button className="btn" disabled title="Available in a later phase">Connect</button>
            </div>
            <p className="muted small">{copy.purpose}</p>
            <p className="muted small">
              Credentials will be read only from <code>.env</code>: {copy.envVars.map((v) => <code key={v} className="env">{v}</code>)}
            </p>
          </Card>
          <div className="stack">
            <Card title="Planned capabilities">
              <div className="chips">
                {connector.planned_capabilities.map((c) => (
                  <span key={c} className="chip">{label(c)}</span>
                ))}
              </div>
            </Card>
            <Card title="Safety limits" actions={<Link className="link" to="/settings">Edit</Link>}>
              <table className="table">
                <thead>
                  <tr><th>Action</th><th>Enabled</th><th>Mode</th><th>Per hour</th><th>Per day</th></tr>
                </thead>
                <tbody>
                  {limits.map((l) => (
                    <tr key={l.id}>
                      <td>{label(l.action)}</td>
                      <td>{l.enabled ? <Badge tone="good">On</Badge> : <Badge>Off</Badge>}</td>
                      <td>{l.mode ? label(l.mode) : <span className="muted">Inherit</span>}</td>
                      <td>{l.max_per_hour}</td>
                      <td>{l.max_per_day}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
