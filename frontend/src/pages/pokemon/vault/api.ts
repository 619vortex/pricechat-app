import { memberApi } from "../../../website/members";

/** Price Chat Vault (public storefront). Prices are integer cents in the listing's own currency, exactly as the seller states them. */

export interface VaultSourceRef { kind: "ebay_seller" | "seller_catalog"; name: string; username: string | null; url: string | null; platform: string }

export interface VaultCard {
  id: number;
  title: string;
  pokemon: string | null;
  set_name: string | null;
  year: number | null;
  language: string;
  condition: string | null; // the source's own condition ("Ungraded")
  seller_grade: string | null; // the seller's tag in the title ("NM")
  price: number | null;
  currency: string | null;
  shipping: number | null;
  availability: "available" | "sold_out" | "ended" | "unknown";
  image: string | null;
  checked_at: string | null;
  stale: boolean;
  featured: boolean;
  source: VaultSourceRef;
  url: string | null;
  can_request: boolean;
}

export interface VaultRequest {
  id: number; listing_id: number; status: "new" | "contacted" | "closed" | "declined" | "withdrawn"; contact: string; note: string;
  price: number | null; currency: string | null; created_at: string;
  listing?: { title: string; image: string | null; availability: string; url: string | null; price: number | null; currency: string | null };
}

export interface VaultDetail extends VaultCard {
  images: string[];
  condition_detail: string | null;
  seller: { username: string | null; feedback_pct: string | null; feedback_score: number | null };
  specifics: Record<string, string>;
  first_seen_at: string;
  ended_at: string | null;
  max_age_hours: number;
  my_request: VaultRequest | null;
}

export interface VaultHome {
  count: number;
  facets: { pokemon: [string, number][]; sets: [string, number][]; years: [number, number][]; price_min: number | null; price_max: number | null };
  sources: (VaultSourceRef & { checked_at: string | null })[];
  requests_enabled: boolean;
  max_age_hours: number;
}

export interface VaultPage { items: VaultCard[]; total: number; page: number; per_page: number; pages: number }

export interface VaultFilters { q?: string; pokemon?: string; set?: string; year?: string; min_price?: string; max_price?: string; sort?: string; sold?: string; page?: number }

export const SORTS: [string, string][] = [
  ["featured", "Featured"], ["newest", "Newest"], ["price_asc", "Price: low to high"], ["price_desc", "Price: high to low"],
  ["year_asc", "Year: oldest first"], ["year_desc", "Year: newest first"],
];

function qs(f: VaultFilters): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  return p.toString();
}

export const vaultApi = {
  home: () => memberApi<VaultHome>("/members/vault/home"),
  browse: (f: VaultFilters) => memberApi<VaultPage>(`/members/vault/listings?${qs(f)}`),
  listing: (id: number) => memberApi<VaultDetail>(`/members/vault/listings/${id}`),
  request: (id: number, contact: string, note: string) =>
    memberApi<VaultRequest>(`/members/vault/listings/${id}/request`, { method: "POST", body: JSON.stringify({ contact, note }) }),
  myRequests: () => memberApi<{ requests: VaultRequest[] }>("/members/vault/my/requests"),
  withdraw: (id: number) => memberApi<VaultRequest>(`/members/vault/requests/${id}/withdraw`, { method: "POST" }),
};

/** $1,234.56 (or ¥/€… in the listing's currency). "—" when the seller gave no price. */
export function money(cents: number | null | undefined, currency: string | null | undefined): string {
  if (cents === null || cents === undefined) return "—";
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: currency || "USD" }).format(cents / 100);
  } catch {
    return `${(cents / 100).toFixed(2)} ${currency ?? ""}`.trim();
  }
}

/** "12 min ago" / "3 h ago" / "2 days ago". */
export function ago(iso: string | null, now = Date.now()): string {
  if (!iso) return "never";
  const s = Math.max(0, (now - new Date(iso).getTime()) / 1000);
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400 * 2) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

export const AVAILABILITY: Record<string, string> = { available: "Available", sold_out: "Sold out", ended: "No longer listed", unknown: "Checking…" };

export const REQUEST_STATUS: Record<string, string> = {
  new: "Sent", contacted: "We're in touch", closed: "Closed", declined: "Not possible", withdrawn: "Withdrawn",
};

/** A short card name: "Lugia" or the start of the seller's title. */
export function cardName(c: Pick<VaultCard, "pokemon" | "title">): string {
  return c.pokemon ?? c.title.replace(/^\s*\[[^\]]*\]\s*/, "").split(/ Pok[eé]mon/i)[0].slice(0, 60);
}
