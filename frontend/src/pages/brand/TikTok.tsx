import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { request } from "../../api/client";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Loading, formatTime } from "../../components/ui";
import { bm, num, type TikTokAccount } from "./api";

const SCOPE_LABEL: Record<string, string> = {
  "user.info.basic": "See your name and avatar", "user.info.profile": "See your profile (bio, verified)", "user.info.stats": "See follower and like counts",
  "video.list": "Read your videos' views, likes, comments and shares (analytics)", "video.upload": "Upload videos to your TikTok drafts (inbox)",
  "video.publish": "Post videos directly (with your approval of each post)",
};
const PRIVACY: Record<string, string> = { PUBLIC_TO_EVERYONE: "Everyone", MUTUAL_FOLLOW_FRIENDS: "Friends", FOLLOWER_OF_CREATOR: "Followers", SELF_ONLY: "Only me" };

export function TikTokTab() {
  const { data, failure, setData } = useApi(bm.tiktok, 30000);
  const [params] = useSearchParams();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const run = async (name: string, fn: () => Promise<TikTokAccount | void>) => {
    setBusy(name);
    setError(null);
    try {
      const r = await fn();
      if (r) setData(r);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };
  const connect = () => run("connect", async () => { window.location.href = (await bm.connect()).url; });
  const connected = data.status === "connected";
  const p = (data.profile ?? {}) as Record<string, number | string | boolean | undefined>;

  return (
    <>
      {params.get("connected") && <div className="bmx-ok" role="status">TikTok connected.</div>}
      {params.get("error") && <div className="bmx-alert" role="alert"><b>TikTok sign-in didn't finish</b><p>{params.get("error")}</p></div>}
      <ApiErrorBanner error={error} />
      <section className="bmx-card bmx-account">
        {connected ? (
          <>
            {data.avatar_url ? <img src={data.avatar_url} alt="" referrerPolicy="no-referrer" /> : <span className="bmx-avatar" />}
            <div>
              <h2>{data.display_name ?? "TikTok account"} <span className="muted">@{data.username}</span>{p.is_verified === true && <span className="bmx-tag">Verified</span>}</h2>
              {p.bio_description && <p className="bmx-bio">{String(p.bio_description)}</p>}
              {p.profile_deep_link && <p><a href={String(p.profile_deep_link)} target="_blank" rel="noreferrer">Open profile on TikTok ↗</a></p>}
              <p className="muted">Connected {formatTime(data.connected_at)} · sign-in renews automatically until {formatTime(data.refresh_expires_at)}</p>
              <p className="bmx-numbers">
                <span><b>{num(p.follower_count as number)}</b> followers</span><span><b>{num(p.following_count as number)}</b> following</span>
                <span><b>{num(p.likes_count as number)}</b> likes</span><span><b>{num(p.video_count as number)}</b> videos</span>
              </p>
            </div>
            <div className="bmx-actions">
              <button className="btn" disabled={!!busy} onClick={() => void run("refresh", bm.refreshTikTok)}>Refresh</button>
              <button className="btn" disabled={!!busy} onClick={() => void connect()}>Reconnect</button>
              <button className="btn btn-ghost" disabled={!!busy} onClick={() => { if (window.confirm("Disconnect TikTok? Price Chat's access is revoked at TikTok.")) void run("disconnect", bm.disconnect); }}>Disconnect</button>
            </div>
          </>
        ) : (
          <>
            <span className="bmx-avatar" />
            <div>
              <h2>{data.status === "expired" ? "TikTok sign-in expired" : "Connect TikTok"}</h2>
              <p className="muted">{data.last_error ?? "You sign in on tiktok.com and choose what Price Chat may do. Price Chat never sees your password."}</p>
              {!data.app_configured && <p className="bmx-note">Connect is unavailable until the TikTok app's <b>Client key</b> and <b>Client secret</b> are saved on
                <a href="/admin/api-keys"> Settings › API keys</a> (from developers.tiktok.com › your app). See the checklist below.</p>}
            </div>
            <div className="bmx-actions">
              <button className="btn btn-primary" disabled={!!busy || !data.app_configured} onClick={() => void connect()}>
                {data.status === "expired" ? "Reconnect TikTok" : "Connect TikTok"}
              </button>
            </div>
          </>
        )}
      </section>

      {connected && (
        <div className="bmx-cols">
          <section className="bmx-card">
            <h2>Permissions TikTok granted</h2>
            <ul className="bmx-scopes">
              {Object.keys(SCOPE_LABEL).map((s) => (
                <li key={s} className={data.scopes.includes(s) ? "is-on" : "is-off"}><i />{SCOPE_LABEL[s]} <code>{s}</code></li>
              ))}
            </ul>
            {data.missing_scopes.length > 0 && <p className="bmx-fine">Missing permissions: reconnect and allow them (your TikTok app must have the matching products).</p>}
          </section>
          <section className="bmx-card">
            <h2>Posting on this account</h2>
            {data.creator_info ? (
              <>
                <p>Posts go to <b>{data.creator_info.creator_nickname}</b> (@{data.creator_info.creator_username}).</p>
                <p className="muted">Who can see posts: {(data.creator_info.privacy_level_options ?? []).map((o) => PRIVACY[o] ?? o).join(" · ")}</p>
                <p className="muted">Comments {data.creator_info.comment_disabled ? "off" : "allowed"} · Duet {data.creator_info.duet_disabled ? "off" : "allowed"} ·
                  Stitch {data.creator_info.stitch_disabled ? "off" : "allowed"} · max {data.creator_info.max_video_post_duration_sec ?? "?"} s</p>
              </>
            ) : <p className="muted">Press Refresh to read the account's posting options (needs video.publish).</p>}
            <h3>How videos reach TikTok</h3>
            <ul className="bmx-fineprint">
              <li><b>Drafts (default):</b> finished videos land in your TikTok inbox; you post them from the TikTok app.</li>
              <li><b>Direct:</b> you approve each post on TikTok's posting screen (privacy, interactions, disclosure). Until TikTok audits the app,
                direct posts can only be private.</li>
              <li>TikTok fetches each video from <code>api.pricechat.live/api/brand-manager/media/</code>: verify that URL prefix in your TikTok app.</li>
            </ul>
          </section>
        </div>
      )}

      {connected && data.can_read_stats && <RecentVideos />}

      <SetupCheck />

      <section className="bmx-card">
        <h2>Setup checklist (one time)</h2>
        <ol className="bmx-steps">
          <li className={data.app_configured ? "is-done" : ""}>Create an app at <a href="https://developers.tiktok.com/apps" target="_blank" rel="noreferrer">developers.tiktok.com</a> with
            <b> Login Kit</b> and the <b>Content Posting API</b> (+ Display API for analytics). Paste its Client key and Client secret on
            <a href="/admin/api-keys"> Settings › API keys</a>.</li>
          <li>Add this redirect URI to the app's Login Kit: <code>{data.redirect_uri}</code></li>
          <li>Verify the URL prefix <code>https://api.pricechat.live/api/brand-manager/media/</code> (Content Posting API › URL properties).</li>
          <li className={connected ? "is-done" : ""}>Connect TikTok here.</li>
          <li>Optional, for public direct posts: submit the app for TikTok's audit. Drafts work without it.</li>
        </ol>
      </section>
    </>
  );
}

/** The account's latest TikTok videos, read live from TikTok (video.list). */
function RecentVideos() {
  const { data, failure, reload, loading } = useApi(bm.tiktokVideos);
  return (
    <section className="bmx-card">
      <h2>Your latest TikTok videos</h2>
      <p className="bmx-fine">Read live from TikTok{data ? ` at ${formatTime(data.at)}` : ""}. Views, likes, comments and shares are TikTok's numbers.</p>
      <button className="btn" disabled={loading} onClick={() => void reload()}>Reload from TikTok</button>
      <ApiErrorBanner error={failure} />
      {data && (data.videos.length === 0 ? <p className="muted">TikTok returned no videos for this account yet.</p> : (
        <ul className="bmx-ttlist">
          {data.videos.map((t) => (
            <li key={t.id}>
              {t.cover_image_url ? <img src={t.cover_image_url} alt="" referrerPolicy="no-referrer" loading="lazy" /> : <span className="bmx-ttcover" />}
              <div>
                <b>{t.title || t.video_description || "Untitled"}</b>
                <span className="muted">{t.create_time ? new Date(t.create_time * 1000).toLocaleDateString() : ""}{t.duration ? ` · ${t.duration}s` : ""}</span>
                <span>{num(t.view_count)} views · {num(t.like_count)} likes · {num(t.comment_count)} comments · {num(t.share_count)} shares</span>
                {t.share_url && <a href={t.share_url} target="_blank" rel="noreferrer">Open on TikTok ↗</a>}
              </div>
            </li>
          ))}
        </ul>
      ))}
    </section>
  );
}

interface Diag {
  client_key: { configured: boolean; source: string | null; value: string | null; length: number; kind_hint: string; env_variable_set: boolean; dashboard_value_saved: boolean };
  client_secret: { configured: boolean; source: string | null; length: number };
  pair: { checked: boolean; valid?: boolean; tiktok_error?: string; tiktok_error_description?: string; log_id?: string };
  authorize_endpoint: string; redirect_uri: string; scopes: string[]; environment: string; problems: string[]; app_side_checks: string[];
}

/** Owner-only setup check: which client key the server really uses (and from where), and whether TikTok accepts the
 * key + secret as a pair. The secret is never shown. */
function SetupCheck() {
  const [d, setD] = useState<Diag | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const run = async () => {
    setBusy(true);
    setError(null);
    try { setD(await request<Diag>("/brand-manager/tiktok/diagnostics")); } catch (e) { setError(e); } finally { setBusy(false); }
  };
  return (
    <section className="bmx-card">
      <h2>Check TikTok setup</h2>
      <p className="bmx-fine">Shows the client key the server actually sends to TikTok and asks TikTok whether the key and secret belong together. The secret is never shown.</p>
      <button className="btn" disabled={busy} onClick={() => void run()}>{busy ? "Checking…" : "Run check"}</button>
      <ApiErrorBanner error={error} />
      {d && (
        <div className="bmx-rows" style={{ marginTop: 12 }}>
          <p>Client key: <code>{d.client_key.value ?? "not set"}</code> · from {d.client_key.source === "dashboard" ? "Settings › API keys" : d.client_key.source === "env" ? "the server variable TIKTOK_CLIENT_KEY" : "nowhere"}
            {" "}· {d.client_key.kind_hint}</p>
          <p>Client secret: {d.client_secret.configured ? `set (${d.client_secret.length} characters, from ${d.client_secret.source === "dashboard" ? "Settings › API keys" : "the server variable"})` : "not set"}</p>
          <p>TikTok accepts the key + secret as a pair: <b>{d.pair.checked ? (d.pair.valid ? "yes" : `no (${d.pair.tiktok_error_description ?? d.pair.tiktok_error})`) : "not checked"}</b></p>
          <p>Sign-in page: <code>{d.authorize_endpoint}</code> · Redirect URI: <code>{d.redirect_uri}</code></p>
          <p className="muted">Permissions requested: {d.scopes.join(", ")}</p>
          {d.problems.length > 0 ? <div className="bmx-alert"><b>Problems on our side</b><ul>{d.problems.map((p) => <li key={p}>{p}</li>)}</ul></div>
            : <div className="bmx-ok">No problem found on Price Chat's side.</div>}
          <h3>Check in the TikTok developer portal</h3>
          <ul className="bmx-fineprint">{d.app_side_checks.map((c) => <li key={c}>{c}</li>)}</ul>
        </div>
      )}
    </section>
  );
}
