import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import { useApi } from "../../api/useApi";
import type { Approval, FeedItem, GrowthRow, LikeCandidate, LikeStatus } from "../../api/types";
import { compact, Reasons, SignalBars } from "../../components/social";
import { ApiErrorBanner, Badge, Card, Empty, Loading, Stat, formatTime, label, relativeTime, type Tone } from "../../components/ui";

const ROW_TONE: Record<string, Tone> = {
  ready: "good", needs_setup: "warn", waiting: "info", problem: "bad", paused: "warn", dry_run: "info", locked: "neutral", disabled: "neutral", supervised: "good",
};
const ROW_TEXT: Record<string, string> = {
  ready: "READY", needs_setup: "NEEDS SETUP", waiting: "WAITING", problem: "PROBLEM", paused: "PAUSED", dry_run: "DRY RUN", locked: "LOCKED", disabled: "DISABLED", supervised: "SUPERVISED",
};
const LABEL_TONE: Record<string, Tone> = { high: "warn", medium: "info", low: "neutral" };
const SOURCE_TONE: Record<string, Tone> = { X: "neutral", Telegram: "info", "Telegram + X": "good" };

export function GrowthPilot() {
  return (
    <>
      <StatusPanel />
      <ReviewSession />
      <LikesCard />
      <div className="grid-2">
        <LearningCard />
        <AnalyticsCard />
      </div>
      <Feed />
      <div className="grid-2">
        <CreatorsCard />
        <div className="stack">
          <SummaryCard />
          <UsageCard />
        </div>
      </div>
    </>
  );
}

function StatusPanel() {
  const { data, failure } = useApi(api.growthStatus, 20000);
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const t = data.today;
  return (
    <Card title="Price Chat social growth pilot" actions={<Badge tone="good">MODE · SUPERVISED</Badge>}>
      <table className="table">
        <tbody>
          {data.rows.map((r: GrowthRow) => (
            <tr key={r.key}>
              <td className="nowrap">
                <strong>{r.label}</strong>
              </td>
              <td className="nowrap">
                <Badge tone={ROW_TONE[r.status] ?? "neutral"}>{ROW_TEXT[r.status] ?? r.status}</Badge>
              </td>
              <td>
                {r.detail}
                {r.next_step && r.status !== "ready" && <div className="muted small">Next: {r.next_step}</div>}
              </td>
            </tr>
          ))}
          <tr>
            <td>
              <strong>Social actions</strong>
            </td>
            <td>
              <Badge tone={data.social_actions_active ? "good" : "warn"}>{data.social_actions_active ? "ACTIVE" : "PAUSED"}</Badge>
            </td>
            <td className="muted small">Pause or resume on X / Twitter › Overview.</td>
          </tr>
        </tbody>
      </table>
      <div className="field-label" style={{ marginTop: 12 }}>
        Today
      </div>
      <div className="stats">
        <Stat label="Telegram messages" value={t.telegram_messages} />
        <Stat label="X posts read" value={t.x_posts_read} />
        <Stat label="Cross-platform signals" value={t.cross_signals} hint={t.early_signals ? `${t.early_signals} early community signals` : undefined} />
        <Stat label="Opportunities found" value={t.opportunities_found} />
        <Stat label="Drafts generated" value={t.drafts_generated} />
        <Stat label="Approved / edited / rejected" value={`${t.approved} / ${t.edited} / ${t.rejected}`} hint={`${t.skipped} skipped`} />
        <Stat label="Executed / failed" value={`${t.executed} / ${t.failed}`} tone={t.failed ? "warn" : "neutral"} />
      </div>
    </Card>
  );
}

// --- review session -----------------------------------------------------------------------------------

function ReviewSession() {
  const [items, setItems] = useState<FeedItem[] | null>(null);
  const [filtered, setFiltered] = useState(0);
  const [index, setIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [log, setLog] = useState<string[]>([]);

  async function start() {
    setBusy(true);
    try {
      const s = await api.reviewSession(8);
      setItems(s.items);
      setFiltered(s.filtered_out);
      setIndex(0);
      setLog([]);
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  const current = items?.[index];
  return (
    <Card
      title="Review session"
      actions={
        <button className="btn btn-primary" disabled={busy} onClick={() => void start()}>
          {busy ? "Gathering…" : items ? "Start a new session" : "Start review session"}
        </button>
      }
    >
      <ApiErrorBanner error={error} />
      {!items ? (
        <p className="muted small">
          Gathers the 5–10 strongest opportunities from Telegram and X that passed the quality filter, and shows them one at a time with a proposed
          action. You approve, edit, reject or skip. Nothing is sent without your approval.
        </p>
      ) : items.length === 0 ? (
        <Empty>Nothing strong enough right now{filtered ? ` (${filtered} weaker items were filtered out)` : ""}. That is fine: zero actions is acceptable.</Empty>
      ) : current ? (
        <>
          <div className="muted small">
            Item {index + 1} of {items.length}
            {filtered ? ` · ${filtered} weaker items filtered out` : ""}
          </div>
          <ReviewItem key={current.key} item={current} onDone={(msg) => { setLog((l) => [...l, msg]); setIndex((i) => i + 1); }} />
        </>
      ) : (
        <div className="stack">
          <div className="banner banner-good">Session complete.</div>
          <ul className="reasons">
            {log.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
          <Link className="link" to="/approvals">
            See the results in Approvals →
          </Link>
        </div>
      )}
    </Card>
  );
}

function ReviewItem({ item, onDone }: { item: FeedItem; onDone: (summary: string) => void }) {
  const [approval, setApproval] = useState<Approval | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<Approval | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    api.prepareItem(item.key).then(
      (a) => {
        if (!alive) return;
        setApproval(a);
        setText(a.payload.text ?? "");
        setBusy(false);
      },
      (e) => {
        if (!alive) return;
        setError(e);
        setBusy(false);
      },
    );
    return () => {
      alive = false;
    };
  }, [item.key]);

  async function decide(kind: "approve" | "reject" | "skip") {
    if (!approval) return onDone(`${item.topic}: skipped (no draft)`);
    setBusy(true);
    try {
      if (kind === "skip") {
        await api.skipApproval(approval.id);
        onDone(`${item.topic}: skipped`);
        return;
      }
      const edited = text.trim() !== (approval.payload.text ?? "").trim();
      const a = await api.decideApproval(approval.id, kind === "approve" ? "approved" : "rejected", "", kind === "approve" && edited ? text.trim() : undefined);
      setResult(a);
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  const p = approval?.payload;
  return (
    <div className="opp">
      <div className="opp-head">
        <strong>{item.topic}</strong>
        <Badge tone={SOURCE_TONE[item.source]}>{item.source}</Badge>
        <Badge tone={LABEL_TONE[item.label]}>{item.label} priority</Badge>
        {item.recency_hours != null && <span className="muted small">{item.recency_hours < 1 ? "under 1 h ago" : `${Math.round(item.recency_hours)} h ago`}</span>}
      </div>
      {item.post && (
        <div className="x-post">
          <div className="x-post-head">
            <strong>@{item.post.author}</strong>
            {item.post.followers != null && <span className="muted small">{compact(item.post.followers)} followers</span>}
            {item.post.url && (
              <a className="link small push-right" href={item.post.url} target="_blank" rel="noreferrer">
                open on X ↗
              </a>
            )}
          </div>
          <div className="x-post-text">{item.post.text}</div>
        </div>
      )}
      <div className="small">
        <div>
          <span className="field-label">Why: </span>
          {item.why}
        </div>
        {item.evidence.map((e) => (
          <div key={e} className="muted">
            {e}
          </div>
        ))}
        {item.relationship && <div className="muted">Relationship: {item.relationship}</div>}
        {item.community && <div className="muted">Community: {item.community}</div>}
        {item.knowledge.length > 0 && <div className="muted">Price Chat knowledge: {item.knowledge.join("; ")}</div>}
        <button className="link-btn" onClick={() => setOpen(!open)}>
          {open ? "Hide ranking" : "Why this rank?"}
        </button>
        {open && (
          <div className="ranking-detail">
            <SignalBars signals={item.signals} />
            <Reasons reasons={item.reasons} />
          </div>
        )}
      </div>

      {busy && !approval && <div className="muted">Writing a proposal…</div>}
      <ApiErrorBanner error={error} />
      {approval && !result && (
        <>
          <div className="field-label">
            Proposed {label(approval.action).toLowerCase()} {approval.target_username ? `to @${approval.target_username}` : ""} · {text.length}/280
            {p?.model ? ` · ${p.model}` : ""}
          </div>
          {(p?.variants ?? []).length > 1 && (
            <div className="btn-group">
              {p!.variants!.map((v) => (
                <button key={v.style} className={`btn btn-sm ${text === v.text ? "btn-primary" : ""}`} onClick={() => setText(v.text)}>
                  {label(v.style)}
                  {v.warnings.length ? " ⚠" : ""}
                </button>
              ))}
            </div>
          )}
          <textarea className="input textarea" rows={4} value={text} onChange={(e) => setText(e.target.value)} />
          {(p?.warnings ?? []).map((w) => (
            <div key={w} className="banner banner-warn">{w}</div>
          ))}
          <div className="btn-group">
            <button className="btn btn-primary" disabled={busy || !text.trim() || text.length > 280} onClick={() => void decide("approve")}>
              {text.trim() !== (approval.payload.text ?? "").trim() ? "Approve edited" : "Approve"}
            </button>
            <button className="btn" disabled={busy} onClick={() => void decide("reject")}>
              Reject
            </button>
            <button className="btn btn-ghost" disabled={busy} onClick={() => void decide("skip")}>
              Skip
            </button>
          </div>
        </>
      )}
      {!approval && !busy && (
        <div className="btn-group">
          <button className="btn btn-ghost" onClick={() => onDone(`${item.topic}: skipped (${error ? "no draft" : "nothing to add"})`)}>
            Next
          </button>
        </div>
      )}
      {result && (
        <>
          <div className={`banner ${result.status === "executed" ? "banner-good" : result.status === "failed" ? "banner-error" : "banner-info"}`}>
            {result.status === "executed" && (
              <>
                Executed on X.{" "}
                {result.execution_result.url && (
                  <a className="link" href={String(result.execution_result.url)} target="_blank" rel="noreferrer">
                    view ↗
                  </a>
                )}
              </>
            )}
            {result.status === "approved" && result.execution_result.dry_run && <>Dry run: WOULD EXECUTE this {result.action}. Nothing was sent.</>}
            {result.status === "rejected" && <>Rejected. Price Chat will learn from it.</>}
            {result.status === "failed" && <>Not executed: {String(result.execution_result.reason ?? result.execution_result.error)}</>}
          </div>
          <button className="btn btn-primary" onClick={() => onDone(`${item.topic}: ${result.status === "approved" && result.execution_result.dry_run ? "approved (dry run)" : result.status}`)}>
            Next
          </button>
        </>
      )}
    </div>
  );
}

// --- likes pilot ----------------------------------------------------------------------------------------

const CAP_TEXT: Record<LikeStatus["capability"], [Tone, string]> = {
  available: ["good", "LIKE AVAILABLE"],
  unavailable: ["bad", "LIKE NOT ALLOWED"],
  unknown: ["warn", "NOT TESTED"],
  needs_user_auth: ["warn", "NEEDS USER AUTH"],
  not_configured: ["warn", "X NOT CONNECTED"],
};

function LikesCard() {
  const [showFiltered, setShowFiltered] = useState(false);
  const { data, failure, reload } = useApi(() => api.likePilot(true), 60000);
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const s = data.status;
  const [tone, capText] = CAP_TEXT[s.capability] ?? ["neutral", s.capability];
  return (
    <Card
      title="Likes: AI-recommended"
      actions={
        <div className="row small">
          <Badge tone={tone}>{capText}</Badge>
          <Badge tone={s.dry_run ? "info" : "good"}>{s.dry_run ? "DRY RUN" : "LIVE"}</Badge>
          <Badge tone="neutral">AUTOMATIC LOCKED</Badge>
        </div>
      }
    >
      <p className="muted small">
        The strongest few posts the AI thinks are worth a like. Each like needs your approval, then passes the safety guard before it reaches X.
        Zero likes is a fine answer.
      </p>
      <div className="stats">
        <Stat label="Likes today" value={`${s.used_today} / ${s.daily_limit}`} />
        <Stat label="This hour" value={`${s.used_this_hour} / ${s.hourly_limit}`} />
        <Stat label="Per account" value={`1 per ${s.account_cooldown_hours} h`} />
      </div>
      {s.problems.map((p) => (
        <div key={p} className="banner banner-warn">
          {p}
        </div>
      ))}
      {data.candidates.length === 0 ? (
        <Empty>No like candidates right now{data.filtered_out ? ` (${data.filtered_out} weaker suggestions filtered out)` : ""}.</Empty>
      ) : (
        <div className="stack">
          {data.candidates.map((c) => (
            <LikeItem key={c.opportunity_id} c={c} status={s} onDone={() => void reload()} />
          ))}
        </div>
      )}
      <label className="row small" style={{ justifyContent: "flex-start", marginTop: 8 }}>
        <input type="checkbox" checked={showFiltered} onChange={(e) => setShowFiltered(e.target.checked)} /> show filtered suggestions
        {data.more ? ` · ${data.more} more candidates held back` : ""}
      </label>
      {showFiltered &&
        (data.filtered ?? []).map((f) => (
          <div key={f.opportunity_id} className="muted small">
            @{f.post.author}: {f.post.text.slice(0, 90)}… · <span className="tone-bad">{(f.problems ?? []).join("; ")}</span>
          </div>
        ))}
    </Card>
  );
}

function LikeItem({ c, status, onDone }: { c: LikeCandidate; status: LikeStatus; onDone: () => void }) {
  const [approval, setApproval] = useState<Approval | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function run(fn: () => Promise<Approval>) {
    setBusy(true);
    try {
      setApproval(await fn());
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  const review = () => run(() => api.proposeLike(c.opportunity_id));
  async function decide(approve: boolean) {
    if (!approval) return;
    if (approve && !window.confirm(`Like @${c.post.author}'s post?${status.dry_run ? " (Dry run: nothing is sent to X.)" : " This is sent to X now."}`)) return;
    await run(() => api.decideApproval(approval.id, approve ? "approved" : "rejected", ""));
  }

  const ex = approval?.execution_result;
  const g = approval?.guard_result;
  const p = approval?.payload;
  return (
    <div className="opp">
      <div className="opp-head">
        <span className="field-label">Suggested action</span>
        <Badge tone="info">Like</Badge>
        <strong>{c.topic}</strong>
        {c.relationship.watched && <Badge>monitored</Badge>}
        {c.post.url && (
          <a className="link small push-right" href={c.post.url} target="_blank" rel="noreferrer">
            open on X ↗
          </a>
        )}
      </div>
      <div className="x-post">
        <div className="x-post-head">
          <strong>@{c.post.author}</strong>
          {c.post.followers != null && <span className="muted small">{compact(c.post.followers)} followers</span>}
          <span className="muted small">{relativeTime(c.post.posted_at)}</span>
        </div>
        <div className="x-post-text">{c.post.text}</div>
      </div>
      <div className="small">
        <span className="field-label">Reason: </span>
        {c.reason}
        <div className="muted">{c.signals.join(" · ")}</div>
      </div>
      <ApiErrorBanner error={error} />

      {!approval && (
        <div className="btn-group">
          <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void review()}>
            {c.pending_approval_id ? "Review pending like" : "Review like"}
          </button>
        </div>
      )}

      {approval && approval.status === "pending" && (
        <>
          <div className="small">
            Target <strong>@{approval.target_username}</strong> · post <span className="mono">{approval.target_external_id}</span> · likes today{" "}
            {p?.daily_likes_used ?? status.used_today} / {p?.daily_limit ?? status.daily_limit}
          </div>
          <div className="row small">
            <Badge tone={g?.allowed ? "good" : "bad"}>{g?.allowed ? "passes safety checks" : "blocked by safety checks"}</Badge>
            <span className="muted">{g?.reason}</span>
          </div>
          <div className="btn-group">
            <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void decide(true)}>
              {busy ? "Working…" : "Approve like"}
            </button>
            <button className="btn btn-sm" disabled={busy} onClick={() => void decide(false)}>
              Reject
            </button>
          </div>
        </>
      )}

      {approval && approval.status !== "pending" && (
        <>
          {approval.status === "executed" && (
            <div className="banner banner-good">
              <strong>LIKED</strong> {formatTime(approval.executed_at)}
              {ex?.url && (
                <>
                  {" "}
                  ·{" "}
                  <a className="link" href={ex.url} target="_blank" rel="noreferrer">
                    view post on X ↗
                  </a>
                </>
              )}
            </div>
          )}
          {approval.status === "approved" && ex?.dry_run && (
            <div className="banner banner-info">
              <strong>WOULD LIKE POST</strong> by @{approval.target_username} (dry run: nothing was sent to X).
            </div>
          )}
          {approval.status === "failed" && (
            <div className="banner banner-error">
              <strong>Failed.</strong> {ex?.reason ?? ex?.error}
            </div>
          )}
          {approval.status === "rejected" && <div className="banner banner-info">Rejected. Nothing was sent.</div>}
          <button className="btn btn-sm" onClick={onDone}>
            Next
          </button>
        </>
      )}
    </div>
  );
}

// --- feed -----------------------------------------------------------------------------------------------

function Feed() {
  const [showFiltered, setShowFiltered] = useState(false);
  const [only, setOnly] = useState<"" | "Like">("");
  const { data: all, failure } = useApi(() => api.growthFeed(true), 60000);
  if (!all) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const data = only ? all.filter((i) => i.suggested_actions.includes(only)) : all;
  const passed = data.filter((i) => i.passed);
  const weak = data.filter((i) => !i.passed);
  const shown = showFiltered ? data : passed;
  return (
    <Card
      title={`Opportunity feed (${passed.length})`}
      actions={
        <div className="row small">
          <select className="input input-sm" value={only} onChange={(e) => setOnly(e.target.value as "" | "Like")} aria-label="Suggested action">
            <option value="">All suggested actions</option>
            <option value="Like">Suggested action = Like</option>
          </select>
          {weak.length > 0 && (
            <label className="row small">
              <input type="checkbox" checked={showFiltered} onChange={(e) => setShowFiltered(e.target.checked)} /> show {weak.length} filtered out
            </label>
          )}
        </div>
      }
    >
      <p className="muted small">From Telegram, X, or both. Fewer, better opportunities: weak ones are filtered out with the reason.</p>
      {shown.length === 0 ? (
        <Empty>No opportunities yet.</Empty>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Topic</th>
              <th>Source</th>
              <th>Priority</th>
              <th>Suggested</th>
              <th>Why ranked</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((i) => (
              <tr key={i.key} className={i.passed ? "" : "muted"}>
                <td>
                  {i.topic}
                  <div className="muted small clamp">{i.title}</div>
                  {!i.passed && <div className="small tone-bad">Filtered: {i.problems.join("; ")}</div>}
                </td>
                <td>
                  <Badge tone={SOURCE_TONE[i.source]}>{i.source}</Badge>
                </td>
                <td>
                  <Badge tone={LABEL_TONE[i.label]}>{i.label}</Badge>
                </td>
                <td className="small">{i.suggested_actions.join(", ")}</td>
                <td className="small">{i.reasons.slice(0, 2).join("; ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

// --- learning, analytics, summary, usage, creators ---------------------------------------------------------

function LearningCard() {
  const { data, failure, setData } = useApi(api.learning);
  const [error, setError] = useState<unknown>(null);
  async function act(key: string, action: "accept" | "disable" | "reset" | "activate") {
    try {
      setData(await api.setLearning(key, action));
      setError(null);
    } catch (e) {
      setError(e);
    }
  }
  return (
    <Card title="What Price Chat is learning">
      <p className="muted small">From your approvals, edits, rejections and skips. Plain rules, no model training. Disabled rules are never used.</p>
      <ApiErrorBanner error={failure ?? error} />
      {!data ? (
        <Loading />
      ) : data.length === 0 ? (
        <Empty>Nothing learned yet. Review a few proposals first.</Empty>
      ) : (
        <ul className="feed">
          {data.map((s) => (
            <li key={s.key}>
              <Badge tone={s.state === "accepted" ? "good" : s.state === "disabled" ? "neutral" : "info"}>{s.state}</Badge>
              <div className={`feed-body ${s.state === "disabled" ? "muted" : ""}`}>
                <span>{s.text}</span>
              </div>
              <div className="btn-group">
                {s.state !== "accepted" && (
                  <button className="btn btn-sm btn-ghost" onClick={() => void act(s.key, "accept")}>
                    Accept
                  </button>
                )}
                {s.state === "disabled" ? (
                  <button className="btn btn-sm btn-ghost" onClick={() => void act(s.key, "activate")}>
                    Enable
                  </button>
                ) : (
                  <button className="btn btn-sm btn-ghost" onClick={() => void act(s.key, "disable")}>
                    Disable
                  </button>
                )}
                <button className="btn btn-sm btn-ghost" title="Forget the evidence so far" onClick={() => void act(s.key, "reset")}>
                  Reset
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

const pct = (v: number | null) => (v == null ? "-" : `${Math.round(v * 100)}%`);

function AnalyticsCard() {
  const { data, failure } = useApi(() => api.growthAnalytics(30), 60000);
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const ai = data.ai_drafts;
  const trust = ai.reviewed ? Math.round((100 * ai.approved_unchanged) / ai.reviewed) : null;
  return (
    <Card title="Pilot analytics (30 days)">
      <div className="stats">
        <Stat label="How often you trust the AI" value={trust == null ? "-" : `${trust}%`} hint={`${ai.approved_unchanged} unchanged · ${ai.approved_after_edit} edited · ${ai.rejected} rejected of ${ai.reviewed} AI drafts`} />
        <Stat label="Reviewed" value={data.reviewed} hint={`${data.skipped} skipped · ${data.ignored} expired`} />
        <Stat label="Approval / edit / rejection" value={`${pct(data.approval_rate)} / ${pct(data.edit_rate)} / ${pct(data.rejection_rate)}`} />
        <Stat label="Executed" value={data.executed} />
      </div>
      <dl className="kv">
        <dt>By source</dt>
        <dd className="small">
          {Object.keys(data.by_origin).length
            ? Object.entries(data.by_origin).map(([k, v]) => `${label(k)}: ${v.accepted}/${v.reviewed} accepted, ${v.executed} executed`).join(" · ")
            : "-"}
          {data.best_origin && <div>Best accepted ideas so far: {label(data.best_origin)}</div>}
        </dd>
        <dt>Top topics</dt>
        <dd className="small">{data.top_topics.length ? data.top_topics.map(([t, n]) => `${t} (${n})`).join(", ") : "-"}</dd>
        <dt>Relationships</dt>
        <dd className="small">
          replied to {data.relationships.accounts_replied_to} accounts; {data.relationships.of_which_interacted_since} of them interacted with us
        </dd>
      </dl>
      <p className="muted small">{data.note}</p>
    </Card>
  );
}

function SummaryCard() {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  return (
    <Card
      title="Daily internal summary"
      actions={
        <button className="btn btn-sm" onClick={() => void api.dailySummary().then((s) => setText(s.text), setError)}>
          Generate for today
        </button>
      }
    >
      <ApiErrorBanner error={error} />
      {text ? <pre className="summary-pre">{text}</pre> : <p className="muted small">Internal only: never posted anywhere.</p>}
    </Card>
  );
}

function UsageCard() {
  const { data, failure } = useApi(() => api.growthUsage(1), 60000);
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const tg = Object.values(data.telegram.requests).reduce((a, b) => a + b, 0);
  return (
    <Card title="Compute & API use (today)">
      <dl className="kv">
        <dt>X API</dt>
        <dd className="small">
          {data.x.today.requests} requests ({data.x.today.writes ?? 0} writes) · est. ${data.x.today.cost.toFixed(3)} of ${data.x.budget.daily_usd.toFixed(2)}
        </dd>
        <dt>Telegram</dt>
        <dd className="small">{tg} requests (no per-request cost)</dd>
        <dt>Fast model</dt>
        <dd className="small">
          {data.models.fast_calls} calls · {data.models.fast_seconds} s
        </dd>
        <dt>Strong model</dt>
        <dd className="small">
          {data.models.strong_calls} calls · {data.models.strong_seconds} s
        </dd>
      </dl>
      <p className="muted small">{data.x.note}</p>
    </Card>
  );
}

function CreatorsCard() {
  const { data, failure, reload } = useApi(api.creators, 120000);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  async function run(fn: () => Promise<unknown>, msg: string) {
    try {
      await fn();
      setNotice(msg);
      setError(null);
      await reload();
    } catch (e) {
      setError(e);
    }
  }
  return (
    <Card title="Creator discovery">
      <p className="muted small">Accounts that keep appearing in monitored X data. Price Chat never follows anyone on its own.</p>
      {notice && <div className="banner banner-good">{notice}</div>}
      <ApiErrorBanner error={failure ?? error} />
      {!data ? (
        <Loading />
      ) : data.length === 0 ? (
        <Empty>No new accounts stand out yet.</Empty>
      ) : (
        <ul className="feed">
          {data.map((c) => (
            <li key={c.profile_id}>
              <Badge>{compact(c.followers)}</Badge>
              <div className="feed-body">
                <span>
                  <strong>{c.name ?? c.username}</strong> <span className="muted">@{c.username}</span>
                </span>
                <span className="muted small">
                  {c.why} · relationship: {c.relationship}
                </span>
              </div>
              <div className="btn-group">
                <button className="btn btn-sm" onClick={() => void run(() => api.watchX({ username: c.username, category: "major_creator", reason: c.why }), `@${c.username} added to the watchlist.`)}>
                  Add to watchlist
                </button>
                <button className="btn btn-sm btn-ghost" onClick={() => void run(() => api.ignoreCreator(c.profile_id), `@${c.username} ignored.`)}>
                  Ignore
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="muted small">Last checked {relativeTime(new Date().toISOString())}.</p>
    </Card>
  );
}
