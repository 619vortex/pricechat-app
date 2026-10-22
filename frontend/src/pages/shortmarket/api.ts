import { request } from "../../api/client";

/** Short Market (owner dashboard): story ideas and the short videos made from them. No social network is involved:
 * a finished short is downloaded and posted by hand. Everything shown is real: statuses come from the backend and the worker. */

export type StageStatus = "waiting" | "running" | "done" | "failed" | "retrying" | "skipped";
export interface Stage { status: StageStatus; at: string; note: string }
export interface Source { name: string; url?: string; published?: string }

export interface Idea {
  id: number; key: string; category: string; category_label: string; topic: string; headline: string; score: number; found_at: string;
  facts: { text: string; source: string }[]; sources: Source[]; used: boolean; short_id: number | null;
}

export interface Short {
  id: number; topic: string; category: string; category_label: string; style: string; style_label: string; voice: string; seconds: number;
  quality: string; status: "queued" | "rendering" | "ready" | "failed"; stages: Record<string, Stage>; title: string | null; hashtags: string[];
  duration: number | null; size_bytes: number | null; has_video: boolean; has_thumb: boolean; error: string | null; generation: number;
  attempts: number; created_at: string; downloaded_at: string | null;
}

export interface ShortDetail extends Short {
  script: { title?: string; description?: string; hashtags?: string[]; engine?: string; character?: string;
            segments?: { text: string; screen: string; visual: string }[] };
  brief: { headline?: string; facts?: { text: string; source: string }[] };
  render_info: { worker?: string; tts?: string; llm?: string; seconds?: number; renderer?: string; scenes?: number; ai_scenes?: number } | null;
  caption: string; sources: Source[];
}

export interface Option { key: string; label: string }
export interface Overview {
  worker: { registered: number; online: boolean; name: string | null; last_seen_at: string | null; video_ai: { model: string } | null;
            voices: string[]; writer: string | null };
  counts: { queued: number; rendering: number; ready: number; failed: number };
  options: { categories: Option[]; styles: (Option & { accent: string })[]; voices: Option[]; lengths: number[];
             qualities: (Option & { detail: string; minutes: number })[]; defaults: Choices };
  pipeline: { key: string; label: string; where: string }[]; max_open: number;
}

/** style AUTO = a different look every time; seconds 0 = as long as the story needs (10 to 50 seconds). */
export interface Choices { style: string; voice: string; seconds: number; quality: string }
export const AUTO = "auto";

const post = <T,>(path: string, body: unknown = {}) => request<T>(path, { method: "POST", body: JSON.stringify(body) }, 90_000);
const S = "/short-market";

export const sm = {
  overview: () => request<Overview>(`${S}/overview`),
  ideas: (category?: string) => request<{ items: Idea[]; refreshed_at: string | null }>(`${S}/ideas${category ? `?category=${category}` : ""}`, {}, 90_000),
  refreshIdeas: () => post<{ items: Idea[]; refreshed_at: string | null }>(`${S}/ideas/refresh`),
  shorts: () => request<{ items: Short[]; total: number }>(`${S}/shorts`),
  short: (id: number) => request<ShortDetail>(`${S}/shorts/${id}`),
  make: (c: Choices, pick: { idea_id?: number; category?: string } = {}) => post<ShortDetail>(`${S}/shorts`, { ...c, seconds: c.seconds || null, idea_id: pick.idea_id ?? null, category: pick.category || null }),
  remake: (id: number, c: Partial<Choices> = {}) => post<ShortDetail>(`${S}/shorts/${id}/remake`, c),
  remove: (id: number) => request<{ deleted: boolean }>(`${S}/shorts/${id}`, { method: "DELETE" }),
  media: (s: Pick<Short, "id" | "generation">, kind: "video" | "thumb") => `/api${S}/shorts/${s.id}/media/${kind}?g=${s.generation}`,
  download: (s: Pick<Short, "id" | "generation">) => `/api${S}/shorts/${s.id}/media/video?download=1&g=${s.generation}`,
};

export const STATUS: Record<Short["status"], [string, "good" | "bad" | "neutral" | "info"]> = {
  queued: ["Waiting for the worker", "neutral"], rendering: ["Being made", "info"], ready: ["Ready to download", "good"], failed: ["Failed", "bad"],
};

const KEY = "pc-short-market-choices";

/** The owner's last style / voice / length / quality (this browser only). */
export function loadChoices(defaults: Choices): Choices {
  try {
    return { ...defaults, ...(JSON.parse(window.localStorage.getItem(KEY) ?? "{}") as Partial<Choices>) };
  } catch {
    return defaults;
  }
}

export function saveChoices(c: Choices): void {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(c));
  } catch { /* private mode: the choices just don't stick */ }
}
