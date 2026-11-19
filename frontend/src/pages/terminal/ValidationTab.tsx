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
type Cost = { per_review: number; per_day: number; per_month: number };
type Chief = { enabled: boolean; status: string; shortlisted: number; queued_off: number; reviewed: number; spent_today: number; daily_cap: number; model: string;
  cost: { candidates_per_day: number; mean_input_tokens: number; output_tokens_assumed: number; by_model: Record<string, Cost> };
  agreement: Record<string, number>; labelled: { n: number; gate_right: number; editor_right: number }; invalid_reasons: number; blocked_unverified: number;
  disagreements: { event: number; title: string; gate: string; editor: string; why_care: string; owner: string[] }[] };
type Feedback = { counts: Record<string, number>; labels: Record<string, string>; rows: { target: string; verdict: string; title: string; published: boolean; gate?: string; at: string }[] };
type Memory = { enforced: boolean; days: number; storylines: number; told: number; followed_up: number;
  leads: { seen: number; confirmed: number; unconfirmed: number; median_lead_minutes: number | null; accounts: { account: string; leads: number; confirmed: number }[] } };
type PerfRow = { lane: string; title: string; impressions: number; index: number; reach: number; engagement: number; bait: boolean; topic: string };
type Perf = { measured: number; lessons_from?: number; excluded_bait_or_boring?: number; best: PerfRow[]; by_topic: { topic: string; posts: number; index: number }[];
  by_hook: { hook: string; posts: number; index: number }[]; telegram: string; note: string };
type Gap = { subject: string; candidates: number; published: number };
type Part<T> = T | { error: string };
export type ValidationData = { candidates: { count: number; target: number; published: number; flagged: number }; images: { count: number; target: number; flagged: number };
  extraordinary_live: boolean; vision: string; rows: Cand[]; image_rows: Img[]; report: Report | null;
  chief?: Part<Chief>; feedback?: Part<Feedback>; memory?: Part<Memory>; performance?: Part<Perf>; photo_gaps?: Gap[] };

const MODEL: Record<string, string> = { "claude-haiku-5-5": "Haiku 5.5", "claude-sonnet-5-5": "Sonnet 5.5", "claude-opus-5-5": "Opus 5.5" };
const usd = (n: number) => (n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`);
function ok<T extends object>(x: Part<T> | undefined): x is T {
  return !!x && !("error" in x);
}

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
      {ok(data.chief) && <ChiefSection c={data.chief} />}
      {ok(data.feedback) && <FeedbackSection f={data.feedback} />}
      {ok(data.memory) && <MemorySection m={data.memory} />}
      {ok(data.performance) && <PerfSection p={data.performance} />}
      {data.photo_gaps && data.photo_gaps.length > 0 && (
        <section className="t2-section">
          <h4>Photos the library lacks</h4>
          <p className="t2-fine">Subjects real candidates named that have no licensed library photo (published stories count double).</p>
          <ul className="t2-plain">{data.photo_gaps.map((g) => <li key={g.subject}>{g.subject}: {g.candidates} candidate{g.candidates === 1 ? "" : "s"}, {g.published} published</li>)}</ul>
        </section>
      )}
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

function ChiefSection({ c }: { c: Chief }) {
  const rows = Object.entries(c.cost.by_model);
  return (
    <section className="t2-section">
      <h4>AI editor-in-chief (shadow)</h4>
      <div className="t2-row"><span>Status</span><strong>{c.enabled ? `On, shadow only · ${MODEL[c.model] ?? c.model}` : "Off until you approve it (no paid calls)"}</strong></div>
      <div className="t2-row"><span>Shortlisted real candidates</span><strong>{c.shortlisted}{c.reviewed ? ` · ${c.reviewed} reviewed` : ""}</strong></div>
      {c.enabled && <div className="t2-row"><span>Spent today</span><strong>{usd(c.spent_today)} of {usd(c.daily_cap)}</strong></div>}
      {rows.length > 0 && (
        <>
          <p className="t2-fine">What it would cost, from the {c.shortlisted} real candidates stored so far (about {c.cost.candidates_per_day} a day, {c.cost.mean_input_tokens} input tokens each, {c.cost.output_tokens_assumed} output assumed):</p>
          <ul className="t2-plain">{rows.map(([m, x]) => <li key={m}>{MODEL[m] ?? m}: {usd(x.per_review)} a story · {usd(x.per_day)} a day · {usd(x.per_month)} a month</li>)}</ul>
        </>
      )}
      {c.reviewed > 0 && (
        <>
          <ul className="t2-plain">{Object.entries(c.agreement).map(([k, n]) => <li key={k}>{n} × {k}</li>)}</ul>
          {c.labelled.n > 0 && <p className="t2-fine">Against your feedback ({c.labelled.n} stories): gate right {c.labelled.gate_right}, AI editor right {c.labelled.editor_right}.</p>}
          {c.disagreements.map((d) => <p key={d.event} className="t2-fine">#{d.event} {d.title}: gate {d.gate}, editor {d.editor}. {d.why_care}</p>)}
        </>
      )}
    </section>
  );
}

function FeedbackSection({ f }: { f: Feedback }) {
  return (
    <section className="t2-section">
      <h4>Your feedback</h4>
      <div className="t2-row"><span>Last 30 days</span><strong>{Object.entries(f.counts).map(([k, n]) => `${n} ${f.labels[k] ?? k}`).join(" · ")}</strong></div>
      {f.rows.length === 0 && <p className="t2-meta">Open any story and use Great, Boring, Wrong image or Missed story.</p>}
      <ul className="t2-plain">{f.rows.slice(0, 8).map((x) => <li key={`${x.target}:${x.verdict}`}>{f.labels[x.verdict] ?? x.verdict} · {x.title}{x.gate ? ` (gate: ${x.gate})` : ""}</li>)}</ul>
    </section>
  );
}

function MemorySection({ m }: { m: Memory }) {
  const l = m.leads;
  return (
    <section className="t2-section">
      <h4>Story memory and X leads</h4>
      <div className="t2-row"><span>Stories in the last {m.days} days</span><strong>{m.storylines} · {m.told} told · {m.followed_up} followed up</strong></div>
      <div className="t2-row"><span>X leads</span><strong>{l.seen} seen · {l.confirmed} confirmed · {l.unconfirmed} unconfirmed{l.median_lead_minutes !== null ? ` · ${l.median_lead_minutes} min ahead (median)` : ""}</strong></div>
      {l.accounts.length > 0 && <ul className="t2-plain">{l.accounts.map((a) => <li key={a.account}>{a.account}: {a.confirmed} of {a.leads} leads confirmed</li>)}</ul>}
      <p className="t2-fine">Follow-up check: {m.enforced ? "enforced" : "shadow (the current repeat gate still decides; disagreements appear in the report)"}.</p>
    </section>
  );
}

function PerfSection({ p }: { p: Perf }) {
  return (
    <section className="t2-section">
      <h4>How posts performed</h4>
      {p.measured === 0 ? <p className="t2-meta">{p.note}</p> : (
        <>
          <p className="t2-fine">{p.note} {p.excluded_bait_or_boring ? `${p.excluded_bait_or_boring} post(s) worded as bait or marked Boring are shown but teach nothing.` : ""}</p>
          <ul className="t2-plain">{p.best.map((x, i) => <li key={i}>{x.index.toFixed(2)}× · {x.title}{x.bait ? " (bait wording)" : ""}</li>)}</ul>
          {p.by_topic.length > 0 && <p className="t2-fine">By topic: {p.by_topic.map((t) => `${t.topic} ${t.index.toFixed(2)}× (${t.posts})`).join(" · ")}</p>}
          {p.by_hook.length > 0 && <p className="t2-fine">By hook: {p.by_hook.map((t) => `${t.hook} ${t.index.toFixed(2)}× (${t.posts})`).join(" · ")}</p>}
        </>
      )}
      <p className="t2-fine">Telegram: {p.telegram}.</p>
    </section>
  );
}
