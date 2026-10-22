import { MemberApiError, memberApi } from "../../../website/members";

/** Pokémon Portfolio API (the member area: the Kiwi Passport session cookie). */

export interface PCardBrief {
  id: number;
  name: string;
  pokemon_name: string;
  set_name: string;
  set_code: string | null;
  card_number: string | null;
  year: number;
  language: string;
  edition: string;
  variant: string;
  rarity: string | null;
  image_url: string | null;
  era: string;
}

export interface HoldingValue {
  each: number | null;
  low: number | null;
  high: number | null;
  total: number | null;
  source: "sales" | "scrydex" | "scrydex_raw" | "comparable" | "grade_equivalent" | "none" | null;
  confidence: "high" | "medium" | "low" | null;
  note: string | null;
  valued_at: string | null;
  reason?: string | null;
  reason_label?: string | null;
}

export interface ConditionReport {
  low: number;
  high: number;
  confidence: "low" | "medium" | "high";
  enough_photos: boolean;
  centering: string;
  corners: string;
  edges: string;
  surface: string;
  issues: string[];
  more_photos: string[];
  label: string;
  at: string;
}

export interface CommunityOpinion {
  open: boolean;
  responses: number;
  min_responses: number;
  mine: number | null;
  label: string;
  distribution?: { estimate: number; share: number }[];
  notes?: string[];
}

export interface Holding {
  id: number;
  card: PCardBrief;
  kind: "raw" | "graded";
  grading_company: "PSA" | "BGS" | "CGC" | null;
  grade: string | null;
  label: string;
  quantity: number;
  added_at: string;
  source: "manual" | "scan" | "giveaway";  // giveaway: a virtual Pack Giveaway card (follows the binder)
  verification: string;
  cert_status: string | null;
  condition: { low: number; high: number; source: "owner" | "ai" | null } | null;
  community_review: boolean;
  is_public: boolean;
  photos: { id: number; side: "front" | "back" | "closeup"; width: number; height: number }[];
  value?: HoldingValue;
  changes?: { "30d"?: number | null; "90d"?: number | null };
  owner?: string;
  community?: CommunityOpinion | null;
  // owner only
  cert_number?: string | null;
  notes?: string;
  purchase_price?: number | null;
  purchase_date?: string | null;
  condition_report?: ConditionReport | null;
}

export interface PeriodChange {
  value: number | null;
  market: number | null;
  flows: number | null;
  pct_market: number | null;
  base?: number;
  base_day?: string;
  reason: string | null;
}

export interface PortfolioSettings {
  id: number;
  username: string;
  visibility: "private" | "public";
  show_value: boolean;
  show_purchase: boolean;
  comments_enabled: boolean;
  bio: string;
  featured: number[];
  review_status: string;
  public_url: string;
}

export interface Group { key: string; copies: number; value: number; unpriced: number }

export interface Dashboard {
  portfolio: PortfolioSettings;
  summary: {
    value: number | null;
    valued_at: string | null;
    unique_cards: number;
    copies: number;
    graded: number;
    raw: number;
    priced_holdings: number;
    unpriced_holdings: number;
    virtual_value?: number; virtual_copies?: number;  // Pack Giveaway cards (virtual) inside the value
    most_valuable: Holding | null;
    changes: Record<"30d" | "90d" | "1y", PeriodChange>;
  };
  top: (Holding & { share?: number | null })[];
  composition: Record<"set" | "language" | "grade" | "rarity" | "kind", Group[]>;
  recent: Holding[];
  holdings: Holding[];
  history: { t: string; value: number; flows: number | null; copies: number | null; current?: boolean }[];
}

export interface Candidate {
  card: PCardBrief;
  suggested: boolean;
  score: number | null;
  reference_prices: Record<string, number>;
  warning?: string;
}

export interface ScanResult {
  candidates: Candidate[];
  confidence: "likely" | "uncertain";
  needs_choice: boolean;
  reason: string;
  detected: { type: string | null; language: string | null; graded: { company: string; grade: string; cert: string | null } | null;
    label?: { year: number | null; reverse: boolean; first: boolean } | null };
  scan_id: number;
  cached: boolean;
}

export interface PublicPortfolio {
  username: string;
  bio: string;
  rank: { name: string; key: string; level: number; art: Record<string, unknown> };
  owner: boolean;
  visibility: string;
  show_value: boolean;
  comments_enabled: boolean;
  value: number | null;
  value_note: string;
  cards: number;
  unique_cards: number;
  graded: number;
  raw: number;
  featured: Holding[];
  top: Holding[];
  composition: { set: Group[]; grade: Group[] } | null;
  holdings: Holding[];
  total: number;
  offset: number;
  review_status: string | null;
}

export interface Comment {
  id: number;
  parent_id: number | null;
  holding_id: number | null;
  author: string;
  body: string | null;
  deleted: boolean;
  at: string;
  can_delete: boolean;
  replies?: Comment[];
}

export interface ExploreRow {
  position: number;
  username: string;
  bio: string;
  rank: { name: string; key: string; art: Record<string, unknown> };
  value: number | null;
  cards: number;
  updated_at: string | null;
}

export interface Status { scanner: boolean; condition: boolean; photos: boolean; scans_per_day: number }

/** What a card would be valued at for the chosen raw / grade, before adding it. */
export interface QuoteValue {
  value: number | null;
  low: number | null;
  high: number | null;
  source: string;
  confidence: "high" | "medium" | "low" | null;
  note: string;
  as_of: string | null;
  reason?: string;
  reason_label?: string;
}

export interface Diagnostics {
  holdings: number;
  identified: number;
  priced: number;
  priced_exact: number;
  priced_comparable: number;
  unpriced: number;
  by_reason: { reason: string; label: string; count: number }[];
  unpriced_holdings: { holding_id: number; portfolio_id: number; card: string; grade: string; reason: string | null; note: string | null; valued_at: string | null; prices_checked_at: string | null }[];
  provider_failures_24h: number;
  last_refresh: { status: string | null; at: string | null };
  scanner_7d: { scans: number; matched: number; median_top_score: number | null; low_confidence: number };
}

export interface HoldingInput {
  card_id: number;
  kind: "raw" | "graded";
  grading_company?: "PSA" | "BGS" | "CGC" | null;
  grade?: string | null;
  quantity?: number;
  cert_number?: string | null;
  condition_low?: number | null;
  condition_high?: number | null;
  purchase_price?: number | null;
  purchase_date?: string | null;
  notes?: string;
  is_public?: boolean;
  source?: "manual" | "scan";
}

const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) });

/** multipart upload (the browser sets the boundary); same error shape as memberApi */
async function upload<T>(path: string, form: FormData): Promise<T> {
  const r = await fetch(`/api${path}`, { method: "POST", body: form, credentials: "same-origin", headers: { Accept: "application/json" } });
  if (!r.ok) {
    let message = `Something went wrong (${r.status}).`;
    try {
      const b = await r.json();
      if (typeof b.detail === "string") message = b.detail;
    } catch {
      /* not JSON */
    }
    throw new MemberApiError(r.status, message);
  }
  return (await r.json()) as T;
}

export const portfolioApi = {
  status: () => memberApi<Status>("/members/portfolio/status"),
  dashboard: () => memberApi<Dashboard>("/members/portfolio"),
  settings: (s: Partial<Omit<PortfolioSettings, "id" | "username" | "review_status" | "public_url">>) =>
    memberApi<PortfolioSettings>("/members/portfolio", { method: "PATCH", ...json(s) }),
  search: (q: string) => memberApi<{ query: string; results: Candidate[] }>(`/members/portfolio/search?q=${encodeURIComponent(q)}`),
  quote: (card_id: number, kind: "raw" | "graded", company: string | null, grade: string | null) =>
    memberApi<QuoteValue>(`/members/portfolio/quote?card_id=${card_id}&kind=${kind}${company ? `&company=${company}` : ""}${grade ? `&grade=${encodeURIComponent(grade)}` : ""}`),
  add: (h: HoldingInput) => memberApi<Holding>("/members/portfolio/holdings", { method: "POST", ...json(h) }),
  update: (id: number, h: Partial<HoldingInput> & { community_review?: boolean }) =>
    memberApi<Holding>(`/members/portfolio/holdings/${id}`, { method: "PATCH", ...json(h) }),
  remove: (id: number) => memberApi<{ removed: boolean }>(`/members/portfolio/holdings/${id}`, { method: "DELETE" }),
  duplicate: (id: number) => memberApi<Holding>(`/members/portfolio/holdings/${id}/duplicate`, { method: "POST" }),
  holding: (id: number) => memberApi<Holding>(`/members/portfolio/holdings/${id}`),
  uploadPhoto: (id: number, file: Blob, side: "front" | "back" | "closeup") => {
    const f = new FormData();
    f.append("file", file, "photo.jpg");
    f.append("side", side);
    return upload<{ id: number }>(`/members/portfolio/holdings/${id}/photos`, f);
  },
  deletePhoto: (id: number) => memberApi<{ deleted: boolean }>(`/members/portfolio/photos/${id}`, { method: "DELETE" }),
  photoUrl: (id: number, size: "thumb" | "full" = "thumb") => `/api/members/portfolio/photos/${id}?size=${size}`,
  scan: (file: Blob) => {
    const f = new FormData();
    f.append("file", file, "scan.jpg");
    return upload<ScanResult>("/members/portfolio/scan", f);
  },
  condition: (id: number) =>
    memberApi<{ status: "ok" | "need_photos"; report?: ConditionReport; missing?: string[]; message?: string; cached?: boolean; holding?: Holding }>(
      `/members/portfolio/holdings/${id}/condition`, { method: "POST" }),
  publicPortfolio: (username: string, sort = "value", offset = 0) =>
    memberApi<PublicPortfolio>(`/members/portfolios/u/${encodeURIComponent(username)}?sort=${sort}&offset=${offset}`),
  explore: (tab: string) => memberApi<{ tab: string; rows: ExploreRow[] | (Holding & { owner: string })[]; note?: string }>(`/members/portfolios/explore?tab=${tab}`),
  comments: (username: string) => memberApi<{ enabled: boolean; comments: Comment[] }>(`/members/portfolios/u/${encodeURIComponent(username)}/comments`),
  comment: (username: string, body: string, parent_id?: number | null) =>
    memberApi<Comment>(`/members/portfolios/u/${encodeURIComponent(username)}/comments`, { method: "POST", ...json({ body, parent_id: parent_id ?? null }) }),
  deleteComment: (id: number) => memberApi<{ deleted: boolean }>(`/members/portfolio-comments/${id}`, { method: "DELETE" }),
  vote: (holdingId: number, estimate: number, note = "") =>
    memberApi<CommunityOpinion>(`/members/portfolio/holdings/${holdingId}/condition-vote`, { method: "POST", ...json({ estimate, note }) }),
  report: (target: "comment" | "portfolio" | "holding", target_id: number, reason: string) =>
    memberApi<{ reported: boolean }>("/members/portfolio-reports", { method: "POST", ...json({ target, target_id, reason }) }),
};

/** Downscale a photo in the browser before upload (fast on mobile data; the server re-encodes anyway). */
export async function shrink(file: Blob, side = 1600, quality = 0.86): Promise<Blob> {
  try {
    const bmp = await createImageBitmap(file);
    const scale = Math.min(1, side / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * scale);
    c.height = Math.round(bmp.height * scale);
    c.getContext("2d")!.drawImage(bmp, 0, 0, c.width, c.height);
    bmp.close?.();
    return await new Promise<Blob>((res) => c.toBlob((b) => res(b ?? file), "image/jpeg", quality));
  } catch {
    return file; // the server validates and re-encodes whatever arrives
  }
}
