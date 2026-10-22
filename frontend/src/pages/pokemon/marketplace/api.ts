import { memberApi, MemberApiError } from "../../../website/members";
import type { PCardBrief } from "../portfolio/api";

/** Card Marketplace API (member area; browsing works signed out). Prices are USDT cents; estimates are USD cents. */

export type ListingStatus = "draft" | "awaiting_verification" | "awaiting_custody" | "verified" | "active" | "reserved" | "sold" | "cancelled" | "rejected";

export interface Listing {
  id: number;
  status: ListingStatus;
  sandbox: boolean;
  card: PCardBrief;
  grading_company: "PSA" | "BGS" | "CGC" | "RAW";
  grade: string; // graded: official grade; RAW: NM | LP | MP | HP | DMG from the AI condition estimate
  cert: string | null; // none for raw cards
  kind?: "graded" | "raw";
  condition?: RawCondition;
  price: number | null;
  currency: "USDT";
  listed_at: string | null;
  sold_at: string | null;
  estimate: { value: number | null; low: number | null; high: number | null; source: string | null; confidence: string | null; note: string | null; as_of: string | null };
  diff_pct: number | null;
  uncertain: boolean;
  custody: { status: string; label: string; vaulted: boolean; partner: string | null; insurance: string; insurance_note: string };
  verified: boolean;
  photos: { id: number; side: string; source: string }[];
  mine: boolean;
  seller?: { username: string; verified: boolean; level: number; since: string | null };
  // detail
  history?: { at: string; kind: string; status: string | null; price: number | null }[];
  fees?: Fees;
  comparables?: { verified_sales: { price: number; currency: string; at: string; source: string }[]; marketplace_sales: { price: number; at: string }[] };
  watching?: boolean;
  offers?: Offer[];
  seller_note?: string;
  ownership_evidence?: string;
  review_note?: string;
  flags?: string[];
}

/** A raw card's condition: the AI estimate (1-10 range, never a professional grade) and members' own grades. */
export interface RawCondition {
  label: string;
  low: number | null;
  high: number | null;
  ai?: { low: number; high: number; confidence: string; centering?: string; corners?: string; edges?: string; surface?: string; issues?: string[]; label?: string; at?: string } | null;
  community?: { count: number; average: number | null; mine: { estimate: number; note: string } | null } | null;
}

export interface SellerStatus { status: "unverified" | "pending" | "verified" | "suspended"; level: number; country: string | null; sandbox: boolean; can_list: boolean }

export interface Comment { id: number; parent_id: number | null; at: string; username: string; seller: boolean; body: string; deleted: boolean; mine: boolean }

export interface Fees {
  price: number; buyer_fee: number; buyer_fee_bps: number; buyer_total: number; seller_fee: number; seller_fee_bps: number; seller_proceeds: number;
  network_fees: Record<string, string>; currency: string;
}

export interface Network {
  id: "tron" | "solana" | "ethereum" | "sepolia"; label: string; token: string; decimals: number; confirmations: number; fee_estimate: string; wallets: string[];
  testnet?: boolean; chain_id?: number | null;
}

export interface MarketStatus { mode: "off" | "simulation" | "live"; sandbox: boolean; signed_in: boolean; networks: Network[]; fees: Fees; photos: boolean; notice: string }

export interface Stats { sales: number; volume: number; sales_30d: number; volume_30d: number; active_listings: number; currency: string }

export interface Home {
  mode: string; sandbox: boolean; featured: Listing[]; newest: Listing[]; cheapest: Listing[]; valuable: Listing[]; sold: Listing[];
  trending: string[]; stats: Stats; vaulted_inventory: number;
}

export interface Page { items: Listing[]; total: number; page: number; per_page: number; sandbox: boolean }

export interface Offer { id: number; listing_id: number; amount: number; status: string; expires_at: string; order_id: number | null; listing_price?: number }

export interface Order {
  id: number; ref: string; status: string; note: string; role: "buyer" | "seller" | "admin"; sandbox: boolean; listing_id: number;
  card: PCardBrief | null; grading_company: string | null; grade: string | null; price: number; buyer_fee: number; seller_fee: number; total: number;
  proceeds: number; currency: string; network: string; network_label: string; token: string; pay_to?: string; amount_units: string; decimals: number;
  confirmations_needed: number; expires_at: string; paid_at: string | null; completed_at: string | null; created_at: string | null;
  testnet?: boolean; chain_id?: number | null;
  payments: { tx: string; status: string; problem: string; amount_units: string; confirmations: number; simulated: boolean; explorer: string | null }[];
}

export interface Sellable {
  holding_id: number; card: PCardBrief; kind?: "graded" | "raw"; grading_company: string; grade: string | null; cert: string | null; value: number | null;
  listed: boolean; verification: string; condition?: { scanned: boolean; low: number | null; high: number | null };
}

export interface VaultItem {
  item_id: number; card: PCardBrief; grading_company: string; grade: string; cert: string; custody: string; custody_label: string; sandbox: boolean;
  vault_partner: string | null; insurance: string; insurance_note: string; can_redeem: boolean; redemption: Redemption | null;
}

export interface Redemption { id: number; item_id: number; status: string; country: string; fees: Record<string, number>; total: number; carrier: string | null; tracking: string | null; note: string; created_at: string | null; address?: Record<string, string> | null }

export interface Filters {
  q?: string; set_name?: string; year?: number; language?: string; edition?: string; number?: string; company?: string; grade?: string;
  min_price?: number; max_price?: number; min_discount?: number; vaulted?: boolean; status?: "active" | "sold"; sort?: string; page?: number;
  kind?: "graded" | "raw";
}

const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) });

function qs(f: Filters): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v !== undefined && v !== null && v !== "" && v !== false) p.set(k, String(v));
  return p.toString();
}

async function upload<T>(path: string, form: FormData): Promise<T> {
  const r = await fetch(`/api${path}`, { method: "POST", body: form, credentials: "same-origin", headers: { Accept: "application/json" } });
  if (!r.ok) {
    let message = `Something went wrong (${r.status}).`;
    try { const b = await r.json(); if (typeof b.detail === "string") message = b.detail; } catch { /* not JSON */ }
    throw new MemberApiError(r.status, message);
  }
  return (await r.json()) as T;
}

export const mkApi = {
  status: () => memberApi<MarketStatus>("/members/market/status"),
  home: () => memberApi<Home>("/members/market/home"),
  browse: (f: Filters) => memberApi<Page>(`/members/market/listings?${qs(f)}`),
  listing: (id: number) => memberApi<Listing>(`/members/market/listings/${id}`),
  photoUrl: (id: number, size: "thumb" | "full" = "thumb") => `/api/members/market/photos/${id}?size=${size}`,
  sellable: () => memberApi<{ holdings: Sellable[] }>("/members/market/sell/holdings"),
  create: (b: { holding_id: number; grading_company: string; grade?: string; cert_number?: string; price: number | null; ownership_evidence: string; note: string }) =>
    memberApi<Listing>("/members/market/listings", { method: "POST", ...json(b) }),
  seller: () => memberApi<SellerStatus>("/members/market/seller"),
  requestVerification: (country: string, about: string) =>
    memberApi<SellerStatus>("/members/market/seller/verification", { method: "POST", ...json({ country, about }) }),
  vote: (id: number, estimate: number, note = "") =>
    memberApi<NonNullable<RawCondition["community"]>>(`/members/market/listings/${id}/condition-vote`, { method: "POST", ...json({ estimate, note }) }),
  comments: (id: number) => memberApi<{ comments: Comment[] }>(`/members/market/listings/${id}/comments`),
  addComment: (id: number, body: string, parent_id: number | null = null) =>
    memberApi<{ comments: Comment[] }>(`/members/market/listings/${id}/comments`, { method: "POST", ...json({ body, parent_id }) }),
  deleteComment: (id: number) => memberApi<{ ok: boolean }>(`/members/market/comments/${id}`, { method: "DELETE" }),
  update: (id: number, b: { price?: number; ownership_evidence?: string; note?: string }) =>
    memberApi<Listing>(`/members/market/listings/${id}`, { method: "PATCH", ...json(b) }),
  photo: (id: number, side: "front" | "back" | "label", file: Blob) => {
    const f = new FormData();
    f.append("file", file, "slab.jpg");
    f.append("side", side);
    return upload<{ id: number; side: string }>(`/members/market/listings/${id}/photos`, f);
  },
  submit: (id: number) => memberApi<Listing>(`/members/market/listings/${id}/submit`, { method: "POST" }),
  publish: (id: number) => memberApi<Listing>(`/members/market/listings/${id}/publish`, { method: "POST" }),
  cancel: (id: number) => memberApi<Listing>(`/members/market/listings/${id}/cancel`, { method: "POST" }),
  myListings: () => memberApi<{ listings: Listing[] }>("/members/market/my/listings"),
  watch: (id: number, on: boolean) => memberApi<{ watching: boolean }>(`/members/market/listings/${id}/watch`, { method: "POST", ...json({ on }) }),
  watchlist: () => memberApi<{ listings: Listing[] }>("/members/market/my/watchlist"),
  report: (id: number, reason: string) => memberApi<{ reported: boolean }>(`/members/market/listings/${id}/report`, { method: "POST", ...json({ reason }) }),
  buy: (id: number, network: string, key: string) =>
    memberApi<Order>(`/members/market/listings/${id}/buy`, { method: "POST", ...json({ network }), headers: { "Idempotency-Key": key } }),
  offer: (id: number, amount: number) => memberApi<Offer>(`/members/market/listings/${id}/offers`, { method: "POST", ...json({ amount }) }),
  cancelOffer: (id: number) => memberApi<Offer>(`/members/market/offers/${id}/cancel`, { method: "POST" }),
  decideOffer: (id: number, accept: boolean, network = "tron") => memberApi<Offer>(`/members/market/offers/${id}/decide`, { method: "POST", ...json({ accept, network }) }),
  myOrders: () => memberApi<{ orders: Order[]; offers_made: Offer[]; offers_received: Offer[] }>("/members/market/my/orders"),
  order: (id: number) => memberApi<Order>(`/members/market/orders/${id}`),
  submitTx: (id: number, tx: string) => memberApi<Order>(`/members/market/orders/${id}/tx`, { method: "POST", ...json({ tx }) }),
  simulatePay: (id: number, outcome = "exact") => memberApi<Order>(`/members/market/orders/${id}/simulate-payment`, { method: "POST", ...json({ outcome }) }),
  cancelOrder: (id: number) => memberApi<Order>(`/members/market/orders/${id}/cancel`, { method: "POST" }),
  dispute: (id: number, reason: string) => memberApi<{ id: number }>(`/members/market/orders/${id}/dispute`, { method: "POST", ...json({ reason }) }),
  vault: () => memberApi<{ items: VaultItem[] }>("/members/market/my/vault"),
  redemptionQuote: (item: number, country: string) =>
    memberApi<{ fees: Record<string, number>; note: string }>(`/members/market/redemption-quote?item_id=${item}&country=${encodeURIComponent(country)}`),
  redeem: (item_id: number, address: Record<string, string>) => memberApi<Redemption>("/members/market/redemptions", { method: "POST", ...json({ item_id, address }) }),
  cancelRedemption: (id: number) => memberApi<Redemption>(`/members/market/redemptions/${id}/cancel`, { method: "POST" }),
};

/** 1,234.50 USDT */
export function usdt(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "—";
  return `${(cents / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} USDT`;
}

export const STATUS_LABEL: Record<string, string> = {
  draft: "Draft", awaiting_verification: "Awaiting verification", awaiting_custody: "Awaiting custody", verified: "Verified", active: "Active",
  reserved: "Reserved", sold: "Sold", cancelled: "Cancelled", rejected: "Rejected",
  awaiting_payment: "Awaiting payment", paid: "Paid", settling: "Settling", completed: "Completed", expired: "Expired", refund_pending: "Refund pending",
  refunded: "Refunded", failed: "Failed", pending: "Pending", accepted: "Accepted",
};
