import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import type { ContentItemRow, YouTubeStatus } from "../api/types";
import { ContentItemsTable } from "../components/contentItems";
import { compact } from "../components/social";
import { ApiErrorBanner, Badge, Card, Loading, PageHeader, Stat, formatTime, relativeTime } from "../components/ui";

const OAUTH_NOTE: Record<string, [string, string]> = {
  ok: ["banner-good", "Signed in with Google (read-only)."],
  cancelled: ["banner-warn", "Google sign-in was cancelled."],
  failed: ["banner-error", "Google sign-in failed. Details are on the Activity page."],
};

export function YouTubePage() {
  const [params] = useSearchParams();
  const { data, failure, setData } = useApi(api.youtube, 30000);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const oauth = OAUTH_NOTE[params.get("oauth") ?? ""];

  async function run(name: string, fn: () => Promise<YouTubeStatus>, msg?: (s: YouTubeStatus) => string) {
    setBusy(name);
    try {
      const s = await fn();
      setData({ ...s, recent: s.recent ?? data?.recent });
      setNotice(msg ? msg(s) : null);
      setError(null);
      if (name === "sync") setData(await api.youtube());
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <PageHeader title="YouTube" subtitle="Read-only. Price Chat reads the channel's uploads and keeps the website's Latest Content up to date. Nothing is uploaded or changed on YouTube." />
      {oauth && <div className={`banner ${oauth[0]}`}>{oauth[1]}</div>}
      {notice && <div className="banner banner-good">{notice}</div>}
      <ApiErrorBanner error={error ?? failure} />
      {!data ? (
        <Loading />
      ) : (
        <div className="stack">
          <ConnectionCard s={data} busy={busy} run={run} />
          {data.channels_available.length > 0 && (!data.channel || data.channels_available.length > 1) && <ChannelPicker s={data} busy={busy} run={run} />}
          {!data.channel && <ConnectCard s={data} busy={busy} run={run} />}
          {data.channel && (
            <>
              <div className="grid-2">
                <Card title="Content">
                  <div className="stats">
                    <Stat label="Shorts detected" value={data.content.shorts} />
                    <Stat label="Long-form videos" value={data.content.long_form} />
                    <Stat label="On the website" value={data.content.on_website} hint={data.content.unavailable ? `${data.content.unavailable} private, unlisted or deleted` : undefined} />
                  </div>
                  <p className="small">
                    Latest upload:{" "}
                    {data.content.latest ? (
                      <a className="link" href={data.content.latest.url} target="_blank" rel="noreferrer">
                        {data.content.latest.title}
                      </a>
                    ) : (
                      "-"
                    )}
                    {data.content.latest && <span className="muted"> · {formatTime(data.content.latest.published_at)}</span>}
                  </p>
                </Card>
                <Card title="Performance (all synced videos)">
                  <div className="stats">
                    <Stat label="Views" value={data.performance.views == null ? "n/a" : compact(data.performance.views)} />
                    <Stat label="Likes" value={data.performance.likes == null ? "n/a" : compact(data.performance.likes)} />
                    <Stat label="Comments" value={data.performance.comments == null ? "n/a" : compact(data.performance.comments)} />
                  </div>
                  <p className="muted small">Public counts from YouTube; "n/a" when YouTube hides them. Snapshots are kept to show growth over time.</p>
                </Card>
              </div>
              <RecentUploads rows={data.recent ?? []} />
            </>
          )}
        </div>
      )}
    </>
  );
}

type Run = (name: string, fn: () => Promise<YouTubeStatus>, msg?: (s: YouTubeStatus) => string) => Promise<void>;

function ConnectionCard({ s, busy, run }: { s: YouTubeStatus; busy: string | null; run: Run }) {
  const ch = s.channel;
  const tone = s.status === "connected" ? "good" : s.status === "error" ? "bad" : "warn";
  return (
    <Card
      title="YouTube"
      actions={
        <div className="btn-group">
          <button className="btn btn-sm" disabled={!ch || busy !== null} onClick={() => void run("test", api.youtubeTest, () => "Connection works.")}>
            {busy === "test" ? "Testing…" : "Test connection"}
          </button>
          <button
            className="btn btn-sm btn-primary"
            disabled={!ch || busy !== null}
            onClick={() => void run("sync", api.youtubeSync, (r) => {
              const res = (r.result ?? {}) as { new?: number; updated?: number };
              return `Synced: ${res.new ?? 0} new, ${res.updated ?? 0} updated.`;
            })}
          >
            {busy === "sync" ? "Syncing…" : "Sync now"}
          </button>
          {s.mode && (
            <button
              className="btn btn-sm btn-ghost"
              disabled={busy !== null}
              onClick={() => window.confirm("Disconnect YouTube? Credentials are removed; synced content stays on the website until you hide it.") && void run("disconnect", api.youtubeDisconnect, () => "Disconnected.")}
            >
              Disconnect
            </button>
          )}
        </div>
      }
    >
      <dl className="kv">
        <dt>Status</dt>
        <dd>
          <Badge tone={tone}>{s.status === "connected" ? "Connected (read-only)" : s.status.replace("_", " ")}</Badge>{" "}
          <span className="muted small">{s.mode === "api_key" ? "API key" : s.mode === "oauth" ? "Google sign-in" : ""}</span>
        </dd>
        <dt>Channel</dt>
        <dd>
          {ch ? (
            <>
              <a className="link" href={`https://www.youtube.com/${ch.handle ?? `channel/${ch.id}`}`} target="_blank" rel="noreferrer">
                {ch.title}
              </a>{" "}
              <span className="muted small">
                {ch.handle} · <span className="mono">{ch.id}</span>
              </span>
            </>
          ) : (
            "-"
          )}
        </dd>
        <dt>Subscribers</dt>
        <dd>{!ch ? "-" : ch.subscribers_hidden ? "hidden by the channel" : ch.subscribers == null ? "n/a" : ch.subscribers.toLocaleString()}</dd>
        <dt>Total videos</dt>
        <dd>{ch?.videos ?? "-"}</dd>
        <dt>Last sync</dt>
        <dd>
          {s.last_sync ? `${relativeTime(s.last_sync.last_sync_at)} (${s.last_sync.new} new, ${s.last_sync.updated} updated)` : "never"}
          {s.next_sync_at && ch && <span className="muted small"> · next {relativeTime(s.next_sync_at)}</span>}
        </dd>
        <dt>Last successful request</dt>
        <dd>{s.last_success_at ? formatTime(s.last_success_at) : "-"}</dd>
        <dt>Last error</dt>
        <dd className={s.last_error ? "tone-bad small" : "muted"}>{s.last_error ? `${s.last_error} (${formatTime(s.last_error_at)})` : "none"}</dd>
        <dt>API quota today</dt>
        <dd className="small">
          {s.quota_today.units} of {s.quota_today.daily_limit.toLocaleString()} units ({s.quota_today.requests} requests)
        </dd>
      </dl>
      <p className="muted small">
        New uploads appear on the website automatically after the next sync (every 15 minutes by default). Website preview:{" "}
        <Link className="link" to="/website">
          Website
        </Link>
        .
      </p>
    </Card>
  );
}

function ConnectCard({ s, busy, run }: { s: YouTubeStatus; busy: string | null; run: Run }) {
  const [key, setKey] = useState("");
  const [channel, setChannel] = useState("@");
  const [cid, setCid] = useState("");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<unknown>(null);

  function submitKey(e: FormEvent) {
    e.preventDefault();
    void run("key", () => api.youtubeApiKey(channel.trim(), key.trim() || undefined), (r) => `Connected to ${r.channel?.title}. Click Sync now.`);
    setKey("");
  }
  function submitClient(e: FormEvent) {
    e.preventDefault();
    void run("client", () => api.youtubeOAuthClient(cid.trim(), secret.trim() || undefined), () => "Google client saved. Now click Sign in with Google.");
    setSecret("");
  }
  async function signIn() {
    try {
      window.location.href = (await api.youtubeOAuthStart()).url;
    } catch (e) {
      setError(e);
    }
  }
  return (
    <div className="grid-2">
      <Card title="Connect with an API key (recommended)">
        <p className="muted small">Simplest: reads your channel's public videos. Steps are in the guide below the form.</p>
        <form className="stack" onSubmit={submitKey}>
          <input className="input mono" type="password" autoComplete="off" placeholder={s.api_key.configured ? "API key saved (leave empty to keep it)" : "YouTube Data API key"} value={key} onChange={(e) => setKey(e.target.value)} />
          <input className="input" placeholder="@pricechat or the channel URL" value={channel} onChange={(e) => setChannel(e.target.value)} />
          <button className="btn btn-primary" disabled={busy !== null || channel.trim().length < 2 || (!key.trim() && !s.api_key.configured)}>
            {busy === "key" ? "Checking…" : "Connect"}
          </button>
        </form>
        <ol className="small muted">
          <li>console.cloud.google.com → create a project.</li>
          <li>APIs & Services → Library → enable "YouTube Data API v3".</li>
          <li>Credentials → Create credentials → API key. Restrict it to "YouTube Data API v3".</li>
          <li>Paste it here with your channel handle.</li>
        </ol>
      </Card>
      <Card title="Or sign in with Google (optional)">
        <p className="muted small">Also shows private/unlisted status of your own videos. Read-only permission (youtube.readonly).</p>
        <ApiErrorBanner error={error} />
        <form className="stack" onSubmit={submitClient}>
          <input className="input mono" placeholder="OAuth client ID (…apps.googleusercontent.com)" value={cid} onChange={(e) => setCid(e.target.value)} />
          <input className="input mono" type="password" autoComplete="off" placeholder={s.oauth_client ? "Client secret saved (leave empty to keep it)" : "OAuth client secret"} value={secret} onChange={(e) => setSecret(e.target.value)} />
          <button className="btn" disabled={busy !== null || cid.trim().length < 10}>
            Save Google client
          </button>
        </form>
        <button className="btn btn-primary" style={{ marginTop: 8 }} disabled={!s.oauth_client} onClick={() => void signIn()}>
          Sign in with Google
        </button>
        <p className="muted small">
          Redirect URI to register on the OAuth client: <code>{s.redirect_uri}</code>
        </p>
      </Card>
    </div>
  );
}

function ChannelPicker({ s, busy, run }: { s: YouTubeStatus; busy: string | null; run: Run }) {
  return (
    <Card title="Choose the Price Chat channel">
      <p className="muted small">This Google account has {s.channels_available.length} channel(s). Only the one you choose is synced to the website.</p>
      <ul className="feed">
        {s.channels_available.map((c) => (
          <li key={c.id}>
            <div className="feed-body">
              <span>
                <strong>{c.title}</strong> <span className="muted">{c.handle}</span>
              </span>
              <span className="muted small mono">{c.id}</span>
            </div>
            {s.channel?.id === c.id ? (
              <Badge tone="good">selected</Badge>
            ) : (
              <button className="btn btn-sm btn-primary" disabled={busy !== null} onClick={() => void run("channel", () => api.youtubeChannel(c.id), () => `${c.title} selected. Click Sync now.`)}>
                Use this channel
              </button>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function RecentUploads({ rows }: { rows: ContentItemRow[] }) {
  const [items, setItems] = useState(rows);
  const [prev, setPrev] = useState(rows);
  if (rows !== prev) {
    setPrev(rows);
    setItems(rows);
  }
  return (
    <Card title="Recent uploads" actions={<Link className="link small" to="/content?tab=published">All in Content →</Link>}>
      <ContentItemsTable items={items} onChanged={(row) => setItems((cur) => cur.map((i) => (i.id === row.id ? row : i)))} />
    </Card>
  );
}
