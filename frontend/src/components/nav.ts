/** The admin's map: a few areas in the sidebar, each with its pages as tabs at the top. Every page keeps its address, so old
 *  links and bookmarks still work. `todo` lists the home to-do keys counted on that area's sidebar badge. */

export interface NavPage { to: string; label: string; about: string; match?: string[] }
export interface NavArea { key: string; label: string; icon: string; pages: NavPage[]; todo?: string[] }

export const AREAS: NavArea[] = [
  { key: "home", label: "Home", icon: "⌂", pages: [{ to: "/", label: "Home", about: "What needs you today, and whether everything is running." }] },
  {
    key: "trading", label: "Trading", icon: "▲", todo: ["signals", "bot_access"],
    pages: [
      { to: "/trading-os", label: "Signals", about: "The signal scanner: signals waiting for your OK, open and past signals, and the switches that control them." },
      { to: "/trading-bot", label: "Bot access", about: "Who asked to join the VIP trading bot, and the rules to get in." },
      { to: "/affiliate", label: "Affiliate", about: "Your Hyperliquid referral link: sign-ups, volume and rewards." },
    ],
  },
  {
    key: "members", label: "Members", icon: "◆", todo: ["knight", "vip", "workers"],
    pages: [
      { to: "/members", label: "Members", about: "Everyone with a Kiwi Passport: their rank, XP and linked accounts." },
      { to: "/rank-rewards", label: "Rank rewards", about: "What each Kiwi rank unlocks, and reward claims waiting for you." },
      { to: "/workers", label: "Workers", about: "Paid helpers who invite people, and their reports." },
      { to: "/realm", label: "Realm (test)", about: "Play Vortex Realm, the Warrior Kiwi game, to test it. Members play it in Passport → Realm." },
    ],
  },
  {
    key: "community", label: "Telegram", icon: "➤", todo: [],
    pages: [
      { to: "/telegram", label: "Community", about: "What people are talking about in your Telegram chats.", match: ["/telegram/"] },
      { to: "/invite-bot/send", label: "Send invites", about: "Invite people to the Telegram group." },
      { to: "/invite-bot", label: "Invite bot", about: "How the invite bot works and its limits." },
    ],
  },
  {
    key: "social", label: "Social media", icon: "✺",
    pages: [
      { to: "/x", label: "X / Twitter", about: "Your X account: what's being said, post ideas and replies waiting for approval." },
      { to: "/youtube", label: "YouTube", about: "Your YouTube channel and new videos." },
      { to: "/brand-manager", label: "Brand Manager", about: "Plan and make posts for your social accounts." },
      { to: "/short-market", label: "Short Market", about: "Short market videos made automatically." },
    ],
  },
  {
    key: "content", label: "Content & AI", icon: "✦", todo: ["approvals", "drafts", "memories"],
    pages: [
      { to: "/approvals", label: "Approvals", about: "Posts and replies the AI prepared. Nothing goes out until you approve it." },
      { to: "/content", label: "Content", about: "Post ideas and drafts." },
      { to: "/brain", label: "AI Brain", about: "How the AI talks: its goals, voice and a test chat." },
      { to: "/knowledge", label: "Knowledge", about: "What the AI knows about Price Chat." },
    ],
  },
  { key: "pokemon", label: "Pokémon", icon: "◈", pages: [{ to: "/pokemon", label: "Pokémon Market", about: "Card prices, the marketplace and the vault.", match: ["/pokemon/"] }] },
  {
    key: "site", label: "Website", icon: "◐",
    pages: [
      { to: "/website", label: "Website", about: "What the public website shows." },
      { to: "/analytics", label: "Analytics", about: "Visitors, sign-ups and growth." },
    ],
  },
  {
    key: "system", label: "System", icon: "⚙",
    pages: [
      { to: "/settings", label: "Settings", about: "The emergency stop, background jobs and AI models." },
      { to: "/api-keys", label: "API keys", about: "Passwords and keys for the services Price Chat connects to." },
      { to: "/activity", label: "Activity log", about: "Everything Price Chat did, newest first." },
    ],
  },
];

/** The page (and its area) for a path: exact match first, then a page's sub-paths. */
export function locate(path: string): { area: NavArea; page: NavPage } {
  const p = path.replace(/\/+$/, "") || "/";
  for (const area of AREAS) for (const page of area.pages) if (page.to === p) return { area, page };
  for (const area of AREAS)
    for (const page of area.pages) if (page.to !== "/" && (page.match ?? [page.to + "/"]).some((m) => p.startsWith(m))) return { area, page };
  return { area: AREAS[0], page: AREAS[0].pages[0] };
}
