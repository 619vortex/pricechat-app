/** Pure website logic, shared by the public site and the dashboard's Website page (no styles, no DOM). */

import type { Network } from "./websiteData";

/** One public content item, as /api/website/* returns it. `title` is the original, unchanged title. */
export interface WebsiteCard {
  platform: string;
  external_id: string;
  type: string;
  type_label: string;
  title: string;
  url: string;
  thumbnail: string | null;
  duration: string | null;
  published_at: string | null;
  featured: boolean;
  views?: number | null;
  series?: string | null;
  category?: string | null;
}

/** A Price Board row. Only ever filled from a real, timestamped market-data source (none connected yet). */
export interface Quote {
  symbol: string;
  name: string;
  price: number;
  currency: string;
  change_24h_pct: number | null;
  as_of: string;
  source: string;
}

export const SECTION_KEYS = ["latest", "trending", "whats_it_worth", "price_board", "originals", "community", "story"] as const;
export type SectionKey = (typeof SECTION_KEYS)[number];

export const SECTION_LABEL: Record<SectionKey, string> = {
  latest: "Latest Content",
  trending: "Trending strip",
  whats_it_worth: "What's It Worth?",
  price_board: "Price Board",
  originals: "Price Chat Originals",
  community: "Community",
  story: "Our Story",
};

/** A platform's real audience count and its recorded history (GET /api/website/home `growth`). Nothing is estimated:
 *  changes are null until a snapshot old enough exists, and history starts at the first snapshot. */
export interface Growth {
  count: number;
  unit: "subscribers" | "followers" | "members";
  as_of: string;
  tracking_since: string; // YYYY-MM-DD
  change_24h: number | null;
  change_7d: number | null;
  change_30d: number | null;
  change_window: number; // over the days in `history`
  change_window_pct: number | null;
  window_start: string;
  history: { day: string; value: number }[]; // last value of each UTC day, at most 30
}

/** The small change shown in a row: 7 days when that much history exists, else since tracking began. */
export function growthChange(g: Growth): { pct: number | null; abs: number; label: string } | null {
  if (g.history.length < 2) return null;
  if (g.change_7d !== null) {
    const base = g.count - g.change_7d;
    return { abs: g.change_7d, pct: base > 0 ? Math.round((g.change_7d / base) * 1000) / 10 : null, label: "7d" };
  }
  const since = new Date(`${g.window_start}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  return { abs: g.change_window, pct: g.change_window_pct, label: `since ${since}` };
}

/** Sparkline points (x 0..w, y 0..h, higher value = higher on screen), spaced by real dates. */
export function sparkPoints(history: Growth["history"], w: number, h: number, pad = 3): [number, number][] {
  if (history.length < 2) return [];
  const t = history.map((p) => Date.parse(`${p.day}T00:00:00Z`));
  const t0 = t[0], span = t[t.length - 1] - t0 || 1;
  const vals = history.map((p) => p.value);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  return history.map((p, i) => [
    Math.round(((t[i] - t0) / span) * w * 10) / 10,
    hi === lo ? h / 2 : Math.round((pad + (1 - (p.value - lo) / (hi - lo)) * (h - pad * 2)) * 10) / 10,
  ]);
}

export interface HomeData {
  sections: Record<SectionKey, boolean>;
  latest: WebsiteCard[];
  trending: string[];
  whats_it_worth: WebsiteCard[];
  originals: WebsiteCard[];
  price_board: Quote[];
  categories: string[];
  links: Record<Network, string | null>;
  growth?: Partial<Record<Network, Growth | null>>; // real counts per linked network (null = not measured yet)
  site_url: string | null;
  predictions?: boolean; // Price Chat Predictions is live: the Predictions link shows
  trading_bot?: boolean; // the VIP Trading Bot funnel is open to the public: the TRADING BOT tab shows
}

export interface SectionState {
  on: boolean; // switched on in the dashboard
  hasData: boolean; // real content exists
  visible: boolean; // on && hasData
  reason: string; // why it is hidden (empty when visible)
}

export function sectionStates(home: HomeData): Record<SectionKey, SectionState> {
  const data: Record<SectionKey, [boolean, string]> = {
    latest: [home.latest.length > 0, "No public videos synced yet"],
    trending: [home.trending.length > 0, "No approved community topics this week"],
    whats_it_worth: [home.whats_it_worth.length > 0, "Nothing placed in What's It Worth? yet"],
    price_board: [home.price_board.length > 0, "No market-data source connected"],
    originals: [home.originals.length > 0, "Nothing placed in Originals yet"],
    community: [Object.values(home.links).some(Boolean), "No public links set"],
    story: [true, ""],
  };
  const out = {} as Record<SectionKey, SectionState>;
  for (const k of SECTION_KEYS) {
    const on = home.sections[k] !== false;
    const [hasData, why] = data[k];
    out[k] = { on, hasData, visible: on && hasData, reason: !on ? "Switched off" : hasData ? "" : why };
  }
  return out;
}

const HASHTAG = /#[\p{L}\p{N}_]+/u;
const TRAILING_TAG = /[\s.,;:!?…|·-]*#[\p{L}\p{N}_]+\s*$/u;

/**
 * DISPLAY ONLY: a tidier headline for the website. Hashtags at the end are dropped and a hashtag inside
 * the sentence keeps its word ("Why #Bitcoin rose" → "Why Bitcoin rose"). The stored title never changes.
 */
export function displayTitle(raw: string): string {
  let t = raw.trim();
  // drop trailing hashtags, keeping the sentence's own punctuation ("millions.#story" → "millions.")
  for (let m = t.match(TRAILING_TAG); m && m.index !== undefined; m = t.match(TRAILING_TAG)) {
    const lead = m[0].match(/^[\s]*([.,;:!?…]*)/u)?.[1] ?? "";
    t = (t.slice(0, m.index) + lead).trimEnd();
  }
  t = t.replace(new RegExp(HASHTAG.source, "gu"), (tag) => tag.slice(1));
  t = t.replace(/\s{2,}/g, " ").replace(/[\s|·-]+$/u, "").trim();
  if (!t) t = raw.replace(/#/g, "").replace(/\s{2,}/g, " ").trim();
  return t.charAt(0).toLocaleUpperCase() + t.slice(1);
}

export function formatViews(n: number | null | undefined): string | null {
  if (n == null) return null;
  const v = n >= 1_000_000 ? `${trim1(n / 1_000_000)}M` : n >= 1000 ? `${trim1(n / 1000)}K` : String(n);
  return `${v} ${n === 1 ? "view" : "views"}`;
}

function trim1(x: number): string {
  return (Math.floor(x * 10) / 10).toFixed(1).replace(/\.0$/, "");
}

export function formatDate(iso: string | null, now: Date = new Date()): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "short", timeZone: "UTC" };
  if (d.getUTCFullYear() !== now.getUTCFullYear()) opts.year = "numeric";
  return d.toLocaleDateString("en-GB", opts);
}

/** YouTube serves a lower-resolution thumbnail for every video; used if the stored one fails to load. */
export function fallbackThumb(c: WebsiteCard): string | null {
  return c.platform === "youtube" ? `https://i.ytimg.com/vi/${c.external_id}/hqdefault.jpg` : null;
}

export type ArchiveFilter = "all" | "short" | "long_form" | (string & {}); // later: "article", "x_post"

export function filterArchive(items: WebsiteCard[], filter: ArchiveFilter, category: string | null): WebsiteCard[] {
  return items.filter((i) => (filter === "all" || i.type === filter) && (!category || i.category === category));
}

/** "@handle" from a public profile link (t.me/x, x.com/x, youtube.com/@x); null if the link has no handle. */
export function handleFromUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    const first = u.pathname.split("/").filter(Boolean)[0];
    if (!first || ["channel", "c", "user", "joinchat", "+"].includes(first) || first.startsWith("+")) return null;
    const name = first.replace(/^@/, "");
    return /^[A-Za-z0-9_.-]{2,64}$/.test(name) ? `@${name}` : null;
  } catch {
    return null;
  }
}
