/** The admin's map (owner-approved redesign, 2026-11-22): six sections in the sidebar, each with its pages. Every page keeps its
 *  address, so old links and bookmarks still work. `todo` lists the home to-do keys counted on that section's badge.
 *  Price Chat Terminal is its own section and is deliberately left exactly as it was (its page list and sub-paths unchanged). */

export type NavIconKey = "overview" | "content" | "publishing" | "terminal" | "community" | "system";
/** `kind` (sections that have a one-page hub): "hub" is the section's page; "panel" is shown on the hub, folded, and its old address
 *  opens the hub with that panel open (`panel` names it); "tool" is a full screen of its own, one click from the hub, with a back link. */
export interface NavPage { to: string; label: string; about: string; match?: string[]; kind?: "hub" | "panel" | "tool"; panel?: string }
/** `hub`: the section is one page (its first page) with no tabs; its other pages are panels on it or tools one click away. */
export interface NavArea { key: string; label: string; short: string; icon: NavIconKey; pages: NavPage[]; todo?: string[]; hub?: boolean }

export const AREAS: NavArea[] = [
  {
    key: "overview", label: "Overview", short: "Overview", icon: "overview", todo: ["approvals"],
    pages: [
      { to: "/", label: "Home", about: "What needs you today, and whether everything is running." },
      { to: "/approvals", label: "Approvals", about: "Posts and replies the AI prepared. Nothing goes out until you approve it." },
      { to: "/analytics", label: "Analytics", about: "Visitors, sign-ups and growth." },
      { to: "/affiliate", label: "Affiliate", about: "Your Hyperliquid referral link: sign-ups, volume and rewards." },
    ],
  },
  {
    key: "content", label: "Content", short: "Content", icon: "content", todo: ["drafts", "memories"], hub: true,
    pages: [
      { to: "/content", label: "Ideas & drafts", about: "Everything being made: post ideas and drafts, Brand Manager posts, Short Market videos, and the AI's voice and knowledge.", kind: "hub" },
      { to: "/brand-manager", label: "Brand Manager", about: "Plan and make posts for your social accounts.", kind: "tool" },
      { to: "/short-market", label: "Short Market", about: "Short market videos made automatically.", kind: "tool" },
      { to: "/brain", label: "AI voice", about: "How the AI talks: its goals, voice and a test chat.", kind: "panel", panel: "ai" },
      { to: "/knowledge", label: "Knowledge", about: "What the AI knows about Price Chat.", kind: "panel", panel: "knowledge" },
    ],
  },
  {
    key: "publishing", label: "Publishing", short: "Publish", icon: "publishing", todo: ["signals"], hub: true,
    pages: [
      { to: "/publishing", label: "Publishing", about: "Where things go out: X, signals, YouTube and the public website.", kind: "hub" },
      { to: "/x", label: "X / Twitter", about: "Your X account: what's being said, post ideas and replies waiting for approval.", kind: "tool" },
      { to: "/trading-os", label: "Signals", about: "The signal scanner: signals waiting for your OK, open and past signals, and the switches that control them.", kind: "tool" },
      { to: "/youtube", label: "YouTube", about: "Your YouTube channel and new videos.", kind: "panel", panel: "youtube" },
      { to: "/website", label: "Website", about: "What the public website shows.", kind: "panel", panel: "website" },
    ],
  },
  {
    // owner, 2026-11-18: everything to do with Price Chat news in one place. Owner, 2026-11-22: left exactly as it is by the redesign.
    key: "terminal", label: "Price Chat Terminal", short: "Terminal", icon: "terminal",
    pages: [
      // Terminal 2.0 (owner, 2026-11-18): one page. News Desk, Trending, X Intelligence, World Monitor and diagnostics live in its Settings
      // drawer; their old addresses still open the full pages.
      { to: "/terminal", label: "Terminal", about: "Breaking news, emerging stories and everything Price Chat publishes, on one page.", match: ["/terminal/", "/world-monitor"] },
    ],
  },
  {
    key: "community", label: "Community", short: "Community", icon: "community", todo: ["bot_access", "knight", "vip", "workers"], hub: true,
    pages: [
      { to: "/community", label: "Community", about: "Members and what they ask for, Telegram, invites, Telegram trading and the Pokémon market.", kind: "hub" },
      { to: "/members", label: "Members", about: "Everyone with a Kiwi Passport: their rank, XP and linked accounts.", kind: "tool" },
      { to: "/telegram", label: "Telegram", about: "What people are talking about in your Telegram chats.", match: ["/telegram/"], kind: "tool" },
      { to: "/telegram-trading", label: "Telegram trading", about: "Members trading on Hyperliquid from the Telegram bot: mainnet switch, emergency stop, referral codes.", kind: "tool" },
      { to: "/pokemon", label: "Pokémon Market", about: "Card prices, the marketplace and the vault.", match: ["/pokemon/"], kind: "tool" },
      { to: "/rank-rewards", label: "Rank rewards", about: "What each Kiwi rank unlocks, and reward claims waiting for you.", kind: "panel", panel: "rewards" },
      { to: "/trading-bot", label: "VIP bot access", about: "Who asked to join the VIP trading bot, and the rules to get in.", kind: "panel", panel: "vip" },
      { to: "/workers", label: "Workers", about: "Paid helpers who invite people, and their reports.", kind: "panel", panel: "workers" },
      { to: "/invite-bot/send", label: "Send invites", about: "Invite people to the Telegram group.", kind: "panel", panel: "invites" },
      { to: "/invite-bot", label: "Invite bot", about: "How the invite bot works and its limits.", kind: "panel", panel: "invites" },
    ],
  },
  {
    key: "system", label: "System", short: "System", icon: "system",
    pages: [
      { to: "/settings", label: "Settings", about: "The emergency stop, background jobs and AI models." },
      { to: "/api-keys", label: "API keys", about: "Passwords and keys for the services Price Chat connects to." },
      { to: "/activity", label: "Activity log", about: "Everything Price Chat did, newest first." },
      { to: "/vpn", label: "Kiwi VPN", about: "Who can use it, whether it costs anything, servers and devices." },
      { to: "/kalshi", label: "Kalshi bot", about: "Your Kalshi prediction-market bot on your PC: balance, fee curve and the markets it watches. Read-only." },
      { to: "/realm", label: "Realm (test)", about: "Play Vortex Realm, the Warrior Kiwi game, to test it. Members play it in Passport → Realm." },
    ],
  },
];

/** The page (and its section) for a path: exact match first, then a page's sub-paths. */
export function locate(path: string): { area: NavArea; page: NavPage } {
  const p = path.replace(/\/+$/, "") || "/";
  for (const area of AREAS) for (const page of area.pages) if (page.to === p) return { area, page };
  for (const area of AREAS)
    for (const page of area.pages) if (page.to !== "/" && (page.match ?? [page.to + "/"]).some((m) => p.startsWith(m))) return { area, page };
  return { area: AREAS[0], page: AREAS[0].pages[0] };
}

/** Price Chat Terminal's routes: the shell around them stays exactly as it was (no bottom bar on phones, same frame). */
export function isTerminalPath(path: string): boolean {
  return locate(path).area.key === "terminal";
}

export interface JumpTarget { to: string; label: string; section: string; about: string }

/** Everything the "Jump to…" box can open: each section and each of its pages, matched on label, section and description. */
export function jumpTargets(): JumpTarget[] {
  const out: JumpTarget[] = [];
  for (const a of AREAS) {
    out.push({ to: a.pages[0].to, label: a.label, section: "Section", about: a.pages.map((p) => p.label).join(", ") });
    if (a.pages.length > 1) for (const p of a.pages) out.push({ to: p.to, label: p.label, section: a.label, about: p.about });
  }
  return out;
}

/** The best matches for what was typed: every word must appear; labels starting with the first word come first. */
export function searchJump(query: string, limit = 8): JumpTarget[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  return jumpTargets()
    .map((t, i) => {
      const label = t.label.toLowerCase();
      const hay = `${label} ${t.section.toLowerCase()} ${t.about.toLowerCase()}`;
      if (!words.every((w) => hay.includes(w))) return null;
      return { t, score: (label.startsWith(words[0]) ? 0 : label.includes(words[0]) ? 1 : 2) * 1000 + i };
    })
    .filter((x): x is { t: JumpTarget; score: number } => x !== null)
    .sort((a, b) => a.score - b.score)
    .slice(0, limit)
    .map((x) => x.t);
}
