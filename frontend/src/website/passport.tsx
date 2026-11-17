import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { ConnectButton, PIcon, SitePage, TelegramButton, XPRow, when } from "./community";
import { EmailLogin } from "./emailLogin";
import {
  MemberApiError, memberApi, refreshSession, useCanonicalOrigin, useSession,
  type Passport, type Rank, type Reputation, type XPEvent, type XPRuleInfo,
} from "./members";
import { RankKiwi } from "./RankKiwi";
import { ManagerBadge, ManagerTab, WorkerBadge, WorkerTab } from "./workers";
import { RealmTab } from "./realm";
import { signWith, walletError } from "./wallet";
import { BurnerRow } from "./burner";
import { burst } from "./fx";
import { reducedMotion } from "./motion";
import { playSound, warmSounds } from "./sound";
import { useStored } from "./Website";
import { profileHead } from "./seo";
import type { HomeData } from "./websiteLogic";
import { PassportPlay } from "./play";
import { WatchEarn } from "./watch";
import { XFeedSection } from "./xfeed";
import { VipStatus } from "./crew";
import { VpnStatus } from "./vpn";
import { desktopSignInToResume } from "./vpnDesktop";
import { vpnReturnToResume } from "./vpnConfig";
import { RankRewards, useRewards } from "./rewards";
import "./rewards.css";

/**
 * Kiwi Passport. The top is a small game (play.tsx): your Kiwi and rank, the XP bar, ONE mission with one big button,
 * "2 / 5 today". Then Watch & Earn (watch.tsx). Below, calmer: Recent XP, Rank progression, Connected accounts, Settings.
 */

function param(name: string): string | null {
  return typeof location === "undefined" ? null : new URLSearchParams(location.search).get(name);
}

function ago(iso: string | null | undefined): string {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return when(iso);
}

/** A connection that came back with an error (?error=…&code=…&platform=…): shown on that account's row. */
type LinkError = { platform: string; message: string; code: string } | null;

/** Every sign-in ends here. If this tab was signing in to the Kiwi VPN app, it goes back to finish that, before the Passport loads.
 *  If it signed in from the Kiwi VPN page (/join?next=/vpn), a fresh sign-in (?welcome=…) goes back there, once. */
export function ProfilePage() {
  const [leaving] = useState(() => {
    const page = desktopSignInToResume() ?? (param("welcome") ? vpnReturnToResume() : null);
    if (page) location.replace(page);
    return !!page;
  });
  return leaving ? null : <Passport />;
}

function Passport() {
  useCanonicalOrigin();
  const [p, setP] = useState<Passport | null>(null);
  const [tab, setTab] = useState(() => (typeof location !== "undefined" ? location.hash.replace("#", "") : "") || "passport");
  const selectTab = (k: string) => { setTab(k); try { history.replaceState(null, "", k === "passport" ? location.pathname : `#${k}`); } catch { /* sandboxed */ } };
  useEffect(() => {  // a link to /passport#realm from this very page switches the tab too
    const onHash = () => { const h = location.hash.replace("#", ""); if (h === "realm" || h === "passport") setTab(h); };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const [linkError] = useState<LinkError>(() => {
    const platform = param("platform");
    const message = param("error");
    return platform && message ? { platform, message, code: param("code") ?? "error" } : null;
  });
  const [rules, setRules] = useState<XPRuleInfo[]>([]);
  const [ranks, setRanks] = useState<Rank[]>([]);
  const [error, setError] = useState<string | null>(param("platform") ? null : param("error"));
  const [notice, setNotice] = useState<string | null>(null);
  const [editName, setEditName] = useState(false);
  const rewardFrom = useRef<Element | null>(null); // Watch & Earn's "+N XP": where the next XP flies from
  const welcome = param("welcome");
  const linked = param("linked");
  const botOpen = !!useStored<HomeData>("/api/website/home")?.trading_bot;
  const rw = useRewards();  // rank rewards: unlocks, claims, the Kiwi reward's display name and frame

  /** Fresh passport from the server (the play area compares it with the last one to show real XP arriving). */
  const load = useCallback(async (): Promise<Passport | null> => {
    try {
      const next = await memberApi<Passport>("/members/me");
      setP(next);
      return next;
    } catch (e) {
      if (!(e instanceof MemberApiError && e.status === 401)) setError("Could not load your passport.");
      else if (!loaded.current) location.replace("/website/join");  // the session ended: sign in again, never "Loading…" forever
      return null;
    }
  }, []);
  const loaded = useRef(false);
  useEffect(() => { if (p) loaded.current = true; }, [p]);

  const [live, setLive] = useState<string | null>(null);  // "💬 +3 XP from your Telegram messages"
  const lastXp = useRef<number | null>(null);
  useEffect(() => {
    // every fresh Passport is the new baseline: XP a mission or Watch & Earn already celebrated isn't announced again
    if (p) lastXp.current = p.member.xp;
  }, [p]);
  useEffect(() => {
    const id = window.setInterval(async () => {
      if (document.visibilityState !== "visible" || lastXp.current === null) return;
      try {
        const sess = await memberApi<{ signed_in: boolean; member: { xp: number } | null }>("/members/session");
        const now = sess.member?.xp ?? 0;
        if (now <= lastXp.current) return;
        const gained = now - lastXp.current;
        lastXp.current = now;
        const recent = await memberApi<{ events: XPEvent[] }>("/members/me/xp?limit=10").catch(() => ({ events: [] as XPEvent[] }));
        const tg = recent.events.filter((e) => e.event_type.startsWith("telegram.")).length > 0;
        playSound("coin");
        window.setTimeout(() => playSound("xp"), 200);
        setLive(tg ? `💬 +${gained} XP from your Telegram messages` : `✨ +${gained} XP just arrived`);
        window.setTimeout(() => setLive(null), 4500);
        await load();  // the Passport above counts it in: XP bar, rank, recent XP
      } catch { /* offline: try again next time */ }
    }, 15000);
    return () => window.clearInterval(id);
  }, [load]);

  const start = useCallback(() => {
    setError(null);
    memberApi<{ signed_in: boolean }>("/members/session")
      .then((s) => {
        if (!s.signed_in) {
          location.replace("/website/join");
          return;
        }
        void load();
      })
      .catch(() => setError("Could not reach Price Chat."));
    void memberApi<{ rules: XPRuleInfo[] }>("/members/rules").then((r) => setRules(r.rules)).catch(() => undefined);
    void memberApi<{ ranks: Rank[] }>("/members/ranks").then((r) => setRanks(r.ranks)).catch(() => undefined);
  }, [load]);
  useEffect(start, [start]);

  async function act(fn: () => Promise<unknown>, ok?: string) {
    setError(null);
    try {
      await fn();
      await load();  // the full Passport (missions, streak, feeds...), never the short profile reply
      if (ok) setNotice(ok);
      refreshSession();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    }
  }

  if (!p)
    return (
      <SitePage title="Kiwi Passport" description="Your Price Chat identity." path="/passport" noindex>
        {error ? (
          <div className="site-empty" role="alert">
            <p className="site-error">{error}</p>
            <button type="button" className="site-button" onClick={start}>Try again</button>
          </div>
        ) : <p className="site-empty">Loading your passport…</p>}
      </SitePage>
    );

  const { member, rank } = p;
  const roles = p.roles ?? [];
  const tabs = [["passport", "🥝 Passport"], ["realm", "⚔️ Realm"], ...(roles.includes("WORKER") ? [["worker", "👷 Worker"]] : []),
    ...(roles.includes("MANAGER") ? [["manager", "🧭 Manager"]] : [])] as [string, string][];
  const shownTab = tabs.some(([k]) => k === tab) ? tab : "passport";
  return (
    <SitePage title="Kiwi Passport" description="Your Price Chat rank, XP and today's missions." path="/passport" noindex>
      {welcome && !member.username_chosen && (
        <p className="site-banner" role="status">Welcome to Price Chat. Pick your name just below: it's how the community will see you.</p>
      )}
      {linked && <LinkedBanner platform={linked} p={p} />}
      {notice && <p className="site-banner" role="status">{notice}</p>}
      {live && <p className="pp-live" role="status">{live}</p>}
      {error && <p className="site-error" role="alert">{error}</p>}

      {tabs.length > 1 && (
        <nav className="pp-tabs" role="tablist" aria-label="Passport sections">
          {tabs.map(([k, l]) => <button key={k} role="tab" aria-selected={shownTab === k} onClick={() => selectTab(k)}>{l}</button>)}
        </nav>
      )}
      {shownTab === "realm" ? <RealmTab /> : shownTab === "worker" ? <WorkerTab /> : shownTab === "manager" ? <ManagerTab /> : (
      <div className="pp-flow">
        {roles.length > 0 && <div className="pub-roles">{roles.includes("WORKER") && <WorkerBadge />}{roles.includes("MANAGER") && <ManagerBadge />}</div>}
        {/* 0. Your name: picked right here on day one, then changed from the pencil next to it in the card below */}
        {(editName || !member.username_chosen) && (
          <NameEditor current={member.username} chosen={member.username_chosen} onCancel={() => setEditName(false)}
                      onSaved={async (name) => { await load(); setEditName(false); setNotice(`Saved ✓ You're now ${name}.`); }} />
        )}

        {/* 0b. A Pokémon pack for every level up */}
        {(p.level_packs ?? 0) > 0 && (
          <a className="pp-levelpack" href="/giveaway">
            <span className="pp-levelpack-icon" aria-hidden>🎁</span>
            <span><b>{p.level_packs} Level-up {p.level_packs === 1 ? "pack" : "packs"} to open!</b>
              <small>Every Kiwi level up earns a Pokémon pack: holo guaranteed.</small></span>
            <span className="pp-levelpack-go">Open →</span>
          </a>
        )}

        {/* 1. The game: who you are, your rank and XP, the bar (labelled), your next mission, "2 / 5 today" */}
        {(() => {
          const play = <PassportPlay p={p} ranks={ranks} reload={load} rewardFrom={rewardFrom} onEditName={() => setEditName(true)} rules={rules}
                                     extra={p.reputation?.combot ? <ScoreDetails r={p.reputation} /> : null} />;
          const prof = rw.data?.profile;
          if (!prof?.accent && !prof?.display_name) return play;
          return (  // the Kiwi rank reward: a chosen display name (next to the @username) and Passport frame
            <div className="pp-accent" data-accent={prof.accent ?? undefined}>
              {prof.display_name && <p className="pp-dname">{prof.display_name}<small>@{member.username}</small></p>}
              {play}
            </div>
          );
        })()}


        {/* 2. Watch & Earn: the newest Price Chat videos, played right here */}
        <WatchEarn reload={load} rewardFrom={rewardFrom} />

        {/* 2b. Read & Earn: Price Chat's latest X posts, one at a time (reading here earns; actions on X never do) */}
        <XFeedSection reload={load} rewardFrom={rewardFrom} />

        {/* 4. Rank rewards: every rank a milestone (the server decides what's unlocked); the plain ladder if that can't load */}
        <section className="pp-section site-reveal pp-ranks" aria-labelledby="ranks-title">
          <SectionTitle id="ranks-title" title={rw.data ? "Rank rewards" : "Rank progression"} />
          {p.badges.length > 0 && (
            <ul className="site-badges pp-badge-chips" aria-label="Badges">
              {p.badges.map((b) => <li key={b.key} title={b.description}>{b.name}</li>)}
            </ul>
          )}
          {rw.data ? <RankRewards data={rw.data} reload={rw.reload} onProfileSaved={() => void load()} />
            : <RankLadder ranks={ranks} current={rank.current.level} progress={rank.progress} entitlements={p.entitlements} />}
          {!rw.data && p.entitlements.length > 0 && (
            <>
              <h3 className="site-panel-sub">Unlocks</h3>
              <ul className="site-rules">
                {p.entitlements.map((e) => (
                  <li key={e.key}>
                    <span>{e.unlocked ? "✓ " : ""}{e.name}</span>
                    <span className="site-muted">{e.required_rank ? `Rank ${e.required_rank}` : ""} · {e.description}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
          <HowXPWorks rules={rules} />
        </section>

        {/* 4a. VIP Trading Bot: one compact line (locked / you qualify / unlocked), never a big banner above your Passport */}
        {botOpen && <VipStatus level={rank.current.level} />}

        {/* 4a'. Kiwi VPN: the same compact line (hidden until the owner opens it) */}
        <VpnStatus />

        {/* 4b. Trade on Hyperliquid: sign up with our link, connect the API, link Telegram, then trade in the group */}
        <TradeSetup telegramLinked={!!p.telegram} />

        {/* 5. Settings: connected accounts, privacy, XP history (opened when a connect just failed, so the error shows) */}
        <details className="site-details pp-settings-wrap" open={!member.username_chosen || !!linkError}>
          <summary>Settings: connected accounts, privacy & XP history</summary>
          <ConnectedAccounts p={p} act={act} linkError={linkError} />
          <Settings p={p} act={act} />
          {/* XP history lives here, out of the main page */}
          <RecentXP recent={p.recent} />
        </details>
      </div>
      )}
    </SitePage>
  );
}

function SectionTitle({ id, title, note }: { id: string; title: string; note?: string }) {
  return (
    <div className="pp-head">
      <h2 className="site-display pp-title" id={id}>{title}</h2>
      {note && <p className="site-note">{note}</p>}
    </div>
  );
}

/** "Includes Telegram legacy reputation" and, behind Details, where the number comes from. */
function ScoreDetails({ r }: { r: Reputation }) {
  return (
    <details className="site-details pp-score-details pp-rep">
      <summary>{r.combot_counts && r.legacy_points > 0 ? "Includes Telegram legacy reputation" : "Telegram legacy"} · Details</summary>
      <dl className="pp-rep-grid">
        <div><dt>Price Chat XP</dt><dd>{r.price_chat_xp.toLocaleString("en-GB")}</dd></div>
        <div><dt>Telegram legacy</dt><dd>{r.combot ? r.combot.xp.toLocaleString("en-GB") : 0}</dd></div>
        <div><dt>Rank contribution</dt><dd>{r.combot_counts ? `+${r.legacy_points.toLocaleString("en-GB")}` : "not counted"}</dd></div>
      </dl>
      <p className="site-note pp-small">
        Your old Telegram reputation is kept separate from new Price Chat XP.
        {r.combot_counts ? ` It adds ${Math.round(r.combot_weight * 100)}% of it to your rank${r.legacy_cap ? `, at most ${r.legacy_cap.toLocaleString("en-GB")}` : ""}.` : ""}
      </p>
    </details>
  );
}

function HowXPWorks({ rules }: { rules: XPRuleInfo[] }) {
  return (
    <details className="site-details">
      <summary>How XP works</summary>
      <ul className="site-rules">
        {rules.filter((r) => r.availability === "available" && r.enabled && r.verification_source !== "admin").map((r) => (
          <li key={r.event_type}>
            <span>{r.label}</span>
            <span className="site-muted">{r.xp > 0 ? `+${r.xp} XP` : "set by the mission"}{r.max_per_member === 1 ? " · once" : r.daily_cap ? ` · up to ${r.daily_cap}/day` : ""}</span>
          </li>
        ))}
      </ul>
      <p className="site-note">Replies, reposts and likes on X don't earn XP: X doesn't allow apps to reward them. You can still do them from here.</p>
    </details>
  );
}

// --- connected accounts ---------------------------------------------------------------------------------------------

/** The sign-in email: shown masked once verified, or added here with a code (existing Google / X / Telegram accounts). */
function EmailRow({ p }: { p: Passport }) {
  const [adding, setAdding] = useState(false);
  const email = p.member.email;
  return (
    <li id="account-email">
      <span className="pp-mail-icon" aria-hidden>✉</span>
      <span className="pp-account-name">
        <strong>Email</strong>
        {email ? <span className="site-muted"> {email} · you sign in with a code</span>
          : <span className="site-muted"> Add it to sign in with a code (no password)</span>}
      </span>
      {email ? <span className="pp-ok">Verified ✓</span>
        : !adding && <button type="button" className="site-textbutton" onClick={() => setAdding(true)}>Add email</button>}
      {!email && adding && (
        <div className="pp-account-extra pp-add-email">
          <EmailLogin purpose="link" onDone={() => location.assign("/passport?linked=email")} />
        </div>
      )}
    </li>
  );
}

/** Ethereum wallet: proved by a free signature (never a key). The first one gives +100 XP, and it's the wallet
 *  the Earn funnel checks on Hyperliquid. */
function WalletRow() {
  const [w, setW] = useState<{ wallet: { address: string } | null; free_level_claimed: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { void memberApi<typeof w>("/members/me/wallet").then(setW).catch(() => undefined); }, []);
  useEffect(() => {
    if (w && location.hash === "#account-wallet") {
      const row = document.getElementById("account-wallet");
      row?.scrollIntoView({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" });
      row?.classList.add("is-flash");
    }
  }, [w]);
  async function connect(e: React.MouseEvent<HTMLButtonElement>) {
    const row = e.currentTarget.closest("li");
    warmSounds();
    playSound("tick");
    setBusy(true);
    setMsg(null);
    try {
      const s = await signWith("/members/me/wallet/nonce");
      const r = await memberApi<NonNullable<typeof w> & { leveled_up: boolean; rank_level: number }>("/members/me/wallet/verify", { method: "POST", body: JSON.stringify(s) });
      setW(r);
      refreshSession();
      playSound(r.leveled_up ? "levelup" : "coin");  // wallet connected: coin, or the level-up fanfare
      if (row && !reducedMotion()) { row.classList.add("is-flash"); burst(row, r.leveled_up ? 24 : 14, 110, true); }
      setMsg({ ok: true, text: r.leveled_up ? `Wallet connected ✓ +XP and a level up: you're now Kiwi Rank ${r.rank_level}!`
        : w && !w.free_level_claimed ? "Wallet connected ✓ Bonus XP added!" : "Wallet connected ✓" });
      window.setTimeout(() => location.reload(), 1800);  // fresh XP bar and rank
    } catch (e) {
      const text = walletError(e);
      if (text) setMsg({ ok: false, text });
    } finally {
      setBusy(false);
    }
  }
  const addr = w?.wallet?.address;
  return (
    <li id="account-wallet">
      <span className="pp-wallet-icon" aria-hidden>🦊</span>
      <span className="pp-account-name">
        <strong>Ethereum wallet</strong>
        {addr ? <span className="site-muted"> {addr.slice(0, 6)}…{addr.slice(-4)}</span>
          : w && !w.free_level_claimed ? <span className="site-green pp-optional"> +100 XP</span> : null}
      </span>
      {addr ? <span className="pp-ok">Verified ✓</span>
        : <button type="button" className={`site-button pp-cta${busy ? " is-waiting" : ""}`} disabled={busy || !w} onClick={connect}>{busy ? "Check your wallet…" : "Connect"}</button>}
      {msg && <p className={`pp-account-extra ${msg.ok ? "pp-ok" : "pp-connect-error"}`} role={msg.ok ? "status" : "alert"}>{msg.text}</p>}
      {!addr && <p className="pp-account-extra site-muted">MetaMask, OKX, Binance Wallet, Trust, Coinbase and more. A free signature proves the wallet is yours: no transaction, and we never ask for a seed phrase or key. First wallet: +100 XP.</p>}
    </li>
  );
}

/** Trade on Hyperliquid (replaces the old Track record): the three things that let a member trade from the Price Chat Telegram
 *  group, each ticked from the server's own checks: (1) a Hyperliquid account made with our sign-up link (Hyperliquid's record),
 *  (2) that account connected here with an API key (the existing Connect dialog), (3) Telegram linked, then the group link. */
interface SetupConn { referral: { status: "VERIFIED" | "NOT_VERIFIED" | "EXISTING_ACCOUNT" | "PENDING" }; authorization: { status: string }; wallet: { address: string } | null }
interface SetupBot { telegram?: { linked: boolean; username?: string | null }; access?: { group_url: string | null } }
function TradeSetup({ telegramLinked }: { telegramLinked: boolean }) {
  const [c, setC] = useState<SetupConn | null>(null);
  const [b, setB] = useState<SetupBot | null>(null);
  const [Dialog, setDialog] = useState<null | ((p: { onClose: () => void; onChanged: () => void }) => ReactNode)>(null);
  const load = useCallback(() => {
    void memberApi<SetupConn>("/members/trading/connection").then(setC).catch(() => undefined);
    void memberApi<SetupBot>("/members/trading-bot").then(setB).catch(() => undefined);
  }, []);
  useEffect(() => { load(); }, [load]);
  async function openConnect() {
    const m = await import("./terminal");  // loaded only when needed: the same Connect dialog as the trading terminal
    setDialog(() => m.ConnectDialog);
  }
  const ref = c?.referral.status ?? "PENDING";
  const signedUp = ref === "VERIFIED";
  const connected = c?.authorization.status === "ACTIVE" || !!c?.wallet;
  const tg = telegramLinked || !!b?.telegram?.linked;
  const ready = signedUp && connected && tg;
  const group = b?.access?.group_url || "https://t.me/botpricechat";
  const tick = (ok: boolean) => <span className={`pp-step-tick${ok ? " is-done" : ""}`} aria-hidden>{ok ? "✓" : ""}</span>;
  return (
    <section className="pp-section site-reveal pp-trade" aria-labelledby="trade-title">
      <SectionTitle id="trade-title" title="Trade on Hyperliquid" note="Three steps, then you trade straight from the Price Chat Telegram group." />
      {ready ? (
        <div className="pp-trade-ready" role="status">
          <strong>✅ You can trade in the group</strong>
          <span>Type <code>/long btc 3x</code> or <code>/short eth 100 2x tp 3k</code> in the group: you get a preview, then press Confirm.</span>
          <a className="site-button pp-trade-go" href={group} target="_blank" rel="noopener noreferrer">Open the Telegram group ↗</a>
        </div>
      ) : null}
      <ol className="pp-trade-steps">
        <li className={signedUp ? "is-done" : ""}>
          {tick(signedUp)}
          <div>
            <b>1. Sign up on Hyperliquid with our link</b>
            <span>{signedUp ? "Done: Hyperliquid shows your account was made with our link (4% off fees)."
              : ref === "EXISTING_ACCOUNT" ? "This Hyperliquid account was made with another link, and Hyperliquid doesn't allow changing it. Make a new account with our link."
                : "4% off trading fees. Only accounts made with this link can trade from the group."}</span>
            {!signedUp && <a className="pp-hl-signup" href="/api/r/site_passport?src=web" target="_blank" rel="noopener noreferrer">
              <span className="pp-hl-signup-title">🚀 Sign up on Hyperliquid</span>
              <span className="pp-hl-signup-sub">Our link · 4% off fees · then come back for step 2</span>
            </a>}
          </div>
        </li>
        <li className={connected ? "is-done" : ""}>
          {tick(connected)}
          <div>
            <b>2. Connect Hyperliquid with an API key</b>
            <span>{connected ? `Done: ${c?.wallet ? `account ${c.wallet.address.slice(0, 6)}…${c.wallet.address.slice(-4)}` : "connected"}.`
              : "On Hyperliquid make an API key (More → API) and paste it here. It can trade, never withdraw. Your main key is never asked for."}</span>
            <button type="button" className="site-button pp-trade-btn" onClick={() => void openConnect()}>{connected ? "Manage connection" : "Connect Hyperliquid"}</button>
          </div>
        </li>
        <li className={tg ? "is-done" : ""}>
          {tick(tg)}
          <div>
            <b>3. Link Telegram and join the group</b>
            <span>{tg ? "Telegram is linked to this Passport." : "Link Telegram under Settings → Connected accounts below, so the bot knows it's you."}</span>
            <a className="site-button pp-trade-btn" href={group} target="_blank" rel="noopener noreferrer">Join the Telegram group ↗</a>
          </div>
        </li>
      </ol>
      {Dialog && <Dialog onClose={() => { setDialog(null); load(); }} onChanged={load} />}
    </section>
  );
}

function ConnectedAccounts({ p, act, linkError }: {
  p: Passport; act: (fn: () => Promise<unknown>, ok?: string) => Promise<void>; linkError: LinkError;
}) {
  const t = p.telegram;
  useEffect(() => {
    if (linkError) document.getElementById(`account-${linkError.platform}`)?.scrollIntoView?.({ block: "center" });
  }, [linkError]);
  return (
    <section className="pp-linked" aria-labelledby="linked-title">
      <h3 className="site-panel-sub" id="linked-title">Connected accounts</h3>
      <ul className="pp-accounts">
        <EmailRow p={p} />
        {p.linked.map((l) => {
          const label = l.platform === "google" ? "YouTube" : l.label;
          const optional = l.platform === "google" && !l.connected; // never required: nothing in the Passport waits for it
          const reconnect = l.platform === "x" && l.connected && p.x?.needs_reconnect;
          return (
            <li key={l.platform} id={`account-${l.platform}`} className={optional ? "is-optional" : undefined}>
              <PIcon p={l.platform === "google" ? "youtube" : l.platform} />
              <span className="pp-account-name">
                <strong>{label}</strong>
                {l.connected && l.username ? <span className="site-muted"> @{l.username}</span> : null}
                {optional ? <span className="site-muted pp-optional"> Optional</span> : null}
              </span>
              {l.connected ? (
                reconnect ? (
                  <ConnectButton provider="x" label="Reconnect" />
                ) : (
                  <span className="pp-ok">Connected ✓</span>
                )
              ) : l.platform === "telegram" ? (
                <TelegramButton purpose="link" available label="Connect" className="site-button pp-cta" onDone={() => location.assign("/passport?linked=telegram")} />
              ) : (
                <ConnectButton provider={l.platform} label="Connect" className={optional ? "site-textbutton pp-connect-quiet" : undefined} />
              )}
              {linkError?.platform === l.platform && !l.connected && (
                <p className="pp-account-extra pp-connect-error" role="alert">
                  {linkError.code === "cancelled" ? linkError.message : <><strong>Couldn't connect {label}.</strong> {linkError.message}</>}
                </p>
              )}
              {l.platform === "telegram" && l.connected && t && (
                <div className="pp-account-extra">
                  <span>Today: <strong className="site-green">+{t.xp_today} XP</strong></span>
                  <details className="site-details pp-account-details">
                    <summary>Details</summary>
                    <dl className="pp-stats">
                      <div><dt>Messages counted today</dt><dd>{t.eligible_today} <span className="site-muted">of {t.messages_today}</span></dd></div>
                      <div><dt>Daily cap</dt><dd>{t.daily_cap} XP</dd></div>
                      <div><dt>Last activity</dt><dd className="pp-small">{t.last_verified_at ? ago(t.last_verified_at) : "none yet"}</dd></div>
                    </dl>
                  </details>
                </div>
              )}
              {l.platform === "x" && reconnect && <p className="pp-account-extra site-muted">X needs permission to reply and repost for you.</p>}
              {l.connected && (
                <button className="site-textbutton pp-disconnect"
                        onClick={() => void act(() => memberApi(`/members/me/disconnect/${l.platform}`, { method: "POST" }), `${label} disconnected.`)}>
                  Disconnect
                </button>
              )}
            </li>
          );
        })}
        <WalletRow />
        <BurnerRow />
      </ul>
    </section>
  );
}

function RecentXP({ recent }: { recent: XPEvent[] }) {
  const [all, setAll] = useState<XPEvent[] | null>(null);
  const [more, setMore] = useState<"idle" | "busy" | "error">("idle");
  const rows = all ?? recent.slice(0, 10);
  const loadAll = () => {
    setMore("busy");
    memberApi<{ events: XPEvent[] }>("/members/me/xp?limit=500").then((r) => { setAll(r.events); setMore("idle"); }).catch(() => setMore("error"));
  };
  return (
    <section className="pp-section site-reveal pp-recent" aria-labelledby="xp-title">
      <SectionTitle id="xp-title" title="Recent XP" note="Every award shows where it came from." />
      {rows.length === 0 ? <p className="site-note">No XP yet.</p> : <ul className="site-xp-list">{rows.map((e) => <XPRow key={e.id} e={e} />)}</ul>}
      {all === null && recent.length > 0 && (
        <button className="site-textbutton" disabled={more === "busy"} onClick={loadAll}>
          {more === "busy" ? "Loading your ledger…" : more === "error" ? "Couldn't load it. Try again" : "See your full XP ledger"}
        </button>
      )}
    </section>
  );
}

/** Pick or change the username: type, see if it's free as you type, Save. Opened from the ✏️ next to the name. */
function NameEditor({ current, chosen, onSaved, onCancel }: {
  current: string; chosen: boolean; onSaved: (name: string) => Promise<unknown> | void; onCancel: () => void;
}) {
  const [name, setName] = useState(current);
  const [check, setCheck] = useState<{ ok: boolean; problem: string | null } | null>(null);
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [err, setErr] = useState("");
  const value = name.trim();
  const changed = value !== current;
  useEffect(() => { setName(current); }, [current]);
  useEffect(() => {
    if (!changed || value.length < 3) { setCheck(null); return; }
    const t = window.setTimeout(() => {
      void memberApi<{ ok: boolean; problem: string | null }>("/members/username/check", { method: "POST", body: JSON.stringify({ username: value }) })
        .then(setCheck).catch(() => setCheck(null));
    }, 300);
    return () => window.clearTimeout(t);
  }, [value, changed]);
  async function save() {
    setState("saving"); setErr("");
    try {
      await memberApi("/members/me", { method: "PATCH", body: JSON.stringify({ username: value }) });
      setState("saved");
      refreshSession();
      await onSaved(value);
    } catch (e) {
      setState("error"); setErr(e instanceof Error ? e.message : "Couldn't save. Try again.");
    }
  }
  return (
    <form className="pp-name is-editing" onSubmit={(e) => { e.preventDefault(); if (changed && check?.ok !== false) void save(); }}>
      <label className="pp-name-label" htmlFor="pp-name-input">{chosen ? "Change your name" : "Pick your name"}</label>
      <input id="pp-name-input" className="site-input" value={name} onChange={(e) => { setName(e.target.value); setState("idle"); }} maxLength={20}
             autoComplete="off" spellCheck={false} autoFocus={chosen} />
      <div className="pp-name-actions">
        <button type="submit" className="site-button" disabled={!changed || value.length < 3 || check?.ok === false || state === "saving"}>
          {state === "saving" ? "Saving…" : "Save"}</button>
        {chosen && <button type="button" className="site-textbutton" onClick={onCancel}>Cancel</button>}
      </div>
      <span className={`pp-name-hint${check?.ok === false || state === "error" ? " is-bad" : check?.ok ? " is-good" : ""}`} role="status">
        {state === "error" ? err : check?.ok === false ? check.problem : check?.ok ? "Available ✓" : "3-20 letters, numbers or underscores. Shown on the leaderboard."}
      </span>
    </form>
  );
}

function Settings({ p, act }: { p: Passport; act: (fn: () => Promise<unknown>, ok?: string) => Promise<void> }) {
  const { member } = p;
  const [confirmDelete, setConfirmDelete] = useState("");
  return (
    <div className="pp-settings">
      <p className="site-muted">Change your name with the pencil next to it at the top of your Passport.</p>
      <label className="site-check">
        <input type="checkbox" checked={!member.leaderboard_opt_out}
               onChange={(e) => void act(() => memberApi("/members/me", { method: "PATCH", body: JSON.stringify({ leaderboard_opt_out: !e.target.checked }) }))} />
        Show me on the public leaderboard
      </label>
      <label className="site-check">
        <input type="checkbox" checked={!!p.public_profile}
               onChange={(e) => void act(() => memberApi("/members/me", { method: "PATCH", body: JSON.stringify({ public_profile: e.target.checked }) }))} />
        Public profile page (username, rank, badges and join date only)
        {p.public_profile && <a className="site-link" href={`/website/u/${member.username}`}> · view</a>}
      </label>
      <div className="site-settings-actions">
        <button className="site-textbutton" onClick={() => void memberApi("/members/logout", { method: "POST" }).then(() => {
          refreshSession();
          location.assign("/");
        })}>Sign out</button>
      </div>
      <details className="site-details site-danger">
        <summary>Delete my Price Chat account</summary>
        <p className="site-note">This removes your account, linked accounts, XP history and badges for good. Type <strong>{member.username}</strong> to confirm.</p>
        <input className="site-input" value={confirmDelete} onChange={(e) => setConfirmDelete(e.target.value)} aria-label="Type your username to confirm" />
        <button className="site-textbutton site-danger-button" disabled={confirmDelete !== member.username}
                onClick={() => void act(() => memberApi("/members/me", { method: "DELETE", body: JSON.stringify({ confirm_username: confirmDelete }) })
                  .then(() => location.assign("/")))}>
          Delete account
        </button>
      </details>
    </div>
  );
}

// --- public profile ---------------------------------------------------------------------------------------------

interface PublicProfile {
  roles?: string[];
  username: string;
  joined_at: string;
  rank: Rank;
  badges: { key: string; name: string; description: string }[];
}

export function PublicProfilePage() {
  const { username = "" } = useParams();
  const [p, setP] = useState<PublicProfile | null | undefined>(undefined);
  const [ranks, setRanks] = useState<Rank[]>([]);
  const [shared, setShared] = useState(false);
  const viewer = useSession();
  useEffect(() => {
    memberApi<PublicProfile>(`/members/public/${encodeURIComponent(username)}`).then(setP).catch(() => setP(null));
    void memberApi<{ ranks: Rank[] }>("/members/ranks").then((r) => setRanks(r.ranks)).catch(() => undefined);
  }, [username]);
  async function share() {
    const url = `${location.origin}/u/${encodeURIComponent(p?.username ?? username)}`;
    const title = `${p?.username ?? username} on Price Chat`;
    try {
      if (navigator.share) { await navigator.share({ title, url }); return; }
      await navigator.clipboard.writeText(url);
      setShared(true);
      window.setTimeout(() => setShared(false), 2200);
    } catch { /* cancelled */ }
  }
  const level = p?.rank.level ?? 0;
  const isMe = !!viewer && !!p && viewer.username.toLowerCase() === p.username.toLowerCase();
  return (
    <SitePage {...(p ? profileHead(p) : { title: "Kiwi Passport", description: "A Price Chat member." })} path={`/u/${p?.username ?? username}`} noindex={p === null}>
      {p === undefined ? (
        <p className="site-empty">Loading…</p>
      ) : p === null ? (
        <div className="pub-missing">
          <p className="site-empty">This member's profile is private, or doesn't exist.</p>
          <a className="site-button" href="/leaderboard">See the leaderboard</a>
        </div>
      ) : (
        <>
          <section className={`site-passport pub-card rkd-tint-${p.rank.key} site-reveal`} aria-label={`${p.username}'s Kiwi Passport`}>
            <div className="site-passport-art"><RankKiwi art={p.rank.art} rankKey={p.rank.key} size={220} label={`${p.rank.name} Kiwi`}
                                                         worker={(p.roles ?? []).includes("WORKER")} /></div>
            <div className="site-passport-body">
              <p className="site-label">Kiwi Passport</p>
              <h1 className="site-display site-passport-name">{p.username}</h1>
              <p className="pub-rank"><span>{p.rank.name}</span>{ranks.length > 0 && <small>Rank {level} of {ranks.length}</small>}</p>
              {(p.roles ?? []).length > 0 && <div className="pub-roles">{p.roles!.includes("WORKER") && <WorkerBadge />}{p.roles!.includes("MANAGER") && <ManagerBadge />}</div>}
              <dl className="site-passport-facts pub-facts">
                <div><dt>Member since</dt><dd>{when(p.joined_at)}</dd></div>
                <div><dt>Badges</dt><dd>{p.badges.length}</dd></div>
              </dl>
              {p.badges.length > 0 && (
                <ul className="site-badges">{p.badges.map((b) => <li key={b.key} title={b.description}>{b.name}</li>)}</ul>
              )}
              <div className="pub-actions">
                <button type="button" className="site-button pub-share" onClick={() => void share()}>{shared ? "Link copied ✓" : "Share profile"}</button>
                {isMe && <a className="site-textbutton" href="/passport">Open my Passport →</a>}
              </div>
            </div>
          </section>

          {ranks.length > 0 && (
            <section className="pp-section pub-journey site-reveal" aria-labelledby="pub-journey-title">
              <h2 className="site-label" id="pub-journey-title">The Kiwi journey</h2>
              <ol className="pub-steps">
                {ranks.map((r) => (
                  <li key={r.level} className={r.level < level ? "is-done" : r.level === level ? "is-current" : "is-future"}>
                    <RankKiwi art={r.art} rankKey={r.key} size={r.level === level ? 64 : 44} />
                    <span>{r.name}</span>
                  </li>
                ))}
              </ol>
            </section>
          )}

          {viewer === null && (
            <section className="pub-cta site-reveal">
              <h2 className="site-display">Start your own Kiwi.</h2>
              <p>Join Price Chat, take part and grow from an Egg to a Mythic Kiwi.</p>
              <a className="site-button" href="/join">Join Price Chat</a>
            </section>
          )}
        </>
      )}
    </SitePage>
  );
}


function LinkedBanner({ platform, p }: { platform: string; p: Passport }) {
  if (platform === "email")
    return <p className="site-banner pp-connected" role="status"><strong>Email added.</strong> From now on you can sign in with a code sent to it. Your XP, rank and
      missions are unchanged.</p>;
  const l = p.linked.find((x) => x.platform === platform);
  const name = platform === "x" ? "X" : platform === "telegram" ? "Telegram" : "Google";
  if (!l?.connected) return <p className="site-banner" role="status">Connected.</p>;
  return (
    <p className="site-banner pp-connected" role="status">
      <strong>{name} connected</strong>
      {l.username ? ` @${l.username}` : ""} <span className="pp-verify pp-verify-verified">Verified</span>
    </p>
  );
}


const TREATMENT_LABEL: Record<string, string> = {
  egg: "Egg shell", chick: "Young Kiwi", none: "Classic Kiwi", gold: "Gold finish", aura: "Legend aura", mythic: "Mythic ring",
};

/** Done ranks normal, the current one bright, the next one glowing a little more as you get closer, the rest dimmed.
 *  Tap (or click) a rank for what it needs and what it brings. */
function RankLadder({ ranks, current, progress, entitlements }: {
  ranks: Rank[]; current: number; progress: number; entitlements: Passport["entitlements"];
}) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <ol className="site-ladder">
      {ranks.map((r) => {
        const state = r.level === current ? "is-current" : r.level < current ? "is-done" : r.level === current + 1 ? "is-next" : "is-future";
        const perks = entitlements.filter((e) => e.required_rank === r.level);
        return (
          <li key={r.level} className={state} style={r.level === current + 1 ? ({ "--near": progress.toFixed(2) } as React.CSSProperties) : undefined}>
            <button className="pp-rank-row" aria-expanded={open === r.level} onClick={() => setOpen(open === r.level ? null : r.level)}>
              <RankKiwi art={r.art} rankKey={r.key} size={40} />
              <span>{r.name}</span>
              <span className="site-muted">{r.min_xp.toLocaleString("en-GB")} XP</span>
            </button>
            {open === r.level && (
              <div className="pp-rank-detail">
                <p>Needs <strong>{r.min_xp.toLocaleString("en-GB")} XP</strong>{r.description ? `. ${r.description}` : ""}</p>
                <p className="site-muted">
                  Cosmetic: {TREATMENT_LABEL[r.art.treatment] ?? r.art.treatment}{r.art.placeholder ? " + accessory (official artwork coming)" : ""}
                </p>
                {perks.length > 0 && <p className="site-muted">Unlocks: {perks.map((e) => e.name).join(", ")}</p>}
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
