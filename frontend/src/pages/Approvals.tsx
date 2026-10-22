import { useCallback, useState, type FormEvent } from "react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import type { Approval, ApprovalStatus, GuardDecision, GuardSettings, Permission, PermissionMode, Platform, SocialAction } from "../api/types";
import { GuardChecks, compact } from "../components/social";
import { ApiErrorBanner, Badge, Card, Empty, Loading, PageHeader, Tabs, formatTime, label, relativeTime, type Tone } from "../components/ui";

type Tab = "queue" | "permissions" | "check";

export function ApprovalsPage() {
  const [tab, setTab] = useState<Tab>("queue");
  const guard = useApi(api.guard, 15000);
  const g = guard.data;
  return (
    <>
      <PageHeader title="Approvals" subtitle="Every X action Price Chat proposes waits here for you. Nothing is sent without your approval." />
      {g && (
        <div className={`banner ${!g.social_actions_active ? "banner-warn" : g.dry_run ? "banner-info" : "banner-good"}`}>
          {!g.social_actions_active ? (
            <>
              <strong>Social actions are PAUSED.</strong> Approving an action records your decision, but the safety guard blocks it.
            </>
          ) : g.dry_run ? (
            <>
              <strong>DRY RUN.</strong> Approved actions go through every safety check and show exactly what WOULD be sent, but nothing reaches X.
              Turn dry run off on X / Twitter › Overview when you are ready.
            </>
          ) : (
            <>
              <strong>LIVE (supervised).</strong> Approving sends the action to X after the safety guard checks it again.
            </>
          )}
        </div>
      )}
      <Tabs
        tabs={[
          { key: "queue", label: "Approval queue" },
          { key: "permissions", label: "Permissions & safety" },
          { key: "check", label: "Test the safety check" },
        ]}
        value={tab}
        onChange={setTab}
      />
      {tab === "queue" && <Queue />}
      {tab === "permissions" && <Permissions />}
      {tab === "check" && <GuardTester />}
    </>
  );
}

const STATUS_TONE: Record<ApprovalStatus, Tone> = { pending: "warn", approved: "info", rejected: "neutral", expired: "neutral", executed: "good", failed: "bad" };
const FILTERS: { key: ApprovalStatus | ""; label: string }[] = [
  { key: "pending", label: "Pending" },
  { key: "approved", label: "Approved (dry run)" },
  { key: "executed", label: "Executed" },
  { key: "rejected", label: "Rejected" },
  { key: "failed", label: "Failed" },
  { key: "expired", label: "Expired" },
  { key: "", label: "All" },
];

function Queue() {
  const [status, setStatus] = useState<ApprovalStatus | "">("pending");
  const fetcher = useCallback(() => api.approvals(status || undefined), [status]);
  const { data, failure, reload } = useApi(fetcher, 20000);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  async function testPost() {
    try {
      await api.prepareTestPost();
      setNotice("A harmless test post is waiting below. Nothing is sent until you approve it.");
      setError(null);
      setStatus("pending");
      await reload();
    } catch (e) {
      setError(e);
    }
  }
  return (
    <Card
      title="Approval queue"
      actions={
        <div className="btn-group">
          {FILTERS.map((f) => (
            <button key={f.key} className={`btn btn-sm ${status === f.key ? "btn-primary" : ""}`} onClick={() => setStatus(f.key)}>
              {f.label}
            </button>
          ))}
        </div>
      }
    >
      <div className="row">
        <p className="muted small">Proposed replies, posts, likes, reposts and follows. Pending items expire after 48 hours.</p>
        <button className="btn btn-sm" onClick={() => void testPost()}>
          Prepare test post
        </button>
      </div>
      {notice && <div className="banner banner-good">{notice}</div>}
      <ApiErrorBanner error={failure ?? error} />
      {!data ? (
        <Loading />
      ) : data.length === 0 ? (
        <Empty>Nothing here.</Empty>
      ) : (
        <div className="stack">
          {data.map((a) => (
            <ApprovalItem key={`${a.id}-${a.status}`} a={a} onChanged={() => void reload()} />
          ))}
        </div>
      )}
    </Card>
  );
}

function Target({ a }: { a: Approval }) {
  const p = a.payload;
  if (a.action === "follow") {
    const t = p.target_profile;
    return (
      <div>
        <div className="field-label">Account that will be followed</div>
        <a className="link" href={`https://x.com/${a.target_username}`} target="_blank" rel="noreferrer">
          @{a.target_username}
        </a>{" "}
        {t?.name && <span>({t.name})</span>} {t?.followers != null && <span className="muted small">· {compact(t.followers)} followers</span>}
        <span className="muted small mono"> · user id {a.target_external_id}</span>
      </div>
    );
  }
  if (!p.original_post && !a.target_external_id) return null;
  return (
    <div>
      <div className="field-label">
        {a.action === "like" ? "Post that will be liked" : a.action === "repost" ? "Post that will be reposted" : a.action === "reply" ? "Replying to" : "Quoting"}
      </div>
      {p.original_post ? (
        <div className="x-post">
          <div className="x-post-head">
            <strong>@{p.original_post.author ?? a.target_username}</strong>
            <span className="muted small">{formatTime(p.original_post.posted_at)}</span>
            {p.original_post.url && (
              <a className="link small push-right" href={p.original_post.url} target="_blank" rel="noreferrer">
                open on X ↗
              </a>
            )}
          </div>
          <div className="x-post-text">{p.original_post.text}</div>
        </div>
      ) : (
        <span className="mono small">
          post {a.target_external_id} by @{a.target_username}
        </span>
      )}
    </div>
  );
}

function ApprovalItem({ a, onChanged }: { a: Approval; onChanged: () => void }) {
  const p = a.payload;
  const hasText = a.action === "post" || a.action === "reply";
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(p.text ?? "");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [open, setOpen] = useState(false);
  const ex = a.execution_result;
  const g = a.guard_result;

  async function decide(d: "approved" | "rejected", withText?: string) {
    if (d === "approved" && !window.confirm(`Approve this ${a.action}? The safety guard checks it again, then it is sent to X (or only simulated while dry run is on).`)) return;
    setBusy(true);
    try {
      await api.decideApproval(a.id, d, note, withText);
      setError(null);
      onChanged();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="opp">
      <div className="opp-head">
        <strong>{label(a.action)} on X</strong>
        <Badge tone={STATUS_TONE[a.status]}>{a.status === "approved" && ex.dry_run ? "dry run" : a.status}</Badge>
        {p.confidence && <Badge tone={p.confidence === "high" ? "good" : p.confidence === "low" ? "warn" : "neutral"}>{p.confidence} confidence</Badge>}
        {a.target_username && a.action !== "follow" && <span className="muted">to @{a.target_username}</span>}
        <span className="muted small push-right">
          #{a.id} · {label(a.source_kind)} · {relativeTime(a.created_at)}
          {a.status === "pending" && a.expires_at ? ` · expires ${formatTime(a.expires_at)}` : ""}
        </span>
      </div>

      {a.action === "like" && (
        <div className="small">
          <strong>Action:</strong> Like · <strong>Target author:</strong> @{a.target_username}
          {p.target_profile?.followers != null && <span className="muted"> ({compact(p.target_profile.followers)} followers)</span>}
          {p.daily_limit != null && (
            <span className="muted">
              {" "}
              · likes today when proposed: {p.daily_likes_used ?? 0} / {p.daily_limit}
            </span>
          )}
          <span className="muted"> · created {formatTime(a.created_at)}</span>
        </div>
      )}
      <Target a={a} />

      {hasText && (
        <div>
          <div className="field-label">
            Proposed {a.action === "reply" ? "reply" : "post"}
            {p.original_text ? " (edited by you)" : ""} · {(editing ? text : p.text ?? "").length}/280
          </div>
          {editing ? (
            <textarea className="input textarea" rows={4} value={text} onChange={(e) => setText(e.target.value)} maxLength={1000} />
          ) : (
            <div className="draft-body">{p.text}</div>
          )}
          {p.original_text && <div className="muted small">Originally proposed: “{p.original_text}”</div>}
        </div>
      )}

      {(p.warnings ?? []).map((w) => (
        <div key={w} className="banner banner-warn">{w}</div>
      ))}

      <div className="small">
        {p.reason && (
          <div>
            <span className="field-label">Why: </span>
            {p.reason}
          </div>
        )}
        {(p.model || p.topic) && (
          <div className="muted">
            {p.topic ? `Topic: ${p.topic}` : ""}
            {p.topic && p.model ? " · " : ""}
            {p.model ? `Written by ${p.model}` : ""}
          </div>
        )}
        {(p.context ?? []).length > 0 && (
          <button className="link-btn" onClick={() => setOpen(!open)}>
            {open ? "Hide context" : `Context used (${p.context!.length})`}
          </button>
        )}
        {open && (
          <ul className="reasons">
            {p.context!.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="row small">
        <Badge tone={g.allowed ? "good" : "bad"}>{g.allowed ? "passes safety checks" : "blocked by safety checks"}</Badge>
        <span className="muted">{g.reason}</span>
      </div>
      {!g.allowed && <GuardChecks checks={g.checks} />}

      {ex.dry_run && ex.would_execute && (
        <div className="banner banner-info">
          <strong>{a.action === "like" ? "WOULD LIKE POST" : "WOULD EXECUTE"}</strong> (dry run, nothing sent): {ex.would_execute.action}
          {ex.would_execute.target_account ? ` → @${ex.would_execute.target_account}` : ""}
          {ex.would_execute.target_post_id ? ` · post ${ex.would_execute.target_post_id}` : ""}
          {ex.would_execute.target_user_id ? ` · user ${ex.would_execute.target_user_id}` : ""}
          {ex.would_execute.text ? <div className="draft-body">{ex.would_execute.text}</div> : null}
        </div>
      )}
      {a.status === "executed" && (
        <div className="banner banner-good">
          <strong>{a.action === "like" ? "LIKED" : "Executed on X"}</strong> {formatTime(a.executed_at)}
          {ex.url ? (
            <>
              {" "}
              ·{" "}
              <a className="link" href={ex.url} target="_blank" rel="noreferrer">
                view on X ↗
              </a>
            </>
          ) : null}
          {ex.x_id ? <span className="mono small"> · id {ex.x_id}</span> : null}
          {a.performance && (
            <div className="x-metrics">
              {(["impressions", "likes", "replies", "reposts", "bookmarks"] as const).map((k) => (
                <span key={k}>
                  {compact(a.performance?.[k])} {k}
                </span>
              ))}
            </div>
          )}
          {!a.performance && hasText && <div className="muted small">Performance appears after the next X read.</div>}
        </div>
      )}
      {a.status === "failed" && (
        <div className="banner banner-error">
          <strong>Not executed.</strong> {ex.reason ?? ex.error}
          {ex.uncertain && <div>The request may still have reached X: check your X account before trying again.</div>}
        </div>
      )}
      {a.notes && <div className="muted small">Your note: {a.notes}</div>}
      <ApiErrorBanner error={error} />

      {a.status === "pending" && (
        <div className="row">
          <input className="input" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} />
          <div className="btn-group">
            {editing ? (
              <>
                <button className="btn btn-sm btn-primary" disabled={busy || !text.trim() || text.length > 280} onClick={() => void decide("approved", text)}>
                  Approve edited
                </button>
                <button className="btn btn-sm btn-ghost" onClick={() => setEditing(false)}>
                  Cancel edit
                </button>
              </>
            ) : (
              <>
                <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void decide("approved")}>
                  {busy ? "Working…" : a.action === "like" ? "Approve like" : "Approve"}
                </button>
                {hasText && (
                  <button className="btn btn-sm" disabled={busy} onClick={() => setEditing(true)}>
                    Edit & approve
                  </button>
                )}
              </>
            )}
            <button className="btn btn-sm" disabled={busy} onClick={() => void decide("rejected")}>
              Reject
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

const MODE_TEXT: Record<PermissionMode, string> = { disabled: "Disabled", approval: "Approval required", automatic: "Automatic (locked)" };

function Permissions() {
  const { data, failure, setData } = useApi(api.guard);
  const [error, setError] = useState<unknown>(null);
  const [blocked, setBlocked] = useState<string | null>(null);
  async function save(fn: () => Promise<GuardSettings>) {
    try {
      setData(await fn());
      setError(null);
    } catch (e) {
      setError(e);
    }
  }
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const byPlatform = data.permissions.reduce<Record<string, Permission[]>>((acc, p) => ((acc[p.platform] ??= []).push(p), acc), {});
  return (
    <>
      <ApiErrorBanner error={error} />
      <Card
        title="Social actions"
        actions={
          <div className="switch-row">
            <Badge tone={data.social_actions_active ? "good" : "warn"}>{data.social_actions_active ? "ACTIVE" : "PAUSED"}</Badge>
            <button className={`btn btn-sm ${data.social_actions_active ? "" : "btn-primary"}`} onClick={() => void save(() => api.setSocialActions(!data.social_actions_active))}>
              {data.social_actions_active ? "Pause all" : "Activate"}
            </button>
            <Badge tone={data.dry_run ? "info" : "warn"}>{data.dry_run ? "DRY RUN" : "LIVE"}</Badge>
            <button
              className="btn btn-sm"
              onClick={() => {
                if (data.dry_run && !window.confirm("Turn dry run OFF? Actions you approve will really be sent to X.")) return;
                void save(() => api.updateGuard({ dry_run: !data.dry_run }));
              }}
            >
              {data.dry_run ? "Go live" : "Back to dry run"}
            </button>
          </div>
        }
      >
        <p className="muted small">{data.note}</p>
        <p className="muted small">Pausing blocks every new write immediately. Reading, analysis and proposals continue.</p>
      </Card>
      <Card title="Permissions per action">
        <p className="muted small">The limits are safety ceilings, not targets: Price Chat never acts just to use an allowance.</p>
        {Object.entries(byPlatform).map(([platform, perms]) => (
          <table key={platform} className="table">
            <thead>
              <tr>
                <th>{label(platform)}</th>
                <th>Permission</th>
                <th>Max per hour</th>
                <th>Max per day</th>
              </tr>
            </thead>
            <tbody>
              {perms.map((p) => (
                <tr key={p.action}>
                  <td>{label(p.action)}</td>
                  <td>
                    <select className="input select select-sm" value={p.mode} onChange={(e) => void save(() => api.setPermission(p.platform, p.action, { mode: e.target.value as PermissionMode }))}>
                      {(["disabled", "approval", "automatic"] as PermissionMode[]).map((m) => (
                        <option key={m} value={m} disabled={!p.available_modes.includes(m)}>
                          {MODE_TEXT[m]}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input
                      className="input input-sm"
                      type="number"
                      min={0}
                      max={1000}
                      defaultValue={p.max_per_hour}
                      onBlur={(e) => Number(e.target.value) !== p.max_per_hour && void save(() => api.setPermission(p.platform, p.action, { max_per_hour: Number(e.target.value) }))}
                    />
                  </td>
                  <td>
                    <input
                      className="input input-sm"
                      type="number"
                      min={0}
                      max={10000}
                      defaultValue={p.max_per_day}
                      onBlur={(e) => Number(e.target.value) !== p.max_per_day && void save(() => api.setPermission(p.platform, p.action, { max_per_day: Number(e.target.value) }))}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}
        <p className="muted small">Automatic: {data.automatic_locked_reason}.</p>
      </Card>
      <Card title="Safety rules">
        <dl className="kv">
          <dt>Platforms allowed</dt>
          <dd className="row">
            {(["x", "telegram"] as Platform[]).map((pl) => (
              <label key={pl} className="row small">
                <input
                  type="checkbox"
                  checked={data.platforms_enabled[pl] ?? true}
                  onChange={(e) => void save(() => api.updateGuard({ platforms_enabled: { ...data.platforms_enabled, [pl]: e.target.checked } }))}
                />
                {label(pl)}
              </label>
            ))}
          </dd>
          <dt>Only watched accounts</dt>
          <dd>
            <label className="row small">
              <input type="checkbox" checked={data.watchlist_only} onChange={(e) => void save(() => api.updateGuard({ watchlist_only: e.target.checked }))} />
              Only interact with accounts on the X watchlist
            </label>
          </dd>
          <dt>Cooldown per account</dt>
          <dd>
            <input className="input input-sm" type="number" min={0} max={10080} defaultValue={data.cooldown_minutes} onBlur={(e) => void save(() => api.updateGuard({ cooldown_minutes: Number(e.target.value) }))} /> minutes
            <span className="muted small"> · replies to the same account: at most one per hour</span>
          </dd>
          <dt>Block duplicate text for</dt>
          <dd>
            <input className="input input-sm" type="number" min={0} max={720} defaultValue={data.duplicate_window_hours} onBlur={(e) => void save(() => api.updateGuard({ duplicate_window_hours: Number(e.target.value) }))} /> hours
          </dd>
          <dt>Blocked accounts</dt>
          <dd>
            <textarea className="input textarea" rows={3} defaultValue={data.blocked_targets.join("\n")} placeholder="One @username per line" onChange={(e) => setBlocked(e.target.value)} />
            {blocked !== null && (
              <button
                className="btn btn-sm"
                onClick={() => {
                  const list = blocked.split("\n").map((x) => x.trim()).filter(Boolean);
                  setBlocked(null);
                  void save(() => api.updateGuard({ blocked_targets: list }));
                }}
              >
                Save blocked accounts
              </button>
            )}
          </dd>
        </dl>
      </Card>
    </>
  );
}

const ACTIONS: SocialAction[] = ["post", "reply", "like", "repost", "follow"];

function GuardTester() {
  const [action, setAction] = useState<SocialAction>("reply");
  const [target, setTarget] = useState("");
  const [text, setText] = useState("");
  const [result, setResult] = useState<GuardDecision | null>(null);
  const [error, setError] = useState<unknown>(null);
  async function check(e: FormEvent) {
    e.preventDefault();
    try {
      setResult(await api.guardCheck({ platform: "x", action, target: target.trim() || undefined, text: text.trim() || undefined }));
      setError(null);
    } catch (err) {
      setError(err);
    }
  }
  return (
    <Card title="Test the safety check">
      <p className="muted small">See exactly which rule would allow or block an action. Nothing is proposed, sent or recorded as an attempt.</p>
      <form className="goal-add" onSubmit={check}>
        <select className="input select" value={action} onChange={(e) => setAction(e.target.value as SocialAction)} aria-label="Action">
          {ACTIONS.map((a) => (
            <option key={a} value={a}>
              {label(a)}
            </option>
          ))}
        </select>
        <input className="input" placeholder="@account (optional)" value={target} onChange={(e) => setTarget(e.target.value)} maxLength={64} />
        <input className="input" placeholder="Text (optional)" value={text} onChange={(e) => setText(e.target.value)} />
        <button className="btn btn-primary">Check</button>
      </form>
      <ApiErrorBanner error={error} />
      {result && (
        <div className="stack">
          <div className="row">
            <Badge tone={result.allowed ? "good" : "bad"}>{result.allowed ? "allowed by rules" : "blocked"}</Badge>
            {result.requires_approval && <Badge tone="warn">needs your approval</Badge>}
            <span>{result.reason}</span>
          </div>
          <GuardChecks checks={result.checks} />
        </div>
      )}
    </Card>
  );
}
