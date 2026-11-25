import { useEffect, useRef, useState, type ReactNode } from "react";
import { request } from "../api/client";
import { useApi } from "../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, Loading, PageHeader, Stat, relativeTime, type Tone } from "../components/ui";

/** Admin › System › Intelligence (shadow) (Terminal Intelligence Engine, Phase 0). One read of GET /api/intel/dashboard: what the AI
 *  models decided next to what the lanes decided, the disagreements, possible missed stories, latency, the owner PC's availability and
 *  the AI spend against the daily cap. Read-only: nothing on this page changes a switch, enqueues a job or publishes anything. */

export type ShadowItem = { id: number; lane: string; ref: string; title: string; lane_ok: boolean; gate_ok: boolean; agree: boolean; tier: string;
  state: string; decider: string; model: string; latency_ms: number | null; event_type: string; lane_reason: string; gate_reasons: string[];
  hard_veto: string[]; seen: number; last_seen: string | null };
type Disagreement = { lane: string; ref: string; title: string; rules_ok: boolean; semantic_ok: boolean; tier: string; source: string;
  rules_reason: string; semantic_reasons: string[]; hard_veto: string[]; seen: number; last_seen: string | null };
type LaneCounts = { total: number; agree: number; rules_only: number; semantic_only: number; pending: number };
type LatencyGroup = { route: string; model: string; kind: string; n: number; p50_ms: number | null; p95_ms: number | null; max_ms: number;
  compute_p50_ms: number | null; compute_p95_ms: number | null };
type PcJobs = { claimed: number; completed: number; failed: number; expired_or_taken: number; in_flight: number };
type Worker = { id: number; name: string; enabled: boolean; online: boolean; ready: boolean; last_seen_at: string | null; models: string[]; busy: boolean;
  version: string; gpu: string; jobs: PcJobs; availability: { online_seconds: number; ready_seconds: number; window_seconds: number; covered_seconds: number;
    history_since: string | null; pct_online: number | null; pct_ready: number | null } };
export type IntelDashboard = {
  generated_at: string; hours: number; since: string; mode: string; banner: string;
  switches: { intel_enabled: boolean; intel_shadow_enabled: boolean; breaking_cloud_first: boolean; cloud_model: string; token_configured: boolean; pc_online: boolean };
  decisions: { total: number; by_decider: Record<string, number>; by_model: Record<string, number>; tiers: Record<string, number>;
    model_tiers: Record<string, number>; by_lane: Record<string, number>; recent: ShadowItem[] };
  disagreements: { counts: LaneCounts & { by_source: Record<string, number>; by_lane: Record<string, LaneCounts> }; decided: number;
    agreement_rate: number | null; lane_yes_ai_no: Disagreement[]; lane_no_ai_yes: Disagreement[]; prompt_version: string };
  latency: { groups: LatencyGroup[]; jobs: { total: number; by_status: Record<string, number>; by_result: Record<string, number>; refused_over_cap: number } };
  workers: Worker[];
  pc_jobs: PcJobs;
  missed: { possible_misses: ShadowItem[]; possible_false_positives: ShadowItem[]; misses_total: number; false_positives_total: number };
  spend: { today: { day: string; spent_usd: number; cap_usd: number; over_cap: boolean }; cap_usd: number; window_usd: number; window_calls: number;
    per_model: { model: string; usd: number; calls: number; tokens_in: number; tokens_out: number }[]; per_day: { day: string; usd: number; calls: number }[];
    refused_over_cap: number };
};

const WINDOWS: { hours: number; label: string }[] = [
  { hours: 6, label: "6 hours" }, { hours: 24, label: "24 hours" }, { hours: 72, label: "3 days" }, { hours: 168, label: "7 days" }, { hours: 336, label: "14 days" },
];
const DECIDER: Record<string, string> = { pc: "PC model", cloud: "Cloud model", rules: "Rules (no AI)", pending: "Waiting for AI" };
const TIER_TONE: Record<string, Tone> = { major: "bad", significant: "warn", notable: "info", noise: "neutral" };
const usd = (n: number | null | undefined) => `$${(n ?? 0).toFixed(2)}`;
const pctText = (n: number | null | undefined) => (n == null ? "—" : `${Math.round(n * 100)}%`);
function msText(n: number | null | undefined): string {
  if (n == null) return "—";
  return n >= 1000 ? `${(n / 1000).toFixed(1)} s` : `${Math.round(n)} ms`;
}
const yesNo = (ok: boolean, yes = "publish", no = "no") => <Badge tone={ok ? "good" : "neutral"}>{ok ? yes : no}</Badge>;
const tier = (t: string) => (t ? <Badge tone={TIER_TONE[t] ?? "neutral"}>{t}</Badge> : <span className="muted">—</span>);

function Table({ head, children }: { head: string[]; children: ReactNode }) {
  return (
    <div className="table-wrap"><table className="table">
      <thead><tr>{head.map((h) => <th key={h}>{h}</th>)}</tr></thead>
      <tbody>{children}</tbody>
    </table></div>
  );
}

function Reasons({ item }: { item: { hard_veto: string[]; reasons: string[]; lane: string } }) {
  return (
    <>
      {item.hard_veto.length > 0 && <div className="small"><b>Hard veto:</b> {item.hard_veto.join("; ")}</div>}
      {item.reasons.length > 0 && <div className="small muted">{item.reasons.join(" · ")}</div>}
    </>
  );
}

function StoryRows({ items, empty }: { items: ShadowItem[]; empty: string }) {
  if (!items.length) return <Empty>{empty}</Empty>;
  return (
    <Table head={["Story", "Lane", "Lane said", "AI tier", "AI gate", "By", "Seen"]}>
      {items.map((x) => (
        <tr key={x.id}>
          <td>{x.title}<Reasons item={{ hard_veto: x.hard_veto, reasons: x.gate_reasons, lane: x.lane }} /></td>
          <td>{x.lane}</td><td>{yesNo(x.lane_ok)}</td><td>{tier(x.tier)}</td><td>{yesNo(x.gate_ok)}</td>
          <td className="small">{DECIDER[x.decider] ?? x.decider}{x.model ? <div className="muted">{x.model}</div> : null}</td>
          <td className="small muted">{relativeTime(x.last_seen)}</td>
        </tr>
      ))}
    </Table>
  );
}

function DisagreementRows({ items, empty }: { items: Disagreement[]; empty: string }) {
  if (!items.length) return <Empty>{empty}</Empty>;
  return (
    <Table head={["Story", "Lane", "AI tier", "Lane's reason", "AI's reasons", "Seen"]}>
      {items.map((x) => (
        <tr key={`${x.lane}-${x.ref}`}>
          <td>{x.title}<div className="small muted">{x.source}</div></td><td>{x.lane}</td><td>{tier(x.tier)}</td>
          <td className="small">{x.rules_reason || <span className="muted">—</span>}</td>
          <td><Reasons item={{ hard_veto: x.hard_veto, reasons: x.semantic_reasons, lane: x.lane }} /></td>
          <td className="small muted">{relativeTime(x.last_seen)}</td>
        </tr>
      ))}
    </Table>
  );
}

export function IntelShadowPage() {
  const [hours, setHours] = useState(24);
  const { data, failure, loading, reload } = useApi(() => request<IntelDashboard>(`/intel/dashboard?hours=${hours}`), 60_000);
  const mounted = useRef(false);
  useEffect(() => {  // useApi fetched on mount; a new window fetches again
    if (mounted.current) void reload();
    mounted.current = true;
  }, [hours, reload]);

  const header = (
    <PageHeader title="Intelligence (shadow)" subtitle="What the AI models would decide next to what News Desk and Trending decided. Read-only."
      actions={<select className="input select select-sm" aria-label="Window" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
        {WINDOWS.map((w) => <option key={w.hours} value={w.hours}>{w.label}</option>)}
      </select>} />
  );
  const banner = <div className="banner banner-info" role="note"><strong>Shadow mode — AI decisions are recorded only; nothing is published by AI.</strong></div>;
  if (!data) return <div className="stack">{header}{banner}{loading ? <Loading /> : <ApiErrorBanner error={failure} />}</div>;

  const d = data.decisions, g = data.disagreements, sp = data.spend, sw = data.switches;
  const capShare = sp.cap_usd ? Math.min(1, sp.today.spent_usd / sp.cap_usd) : 0;
  return (
    <div className="stack">
      {header}
      {banner}
      {failure ? <ApiErrorBanner error={failure} /> : null}
      <div className="chips" aria-label="Switches">
        <Badge tone={sw.intel_enabled ? "good" : "neutral"}>intel_enabled: {sw.intel_enabled ? "ON" : "off"}</Badge>
        <Badge tone={sw.intel_shadow_enabled ? "good" : "neutral"}>intel_shadow_enabled: {sw.intel_shadow_enabled ? "ON" : "off"}</Badge>
        <Badge tone={sw.pc_online ? "good" : "neutral"}>PC {sw.pc_online ? "online" : "offline"}</Badge>
        <Badge tone="neutral">cloud model: {sw.cloud_model}</Badge>
        {!sw.token_configured && <Badge tone="warn">PC token not configured</Badge>}
        <span className="small muted">window: last {WINDOWS.find((w) => w.hours === data.hours)?.label ?? `${data.hours} h`} · updated {relativeTime(data.generated_at)}</span>
      </div>
      {!sw.intel_shadow_enabled && d.total === 0 && <Empty>The shadow record is off (intel_shadow_enabled), so there is nothing to compare yet.</Empty>}

      <Card title="1 · Model decisions">
        <div className="stats">
          <Stat label="Judged stories" value={d.total} />
          {Object.keys(DECIDER).map((k) => <Stat key={k} label={DECIDER[k]} value={d.by_decider[k] ?? 0} />)}
        </div>
        <p className="small muted">
          AI tiers: {["major", "significant", "notable", "noise"].map((t) => `${t} ${d.model_tiers[t] ?? 0}`).join(" · ")}
          {Object.keys(d.by_model).length > 0 && <> · by model: {Object.entries(d.by_model).map(([m, n]) => `${m} ${n}`).join(" · ")}</>}
        </p>
        {!d.recent.length ? <Empty>No decision in this window.</Empty> : (
          <Table head={["Story", "Lane", "Lane said", "AI tier", "AI gate", "Model", "Latency"]}>
            {d.recent.map((x) => (
              <tr key={x.id}>
                <td>{x.title}{!x.agree && <> <Badge tone="warn">disagree</Badge></>}</td><td>{x.lane}</td><td>{yesNo(x.lane_ok)}</td><td>{tier(x.tier)}</td>
                <td>{x.state === "pending" ? <Badge tone="info">pending</Badge> : yesNo(x.gate_ok)}</td>
                <td className="small">{DECIDER[x.decider] ?? x.decider}{x.model ? <div className="muted">{x.model}</div> : null}</td>
                <td className="small">{msText(x.latency_ms)}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>

      <Card title="2 · Disagreements">
        <div className="stats">
          <Stat label="Agreement rate" value={pctText(g.agreement_rate)} hint={`${g.counts.agree} of ${g.decided} decided`} />
          <Stat label="Lane yes, AI no" value={g.counts.rules_only} tone={g.counts.rules_only ? "warn" : "neutral"} />
          <Stat label="Lane no, AI yes" value={g.counts.semantic_only} tone={g.counts.semantic_only ? "warn" : "neutral"} />
          <Stat label="Waiting for AI" value={g.counts.pending} />
        </div>
        <h3 className="small">Lane said publish, AI said no</h3>
        <DisagreementRows items={g.lane_yes_ai_no} empty="None in this window." />
        <h3 className="small">Lane said no, AI said publish</h3>
        <DisagreementRows items={g.lane_no_ai_yes} empty="None in this window." />
        <p className="small muted">Prompt {g.prompt_version}. The rules' hard vetoes (hedges, round-ups, repeats, caps, pauses) always win over the AI.</p>
      </Card>

      <Card title="3 · Latency">
        {!data.latency.groups.length ? <Empty>No answered job in this window.</Empty> : (
          <Table head={["Route", "Model", "Kind", "Jobs", "p50", "p95", "Max", "PC compute p50 / p95"]}>
            {data.latency.groups.map((x) => (
              <tr key={`${x.route}-${x.model}-${x.kind}`}>
                <td>{DECIDER[x.route] ?? x.route}</td><td className="small">{x.model}</td><td className="small">{x.kind}</td><td>{x.n}</td>
                <td>{msText(x.p50_ms)}</td><td>{msText(x.p95_ms)}</td><td>{msText(x.max_ms)}</td>
                <td className="small">{x.compute_p50_ms == null ? "—" : `${msText(x.compute_p50_ms)} / ${msText(x.compute_p95_ms)}`}</td>
              </tr>
            ))}
          </Table>
        )}
        <p className="small muted">Latency is queue time plus answer time (job created → answered); bulk shadow jobs wait for the PC, so they are slow by design.
          Jobs: {data.latency.jobs.total} ({Object.entries(data.latency.jobs.by_status).map(([k, v]) => `${k} ${v}`).join(" · ") || "none"}).</p>
      </Card>

      <Card title="4 · PC worker availability">
        {!data.workers.length ? <Empty>No PC worker has connected yet.</Empty> : (
          <Table head={["Worker", "Status", "Last heartbeat", "Models", "Online in window", "Ready in window", "Claimed", "Completed", "Expired / taken", "Failed"]}>
            {data.workers.map((w) => (
              <tr key={w.id}>
                <td>{w.name}{w.gpu ? <div className="small muted">{w.gpu}{w.version ? ` · v${w.version}` : ""}</div> : null}</td>
                <td>{!w.enabled ? <Badge tone="neutral">switched off</Badge> : w.online ? <Badge tone={w.ready ? "good" : "warn"}>{w.ready ? "online" : w.busy ? "online, busy" : "online, no models"}</Badge> : <Badge tone="bad">offline</Badge>}</td>
                <td className="small">{relativeTime(w.last_seen_at)}</td>
                <td className="small">{w.models.join(", ") || "—"}</td>
                <td>{pctText(w.availability.pct_online)}{w.availability.history_since && w.availability.covered_seconds < w.availability.window_seconds
                  ? <div className="small muted">recorded since {relativeTime(w.availability.history_since)}</div> : null}</td>
                <td>{pctText(w.availability.pct_ready)}</td>
                <td>{w.jobs.claimed}</td><td>{w.jobs.completed}</td><td>{w.jobs.expired_or_taken}</td><td>{w.jobs.failed}</td>
              </tr>
            ))}
          </Table>
        )}
        <p className="small muted">Online = a heartbeat in the last 60 s; ready = online with models loaded and not busy (only then are jobs offered to the PC).</p>
      </Card>

      <Card title="5 · Missed stories">
        <h3 className="small">Possible misses: the AI rated it major, or significant and newsworthy, but the lane did not publish ({data.missed.misses_total})</h3>
        <StoryRows items={data.missed.possible_misses} empty="No possible miss in this window." />
        <h3 className="small">Possible false positives: the lane published, the AI calls it noise ({data.missed.false_positives_total})</h3>
        <StoryRows items={data.missed.possible_false_positives} empty="No possible false positive in this window." />
      </Card>

      <Card title="6 · API expenditure">
        <div className="stats">
          <Stat label="Today" value={`${usd(sp.today.spent_usd)} of ${usd(sp.cap_usd)}`} tone={sp.today.over_cap ? "bad" : capShare > 0.8 ? "warn" : "good"}
            hint={sp.today.over_cap ? "over the cap: cloud calls are refused, rules answer" : `${Math.round(capShare * 100)}% of the daily cap`} />
          <Stat label="This window" value={usd(sp.window_usd)} hint={`${sp.window_calls} paid calls`} />
          <Stat label="Refused over cap" value={sp.refused_over_cap} tone={sp.refused_over_cap ? "warn" : "neutral"} />
        </div>
        {sp.per_model.length > 0 && (
          <Table head={["Model", "Spend", "Calls", "Tokens in", "Tokens out"]}>
            {sp.per_model.map((m) => <tr key={m.model}><td>{m.model}</td><td>{usd(m.usd)}</td><td>{m.calls}</td><td>{m.tokens_in}</td><td>{m.tokens_out}</td></tr>)}
          </Table>
        )}
        <Table head={["Day (UTC)", "Spend", "Calls"]}>
          {sp.per_day.map((x) => <tr key={x.day}><td>{x.day}</td><td>{usd(x.usd)}</td><td>{x.calls}</td></tr>)}
        </Table>
      </Card>
    </div>
  );
}
