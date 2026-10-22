import { useState } from "react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import type { Route } from "../api/types";
import { ApiErrorBanner, Badge, Card, Loading } from "./ui";

/** Which model handles each kind of AI work. Provider-neutral: only model names and tiers. */
export function RoutingCard() {
  const { data, failure, setData } = useApi(api.routing);
  const [error, setError] = useState<unknown>(null);

  async function change(route: Route, value: string) {
    try {
      const body = value === "" ? {} : value.startsWith("tier:") ? { tier: value.slice(5) } : { model: value };
      setData(await api.setRoute(route.task, body));
      setError(null);
    } catch (e) {
      setError(e);
    }
  }

  if (!data) return <Card title="Model routing">{failure ? <ApiErrorBanner error={failure} /> : <Loading />}</Card>;
  const models = data.installed_models.filter((m) => !m.includes("embed"));
  return (
    <Card title="Model routing">
      <p className="muted small">
        Background AI work is routed by task, so routine work uses the fast model and important synthesis uses the strong one. Tiers:{" "}
        <strong>fast</strong> = <span className="mono">{data.tiers.fast}</span>, <strong>strong</strong> = <span className="mono">{data.tiers.strong}</span>,{" "}
        <strong>default</strong> = the chat model (<span className="mono">{data.tiers.default}</span>). Change the tiers in <code>.env</code> (MODEL_TIER_FAST / MODEL_TIER_STRONG).
      </p>
      <ApiErrorBanner error={error} />
      <table className="table">
        <thead>
          <tr>
            <th>Task</th>
            <th>Uses</th>
            <th>Model</th>
          </tr>
        </thead>
        <tbody>
          {data.routes.map((r) => {
            const value = !r.customised ? "" : r.tier ? `tier:${r.tier}` : r.model;
            return (
              <tr key={r.task}>
                <td>
                  <strong>{r.label}</strong>
                  <div className="muted small">{r.description}</div>
                </td>
                <td>
                  <select className="input select select-sm" value={value} onChange={(e) => void change(r, e.target.value)} aria-label={`Model for ${r.label}`}>
                    <option value="">Default ({r.default_tier} tier)</option>
                    <option value="tier:fast">Fast tier</option>
                    <option value="tier:strong">Strong tier</option>
                    <option value="tier:default">Chat model</option>
                    {models.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="mono small">
                  {r.model} {r.customised && <Badge tone="info">custom</Badge>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}
