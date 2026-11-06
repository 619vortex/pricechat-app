import { useEffect, useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { SiteFooter, SiteHeader, useReveal, useStored } from "./Website";
import { pageHead } from "./seo";
import { useHead } from "./useHead";
import { site } from "./websiteData";
import type { HomeData } from "./websiteLogic";
import {
  memberApi, useCanonicalOrigin, useSession,
  type Providers, type Rank, type XPEvent,
} from "./members";
import { RankKiwi } from "./RankKiwi";
import { InviteModal, useReferrals } from "./invite";
import { LoginPanel } from "./emailLogin";
import { ConnectButton, PIcon, TelegramButton } from "./socialButtons";

export { ConnectButton, PIcon, TelegramButton };

/** A website page with the standard header and footer. A title that already names Price Chat is used as it is (the fixed
 * pages' heads live in seo.ts: `<SitePage {...pageHead("vpn")}>`); `noindex` for personal pages. */
export function SitePage({ title, description, path, noindex, children }: { title: string; description: string; path: string; noindex?: boolean; children: ReactNode }) {
  const home = useStored<HomeData>("/api/website/home");
  useHead({ title: title.includes("Price Chat") ? title : `${title} · Price Chat`, description, path, siteUrl: home?.site_url ?? null,
            image: site.shareImage, noindex });
  const root = useReveal();
  return (
    <div className="site" ref={root}>
      <SiteHeader home={home} base="/" solid />
      <main className="site-page site-page-anim">
        <div className="site-wrap">{children}</div>
      </main>
      <SiteFooter home={home} />
    </div>
  );
}

function param(name: string): string | null {
  return typeof location === "undefined" ? null : new URLSearchParams(location.search).get(name);
}

export function when(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export function JoinPage() {
  useCanonicalOrigin();
  const member = useSession();
  const { code } = useParams();
  const [providers, setProviders] = useState<Providers | null>(null);
  const [invited, setInvited] = useState<{ welcome_xp: number } | null>(null);
  const error = param("error");
  useEffect(() => {
    void memberApi<Providers>("/members/auth/providers").then(setProviders).catch(() => setProviders(null));
  }, []);
  useEffect(() => {
    // an invite link (/website/join/<code>): the server remembers the code for when this browser creates a Passport
    if (!code) return;
    void memberApi<{ valid: boolean; welcome_xp?: number; signed_in?: boolean }>("/members/referrals/visit", {
      method: "POST", body: JSON.stringify({ code }),
    }).then((r) => r.valid && !r.signed_in && setInvited({ welcome_xp: r.welcome_xp ?? 0 })).catch(() => undefined);
  }, [code]);
  useEffect(() => {
    if (member) location.replace("/passport");
  }, [member]);
  const emailOn = providers ? !!providers.email?.available : null;
  return (
    <SitePage {...pageHead("join")}>
      <section className="site-join-page site-reveal">
        <div className="site-join-intro">
          <RankKiwi art={{ treatment: "egg", overlay: null, placeholder: null, slot: null }} rankKey="egg" size={120} />
          <h1 className="site-display site-h1">
            <span>Join</span>
            <span className="site-green">Price Chat.</span>
          </h1>
          <p className="site-join-lines">
            <span>{signInWays(providers)}. No password.</span>
            <span>Earn XP. Level up.</span>
            <span>Connect your socials later for missions.</span>
          </p>
          <ul className="site-join-perks" aria-label="What you get">
            <li><span aria-hidden>▶</span> Watch Price Chat videos and read our posts for Kiwi XP</li>
            <li><span aria-hidden>🥝</span> Grow your Kiwi from The Egg to Mythic: every rank has a reward</li>
            <li><span aria-hidden>🎁</span> Free virtual Pokémon packs, a daily streak and the leaderboard</li>
          </ul>
        </div>
        <div className="site-join-box">
          {invited && (
            <p className="site-banner lb-invited" role="status">
              🥝 A friend invited you.{invited.welcome_xp > 0 ? <> Create your Kiwi Passport and do one real thing in Price Chat to get a
                <strong> +{invited.welcome_xp} XP</strong> welcome bonus.</> : null}
            </p>
          )}
          {error && <p className="site-error" role="alert">{error}</p>}
          <LoginPanel available={emailOn} onDone={({ created }) => location.assign(`/passport?welcome=${created ? "new" : "back"}`)} />


        </div>
      </section>
    </SitePage>
  );
}

/** "Telegram, X or Google": only the sign-in methods this server has switched on (email needs its mail provider). */
export function signInWays(p: Providers | null): string {
  const ways = p ? [p.telegram.available && "Telegram", p.x.available && "X", p.google.available && "Google", p.email?.available && "email"]
    .filter((w): w is string => !!w) : [];
  if (!ways.length) return "Telegram, X or Google";
  return ways.length === 1 ? ways[0] : `${ways.slice(0, -1).join(", ")} or ${ways[ways.length - 1]}`;
}

export function XPRow({ e }: { e: XPEvent }) {
  return (
    <li className={e.reversed ? "is-reversed" : ""}>
      <span className={`site-xp ${e.xp < 0 ? "is-neg" : ""}`}>
        {e.xp > 0 ? "+" : ""}
        {e.xp} XP
      </span>
      <span className="site-xp-reason">
        {e.reason}
        <span className="site-muted"> · {when(e.occurred_at)}{e.reversed ? " · reversed" : ""}</span>
      </span>
    </li>
  );
}

// --- leaderboard ------------------------------------------------------------------------------------------------

type Period = "week" | "all";
type Metric = "score" | "referrals";
interface Row {
  position: number;
  username: string;
  value: number;
  rank_score: number;
  xp: number;
  legacy_points: number;
  rank: Rank;
}
/** The signed-in member's own place, computed by the server (also when outside the top 100). */
interface MeRow {
  username: string;
  rank: Rank;
  position: number | null;
  value: number;
  hidden: boolean;
}
interface Board {
  rows: Row[];
  note: string;
  me: MeRow | null;
}

const MEDAL = ["🥇", "🥈", "🥉"];

/** Position changes since this browser last saw this board (e.g. #17 to #14 shows "↑ 3"). Nothing on a first look. */
function useStandingMoves(key: string, rows: { username: string; position: number }[] | null): Record<string, number> {
  const [moves, setMoves] = useState<Record<string, number>>({});
  useEffect(() => {
    if (!rows) return;
    let before: Record<string, number> = {};
    try {
      before = JSON.parse(localStorage.getItem(key) ?? "{}");
    } catch {
      before = {};
    }
    const out: Record<string, number> = {};
    for (const r of rows) if (before[r.username] && before[r.username] !== r.position) out[r.username] = before[r.username] - r.position;
    setMoves(out);
    try {
      localStorage.setItem(key, JSON.stringify(Object.fromEntries(rows.map((r) => [r.username, r.position]))));
    } catch {
      /* fine */
    }
  }, [key, rows]);
  return moves;
}

export function LeaderboardPage() {
  const [period, setPeriod] = useState<Period>("all");
  const [metric, setMetric] = useState<Metric>("score");
  const [data, setData] = useState<Board | null>(null);
  const [invite, setInvite] = useState(false);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const mine = useReferrals();
  const viewer = useSession();
  useEffect(() => {
    setData(null);
    setFailed(false);
    void memberApi<Board>(`/members/leaderboard?period=${period}&metric=${metric}&limit=100`)
      .then(setData).catch(() => setFailed(true));  // never "nobody here yet" when the board just didn't load
  }, [period, metric, attempt]);
  const unit = metric === "referrals" ? "referrals" : "XP";
  const moves = useStandingMoves(`pc-lb:${period}:${metric}`, data?.rows ?? null);
  const me = data?.me ?? null;
  const meInList = !!me?.position && !!data?.rows.some((r) => r.position === me.position && r.username === me.username);
  const tabs = <T extends string>(label: string, items: [T, string][], value: T, set: (v: T) => void) => (
    <div className="site-filters lb-tabs" role="tablist" aria-label={label}>
      {items.map(([k, text]) => (
        <button key={k} role="tab" aria-selected={value === k} className="site-filter" onClick={() => set(k)}>{text}</button>
      ))}
    </div>
  );
  const row = (r: { position: number | null; username: string; rank: Rank; value: number }, isMe: boolean, key: string) => (
    <li key={key} className={[r.position && r.position <= 3 ? `lb-top lb-top-${r.position}` : "", isMe ? "is-me" : "",
                              moves[r.username] ? "lb-moved" : ""].filter(Boolean).join(" ") || undefined}>
      <span className="site-board-pos">{r.position && r.position <= 3 ? <span className="lb-medal" aria-label={`#${r.position}`}>{MEDAL[r.position - 1]}</span>
        : r.position ? `#${r.position}` : "–"}</span>
      <RankKiwi art={r.rank.art} rankKey={r.rank.key} size={44} />
      <span className="site-board-member">
        <span className="site-board-user">
          {r.username}{isMe ? <span className="lb-you"> You</span> : null}
          {moves[r.username] ? (
            <span className={`lb-move${moves[r.username] > 0 ? " is-up" : ""}`} aria-label={`${moves[r.username] > 0 ? "up" : "down"} ${Math.abs(moves[r.username])}`}>
              {moves[r.username] > 0 ? "↑" : "↓"} {Math.abs(moves[r.username])}
            </span>
          ) : null}
        </span>
        <span className="site-muted">{r.rank.name}</span>
      </span>
      <span className="site-board-xp">{r.value.toLocaleString("en-GB")} {unit}</span>
    </li>
  );
  return (
    <SitePage {...pageHead("leaderboard")}>
      <div className="site-archive-head">
        <h1 className="site-display site-h1">🏆 Kiwi Leaderboard</h1>
        {tabs("Period", [["all", "All time"], ["week", "This week"]], period, setPeriod)}
      </div>
      {tabs("Board", [["score", "XP"], ["referrals", "Referrals"]], metric, setMetric)}
      {metric === "referrals" && mine && (
        <div className="lb-mine">
          <dl className="iv-stats" aria-label="Your referrals">
            <div><dt>Joined</dt><dd>{mine.joined}</dd></div>
            <div><dt>Qualified</dt><dd>{mine.qualified}</dd></div>
            <div><dt>XP earned</dt><dd>{mine.xp_earned}</dd></div>
          </dl>
          {mine.enabled && <button type="button" className="pp-go pp-go-sm" onClick={() => setInvite(true)}>👥 Invite friends</button>}
        </div>
      )}
      {data?.note && <p className="site-note lb-note">{data.note}</p>}
      {failed ? (
        <div className="site-empty" role="alert">
          <p>Couldn't load the leaderboard right now.</p>
          <button type="button" className="site-button" onClick={() => setAttempt((n) => n + 1)}>Try again</button>
        </div>
      ) : data === null ? (
        <p className="site-empty">Loading…</p>
      ) : data.rows.length === 0 ? (
        <p className="site-empty">
          {metric === "referrals" ? "No qualified invites yet. Be the first."
            : viewer ? <>Nobody here yet. <a className="site-link" href="/passport">Earn your first XP</a> and be the first.</>
            : <>Nobody here yet. <a className="site-link" href="/website/join">Join Price Chat</a> and be the first.</>}
        </p>
      ) : (
        <>
        {data.rows.length >= 3 && data.rows[2].position === 3 && (
          <div className="lb-podium" role="group" aria-label="Top 3">
            {[data.rows[1], data.rows[0], data.rows[2]].map((r) => {
              const isMe = !!me && meInList && r.username === me.username;
              return (
                <div key={r.username} className={`lb-pod lb-pod-${r.position}${isMe ? " is-me" : ""}`}>
                  <span className="lb-pod-kiwi"><RankKiwi art={r.rank.art} rankKey={r.rank.key} size={r.position === 1 ? 108 : 84} label={`${r.username}, ${r.rank.name}`} /></span>
                  <strong className="lb-pod-name">{r.username}{isMe ? <span className="lb-you"> You</span> : null}</strong>
                  <span className="lb-pod-val">{r.value.toLocaleString("en-GB")} {unit}</span>
                  <span className="lb-pod-step" aria-hidden><span>{MEDAL[(r.position ?? 1) - 1]}</span></span>
                </div>
              );
            })}
          </div>
        )}
        <ol className="site-board-list lb-list">
          {data.rows.map((r) => row(r, !!me && meInList && r.username === me.username, r.username))}
        </ol>
        </>
      )}
      {me && !meInList && (
        me.position ? (
          <ol className="site-board-list lb-list lb-me-row" aria-label="Your position">{row(me, true, "me")}</ol>
        ) : (
          <p className="site-note lb-note">
            {me.hidden ? "You're hidden from the leaderboard (Settings in your Kiwi Passport)."
              : metric === "referrals" ? "You'll appear here once a friend you invited qualifies." : "Earn some XP to appear on this board."}
          </p>
        )
      )}
      <p className="site-note site-board-foot">
        {metric === "referrals" ? "Counts friends who joined through an invite and then did something real in Price Chat."
          : period === "all" ? "All time: your Kiwi Rank Score (Price Chat XP plus a capped share of historical Telegram reputation)."
          : "This week: XP earned in the last 7 days."} Usernames only; members can hide themselves in their Kiwi Passport.
      </p>
      {invite && <InviteModal onClose={() => setInvite(false)} />}
    </SitePage>
  );
}

// --- legal (draft) ----------------------------------------------------------------------------------------------

type LegalKind = "privacy" | "vpnprivacy" | "terms" | "disclaimer" | "contact";

const LEGAL: Record<LegalKind, { title: string; description: string; sections: [string, string[]][] }> = {
  privacy: {
    title: "Privacy",
    description: "How Price Chat handles member data, connected accounts and Kiwi XP.",
    sections: [
      ["What we keep", [
        "Your Price Chat member id, username, join date and settings.",
        "For each account you choose to connect (Google, X, Telegram): the platform's account id, your public username where the platform gives one, and when you connected it.",
        "Your Kiwi XP history: every award or change, with its source and reason, and your badges.",
      ]],
      ["How connected accounts are used", [
        "Connected accounts may be used to verify eligible Price Chat community activity and to calculate your Kiwi XP, rank and badges.",
        "Only activity in Price Chat's own spaces counts: authorised Price Chat Telegram chats, and public posts that mention or reply to Price Chat on X. We never read unrelated chats.",
        "Accounts are only linked when you sign in to them yourself. We never assume two accounts belong to the same person because their names look alike.",
      ]],
      ["What we never do", [
        "We never ask for or see your social media passwords.",
        "We don't keep access tokens from Google or Telegram after confirming who you are. If you connect X, its token is kept encrypted so you can reply to or repost Price Chat's posts from your Kiwi Passport: we only post on X when you press Reply or Repost yourself, and we never like or message anything on your behalf.",
        "We don't infer sensitive characteristics or build hidden behaviour profiles. XP is never given because an AI “thinks” something about you.",
        "We don't know who watches our videos: YouTube doesn't tell channels that.",
      ]],
      ["Your controls", [
        "See every connected account and every XP award in your Kiwi Passport.",
        "Disconnect any connected account (keep at least one to sign in).",
        "Hide yourself from the public leaderboard.",
        "Delete your Price Chat account. This removes your account, connected accounts, XP history and badges. We keep only an audit note that an account was deleted, and a one-way fingerprint of each connected account so one-time rewards can't be claimed twice. Neither identifies you.",
      ]],
      ["Questions", ["Message us on Telegram or X (see Contact)."]],
    ],
  },
  vpnprivacy: {
    title: "Kiwi VPN privacy",
    description: "What Kiwi VPN and its browser extension store, what they don't, and why they need their permissions.",
    sections: [
      ["The short version", [
        "Kiwi VPN is run by Price Chat. It carries your traffic so websites see our server's address instead of yours. We don't record the sites you visit, and we don't sell your data or use it for advertising.",
      ]],
      ["What we keep", [
        "Your account: your Kiwi Passport member id, or a one-way fingerprint of your key if you use an anonymous key. Your Price Chat privacy page covers the Passport itself.",
        "For each browser using the extension: a random login for one server. We keep only a one-way fingerprint of its password, and the password itself stays in your browser's extension storage.",
        "For each WireGuard device: the name you gave it, its public key and the private address we assigned it. Its private key is created on our server, handed to you once in its config, and not stored.",
        "Usage: how many bytes each login or device used this month, and when it was last active. We use this to show you your usage, apply any monthly limit and keep the service fair.",
        "If payments are switched on and you pay: the amount, date and status of each payment. Payment providers handle the payment itself.",
      ]],
      ["What we don't keep", [
        "We don't log the websites or addresses you visit, your DNS lookups or the content of your traffic, and we don't keep a per-connection history of your use.",
        "Your traffic does pass through our server while the VPN is on, and it has to read the destination to carry it there. Pages on https stay encrypted between your browser and the website. Pages on plain http aren't encrypted beyond our server, as with any VPN.",
        "Our server's ordinary system and firewall logs can record connection attempts to the server, including IP addresses. We use them only for security, and the system rotates them away.",
        "Account requests to pricechat.live, from this website or the apps, are ordinary website visits: our hosting providers' request logs can record them, including your IP address. The extension's don't go through the VPN.",
      ]],
      ["The browser extension", [
        "While switched on, it sends that browser's traffic through the server you chose, except pricechat.live and addresses on your own network. It doesn't cover other apps. If you switch it off, disable it or remove it, your browser goes back to its normal connection.",
        "Proxy: to send traffic through the server. Web requests: to answer the server's password request and notice a refused login. Site access to all sites: the browser only lets an extension answer the server's password request, and notice a refused login, on sites it has access to, and the VPN carries every site. It also uses that access to reach your account on pricechat.live and the server's own check address, to confirm the connection is working. It doesn't read or change the pages you visit, and it runs no scripts on them. Privacy: to stop WebRTC revealing your real address while on. Storage: to remember your on/off state, server and login on your device.",
        "It uses your Kiwi Passport sign-in on pricechat.live to get its login. It has no ads or analytics and loads no code from outside its package.",
        "The use of information received through the extension is limited to providing this feature, in line with the Limited Use requirements of the Chrome Web Store User Data Policy and the Microsoft Edge Add-ons developer policies.",
      ]],
      ["The Windows app (public beta)", [
        "It keeps your sign-in and each device's private key on your PC, encrypted with Windows DPAPI so other Windows users can't read them. The private key is made on your PC and never sent to us. While you want the VPN on, the Kiwi VPN service also holds it in a folder only administrators can read, and it's deleted when you disconnect, sign out or uninstall.",
        "We keep the device's name, public key and private address, as for any device, and mark its sign-in as coming from the app. To show your public address it asks our own server, which sends the address back and doesn't store it. Our hosting providers' request logs can record it, as with any visit.",
        "It's a beta: it isn't code signed, it has no kill switch, and its connection, security, DNS leak and IPv6 leak testing isn't finished.",
      ]],
      ["Who else is involved", [
        "Our service runs on Hetzner (VPN servers), Railway (accounts and the service's data) and Vercel (this website). WireGuard devices look up addresses through Cloudflare's public DNS unless the server blocks ads, so Cloudflare receives those lookups under its own privacy policy. These companies provide infrastructure for us; we don't send them your data for their own use.",
        "We'd only share data beyond this if the law requires it, and we can only share what's listed above.",
      ]],
      ["How long, and your controls", [
        "In the extension, Remove this browser deletes its login straight away. Under My Devices you can delete any device.",
        "If your access ends, your devices are switched off and removed after 30 days. Deleting your Price Chat account removes your Kiwi VPN account, devices and browser logins with it.",
      ]],
      ["Changes and questions", ["We'll update this page if any of this changes. To ask about it, message us on Telegram or X (see Contact)."]],
    ],
  },
  terms: {
    title: "Terms",
    description: "The rules of the Price Chat community and Kiwi XP.",
    sections: [
      ["Your account", ["One Price Chat account per person. Keep your connected accounts secure; you're responsible for activity on your account."]],
      ["Kiwi XP, ranks and badges", [
        "Kiwi XP, ranks and badges recognise genuine participation. They have no monetary value and can't be sold, transferred or exchanged.",
        "Price Chat may change how XP is earned, adjust or reverse XP that was earned through spam, automation, fake accounts or other abuse, and pause XP on accounts under review.",
        "Ranks can't be bought. Any future paid perks will be kept separate from community rank.",
      ]],
      ["Community conduct", ["Be respectful. No spam, scams, impersonation, harassment or automated activity. Accounts that break the rules may be suspended or closed."]],
      ["Kiwi VPN", ["Don't use Kiwi VPN for anything illegal, or for spam, attacks on other systems, or to get around a service's rules on unauthorised access. We may suspend or remove access that puts the service or others at risk. It's provided as is, without a promise that it will always be available."]],
      ["Changes", ["We may update these terms as the community grows. Material changes will be announced."]],
    ],
  },
  disclaimer: {
    title: "Disclaimer",
    description: "Price Chat content is for information and entertainment.",
    sections: [
      ["Not financial advice", [
        "Everything on Price Chat (videos, posts, community discussions, the Price Board and any valuations) is for information and entertainment only. It is not financial, investment, legal or tax advice.",
        "Markets are risky. Prices can fall as well as rise and you can lose money. Do your own research and consider speaking to a qualified professional before making financial decisions.",
      ]],
      ["Community content", ["Opinions shared by members are their own and are not endorsed by Price Chat."]],
      ["Partners", ["If we ever feature partners or affiliate links, we'll say so clearly."]],
    ],
  },
  contact: {
    title: "Contact",
    description: "How to reach Price Chat.",
    sections: [
      ["Talk to us", [
        "The fastest way to reach the team is the Price Chat Telegram, or a message to @pricechat on X.",
        "For privacy requests (for example, deleting your data), you can also delete your account yourself from your Kiwi Passport at any time.",
      ]],
    ],
  },
};

export function LegalPage({ kind }: { kind: LegalKind }) {
  const page = LEGAL[kind];
  const home = useStored<HomeData>("/api/website/home");
  return (
    <SitePage {...pageHead(kind === "vpnprivacy" ? "vpn/privacy" : `website/${kind}`)}>
      <article className="site-legal site-reveal">
        <p className="site-draft" role="note">
          Draft: this page has not had final legal review yet.
        </p>
        <h1 className="site-display site-h1">{page.title}</h1>
        {page.sections.map(([heading, paras]) => (
          <section key={heading}>
            <h2 className="site-legal-h">{heading}</h2>
            {paras.map((t) => (
              <p key={t} className="site-body">
                {t}
              </p>
            ))}
          </section>
        ))}
        {kind === "contact" && home && (
          <ul className="site-legal-links">
            {(["telegram", "x", "youtube"] as const).filter((n) => home.links[n]).map((n) => (
              <li key={n}>
                <a className="site-link" href={home.links[n]!} target="_blank" rel="noopener noreferrer">
                  {n === "x" ? "X" : n === "telegram" ? "Telegram" : "YouTube"}
                </a>
              </li>
            ))}
          </ul>
        )}
      </article>
    </SitePage>
  );
}
