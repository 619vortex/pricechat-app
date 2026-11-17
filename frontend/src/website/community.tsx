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
            <li><span aria-hidden>🥝</span> Grow your Kiwi from The Egg to Mythic and unlock rewards along the way</li>
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

// --- legal -----------------------------------------------------------------------------------------------------

type LegalKind = "privacy" | "vpnprivacy" | "vpnterms" | "vpnrefunds" | "vpnaup" | "terms" | "disclaimer" | "contact";

/**
 * Business facts only the owner can give. Each renders as a highlighted "[OWNER: …]" on the page, and the Kiwi VPN pages that
 * carry one say so in their note, so none can go live unnoticed. Fill a fact in by replacing its value here with the real text
 * (every page uses it), then run the tests: OWNER_MARKERS lists what's still open. Never guess one.
 */
const OWNER = {
  entity: "[OWNER: legal entity or trader name]",
  address: "[OWNER: postal address]",
  email: "[OWNER: business email for Kiwi VPN]",
  company: "[OWNER: company registration number, or remove if there is none]",
  vat: "[OWNER: VAT number, or remove if not VAT-registered]",
  law: "[OWNER: governing-law country]",
  courts: "[OWNER: courts with jurisdiction]",
  tax: "[OWNER: whether the price includes VAT or other sales tax]",
  taxRecords: "[OWNER: how long tax law requires payment records to be kept]",
  transfers: "[OWNER: safeguard relied on for transfers outside the EU/UK, e.g. Standard Contractual Clauses]",
} as const;
const MARKER = /\[OWNER: [^\]]+\]/g;

/** When the Kiwi VPN pages' text last changed. */
const VPN_UPDATED = "17 November 2026";

/** The checkout box, word for word (vpn.tsx START_NOW): the refund page quotes it. */
const START_NOW_TEXT = "Start my Kiwi VPN time as soon as my payment is confirmed. I understand that if I cancel within 14 days, I get back only the part of my payment for time I haven't used, and that I can't cancel once all the time I paid for has been used.";

/** `note` replaces the standard draft banner. `updated`: a page in force, with the date its text last changed (no draft banner). */
const LEGAL: Record<LegalKind, { title: string; description: string; note?: string; updated?: string; sections: [string, string[]][] }> = {
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
      ["Kiwi VPN", ["If you use Kiwi VPN, its own privacy page (/vpn/privacy) explains what it keeps, who provides it and how to use your data rights."]],
      ["Questions", ["Message us on Telegram or X (see Contact)."]],
    ],
  },
  vpnprivacy: {
    title: "Kiwi VPN privacy",
    description: "What Kiwi VPN and its browser extension store, what they don't, and why they need their permissions.",
    updated: VPN_UPDATED,
    sections: [
      ["The short version", [
        "Kiwi VPN is run by Price Chat. It carries your traffic so websites see our server's address instead of yours. We don't keep a history of the sites you visit, and we don't sell your data or use it for advertising. Some things do pass through other companies, such as your DNS lookups: this page names them.",
      ]],
      ["Who is responsible for your data", [
        `${OWNER.entity}, ${OWNER.address}, decides how the personal data on this page is used (the “controller”, “we” on this page). For privacy questions and requests, write to ${OWNER.email}.`,
        "Your Kiwi Passport itself (your Price Chat account, connected accounts and XP) is covered by the Price Chat privacy page (/privacy).",
      ]],
      ["What we keep", [
        "Your account: your Kiwi Passport member id, or a one-way fingerprint of your key if you use an anonymous key. Your Price Chat privacy page covers the Passport itself.",
        "For each browser using the extension: a random login for one server. We keep only a one-way fingerprint of its password, and the password itself is kept only in your browser's memory while it runs. After the browser restarts, the extension makes a new login with your Price Chat sign-in and the old one is removed.",
        "For each WireGuard device: the name you gave it, its public key and the private address we assigned it. Its private key is created on our server, handed to you once in its config, and not stored.",
        "Usage: how many bytes each login or device used this month, and when it was last active (for a WireGuard device, the time of its last handshake with the server). We use this to show you your usage, apply any monthly limit and keep the service fair.",
        "If payments are switched on and you pay: the amount, date, network and status of each payment, and the address and provider reference it used, and a record that you ticked the box at checkout asking for your time to start straight away. Payment providers handle the payment itself. For an anonymous key, the link between a payment and your key, and its address and reference, are removed once the payment is settled and at least 7 days old, and the amount and date stay for our accounts.",
        "If your paid time or free trial is ending and you use a Kiwi Passport: a reminder in your notifications, and by email if your Passport has an email address. An anonymous key has no way to be reached, so only the Kiwi VPN page shows it.",
        "If you start a free trial: a one-way fingerprint (a salted hash) of each sign-in identity on your Passport, such as your verified email address and your linked Google, X or Telegram accounts, so that one person can't take a second free trial with another account. We can't turn a fingerprint back into the address or account, but we can check whether a given one has already had a trial.",
        "When a free trial starts, our abuse checks may record which check would have refused it, and for the checks on networks, a fingerprint of the network it came from (a salted hash of the first three parts of an IPv4 address, or the first 48 bits of an IPv6 address), never the address itself. How many trials started from an address is counted in memory only, for 24 hours.",
        "If you send a bug report or feedback from the Kiwi VPN app: your message, the app version and the app's diagnostics. Keys and other secrets are removed before anything is stored.",
      ]],
      ["Why we use it", [
        "To provide Kiwi VPN to you (your account, devices and logins, usage, payments, reminders and support): it's needed for our contract with you.",
        "To keep the service secure and fair (usage limits, the free-trial fingerprints and checks, server and hosting logs, and handling abuse): our legitimate interest in protecting the service and the people who use it. You can object to this (see Your rights).",
        "To keep payment records: our legal obligations under tax and accounting law.",
        "We don't make decisions about you by automated means that have legal or similarly significant effects. Our trial checks can turn down a free trial automatically: if that happens and you think it's wrong, contact us and a person will look at it.",
      ]],
      ["What we don't keep", [
        "We don't keep a history of the websites or addresses you visit or of the content of your traffic, and we don't keep a per-connection history of your use.",
        "Your traffic does pass through our server while the VPN is on, and it has to read the destination to carry it there: while a connection is open, the server's memory holds where it's going. Pages on https stay encrypted between your browser and the website. Pages on plain http aren't encrypted beyond our server, as with any VPN.",
        "While a WireGuard device is set up on a server, the server's memory holds the IP address it last connected from, because WireGuard needs it to send your traffic back. We don't copy it to our database, and it's gone when the device is removed or the server restarts.",
        "Our server's ordinary system and firewall logs can record connection attempts to the server, including IP addresses. We use them only for security, and the system rotates them away.",
        "Account requests to pricechat.live, from this website or the apps, are ordinary website visits: our hosting providers' (Railway's and Vercel's) request logs record them, including your IP address and the page or API address asked for, and keep them for as long as those providers keep their logs. The extension's don't go through the VPN.",
      ]],
      ["Your DNS lookups", [
        "Every site you open starts with a DNS lookup that turns its name into an address, so whoever answers those lookups can see the names of the sites you visit.",
        "Depending on the server, they're answered by the server's own resolver or passed to a public resolver: Cloudflare (1.1.1.1) for WireGuard devices, or the hosting provider's (Hetzner's) resolvers for the browser extension. The extension's lookups to Hetzner aren't encrypted. Cloudflare and Hetzner handle them under their own privacy policies.",
        "We're setting our servers' own resolvers up not to record lookups, but we haven't yet verified that on every server, so we don't promise it. We'll update this page when we have.",
        "A WireGuard device's config shows which one it uses on its DNS line: 1.1.1.1 means Cloudflare, and our server's own address means the server's resolver.",
      ]],
      ["The browser extension", [
        "While switched on, it sends that browser's traffic through the server you chose, except pricechat.live and addresses on your own network. pricechat.live always uses your normal connection, so the extension can still reach your account when the VPN is down, which means Price Chat sees your real IP address. It doesn't cover other apps or private (incognito) windows. If you switch it off, disable it or remove it, your browser goes back to its normal connection.",
        "Proxy: to send traffic through the server. Web requests: to answer the server's password request and notice a refused login. Site access to all sites: the browser only lets an extension answer the server's password request, and notice a refused login, on sites it has access to, and the VPN carries every site. It also uses that access to reach your account on pricechat.live and the server's own check address, to confirm the connection is working. It doesn't read or change the pages you visit, and it runs no scripts on them. Privacy: to stop WebRTC revealing your real address while on. Storage: to remember your on/off state and server on your device, and the login's password in memory only.",
        "It uses your Kiwi Passport sign-in on pricechat.live to get its login. It has no ads or analytics and loads no code from outside its package.",
        "The use of information received through the extension is limited to providing this feature, in line with the Limited Use requirements of the Chrome Web Store User Data Policy and the Microsoft Edge Add-ons developer policies.",
      ]],
      ["The Windows app (public beta)", [
        "It keeps your sign-in and each device's private key on your PC, encrypted with Windows DPAPI so other Windows users can't read them. The private key is made on your PC and never sent to us. While you want the VPN on, the Kiwi VPN service also holds it in a folder only administrators can read, and it's deleted when you disconnect, sign out or uninstall.",
        "We keep the device's name, public key and private address, as for any device, and mark its sign-in as coming from the app. To show your public address it asks our own server, which sends the address back and doesn't store it. Our hosting providers' request logs can record it, as with any visit.",
        "It's a beta: it's code signed and has an optional kill switch in Settings. If something doesn't work, tell us here.",
      ]],
      ["Who else is involved", [
        "These companies provide infrastructure for us and handle your data only to do that, not for their own use:",
        "Hetzner (Germany): our VPN servers, and on some servers the DNS resolvers the browser extension uses. Railway: our accounts service and its database. Vercel: this website, which passes account requests on to our service. NOWPayments: USDT payments; it receives the amount, the network and a random order reference, never your name, email address or Passport. Resend: our reminder emails, if your Passport has an email address. Telegram: tells the Price Chat team that a support report has arrived, with your username but not your message.",
        "DNS lookups that a server doesn't answer itself go to Cloudflare or Hetzner, as described above, under their own privacy policies.",
        `Some of these companies are based in, or process data in, countries outside the EU and the UK, including the United States. For those transfers we rely on ${OWNER.transfers}.`,
        "We'd only share data beyond this if the law requires it, and we can only share what's listed above.",
      ]],
      ["How long we keep it", [
        "Your Kiwi VPN account, devices and browser logins: until you delete them or your Price Chat account. In the extension, Remove this browser deletes its login straight away. Under My Devices you can delete any device. If your access ends, your devices are switched off and removed after 30 days.",
        "Usage: only this month's total for each device and login, replaced at the start of each month. When each was last active is kept to the nearest 5 minutes.",
        `Payment records, with the checkout record: for as long as tax and accounting law requires (${OWNER.taxRecords}). For an anonymous key, the link to the key, the payment address and the provider's reference go once the payment is settled and at least 7 days old. If you delete your Price Chat account, your payments are no longer linked to you, but the payment record itself is kept for our accounts.`,
        "Free-trial fingerprints: with no end date, also after you delete your account, since stopping a second free trial is their only purpose. Free-trial check records: 30 days.",
        "Support reports: until you delete your Price Chat account.",
        "Deleting your Price Chat account removes your Kiwi VPN account, devices, browser logins and support reports with it.",
      ]],
      ["Your rights", [
        "Data protection law, such as the GDPR and the UK GDPR, lets you ask for a copy of your data, have it corrected or deleted, restrict or object to how we use it, and get the data you gave us in a portable form.",
        `You can do much of this yourself: delete devices under My Devices, remove a browser in the extension, and delete your whole Price Chat account from your Kiwi Passport. For anything else, write to ${OWNER.email}. We'll answer within one month.`,
        "For an anonymous key we can only find your data with the key, so we'll ask you to show it's yours privately. Never post your key in a public message.",
        "You can also complain to the data protection authority in the country where you live or work.",
      ]],
      ["Children", ["Kiwi VPN is for people aged 18 or over, and we don't knowingly keep data about anyone younger."]],
      ["Changes and questions", [`We'll update this page and its date if any of this changes. To ask about it, write to ${OWNER.email}, or message us on Telegram or X (see Contact).`]],
    ],
  },
  vpnterms: {
    title: "Kiwi VPN terms",
    description: "What you get with Kiwi VPN, what it costs, how renewal works and when access ends.",
    updated: VPN_UPDATED,
    sections: [
      ["Who provides Kiwi VPN", [
        `Kiwi VPN is a Price Chat service provided by ${OWNER.entity}, ${OWNER.address} (company number ${OWNER.company}, VAT number ${OWNER.vat}), “we” in these terms. Contact: ${OWNER.email}.`,
        "These terms, the refund policy (/vpn/refunds) and the acceptable use policy (/vpn/acceptable-use) are the agreement between you and us for Kiwi VPN. The privacy page (/vpn/privacy) explains what we keep.",
        `For quick help, message the Price Chat team on Telegram or X (see Contact). For payments, cancellations and complaints, write to ${OWNER.email} with your receipt number (it starts with KV-), so there's a record.`,
      ]],
      ["Who can use it", [
        "You must be 18 or over, or the age of majority where you live if that's higher, to use Kiwi VPN or pay for it.",
        "You're responsible for checking that using a VPN is legal where you are, and for how you use it.",
      ]],
      ["The plan", [
        `Kiwi VPN costs $9.99 (US dollars) for 30 days, paid in USDT on Ethereum, Tron or Solana through NOWPayments (${OWNER.tax}). Any amount within the limits shown buys its share of that time, so $5 buys about 15 days. You can add between $5 and $500 at a time; each network has its own minimum, shown before you pay, and network fees are yours.`,
        "It gives you access to Kiwi VPN's servers through our apps, the browser extension and manual WireGuard setup, for as many devices and with any monthly data allowance shown for your account on the Kiwi VPN page.",
        "Royal Kiwis and above get Kiwi VPN free for as long as they hold that rank. A Kiwi Passport account can use one free 30-day trial. Opening extra accounts to get more trials isn't allowed, and we may remove access gained that way.",
      ]],
      ["Renewal: only when you choose", [
        "Nothing renews or charges by itself. You add time by hand when you want more, and we hold no card or wallet access. If you use a Kiwi Passport, we'll remind you 3 days and 1 day before your time ends, and when it has ended.",
      ]],
      ["When your time starts and ends", [
        "Paid time starts when your payment is confirmed, and it's added after any paid time or free trial you have left, so paying early doesn't cost you any days. The time we credit follows what actually arrives.",
        "At checkout you ask us to start your time as soon as the payment is confirmed, by ticking a box. That affects your right to cancel: see below.",
        "When your time runs out, your devices are switched off within a few minutes and kept for 30 days. Add time in those 30 days and they come back on by themselves. After that they're removed, and you can add new ones.",
      ]],
      ["Your right to cancel (EU and UK)", [
        "If you live in the EU or the UK, you can cancel a purchase of Kiwi VPN time within 14 days of paying. Because your time starts straight away, you get back the part of that payment for the time you haven't used. The refund policy (/vpn/refunds) explains how.",
      ]],
      ["Anonymous keys", ["If you pay with an anonymous key, the key is the only thing that identifies your account. We can't recover a lost key, and any time on it is lost with it. Keep it safe."]],
      ["Paying correctly", ["Send the exact amount shown, on the network shown, to the address shown. Coins sent on another network or in another currency may be lost, and we may not be able to recover them."]],
      ["Using the service", [
        "Don't use Kiwi VPN for anything illegal, or for spam, attacks on other systems, or to get around a service's rules on unauthorised access. We may suspend or remove access that puts the service or others at risk, and blocked accounts can't buy more time. The acceptable use policy at /vpn/acceptable-use lists the rules in full.",
        "Kiwi VPN is provided as is. We don't promise it will always be available, and no VPN makes you fully anonymous or secure. Our privacy page lists what we keep. Our apps have their own limits, which their download pages explain.",
        "We may add, change or retire servers, locations and features. If a change makes Kiwi VPN materially worse for you, you can end your purchase and we'll refund the paid time you haven't used.",
      ]],
      ["Ending", [
        "You can stop at any time: delete your devices, or your whole Price Chat account. Paid time you don't use isn't refunded, except as the refund policy says.",
        "We may suspend or end your access if you seriously or repeatedly break these terms or the acceptable use policy. Where we can, we'll tell you why first.",
      ]],
      ["Our liability", [
        "Nothing in these terms limits any liability that the law doesn't allow us to limit, such as for death or personal injury caused by negligence, or for fraud, or takes away your legal rights as a consumer.",
        "Apart from that, we're not liable for losses we couldn't reasonably have foreseen, for business losses, or for losses caused by your own devices, software or use of the service. Our total liability to you for Kiwi VPN is limited to what you paid us for it in the 12 months before the claim.",
      ]],
      ["Law and disputes", [
        `These terms are governed by the law of ${OWNER.law}, and disputes go to the courts of ${OWNER.courts}.`,
        "If you're a consumer living in the EU or the UK, you also keep the protection of the mandatory consumer laws of the country where you live, and you can bring a claim in your local courts.",
        `If something goes wrong, please write to ${OWNER.email} first: most problems can be put right quickly.`,
      ]],
      ["Refunds", ["See the refund policy at /vpn/refunds."]],
      ["Changes", ["If the price or these terms change, we'll say so on the Kiwi VPN page before you pay, and this page will show the new date. Time you've already paid for stays at the price you paid."]],
      ["Questions", [`Write to ${OWNER.email}, or message us on Telegram or X (see Contact).`]],
    ],
  },
  vpnrefunds: {
    title: "Kiwi VPN refund policy",
    description: "When a Kiwi VPN payment can be corrected or refunded, and what to do if a payment goes wrong.",
    updated: VPN_UPDATED,
    sections: [
      ["The short version", ["USDT payments go straight over the blockchain and we can't reverse them, so we normally put things right by correcting your time. Use your free trial first: it's there so you can check Kiwi VPN works for you before you pay. If you live in the EU or the UK, you can also cancel within 14 days (below)."]],
      ["Right to cancel", [
        "If you live in the EU or the UK, the law gives you 14 days from the day you pay to cancel a purchase of Kiwi VPN time, without giving a reason.",
        `Your time starts as soon as your payment is confirmed, because you ask for that at checkout by ticking this box: “${START_NOW_TEXT}” So if you cancel within the 14 days, we refund the part of that payment for the time you haven't used yet, at that payment's rate. Once all the time you paid for has been used, it can't be cancelled.`,
        `To cancel, write to ${OWNER.email} within the 14 days, with your receipt number (it starts with KV-) and a USDT address on the network you paid with for the refund. One sentence is enough, for example: “I cancel my Kiwi VPN purchase, receipt KV-000123, paid on 1 December 2026.” For an anonymous key, we'll ask you privately to show the key is yours.`,
        "We refund within 14 days of receiving your cancellation, in USDT on the network you paid with, and we pay the network fee for it. Your access to the refunded time ends when we refund.",
      ]],
      ["If a payment goes wrong", [
        "Your time hasn't appeared: most payments are credited a few minutes after the network confirms them. Open Payment history on the Kiwi VPN page and press Check now. If it still doesn't appear, contact us with the receipt number (it starts with KV-) and we'll look into it.",
        "You paid twice, or paid too much: everything that arrives counts as time, so you won't lose it. If you'd rather have an extra payment back, contact us.",
        "You paid too little: you get time for what arrived, and you can send the rest or start a new payment.",
      ]],
      ["Other refunds", [
        "If you paid and we couldn't provide Kiwi VPN to you, because of a fault on our side that we couldn't fix, contact us within 14 days of the payment, and we'll refund the time you haven't used.",
        "If we close the service, we'll refund the paid time left on your account where we can reach you, which means we may not be able to for an anonymous key.",
        "These refunds go back in USDT to an address you give us, less the network and processing fees, at the amount of USDT that payment's unused time is worth. The time refunded comes off your account.",
      ]],
      ["When we won't", [
        "Apart from the right to cancel above: time you've already used, a change of mind after a trial you could have used first, coins sent on the wrong network or in the wrong currency that can't be recovered, lost anonymous keys, and accounts blocked for breaking our terms.",
      ]],
      ["Your legal rights", ["Nothing here takes away any rights the law gives you where you live."]],
      ["Asking for help", [`Write to ${OWNER.email}, or message us on Telegram or X (see Contact), with your receipt number. Never send us your anonymous key in a public message.`]],
    ],
  },
  vpnaup: {
    title: "Kiwi VPN acceptable use",
    description: "What you can't use Kiwi VPN for, what happens after an abuse report, and what we can and can't see.",
    updated: VPN_UPDATED,
    sections: [
      ["The short version", [
        "Kiwi VPN is for private, everyday browsing. Many people share each server and its address, so one person's abuse can get that address blocked for everyone. Don't use Kiwi VPN for anything below.",
        "This policy is part of the Kiwi VPN terms (/vpn/terms) and applies to everyone who uses Kiwi VPN, on any device, the browser extension or an anonymous key.",
      ]],
      ["Not allowed", [
        "Anything illegal where you are or where the server is.",
        "Spam: bulk or unwanted email, messages, comments or sign-ups. Servers may block outgoing mail ports.",
        "Scanning, probing or attacking other systems: port scans, vulnerability scans, password guessing, denial-of-service traffic, or getting into accounts or systems you're not allowed into.",
        "Fraud, phishing, spreading malware, running bots or botnets, and harassing or threatening people.",
        "Getting around our own limits: sharing one account among many people, reselling access, or opening extra accounts for more trials.",
      ]],
      ["File sharing (P2P)", [
        "Peer-to-peer file sharing, such as BitTorrent, isn't supported for now. Please don't use it through Kiwi VPN. We may slow down or block it on a server without notice.",
      ]],
      ["What we can and can't see", [
        "We don't keep a history of the sites you visit or of your connections, so we usually can't tell which account was behind an address and a time in a report after the fact.",
        "While traffic is flowing, a server can see which of its devices or browser logins is sending it, where it's going and how much data it uses. That's what lets us find and stop abuse that's still happening.",
        "We can't see the content of encrypted (https) traffic. Our privacy page lists everything we keep.",
      ]],
      ["Abuse reports and suspension", [
        "When we receive an abuse report that we can verify, for example from a network operator or our hosting provider, we may suspend the device, browser login or account involved, or block that kind of traffic on the server for everyone.",
        "If your account is suspended, we'll tell you in your Kiwi Passport notifications where we can. Time lost to a suspension for breaking these rules isn't refunded (see /vpn/refunds). If you think we got it wrong, contact us and we'll look again.",
      ]],
      ["Reporting abuse", [
        "We're still setting up a dedicated abuse address. Until it's ready, report abuse coming from a Kiwi VPN server through our support channels: message the Price Chat team on Telegram or X (see Contact), or write to the contact address in the Kiwi VPN terms. Kiwi VPN members can also use Support in the Kiwi VPN app.",
        "Please include the server's address, the time with its time zone, and what happened.",
      ]],
      ["Changes", ["We'll update this page and its date if these rules change. The Kiwi VPN terms (/vpn/terms) and privacy page (/vpn/privacy) still apply."]],
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
      ["Kiwi VPN", ["Don't use Kiwi VPN for anything illegal, or for spam, attacks on other systems, or to get around a service's rules on unauthorised access. We may suspend or remove access that puts the service or others at risk. It's provided as is, without a promise that it will always be available. Payments, renewal and refunds are covered by the Kiwi VPN terms (/vpn/terms) and refund policy (/vpn/refunds), and the full rules by the acceptable use policy (/vpn/acceptable-use)."]],
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
      ["Kiwi VPN", ["For Kiwi VPN payments, cancellations, complaints and privacy requests, use the contact address in the Kiwi VPN terms (/vpn/terms), with your receipt number if you have one."]],
    ],
  },
};

const LEGAL_PATH = {
  privacy: "privacy", vpnprivacy: "vpn/privacy", vpnterms: "vpn/terms", vpnrefunds: "vpn/refunds", vpnaup: "vpn/acceptable-use",
  terms: "terms", disclaimer: "disclaimer", contact: "contact",
} as const satisfies Record<LegalKind, string>;

/** Every owner fact a legal page still waits for, with the pages showing it: empty once the owner has filled them all in. */
export const OWNER_MARKERS: { marker: string; pages: string[] }[] = Object.values(OWNER).filter((v) => v.startsWith("[OWNER:")).map((marker) => ({
  marker,
  pages: (Object.keys(LEGAL) as LegalKind[]).filter((k) => LEGAL[k].sections.some(([, ps]) => ps.some((p) => p.includes(marker)))).map((k) => `/${LEGAL_PATH[k]}`),
}));

function slug(heading: string): string {
  return heading.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

/** A paragraph's text with owner markers highlighted and site paths such as /vpn/refunds turned into links. */
const PIECES = /(\[OWNER: [^\]]+\]|\/(?:vpn\/[a-z-]+|privacy|terms|contact)\b)/;  // no lookbehind: older Safari can't parse one
function LegalText({ text }: { text: string }) {
  return (
    <>
      {text.split(PIECES).map((part, i) => (i % 2 === 0 ? part
        : part.startsWith("[OWNER:") ? <mark key={i} className="site-owner-marker">{part}</mark>
        : <a key={i} className="site-link" href={part}>{part}</a>))}
    </>
  );
}

export function LegalPage({ kind }: { kind: LegalKind }) {
  const page = LEGAL[kind];
  const home = useStored<HomeData>("/api/website/home");
  const open = page.sections.some(([, ps]) => ps.some((p) => new RegExp(MARKER.source).test(p)));
  useEffect(() => {  // /vpn/refunds#right-to-cancel from the checkout: the page renders after the browser looked for the anchor
    const id = typeof location !== "undefined" ? location.hash.slice(1) : "";
    if (id) document.getElementById(id)?.scrollIntoView?.();
  }, [kind]);
  return (
    <SitePage {...pageHead(LEGAL_PATH[kind])}>
      <article className="site-legal site-reveal">
        {page.updated ? (
          open && <p className="site-draft" role="note">Being finalised: the highlighted details are still to be filled in.</p>
        ) : (
          <p className="site-draft" role="note">
            {page.note ?? "Draft: this page has not had final legal review yet."}
          </p>
        )}
        <h1 className="site-display site-h1">{page.title}</h1>
        {page.updated && <p className="site-muted site-legal-updated">Last updated {page.updated}</p>}
        {page.sections.map(([heading, paras]) => (
          <section key={heading} id={slug(heading)}>
            <h2 className="site-legal-h">{heading}</h2>
            {paras.map((t) => (
              <p key={t} className="site-body">
                <LegalText text={t} />
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
