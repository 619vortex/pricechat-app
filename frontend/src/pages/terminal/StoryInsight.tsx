import { useEffect, useState } from "react";
import { request } from "../../api/client";

/** The story panel's editorial extras (owner, 2026-11-19): one-click Great / Boring / Wrong image / Missed story feedback stored against the
 *  real event or topic, the story so far across every lane (story memory, with X leads), how its X post performed, and the AI editor's
 *  shadow verdict when it has one. Feedback never changes a post or a decision by itself. */

type Sighting = { lane: string; ref: string; at: string; title: string; status: string; by?: string };
export type Insight = {
  feedback: string[]; published: boolean; picture: boolean;
  memory: { id: number; first: string; told: number; sightings: Sighting[]; lead_at: string | null; lead_by: string; confirmed_at: string | null } | null;
  performance: { impressions: number; engagements: number; age_hours: number; reach: number; engagement: number; index: number; bait: boolean } | null | { error: string };
  chief: { status: string; verdict?: string; why_care?: string; best_angle?: string; problems?: string[] } | null;
};

const BUTTONS = [
  { key: "great", label: "Great" },
  { key: "boring", label: "Boring" },
  { key: "wrong_image", label: "Wrong image" },
  { key: "missed", label: "Missed story" },
] as const;
const LANES: Record<string, string> = { telegram: "Telegram", "x-newsdesk": "X", "x-trending": "X (Trending)", "x-intel": "X Intelligence", released: "News Desk" };
const STATUS: Record<string, string> = { told: "posted", lead: "early lead, unverified", confirmed: "confirmed" };

const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "");
const minutes = (a: string | null, b: string | null) => (a && b ? Math.max(0, Math.round((Date.parse(b) - Date.parse(a)) / 60_000)) : null);

export function StoryInsight({ target }: { target: string }) {
  const [data, setData] = useState<Insight | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    request<Insight>(`/terminal/insight?target=${encodeURIComponent(target)}`).then((d) => { if (live) setData(d); }).catch(() => { if (live) setData(null); });
    return () => { live = false; };
  }, [target]);

  async function click(verdict: string) {
    setBusy(true); setProblem(null);
    try {
      const r = await request<{ verdicts: string[] }>("/terminal/feedback", { method: "POST", body: JSON.stringify({ target, verdict }) });
      setData((d) => (d ? { ...d, feedback: r.verdicts } : d));
    } catch (e) {
      setProblem(String((e as Error)?.message ?? e).replace(/^\d{3}:?\s*/, ""));
    } finally { setBusy(false); }
  }

  const fb = data?.feedback ?? [];
  const perf = data?.performance && "index" in data.performance ? data.performance : null;
  const mem = data?.memory;
  const lead = mem?.lead_at ? minutes(mem.lead_at, mem.confirmed_at) : null;
  return (
    <section className="t2-section" aria-label="Your verdict">
      <h4>Your verdict</h4>
      <div className="t2-chips" role="group" aria-label="Feedback">
        {BUTTONS.map((b) => {
          const off = !data || (b.key === "wrong_image" && !(data.published && data.picture)) || (b.key === "missed" && data.published);
          return (
            <button key={b.key} className={`t2-chip ${fb.includes(b.key) ? "on" : ""}`} aria-pressed={fb.includes(b.key)} disabled={busy || off}
              title={b.key === "wrong_image" && off ? "Only for a story that went out with a picture" : b.key === "missed" && off ? "Only for a story that wasn't published" : undefined}
              onClick={() => void click(b.key)}>{b.label}</button>
          );
        })}
      </div>
      {problem && <p className="t2-problem">{problem}</p>}
      {perf && (
        <p className="t2-fine">On X: {perf.impressions.toLocaleString()} views · {perf.index.toFixed(2)}× a typical post (reach {perf.reach.toFixed(2)}×, engagement {perf.engagement.toFixed(2)}× its topic)
          {perf.bait ? " · worded as bait, so it teaches nothing" : ""}</p>
      )}
      {mem && (mem.told > 0 || mem.lead_at) && (
        <>
          <p className="t2-fine"><strong>Story so far:</strong> {mem.told > 0 ? `told ${mem.told}×` : "not posted yet"}
            {mem.lead_at ? ` · first seen on X ${when(mem.lead_at)}${mem.lead_by ? ` by ${mem.lead_by}` : ""}${mem.confirmed_at ? `, confirmed ${lead !== null ? `${lead} min later` : ""}` : ", still unconfirmed"}` : ""}</p>
          <ul className="t2-plain">{mem.sightings.slice(-5).map((s, i) => (
            <li key={i} className="t2-fine">{when(s.at)} · {LANES[s.lane] ?? s.lane} · {STATUS[s.status] ?? s.status}: {s.title}</li>))}</ul>
        </>
      )}
      {data?.chief && (
        <p className="t2-fine"><strong>AI editor (shadow):</strong> {data.chief.status === "reviewed"
          ? `${data.chief.verdict}. ${data.chief.why_care ?? ""}${data.chief.problems?.length ? ` (${data.chief.problems.join("; ")})` : ""}`
          : "not reviewed: the AI editor is off until you approve it."}</p>
      )}
    </section>
  );
}
