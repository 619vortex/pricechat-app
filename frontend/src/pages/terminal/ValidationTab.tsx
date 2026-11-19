import { request } from "../../api/client";
import { useApi } from "../../api/useApi";

/** Settings › Validation (owner, 2026-11-19): the validation round kept in production. Every News Desk candidate's decision, audience score,
 *  evidence and exact reason; the extraordinary-news shadow verdict (not live); each published picture's automated audit (rule-based, AI
 *  vision off); and the report made automatically at 30 candidates. Flags go to the owner on Telegram at once; this tab only reads. */

type Cand = { id: number; title: string; decision: "PUBLISH" | "REJECT"; interest: number; evidence: string; outlets: number; virality: number; why: string;
  extraordinary: string; extraordinary_would_publish: boolean; flag: string; seen_at?: string; gate?: { decision: string; interest: number; why: string } };
type Img = { platform: string; ref: string; headline: string; link: string; library?: string; credit?: string; licence?: string; problems: string[]; verdict: string; flag: string; at: string };
type Report = { generated_at: string; candidates: number; published: number; refused: number; refusal_reasons: [string, number][];
  possible_false_negatives: { id: number; title: string; why_flagged: string; gate_reason: string }[]; extraordinary_shadow: { would_add: number; titles: string[] };
  images: { audited: number; flagged: number }; note: string };
export type ValidationData = { candidates: { count: number; target: number; published: number; flagged: number }; images: { count: number; target: number; flagged: number };
  extraordinary_live: boolean; vision: string; rows: Cand[]; image_rows: Img[]; report: Report | null };

const when = (iso?: string) => (iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "");

export function ValidationTab() {
  const { data, error } = useApi(() => request<ValidationData>("/terminal/validation"), 60_000);
  if (!data) return <div className="t2-body">{error ? <p className="t2-problem">{String(error)}</p> : <p className="t2-meta">Loading…</p>}</div>;
  const c = data.candidates, im = data.images, r = data.report;
  return (
    <div className="t2-body">
      <section className="t2-section">
        <h4>Progress</h4>
        <div className="t2-row"><span>Real candidates judged</span><strong>{Math.min(c.count, c.target)} / {c.target}{c.count > c.target ? ` (${c.count} recorded)` : ""}</strong></div>
        <div className="t2-row"><span>Pictures audited</span><strong>{Math.min(im.count, im.target)} / {im.target}</strong></div>
        <div className="t2-row"><span>Flags (sent to you on Telegram)</span><strong>{c.flagged} candidates · {im.flagged} pictures</strong></div>
        <div className="t2-row"><span>Extraordinary news</span><strong>{data.extraordinary_live ? "Live" : "Shadow (judged, never published)"}</strong></div>
        <div className="t2-row"><span>AI vision</span><strong>{data.vision === "off" ? "Off (no paid calls)" : data.vision}</strong></div>
      </section>
      {r ? (
        <section className="t2-section">
          <h4>Report · {when(r.generated_at)}</h4>
          <div className="t2-row"><span>Published / refused</span><strong>{r.published} / {r.refused} of {r.candidates}</strong></div>
          <div className="t2-row"><span>Possible false negatives</span><strong>{r.possible_false_negatives.length}</strong></div>
          <div className="t2-row"><span>Extraordinary (shadow) would add</span><strong>{r.extraordinary_shadow.would_add}</strong></div>
          <div className="t2-row"><span>Pictures flagged</span><strong>{r.images.flagged} of {r.images.audited}</strong></div>
          <ul className="t2-plain">{r.refusal_reasons.map(([why, n]) => <li key={why}>{n} × {why}</li>)}</ul>
          {r.possible_false_negatives.map((f) => <p key={f.id} className="t2-problem">#{f.id} {f.title}: {f.why_flagged}</p>)}
          <p className="t2-fine">{r.note}</p>
        </section>
      ) : <p className="t2-fine">The report is made automatically when {c.target} candidates have been judged.</p>}
      <section className="t2-section">
        <h4>Pictures</h4>
        {data.image_rows.length === 0 && <p className="t2-meta">No picture published since validation started.</p>}
        {data.image_rows.map((i) => (
          <div key={`${i.platform}:${i.ref}`} className="t2-row" style={{ display: "block" }}>
            <strong>{i.verdict === "PASS" ? "✓" : "⚠"} {i.platform} · {i.headline}</strong>
            <p className="t2-fine">{i.library ? `${i.library} · ` : ""}{i.credit}{i.link ? <> · <a href={i.link} target="_blank" rel="noreferrer">open</a></> : null}</p>
            {i.problems.map((p) => <p key={p} className="t2-problem">{p}</p>)}
          </div>))}
      </section>
      <section className="t2-section">
        <h4>Candidates (newest first)</h4>
        {data.rows.length === 0 && <p className="t2-meta">No candidate since validation started.</p>}
        {data.rows.map((x) => (
          <div key={x.id} className="t2-row" style={{ display: "block" }}>
            <strong>{x.decision === "PUBLISH" ? "PUBLISH" : "REJECT"} · {x.interest} · #{x.id} {x.title}</strong>
            <p className="t2-fine">{x.evidence || "?"} · {x.outlets} outlets · desk score {x.virality} · {when(x.seen_at)}</p>
            <p className="t2-fine">{x.why}</p>
            {x.gate && <p className="t2-fine">At release: {x.gate.decision} ({x.gate.interest}) {x.gate.why}</p>}
            <p className="t2-fine">Extraordinary (shadow): {x.extraordinary_would_publish ? "would publish: " : ""}{x.extraordinary}</p>
            {x.flag && <p className="t2-problem">{x.flag}</p>}
          </div>))}
      </section>
    </div>
  );
}
