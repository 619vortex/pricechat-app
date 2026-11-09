import { useState } from "react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import type { Job, JobMonitor } from "../api/types";
import { ApiErrorBanner, Badge, Card, Loading, formatTime, ms, relativeTime, type Tone } from "./ui";

const STATUS_TONE: Record<string, Tone> = { success: "good", failed: "bad", skipped: "neutral", running: "info", never: "neutral" };
const MONITOR_TONE: Record<string, Tone> = { ok: "good", alert: "bad", warn: "bad", off: "neutral", hold: "neutral" };
const MONITOR_WORD: Record<string, string> = { ok: "OK", alert: "Problem", warn: "Can't read", off: "Off", hold: "Not checked now" };
/** Turned off on this server by SCHEDULER_ONLY_JOBS / SCHEDULER_DISABLED_JOBS: never runs here, whatever the switch says. */
const offHere = (j: Job) => j.server_state === "off_here";

function every(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  return `${Math.round(seconds / 3600)} h`;
}

export function JobsCard() {
  const { data, failure, reload } = useApi(api.jobs, 5000);
  const monitors = useApi(api.jobMonitors, 60000);
  const [running, setRunning] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [last, setLast] = useState<string | null>(null);

  async function run(job: Job) {
    setRunning(job.name);
    try {
      const out = await api.runJob(job.name);
      setLast(`${job.label}: ${out.status}${out.error ? ` (${out.error})` : ""}`);
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setRunning(null);
      await reload();
    }
  }

  const groups = new Map<string, Job[]>();
  for (const j of [...(data ?? [])].sort((x, y) => Number(problem(y)) - Number(problem(x)))) {
    const g = group(j.name);
    groups.set(g, [...(groups.get(g) ?? []), j]);
  }
  return (
    <Card title="Background jobs">
      <p className="muted small">Things Price Chat does by itself on a timer. A job with a problem tries again by itself, a little later each time.</p>
      <ApiErrorBanner error={failure ?? error} />
      {last && <div className="banner banner-info">{last}</div>}
      {monitors.data && monitors.data.length > 0 && <Monitors items={monitors.data} />}
      {!data ? (
        <Loading />
      ) : (
        <div className="jobs">
          {[...groups].map(([g, jobs]) => (
            <section key={g}>
              <h3 className="jobs-group">{g}</h3>
              <ul className="jobs-list">
                {jobs.map((j) => (
                  <li key={j.name} className={problem(j) ? "is-problem" : ""}>
                    <div className="jobs-name">
                      <strong>{j.label}</strong>
                      <span className="muted small">{j.description} · every {every(j.interval_seconds)}</span>
                      {problem(j) && j.last_error && <span className="tone-bad small">{j.last_error}</span>}
                    </div>
                    <span className="jobs-state" title={`${j.status}${j.last_duration_ms != null ? ` · took ${ms(j.last_duration_ms)}` : ""}${j.next_run_at ? ` · next ${formatTime(j.next_run_at)}` : ""}`}>
                      {offHere(j) ? <Badge>Off on this server</Badge> : !j.enabled ? <Badge>Off</Badge> : j.running ? <Badge tone="info">Running</Badge> : problem(j) ? <Badge tone="bad">Problem</Badge>
                        : j.status === "never" ? <Badge>Not run yet</Badge> : <Badge tone={STATUS_TONE[j.status] ?? "neutral"}>OK</Badge>}
                      <span className="muted small">{relativeTime(j.last_run_at)}</span>
                    </span>
                    <label className="toggle" title={offHere(j) ? "Turned off on this server (SCHEDULER_ONLY_JOBS / SCHEDULER_DISABLED_JOBS): the switch can't start it" : j.enabled ? "On" : "Off"}>
                      <input type="checkbox" aria-label={`${j.label} on`} checked={j.enabled && !offHere(j)} disabled={offHere(j)} onChange={(e) => void api.setJobEnabled(j.name, e.target.checked).then(reload).catch(setError)} />
                      <span />
                    </label>
                    <button className="btn btn-sm" disabled={running !== null || j.running || offHere(j)} onClick={() => void run(j)}>
                      {running === j.name ? "Running…" : "Run now"}
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </Card>
  );
}

const problem = (j: Job) => j.enabled && !offHere(j) && (j.status === "failed" || j.consecutive_failures > 0);

/** The silence monitors: shown here so they are seen even while owner alerts have no Telegram chat. */
function Monitors({ items }: { items: JobMonitor[] }) {
  return (
    <section>
      <h3 className="jobs-group">Silence monitors</h3>
      <ul className="jobs-list">
        {items.map((m) => (
          <li key={m.key} className={m.status === "alert" ? "is-problem" : ""}>
            <div className="jobs-name">
              <strong>{m.label}</strong>
              <span className="muted small">{m.detail}</span>
            </div>
            <span className="jobs-state"><Badge tone={MONITOR_TONE[m.status] ?? "neutral"}>{MONITOR_WORD[m.status] ?? m.status}</Badge></span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Which part of Price Chat a job belongs to, from its name. */
function group(name: string): string {
  const [head] = name.split(".");
  const map: Record<string, string> = {
    tradingos: "Trading", tradingbot: "Trading", exchange: "Trading", telegram: "Telegram", community: "Telegram", combot: "Telegram", invites: "Telegram",
    workers: "Members", members: "Members", x: "X / Twitter", cross: "X / Twitter", youtube: "YouTube", pokemon: "Pokémon", vault: "Pokémon", market: "Pokémon",
    marketplace: "Pokémon", portfolio: "Pokémon", brand: "Social media", short: "Social media", growth: "Website", content: "Content & AI", knowledge: "Content & AI",
    opportunities: "Content & AI", topic: "Telegram", approvals: "Content & AI",
  };
  return map[head] ?? map[head.split("_")[0]] ?? "Other";
}
