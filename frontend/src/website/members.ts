/** Price Chat community members on the website: session, Kiwi Passport and sign-in API (cookies, same origin). */

import { useEffect, useState } from "react";

export interface RankArt {
  treatment: "egg" | "chick" | "none" | "gold" | "aura" | "mythic";
  overlay: string | null; // future layer art, drawn to the canonical Kiwi's 1000x844 frame
  placeholder: string | null; // marks the accessory slot until real art exists
  slot: string | null;
}

export interface Rank {
  level: number;
  key: string;
  name: string;
  min_xp: number;
  description: string;
  art: RankArt;
}

export interface SessionMember {
  id: string;
  username: string;
  xp: number;
  rank: Rank;
  unread?: number;
}

export type Verification = "verified" | "verified_click" | "self_reported" | "unverifiable";
export type MissionStatus = "available" | "in_progress" | "pending" | "completed" | "unavailable" | "expired";

export interface MissionView {
  id: number;
  key: string;
  kind: string;
  cadence: "once" | "daily" | "weekly";
  title: string;
  description: string;
  platform: "youtube" | "x" | "telegram" | "website";
  xp: number;
  verification: Verification;
  verification_label: string;
  verification_method: string;
  status: MissionStatus;
  frequency: string;
  cta: { label: string; action: "open" | "link" | "x_reply" | "x_repost" | "telegram_join"; url?: string; platform?: string; post_id?: string; claim?: string } | null;
  target: { url?: string; external_id?: string; thumbnail?: string; title?: string; text?: string; replied_to?: string | null;
            duration_seconds?: number; published_at?: string; posted_at?: string; metrics?: Record<string, number> };
  earns_xp?: boolean; // false for actions on X: X's developer policy forbids rewards for them
  goal: number;
  progress: number;
  expires_at: string | null;
  completed_at: string | null;
  xp_awarded: number;
}

export interface ActivityPeriod {
  telegram: number;
  x: number;
  youtube: number;
  website: number;
  xp: number;
  missions: number;
  active_days: number;
}

export interface Reputation {
  price_chat_xp: number;
  price_chat_weight: number;
  price_chat_points: number;
  combot_counts: boolean;
  combot: { xp: number; messages: number | null; rep: number | null; rank: string | null; active_days: number | null;
            source_timestamp: string; imported_at: string; source_type: string } | null;
  combot_weight: number;
  legacy_cap: number;
  legacy_raw: number;
  legacy_points: number;
  capped: boolean;
  rank_score: number;
  formula: string;
}

export interface TelegramActivity {
  username: string | null;
  messages_today: number;
  eligible_today: number;
  xp_today: number;
  daily_cap: number;
  active_days_7d: number;
  last_verified_at: string | null;
  next: string;
  note: string;
  telegram_user_id?: string;
  messages_7d?: number;
  xp_7d?: number;
  /** all time since linking: messages seen in the Price Chat Telegram, and the XP chatting has earned */
  messages_total?: number;
  xp_total?: number;
  historical?: { combot_xp: number; combot_messages: number | null; combot_rep: number | null; imported_at: string;
                 legacy_points: number; counts: boolean } | null;
}

export interface MemberNote {
  id: number;
  kind: string;
  title: string;
  body: string;
  at: string;
  read: boolean;
}

export interface FeedX {
  id: string;
  type: "post" | "reply" | "quote" | "repost";
  text: string;
  posted_at: string | null;
  metrics: { likes?: number; replies?: number; reposts?: number; impressions?: number };
  actions: { open: string; like: string; reply: string; repost: string };
  replied_to?: string | null;
}

export interface Feed {
  x_posts: FeedX[];
  x_conversations: FeedX[];
  videos: { id: string; type: string; title: string; thumbnail: string | null; duration_seconds: number | null; views: number | null;
            published_at: string | null; actions: { watch: string; comment: string; share: string; subscribe: string | null } }[];
  telegram: { url: string } | null;
}

export interface XPEvent {
  id: number;
  event_type: string;
  xp: number;
  category: string;
  source: string;
  reason: string;
  occurred_at: string;
  reversed: boolean;
  is_reversal: boolean;
}

export interface Passport {
  member: {
    id: string;
    username: string;
    display_name: string;
    username_chosen: boolean;
    joined_at: string;
    xp: number;
    bonus_xp: number;
    leaderboard_opt_out: boolean;
    xp_suspended: boolean;
    email?: string | null; // masked (j•••@gmail.com); set only once verified with a code
    email_verified?: boolean;
  };
  rank: { current: Rank; next: Rank | null; xp_to_next: number; progress: number; score?: number };
  linked: { platform: "telegram" | "x" | "google"; label: string; connected: boolean; username: string | null; verified_at: string | null }[];
  badges: { key: string; name: string; description: string; awarded_at: string; awarded_by: string }[];
  entitlements: { key: string; name: string; description: string; required_rank: number | null; enabled: boolean; unlocked: boolean }[];
  recent: XPEvent[];
  missions?: MissionView[];
  streak?: import("./streak").StreakView;
  level_packs?: number;
  roles?: string[];  // Worker System: WORKER / MANAGER (next to the rank)
  today?: MissionView[]; // at most 3: what to do now
  more_missions?: MissionView[];
  x?: { connected: boolean; username: string | null; can_act: boolean; needs_reconnect: boolean };
  next_steps?: { mission_id: number; title: string; xp: number; platform: string }[];
  activity?: Record<"today" | "7d" | "30d", ActivityPeriod>;
  breakdown?: { total: number; sources: { source: string; xp: number }[] };
  telegram?: TelegramActivity | null;
  public_profile?: boolean;
  notifications?: { unread: number; items: MemberNote[] };
  rank_up?: { id: number; title: string; level: number } | null;
  standing?: { position: number; board: string } | null; // all-time place, from the server (never computed here)
  reputation?: Reputation;
}

/** Watch & Earn (GET /members/me/watch): the newest Price Chat videos, featured first, and what each can still pay. */
export interface WatchVideo {
  external_id: string;
  platform: string;
  type: string; // "short" | "long_form"
  title: string;
  url: string;
  thumbnail: string | null;
  duration_seconds: number | null;
  published_at: string | null;
  xp: number;
  status: "available" | "completed" | "no_xp";
  needed_seconds: number; // play time in Price Chat's player before it counts
  latest: boolean; // the "Watch the latest Short" mission
}

export interface WatchList {
  threshold_seconds: number;
  videos: WatchVideo[];
  combo?: { today: number; next: "normal" | "double" | "double +5" }; // today's Watch combo (videos already paid, what the next pays)
}

export interface WatchResult {
  ok: boolean;
  code: "paid" | "already" | "refused" | "too_soon" | "not_started" | "connect_x";
  xp: number;
  message: string;
}

/** Read & Earn (GET /members/me/x-feed): Price Chat's latest X posts, one at a time. Reading here earns; X actions don't. */
export interface FeedItem {
  id: string;
  kind: "post" | "reply" | "quote";
  text: string;
  url: string;
  posted_at: string | null;
  parent: { text: string; name: string | null; username: string | null } | null; // the post Price Chat replied to / quoted
  replying_to: string | null;
  likes: number | null;
  replies: number | null;
  reposts?: number | null;
  xp: number; // what reading it here pays now (0 once today's cap is reached)
  author?: { name: string; username: string };
  media?: { type: string; url: string; width?: number | null; height?: number | null; alt?: string }[];
  done?: ("like" | "reply" | "repost")[]; // what the member already did from here (the server's record)
}

export interface XFeed {
  items: FeedItem[];
  remaining: number;
  read_seconds: number;
  today: { earned: number; cap: number; read?: number; per_post?: number };
  needs_x?: boolean; // Read & Earn shows nothing until X is connected (checked by the server)
  x: { connected: boolean; username: string | null; can_act: boolean; needs_reconnect: boolean };
}

export interface FeedResult {
  ok: boolean;
  code: "paid" | "already" | "refused" | "too_soon" | "not_started" | "connect_x";
  xp: number;
  message: string;
}

export interface XPRuleInfo {
  event_type: string;
  label: string;
  description: string;
  xp: number;
  daily_cap: number | null;
  max_per_member: number | null;
  enabled: boolean;
  availability: "available" | "unavailable" | "future";
  availability_note: string;
  verification_source: string;
}

export interface Providers {
  public_url?: string; // the one canonical website address (callbacks are derived from it on the server)
  google: { available: boolean; note: string; callback: string; javascript_origin?: string; client_id?: string | null };
  x: { available: boolean; note: string; callback: string; scopes?: string; website_url?: string; client_id?: string | null };
  telegram: { available: boolean; note: string; bot: string | null; mode: "deeplink" | "widget" };
  email?: { available: boolean; code_minutes: number; note: string };
}

/** The answer to a reply / repost sent from Price Chat. `ok` only when X confirmed it. */
export interface XActionResult {
  ok: boolean;
  code: string;
  message: string;
  url: string | null;
  reconnect: boolean;
}

export class MemberApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function memberApi<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(`/api${path}`, {
    credentials: "same-origin",
    ...init,
    headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...(init.headers ?? {}) },
  });
  if (!r.ok) {
    let message = `Something went wrong (${r.status}).`;
    try {
      const body = await r.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      /* not JSON */
    }
    throw new MemberApiError(r.status, message);
  }
  return r.status === 204 ? (undefined as T) : ((await r.json()) as T);  // 204 No Content (e.g. a delete) has no body
}

// One session request per page load, shared by the header and the page.
let sessionPromise: Promise<{ signed_in: boolean; member: SessionMember | null }> | null = null;
const listeners = new Set<() => void>();

export function refreshSession(): void {
  sessionPromise = null;
  listeners.forEach((l) => l());
}

function loadSession() {
  sessionPromise ??= memberApi<{ signed_in: boolean; member: SessionMember | null }>("/members/session").catch(() => ({ signed_in: false, member: null }));
  return sessionPromise;
}

/** undefined while loading, null when signed out. */
export function useSession(): SessionMember | null | undefined {
  const [member, setMember] = useState<SessionMember | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    const load = () => void loadSession().then((s) => alive && setMember(s.member));
    load();
    listeners.add(load);
    return () => {
      alive = false;
      listeners.delete(load);
    };
  }, []);
  return member;
}

/** Opening a Price Chat video from the website (XP once per video, server-side caps). Fire and forget. */
export function noteContentOpened(externalId: string): void {
  try {
    void fetch("/api/members/me/content-view", {
      method: "POST",
      credentials: "same-origin",
      keepalive: true,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ external_id: externalId }),
    }).catch(() => undefined);
  } catch {
    /* never block the link */
  }
}

export function signInUrl(provider: "google" | "x", purpose: "login" | "link" = "login"): string {
  return `/api/members/auth/${provider}/start?purpose=${purpose}`; // the start endpoint; callbacks are built server-side only
}

let providersCache: Promise<Providers> | null = null;
/** Which sign-in options work right now (cached; `fresh` asks again, e.g. for "Try again"). */
export function loadProviders(fresh = false): Promise<Providers> {
  if (fresh || !providersCache) {
    providersCache = memberApi<Providers>("/members/auth/providers");
    providersCache.catch(() => (providersCache = null));
  }
  return providersCache;
}

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);
/** OAuth only works on one address: cookies for localhost and 127.0.0.1 are different, and the provider sends the
 *  member back to the registered callback. In development, a page opened on the other local host moves over. */
export function useCanonicalOrigin(): void {
  useEffect(() => {
    if (typeof location === "undefined" || !LOCAL_HOSTS.has(location.hostname)) return;
    loadProviders()
      .then((p) => {
        if (!p.public_url) return;
        const target = new URL(p.public_url);
        if (LOCAL_HOSTS.has(target.hostname) && target.origin !== location.origin)
          location.replace(`${target.origin}${location.pathname}${location.search}${location.hash}`);
      })
      .catch(() => undefined);
  }, []);
}

/** The member's invite link and what it has earned (GET /members/me/referrals). */
export interface ReferralInfo {
  code: string;
  link: string;
  joined: number;
  qualified: number;
  xp_earned: number;
  reward_xp: number;
  welcome_xp: number;
  enabled: boolean;
  qualifying: string[]; // labels of what the invited friend must do first
}

/** The Hyperliquid referral link as the website shows it: /api/r/site_<page> counts the click (no IP / user agent stored),
 *  then 302s to the owner's current link. No link set → "" (nothing to show). */
export const trackedRef = (url: string | null | undefined, page: string): string => (url ? `/api/r/site_${page}?src=web` : "");
