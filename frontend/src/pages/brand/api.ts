import { request } from "../../api/client";

/** Brand Manager (owner dashboard). Everything here is real: statuses come from the backend and TikTok; nothing is simulated. */

export type StageStatus = "waiting" | "running" | "done" | "failed" | "retrying" | "skipped";
export interface Stage { status: StageStatus; at: string; note: string }

export interface Video {
  id: number; topic: string; category: string; category_label: string; template: string; template_label: string;
  status: "queued" | "rendering" | "needs_review" | "ready" | "approved" | "publishing" | "sent_to_drafts" | "published" | "failed" | "cancelled";
  score: number; slot_at: string | null; title: string | null; hashtags: string[]; duration: number | null; has_video: boolean; has_thumb: boolean;
  publish_mode: "drafts" | "direct" | null; publish_status: string | null; published_at: string | null;
  metrics: { views: number | null; likes: number | null; comments: number | null; shares: number | null; share_url?: string; matched_by?: string } | null;
  error: string | null; created_at: string; stages: Record<string, Stage>; approved: boolean; private: boolean; generation: number;
}

export interface VideoDetail extends Video {
  script: { hook?: string; voiceover?: string; title?: string; description?: string; hashtags?: string[]; engine?: string;
            segments?: { scene: string; text: string; screen?: string; visual?: string; beat?: string }[] };
  brief: { headline?: string; facts?: { text: string; source: string }[]; numbers?: Record<string, number> };
  qc: { problems: string[]; duration: number; width: number; height: number; size_bytes: number } | null;
  render_info: Record<string, unknown> | null; post_settings: Record<string, unknown> | null;
  sources: { name: string; url?: string; published?: string }[]; events: BmEvent[];
  creative?: Creative;
  media?: Asset[]; timeline?: Timeline | null;
}

/** Content Studio v4: one piece of authentic media in the video's library, with its rights record. */
export interface Asset {
  id: string; kind: "video" | "image"; provider: string; origin?: "research" | "owner"; url: string; page: string; title: string; creator: string;
  rights_holder: string; license: string; license_url: string; attribution: string;
  permitted?: { commercial: boolean; modify: boolean; attribution: boolean; editorial_only: boolean };
  width: number; height: number; duration: number | null; orientation: string; query?: string; shows: string;
  match: "exact" | "related" | "generic" | "unfit" | ""; score: number; note: string; authentic: boolean; audio?: boolean;
  status: "ok" | "excluded" | "failed" | "skipped"; thumb?: string;
}
export type Fit = "fill" | "frame";
export type Move = "none" | "push" | "pull" | "pan_left" | "pan_right" | "pan_up" | "pan_down";
export interface Clip { id: string; asset: string; start: number; dur: number; in?: number; fit: Fit; focus: [number, number]; zoom: number; move: Move; gain: number; dim?: number; shot?: string }
export type OverlayType = "title" | "lower_third" | "price" | "chart" | "facts" | "source" | "text";
export interface Overlay { id: string; type: OverlayType; start: number; dur: number; data: Record<string, unknown> }
export interface CaptionWord { t: string; s: number; e: number; p: number; hl?: boolean }
export interface Timeline {
  version: number; style: string; duration: number; end_card?: number; video: Clip[]; overlays: Overlay[];
  captions: { style: "clean" | "boxed" | "none"; words?: CaptionWord[] };
  narration: { mode: "ai" | "upload" | "none"; narrator?: string; gain: number }; music: { track: string | null; gain: number };
}
export interface Authenticity {
  score: number; grade: string; flags: string[]; judge: string; parts: Record<string, number>; weights: Record<string, number>;
  metrics: Record<string, number>;
}
export interface NewAsset {
  url: string; kind?: "video" | "image"; title?: string; creator?: string; rights_holder?: string; license: string; license_url?: string; page?: string;
  attribution?: string; shows?: string; match?: "exact" | "related" | "generic"; attest: boolean;
}

/** Content Studio v2: the look, voice and tone of one video ("auto" = chosen from the story) and its scene edits. */
export interface Creative {
  engine?: string; style?: string; tone?: string; narrator?: string; intensity?: string; music?: string; captions?: string; preset?: string;
  seconds?: number | null; pronunciations?: Record<string, string>; scenes?: Record<string, { screen?: string; visual?: string }>;
  production?: string; narration?: string; narration_url?: string | null; writer?: string; // v4
}
export interface CreativeOptions { options: Record<string, Record<string, string>>; defaults: Creative }
export interface QcCheck { id: string; label: string; status: "pass" | "warn" | "fail" | "skipped"; detail: string }
export interface StoryScene { kind: string; start: number; end: number; transition: string; screen: string }

export interface BmEvent { at: string; level: "info" | "warn" | "error"; stage: string | null; message: string; video_id: number | null }

export interface Automation {
  running: boolean; status: "running" | "stopped" | "blocked" | "error"; reason: string; started_at: string | null; last_tick_at: string | null;
  emergency_stop: boolean; upload_consent: boolean; blocking: string[]; warnings: string[];
}

export interface TikTokAccount {
  status: "connected" | "expired" | "revoked" | "error" | "disconnected"; username: string | null; display_name: string | null; avatar_url: string | null;
  open_id: string | null; profile: Record<string, unknown> | null; scopes: string[]; missing_scopes: string[]; can_upload_drafts: boolean;
  can_direct_post: boolean; can_read_stats: boolean; access_expires_at: string | null; refresh_expires_at: string | null;
  creator_info: { creator_nickname?: string; creator_username?: string; creator_avatar_url?: string; privacy_level_options?: string[]; comment_disabled?: boolean;
                  duet_disabled?: boolean; stitch_disabled?: boolean; max_video_post_duration_sec?: number } | null;
  last_error: string | null; connected_at: string | null; app_configured: boolean; redirect_uri: string;
}

/** One video from TikTok's video.list, as TikTok returned it. */
export interface TikTokVideo {
  id: string; title?: string; video_description?: string; create_time?: number; cover_image_url?: string; share_url?: string; duration?: number;
  view_count?: number; like_count?: number; comment_count?: number; share_count?: number;
}

export interface Worker { id: number; name: string; enabled: boolean; cloud: boolean; online: boolean; last_seen_at: string | null; info: Record<string, unknown> }

export interface Overview {
  brand: { id: number; name: string }; automation: Automation; tiktok: TikTokAccount;
  stats: { created_today: number; published_today: number; sent_to_drafts_today: number; upcoming: number; total_views: number | null;
           followers: number | null; followers_gained: number | null; best_video: Video | null; measured_videos: number };
  upcoming: Video[]; in_progress: Video[]; pipeline: { key: string; label: string; where: string }[]; workers: Worker[]; events: BmEvent[];
}

export interface Strategy {
  videos_per_day: number; min_seconds: number; max_seconds: number; language: string; voice: string; voice_name: string; voice_speed: number;
  categories: string[];
  category_weights: Record<string, number>; templates: Record<string, { accent: string; banner: string }>;
  hashtags: { always: string[]; per_category: Record<string, string[]>; max: number }; timezone: string; post_times: string[];
  publish_mode: "drafts" | "direct"; approval_required: boolean; tiktok_audited: boolean; content_disclosure: string; ai_disclosure: boolean;
  watermark: boolean; outro_cta: boolean; llm: string; ollama_model: string; quality: string; monthly_cost_limit_usd: number; risk_level: string;
  banned_topics: string[]; require_sources: boolean; duplicate_days: number; music: string;
}

export interface Brand {
  id: number; name: string; website: string; description: string; audience: string; industry: string; language: string; personality: string;
  logo_url: string; cta: string; topics: string[]; avoid: string[]; colors: string[]; competitors: string[]; examples: { text: string; approved: boolean }[];
  is_default: boolean; strategy: Strategy;
}

/** GET /brand also returns read-only catalogs (categories, templates, voices, defaults): choices to pick from, not settings. */
export interface BrandResponse extends Brand {
  categories: Record<string, { label: string; template: string }>; templates: Record<string, { label: string; accent: string; banner: string; scenes: string[] }>;
  voices?: Record<string, string>; defaults?: Strategy;
}

/** The fields PATCH /brand accepts: exactly the backend's BrandIn schema (app/api/routes/brand_manager.py). The narrator
 *  is a strategy setting (strategy.voice_name), so it's saved with the strategy. */
export const BRAND_FIELDS = ["name", "website", "description", "audience", "industry", "language", "personality", "logo_url", "cta",
  "topics", "avoid", "colors", "competitors", "examples", "strategy"] as const;
export type BrandPatch = Partial<Pick<Brand, (typeof BRAND_FIELDS)[number]>>;

export function brandPatch(b: Brand): BrandPatch {
  const out: Record<string, unknown> = {};
  for (const k of BRAND_FIELDS) if (b[k] !== undefined) out[k] = b[k];
  return out as BrandPatch;
}

export interface Insights {
  videos_measured: number; notes: string[]; best_length?: string | null; category_multipliers?: Record<string, number>; top_hooks?: string[];
  best_video?: { id: number; topic: string; views: number }; underperforming?: { id: number; topic: string; views: number }[];
  by_category?: Group[]; by_template?: Group[]; by_length?: Group[]; by_hour?: Group[];
}
export interface Group { key: string; videos: number; avg_views: number; engagement: number }

export type TrendStatus = "new" | "rising" | "searched" | "measuring" | "steady" | "cooling";
export interface TrendExample { title: string; source: string; url: string; at: string | null; rank?: number; symbol?: string }
export interface Trend {
  term: string; label: string; kind: "headline" | "search"; status: TrendStatus; docs: number; outlets: string[];
  baseline: number | null; momentum: number | null; strength: number; series: number[]; examples: TrendExample[]; day: string;
  videos: { id: number; topic: string; status: string; views: number | null }[];
}
export interface TrendsResponse {
  trends: Trend[]; scanned_at: string | null; outlets: string[]; category_on: boolean;
  learned: { by_trend: { key: string; videos: number; avg_views: number | null }[]; multiplier: number | null; words: { label: string; videos: number; avg_views: number }[] };
  scan?: { headlines: number; terms: number; failed: string[] };
}

const post = <T,>(path: string, body: unknown = {}) => request<T>(path, { method: "POST", body: JSON.stringify(body) });
const B = "/brand-manager";

export const bm = {
  overview: () => request<Overview>(`${B}/overview`),
  start: (consent: boolean) => post<Automation & { started: boolean }>(`${B}/auto/start`, { consent }),
  stop: () => post<Automation>(`${B}/auto/stop`),
  emergency: (on: boolean) => post<Automation>(`${B}/auto/emergency`, { on }),
  tick: () => post<Record<string, unknown>>(`${B}/auto/tick`),
  brand: () => request<BrandResponse>(`${B}/brand`),
  saveBrand: (b: BrandPatch) => request<Brand>(`${B}/brand`, { method: "PATCH", body: JSON.stringify(b) }),
  tiktok: () => request<TikTokAccount>(`${B}/tiktok`),
  connect: () => post<{ url: string }>(`${B}/tiktok/connect`),
  refreshTikTok: () => post<TikTokAccount>(`${B}/tiktok/refresh`),
  disconnect: () => post<TikTokAccount>(`${B}/tiktok/disconnect`),
  creatorInfo: () => post<TikTokAccount>(`${B}/tiktok/creator-info`),
  tiktokVideos: () => request<{ videos: TikTokVideo[]; at: string }>(`${B}/tiktok/videos`),
  setDestination: (id: number, mode: "drafts" | "direct") => request<VideoDetail>(`${B}/videos/${id}/destination`, { method: "PUT", body: JSON.stringify({ mode }) }),
  publishStatus: (id: number) => post<VideoDetail>(`${B}/videos/${id}/publish-status`),
  videos: (status?: string) => request<{ items: Video[]; total: number }>(`${B}/videos${status ? `?status=${status}` : ""}`),
  video: (id: number) => request<VideoDetail>(`${B}/videos/${id}`),
  create: (category?: string) => post<VideoDetail>(`${B}/videos`, { category: category || null }, ),
  edit: (id: number, b: { title?: string; description?: string; hashtags?: string[]; slot_at?: string | null }) =>
    request<VideoDetail>(`${B}/videos/${id}`, { method: "PATCH", body: JSON.stringify(b) }),
  approve: (id: number, b: Record<string, unknown> = {}) => post<VideoDetail>(`${B}/videos/${id}/approve`, b),
  publishNow: (id: number) => post<VideoDetail>(`${B}/videos/${id}/publish-now`),
  regenerate: (id: number) => post<VideoDetail>(`${B}/videos/${id}/regenerate`),
  creativeOptions: () => request<CreativeOptions>(`${B}/creative/options`),
  saveCreative: (id: number, c: Creative) => request<VideoDetail>(`${B}/videos/${id}/creative`, { method: "PUT", body: JSON.stringify(c) }),
  rerender: (id: number, scope: "video" | "scene" | "voice" | "captions" | "timeline", creative?: Creative) =>
    post<VideoDetail>(`${B}/videos/${id}/rerender`, { scope, ...(creative ? { creative } : {}) }),
  library: (id: number) => request<{ assets: Asset[]; updated_at: string | null }>(`${B}/videos/${id}/media`),
  addAsset: (id: number, a: NewAsset) => post<{ asset: Asset; assets: Asset[] }>(`${B}/videos/${id}/media`, a),
  setAsset: (id: number, assetId: string, b: { status?: "ok" | "excluded"; match?: "exact" | "related" | "generic" }) =>
    request<{ asset: Asset; assets: Asset[] }>(`${B}/videos/${id}/media/${assetId}`, { method: "PATCH", body: JSON.stringify(b) }),
  saveTimeline: (id: number, timeline: Timeline) => request<VideoDetail>(`${B}/videos/${id}/timeline`, { method: "PUT", body: JSON.stringify({ timeline }) }),
  remove: (id: number) => request<{ deleted: boolean }>(`${B}/videos/${id}`, { method: "DELETE" }),
  calendar: (days = 14) => request<{ timezone: string; post_times: string[]; free_slots: string[]; items: Video[] }>(`${B}/calendar?days=${days}`),
  analytics: () => request<{ insights: Insights; followers: { at: string; followers: number }[]; videos: Video[]; last_collected: string | null }>(`${B}/analytics`),
  collect: () => post<{ insights: Insights }>(`${B}/analytics/collect`),
  trends: () => request<TrendsResponse>(`${B}/trends`),
  refreshTrends: () => post<TrendsResponse>(`${B}/trends/refresh`),
  trendVideo: (term: string) => post<VideoDetail>(`${B}/trends/video`, { term }),
  workers: () => request<{ workers: Worker[]; queued: number; running: number }>(`${B}/workers`),
  addWorker: (name: string, cloud = false) => post<{ worker: Worker; token: string; command: string }>(`${B}/workers`, { name, cloud }),
  setWorker: (id: number, enabled: boolean) => request<Worker>(`${B}/workers/${id}`, { method: "PATCH", body: JSON.stringify({ enabled }) }),
  deleteWorker: (id: number) => request<{ deleted: boolean }>(`${B}/workers/${id}`, { method: "DELETE" }),
  media: (id: number, kind: "video" | "thumb", gen?: number) => `/api${B}/videos/${id}/media/${kind}${gen ? `?g=${gen}` : ""}`,
};

export const STATUS: Record<Video["status"], [string, "good" | "warn" | "bad" | "neutral" | "info"]> = {
  queued: ["Queued", "neutral"], rendering: ["Rendering", "info"], needs_review: ["Needs your approval", "warn"], ready: ["Ready", "info"],
  approved: ["Approved", "info"], publishing: ["Sending to TikTok", "info"], sent_to_drafts: ["In your TikTok drafts", "good"],
  published: ["Published", "good"], failed: ["Failed", "bad"], cancelled: ["Cancelled", "neutral"],
};

export function num(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${(n / 1e3).toFixed(1)}K` : n.toLocaleString();
}
