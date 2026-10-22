/** Pokémon Market Pulse: public X posts about the card market, in a CoinGecko "Latest news"-style sidebar.
 * Context only: posts are never sales and never affect prices. One budget-checked X search per refresh. */
import { useCallback, useEffect, useState } from "react";
import { request } from "../../api/client";
import { usePublicMarket } from "./mx";
import { relativeTime } from "../../components/ui";
import { play } from "./marketSound";

type Tab = "latest" | "popular" | "auctions" | "vintage";
const TABS: [Tab, string][] = [["latest", "Latest"], ["popular", "Popular"], ["auctions", "Auctions"], ["vintage", "Vintage"]];

export interface PulsePost {
  id: string;
  username: string;
  name: string;
  avatar: string | null;
  verified: boolean;
  trusted: boolean;
  text: string;
  created_at: string;
  url: string;
  media: string | null;
  metrics: { likes: number; reposts: number; replies: number; quotes: number };
  categories: string[];
  cards: { id: number; name: string; set_name: string; edition: string; image_url: string | null }[];
}

export interface PulseStatus {
  enabled: boolean;
  trusted_accounts: string[];
  last_refresh_at: string | null;
  cooldown_seconds: number;
  error: string | null;
  connected: boolean;
  posts_stored: number;
  last_result: { posts_returned?: number; users_returned?: number; estimated_cost_usd?: number; kept?: number; filtered?: number; error?: string };
  budget: { today_usd: number; month_usd: number; daily_usd: number; monthly_usd: number; left_usd: number };
}

export const pulseApi = {
  feed: (tab: Tab) => request<{ posts: PulsePost[]; status: PulseStatus }>(`/pokemon/pulse?tab=${tab}&limit=30`),
  refresh: () => request<{ ok: boolean; error?: string; kept?: number; estimated_cost_usd?: number; status: PulseStatus }>("/pokemon/pulse/refresh", { method: "POST" }, 60_000),
  config: (body: { enabled?: boolean; trusted_accounts?: string[] }) => request<PulseStatus>("/pokemon/pulse/config", { method: "PUT", body: JSON.stringify(body) }),
};

const fmt = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)}K` : String(n));

export function MarketPulse({ onCollapse }: { onCollapse?: () => void }) {
  const [tab, setTab] = useState<Tab>("latest");
  const [posts, setPosts] = useState<PulsePost[] | null>(null);
  const [st, setSt] = useState<PulseStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);
  const [showCfg, setShowCfg] = useState(false);
  const load = useCallback(async (t: Tab) => {
    try {
      const r = await pulseApi.feed(t);
      setPosts(r.posts);
      setSt(r.status);
      setCooldown(r.status.cooldown_seconds);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Couldn't load the feed.");
      setPosts([]);
    }
  }, []);
  useEffect(() => { void load(tab); }, [tab, load]);
  useEffect(() => {
    if (cooldown <= 0) return;
    const t = window.setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => window.clearTimeout(t);
  }, [cooldown]);

  async function refresh() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await pulseApi.refresh();
      setSt(r.status);
      setCooldown(r.status.cooldown_seconds);
      if (r.ok && (r.kept ?? 0) > 0) play("chime");
      setMsg(r.ok ? `${r.kept ?? 0} new post${r.kept === 1 ? "" : "s"} · est. cost $${(r.estimated_cost_usd ?? 0).toFixed(3)}` : r.error ?? "Refresh failed.");
      await load(tab);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Refresh failed.");
    } finally {
      setBusy(false);
    }
  }

  const isPublic = usePublicMarket();
  return (
    <aside className="mx-pulse" aria-label="Pokémon Market Pulse">
      <div className="mx-pulse-head">
        <h2 className="mx-pulse-title"><span className="mx-pulse-dot" aria-hidden="true" />Pokémon Market Pulse</h2>
        <div className="mx-pulse-actions">
          {!isPublic && <button type="button" className="mx-iconbtn" title={cooldown > 0 ? `Refresh available in ${cooldown}s` : "Refresh now (one X search)"}
                  disabled={busy || cooldown > 0 || !st?.connected} onClick={() => void refresh()} aria-label="Refresh Market Pulse">
            {busy ? "…" : cooldown > 0 ? `${Math.floor(cooldown / 60)}:${String(cooldown % 60).padStart(2, "0")}` : "↻"}
          </button>}
          {!isPublic && <button type="button" className="mx-iconbtn" aria-expanded={showCfg} onClick={() => setShowCfg(!showCfg)} aria-label="Market Pulse settings" title="Settings">⚙</button>}
          {onCollapse && <button type="button" className="mx-iconbtn" onClick={onCollapse} aria-label="Collapse Market Pulse" title="Collapse">›</button>}
        </div>
      </div>
      <div className="mx-pulse-tabs" role="tablist" aria-label="Market Pulse tabs">
        {TABS.map(([k, label]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => { if (tab !== k) play("tick"); setTab(k); }}>{label}</button>
        ))}
      </div>
      {!isPublic && showCfg && st && <PulseSettings st={st} onSaved={(s) => setSt(s)} />}
      {st?.error && <p className="mx-pulse-error" role="alert">{st.error}</p>}
      {msg && !st?.error && <p className="mx-pulse-msg" role="status">{msg}</p>}
      <div className="mx-pulse-list">
        {posts === null ? <p className="mx-empty">Loading…</p> : posts.length === 0 ? (
          <p className="mx-empty">{st?.posts_stored ? "No posts in this tab yet." : "No posts yet. Refresh to run one X search for card-market posts."}</p>
        ) : posts.map((p) => <PostItem key={p.id} p={p} />)}
      </div>
      <p className="mx-pulse-foot">
        Public posts from X via the official API{st?.last_refresh_at ? ` · updated ${relativeTime(st.last_refresh_at)}` : ""}. Context only: never used as sales or prices.
      </p>
    </aside>
  );
}

function PostItem({ p }: { p: PulsePost }) {
  return (
    <a className="mx-post" href={p.url} target="_blank" rel="noopener noreferrer" aria-label={`Post by @${p.username} on X`}>
      <span className="mx-post-head">
        {p.avatar ? <img className="mx-post-avatar" src={p.avatar} alt="" loading="lazy" width={28} height={28} /> : <span className="mx-post-avatar" aria-hidden="true">{p.name.slice(0, 1)}</span>}
        <span className="mx-post-who">
          <b>{p.name}</b>{p.verified && <span className="mx-post-badge" title="Verified">✓</span>}{p.trusted && <span className="mx-post-trusted" title="Trusted account">★</span>}
          <span className="mx-muted"> @{p.username} · {relativeTime(p.created_at)}</span>
        </span>
      </span>
      <span className="mx-post-text">{p.text}</span>
      {p.media && <img className="mx-post-media" src={p.media} alt="" loading="lazy" />}
      {p.cards?.length > 0 && (
        <span className="mx-post-cards">
          {p.cards.map((c) => (
            <span key={c.id} role="link" tabIndex={0} className="mx-post-card" title={`${c.name} · ${c.set_name} · ${c.edition}`}
                  onClick={(e) => { e.preventDefault(); e.stopPropagation(); window.location.assign(`/admin/pokemon/cards/${c.id}`); }}
                  onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); window.location.assign(`/admin/pokemon/cards/${c.id}`); } }}>
              {c.image_url && <img src={c.image_url} alt="" width={14} height={20} loading="lazy" />}{c.name}
            </span>
          ))}
        </span>
      )}
      <span className="mx-post-meta">
        {p.categories.filter((c) => c === "auctions" || c === "vintage").map((c) => <span key={c} className="mx-tag">{c === "auctions" ? "Auction" : "Vintage"}</span>)}
        <span className="mx-muted">♥ {fmt(p.metrics.likes)} · ↻ {fmt(p.metrics.reposts)}</span>
      </span>
    </a>
  );
}

function PulseSettings({ st, onSaved }: { st: PulseStatus; onSaved: (s: PulseStatus) => void }) {
  const [text, setText] = useState(st.trusted_accounts.join(", "));
  const [err, setErr] = useState<string | null>(null);
  async function save(body: { enabled?: boolean; trusted_accounts?: string[] }) {
    setErr(null);
    try {
      onSaved(await pulseApi.config(body));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't save.");
    }
  }
  const lr = st.last_result;
  return (
    <div className="mx-pulse-cfg">
      <label className="mx-switch">
        <span>Auto-refresh every ~15 min</span>
        <input type="checkbox" role="switch" checked={st.enabled} onChange={() => void save({ enabled: !st.enabled })} aria-label="Auto-refresh Market Pulse" />
        <i aria-hidden="true" />
      </label>
      <p className="mx-note">
        One X search per refresh. Last: {lr.posts_returned ?? 0} posts + {lr.users_returned ?? 0} authors ≈ ${(lr.estimated_cost_usd ?? 0).toFixed(3)}.
        X budget: ${st.budget.today_usd.toFixed(2)} / ${st.budget.daily_usd.toFixed(2)} today, ${st.budget.month_usd.toFixed(2)} / ${st.budget.monthly_usd.toFixed(2)} this month.
      </p>
      <label className="mx-pulse-field">
        <span>Trusted accounts (comma separated)</span>
        <input className="mx-select" style={{ width: "100%", cursor: "text" }} value={text} onChange={(e) => setText(e.target.value)} placeholder="@goldin, @heritageauctions" />
      </label>
      <button type="button" className="mx-btn" style={{ height: 28 }}
              onClick={() => void save({ trusted_accounts: text.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean) })}>Save accounts</button>
      {err && <p className="mx-pulse-error" role="alert">{err}</p>}
    </div>
  );
}
