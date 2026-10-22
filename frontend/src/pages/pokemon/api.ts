/** Pokémon Vintage Market Cap API (backend: app/api/routes/pokemon.py). Money is in cents; null means unknown ("—"). */
import { request } from "../../api/client";

export interface PCard {
  id: number;
  psa_spec?: string | null;
  psa_certs?: string[];
  name: string;
  pokemon_name: string;
  set_name: string;
  set_code: string | null;
  card_number: string | null;
  year: number;
  language: string;
  edition: string;
  rarity: string | null;
  variant: string;
  holo_type: string | null;
  era: string;
  is_promo: boolean;
  image_url: string | null;
  image_source: string | null;
  release_date: string | null;
}

export interface MarketRow {
  card: PCard;
  rank?: number;
  grading_company: string | null;
  grade: string | null;
  label: string | null;
  market_price: number | null;
  confidence: Confidence | null;
  change_sources?: Partial<Record<Period, ChangeSource>>;
  change_30d: number | null;
  population: number | null;
  market_cap: number | null;
  sales_7d: number;
  volume_7d: number;
  sales_30d: number;
  volume_30d: number;
  last_sale: number | null;
  last_sale_at: string | null;
}

export type Confidence = "HIGH" | "MEDIUM" | "LOW";

export interface Sale {
  id: number;
  card_id: number;
  card: PCard | null;
  grading_company: string;
  grade: string;
  label: string;
  price: number;
  currency: string;
  price_usd: number;
  sale_date: string;
  source: string;
  auction_house: string | null;
  sale_type: string | null;
  url: string | null;
  verified: boolean;
  verified_by: "manual" | "source" | null;
  excluded: boolean;
  cert_number: string | null;
  source_listing_id?: string | null;
  fx_rate?: number | null;
  fx_source?: string | null;
  title?: string | null;
  match_confidence?: number | null;
}

export interface Overview {
  hero: { total_market_cap: number | null; change_24h: number | null; change_7d: number | null; cards_tracked: number; sales_tracked: number; volume_24h: number };
  top: MarketRow[];
  sections: { gainers: MarketRow[]; losers: MarketRow[]; most_traded: MarketRow[]; most_valuable: MarketRow[]; new_sales: Sale[]; low_pop: MarketRow[] };
  filters: { years: number[]; sets: string[]; pokemon: string[]; languages: string[]; editions: string[] };
  has_market_data: boolean;
}

export interface Stats {
  market_price: number | null;
  confidence: Confidence | null;
  confidence_reasons: string[];
  last_sale: number | null;
  last_sale_at: string | null;
  sales_count: number;
  sales_7d: number;
  sales_30d: number;
  sales_90d: number;
  volume_7d: number;
  volume_30d: number;
  median_30d: number | null;
  median_90d: number | null;
  average_30d: number | null;
  high_30d: number | null;
  low_30d: number | null;
  window_days: number;
}

export interface Valuation {
  price_source?: PriceSource;
  provider?: ProviderPrice | null;
  reference?: ReferencePrice | null;
  grading_company: string;
  grade: string;
  label: string;
  graded: boolean;
  market_price: number | null;
  confidence: Confidence | null;
  change_30d: number | null;
  change_90d: number | null;
  change_sources?: Partial<Record<Period, ChangeSource>>;
  change_reasons?: Record<string, string>;
  stats: Stats;
  population: number | null;
  population_source: string | null;
  population_as_of: string | null;
  market_cap: number | null;
  sales_7d: number;
  volume_7d: number;
  sales_30d: number;
  volume_30d: number;
}

export interface Listing {
  id: number;
  provider: string;
  kind: "active" | "sold";
  listing_id: string;
  title: string;
  price: number;
  currency: string;
  condition: string | null;
  seller: string | null;
  listing_type: string | null;
  url: string | null;
  image_url: string | null;
  observed_at: string;
  sold_at: string | null;
  card_id: number | null;
  grade: string | null;
  match_status: string;
  match_confidence: number | null;
  match_reasons: string[];
}

export interface CardDetail {
  card: PCard;
  headline: Valuation | null;
  active_listings: Listing[];
  reference_prices: { provider: string; provider_grade: string; grade: string | null; price: number; currency: string; as_of: string }[];
  valuations: Valuation[];
  other_grades: Valuation[];
  population: { grading_company: string; grade: string; count: number; source: string; as_of: string }[];
  population_totals: Record<string, number>;
  recent_sales: Sale[];
  total_market_cap: number | null;
  watched: boolean;
}

export interface History {
  grade: string;
  range: string;
  line: { t: string; v: number }[];
  sales: { t: string; v: number; source: string; excluded: boolean }[];
}

export interface Source {
  key: string;
  name: string;
  connected: boolean;
  reason: string;
  serves: string[];
  access: string;
  licence: string;
  credentials: string[];
  missing: string[];
  credentials_present: boolean | null;
  rate_limit: string | null;
  notes: string[];
  last_request_at: string | null;
  requests_today: number;
  last_sync_at: string | null;
  last_success_at: string | null;
  records_imported: number;
  last_error: string | null;
}

export interface ProvenanceRow {
  sale_id: number;
  price: number;
  sale_date: string;
  age_days: number;
  source: string;
  weight?: number;
  reason?: string;
  url?: string | null;
  source_listing_id?: string | null;
  verified_by?: string | null;
}

export interface Provenance {
  grade: string;
  market_price: number | null;
  algorithm: string;
  window_days: number;
  included: ProvenanceRow[];
  excluded: ProvenanceRow[];
  confidence: { level: Confidence | null; reasons: string[] };
  latest_sale: number | null;
  latest_sale_at: string | null;
  population: { count: number; source: string; as_of: string } | null;
  market_cap: number | null;
  computed_at: string;
}

export interface PreviewRow {
  row: number;
  input: Record<string, string>;
  status: "ready" | "review" | "duplicate" | "error";
  errors: string[];
  warnings: string[];
  card_id: number | null;
  card: string | null;
  confidence: number | null;
  candidates: { card_id: number; label: string; confidence: number }[];
  reasons: string[];
  grade?: string;
  price?: number;
  currency?: string;
  price_usd?: number;
  fx_rate?: number;
  fx_source?: string;
  sale_date?: string;
  source?: string;
}

export type Filters = Partial<Record<"era" | "language" | "edition" | "year" | "set" | "pokemon" | "grade" | "company", string>> & { promo?: boolean };

const qs = (p: Record<string, string | number | boolean | undefined | null>) => {
  const e = Object.entries(p).filter(([, v]) => v !== undefined && v !== null && v !== "" && v !== false);
  return e.length ? `?${new URLSearchParams(e.map(([k, v]) => [k, String(v)]))}` : "";
};

interface TaskStatus<T> { name: string; state: "idle" | "running" | "done" | "error"; result: T | null; error: string | null }

/** A long admin action (sync, import): started as a server-side task and polled, because the hosting proxies cut off
 * requests after a few minutes. Resolves with the action's result, rejects with its error. */
async function runTask<T>(path: string, init: RequestInit, everyMs = 3000, maxMs = 30 * 60_000): Promise<T> {
  let st = await request<TaskStatus<T>>(`${path}${path.includes("?") ? "&" : "?"}background=true`, init);
  const until = Date.now() + maxMs;
  while (st.state === "running" && Date.now() < until) {
    await new Promise((r) => setTimeout(r, everyMs));
    st = await request<TaskStatus<T>>(`/pokemon/tasks/${encodeURIComponent(st.name)}`);
  }
  if (st.state === "done" && st.result !== null) return st.result;
  throw new Error(st.state === "running" ? "Still running on the server: check back in a few minutes." : st.error || "The task failed.");
}

export const pokemonApi = {
  overview: (f: Filters) => request<Overview>(`/pokemon/overview${qs(f)}`),
  search: (q: string) => request<{ query: string; grade: string | null; results: { card: PCard; grade: string | null; market_price: number | null; market_cap: number | null; change_7d: number | null }[] }>(
    `/pokemon/cards${qs({ q, limit: 12 })}`),
  card: (id: number) => request<CardDetail>(`/pokemon/cards/${id}`),
  history: (id: number, grade: string, range: string) => request<History>(`/pokemon/cards/${id}/history${qs({ grade, range })}`),
  watchlist: () => request<{ cards: { card: PCard; headline: MarketRow | null; latest_sale: Sale | null; total_market_cap: number | null }[] }>("/pokemon/watchlist"),
  star: (id: number, on: boolean) => request<{ watched: boolean }>(`/pokemon/watchlist/${id}`, { method: on ? "PUT" : "DELETE" }),
  sources: () => request<{ sources: Source[] }>("/pokemon/sources"),
  fetchImages: () => request<{ updated: number; not_found: number[]; skipped: number; error: string | null }>("/pokemon/catalog/fetch-images", { method: "POST" }),
  importSales: (rows: unknown[]) => request<{ imported: number; duplicates: number; errors: { row: number; error: string }[] }>(
    "/pokemon/import/sales", { method: "POST", body: JSON.stringify({ rows }) }),
  importPopulation: (rows: unknown[]) => request<{ imported: number; errors: { row: number; error: string }[] }>(
    "/pokemon/import/population", { method: "POST", body: JSON.stringify({ rows }) }),
  csvPreview: (csv: string) => request<{ rows: PreviewRow[]; counts: Record<"ready" | "review" | "duplicate" | "error", number>; total: number }>(
    "/pokemon/import/csv/preview", { method: "POST", body: JSON.stringify({ csv }) }),
  csvCommit: (csv: string, accept: { row: number; card_id?: number | null }[], verified: boolean) =>
    request<{ imported: number; skipped: number; failed: number; results: { row: number; outcome: string; why?: string }[] }>(
      "/pokemon/import/csv/commit", { method: "POST", body: JSON.stringify({ csv, accept, verified }) }),
  provenance: (id: number, grade: string) => request<Provenance>(`/pokemon/cards/${id}/provenance${qs({ grade })}`),
  review: () => request<{ items: Listing[] }>("/pokemon/review"),
  resolveReview: (id: number, body: { card_id?: number; grade?: string; reject?: boolean }) =>
    request<Listing>(`/pokemon/review/${id}`, { method: "POST", body: JSON.stringify(body) }),
  testSource: (key: string) => request<{ ok: boolean; message: string }>(`/pokemon/sources/${key}/test`, { method: "POST" }),
  syncSource: (key: "ebay" | "pricecharting" | "psa") => runTask<Record<string, unknown>>(`/pokemon/sources/${key}/sync`, { method: "POST" }),
  psaLink: (cert_number: string, card_id?: number, confirm = false) =>
    request<PsaLinkResult>("/pokemon/psa/link", { method: "POST", body: JSON.stringify({ cert_number, card_id, confirm }) }),
  importTcgdex: (limit = 100) => request<{ tcgdex_cards: number; created: number; linked_existing: number; japanese_linked: number; images_added: number;
    error: string | null }>(`/pokemon/catalog/import-tcgdex?limit=${limit}`, { method: "POST" }, 600_000),
  scrydexSync: (card_ids: number[] | undefined, history: boolean) =>
    runTask<ScrydexLog>("/pokemon/sources/scrydex/sync", { method: "POST", body: JSON.stringify({ card_ids, history }) }),
  syncLogs: (provider: string) => request<{ logs: ScrydexLog[] }>(`/pokemon/sync-logs?provider=${provider}`),
  psaRefresh: (id: number) => request<PsaPopulation>(`/pokemon/cards/${id}/psa/refresh`, { method: "POST" }),
  psaCert: (cert: string) => request<{ cert: Record<string, string | number> }>(`/pokemon/psa/cert/${encodeURIComponent(cert)}`),
};

/** Card-wide PSA population (one PSA spec = one exact card), never a single slab's figures. */
export interface PsaPopulation {
  spec_id: string;
  description: string | null;
  grades_stored: number;
  total: number | null;
  qualified: Record<string, number>;
  as_of: string;
}

export interface PsaLinkResult {
  cert: Record<string, string | number>;  // certificate-specific: this one slab
  match: { status: "matched" | "review" | "not_in_catalog" | "mismatch"; card_id: number | null; reasons: string[]; candidates: number[] };
  linked: boolean;
  card_id?: number;
  population: PsaPopulation | null;
  population_error: string | null;
}

// --- Market V2 (CoinGecko-style) ------------------------------------------------------------------------------------

/** One ranked row at one grade. `sparkline` = [[unix seconds, cents], ...] of Price Chat's estimate (null: not enough history). */
/** A market price published by a provider (Scrydex): an estimate, never a verified sale. */
export interface ProviderPrice {
  provider: string;
  name: string;
  market: number | null;
  low: number | null;
  mid: number | null;
  high: number | null;
  currency: string;
  as_of: string;
  fetched_at: string;
  variant: string;
  provider_card_id: string;
  trends: Record<"24h" | "7d" | "30d" | "90d", number | null>;
}

/** A second source's value for the same exact grade (PriceCharting): reference only, never a sale. */
export interface ReferencePrice {
  provider: string;
  name: string;
  price: number;
  as_of: string;
  product_id: string | null;
  diff_pct: number | null; // shown price vs this reference, in %
  disagree: boolean; // the two differ by more than 25%
}

export type PriceSource = "sales" | "scrydex" | "pricecharting" | null;

export interface ScrydexLog {
  id: number;
  status: "ok" | "partial" | "error" | "running";
  started_at: string;
  finished_at: string | null;
  requests: number;
  cache_hits: number;
  error: string | null;
  summary: Record<string, number | null | undefined> & { usage_before?: unknown };
  entries: { card_id: number; card: string; scrydex_id?: string; status: string; variant?: string; match?: string; reason?: string; history?: string;
             history_points?: number; prices?: Record<string, { market: number | null } | number>; population?: Record<string, number>;
             product?: string; sales_volume?: string | number | null; candidates?: string[] }[];
}

/** A card's PSA 10 data quality (Top 100 eligibility): score 0-100, confidence, failed checks in plain words. */
export interface QualityBrief {
  eligible: boolean;
  score: number;
  confidence: "high" | "medium" | "low";
  history_points: number;
  reasons: string[];
}

export interface QualityCheck { code: string; label: string; ok: boolean | null; detail: string }
export interface QualityFull extends QualityBrief { checks: QualityCheck[]; failed: string[] }

export interface Top100Summary {
  scope: string;
  candidates: number;
  eligible: number;
  shown: number;
  excluded: number;
  limit: number;
  min_history: number;
  max_age_days: number;
  excluded_by_reason: Record<string, number>;
  labels: Record<string, string>;
  total_market_cap: number | null;
}

export interface CardQuality {
  card_id: number;
  grade: string;
  era: string;
  quality: QualityFull | null;
  top100_rank: number | null;
  in_scope: boolean;
  min_history: number;
  eligible_count: number;
  market_cap: { price: number; price_source: string; price_as_of: string | null; population: number | null; population_source: string | null;
    population_as_of: string | null; market_cap: number | null } | null;
}

export interface EbayListing {
  id: number;
  listing_id: string;
  title: string;
  price: number;
  currency: string;
  condition: string | null;
  seller: string | null;
  listing_type: string | null;
  url: string | null;
  image_url: string | null;
  observed_at: string;
  grade: string | null;
}

export interface CardEbay {
  connected: boolean;
  query: string;
  grade: string;
  listings: EbayListing[];
  fetched: boolean;
  message: string | null;
  sold: { access: boolean; message?: string | null; seen?: number; verified_sales?: number } | null;
  counts: Record<string, number> | null;
}

export interface Top100QualityResp {
  summary: Top100Summary;
  missing: { price: number; population: number; history: number };
  total: number;
  offset: number;
  limit: number;
  rows: (RankRow & { quality: QualityFull })[];
  events: { at: string; kind: "entered" | "left"; rank: number | null; reasons: string[]; card: PCard }[];
}

export interface RankRow {
  card: PCard;
  quality?: QualityBrief | null;
  price_source?: PriceSource;
  provider?: ProviderPrice | null;
  reference?: ReferencePrice | null;
  rank?: number;
  grading_company: string;
  grade: string;
  label: string;
  market_price: number | null;
  confidence: Confidence | null;
  confidence_reasons: string[];
  change_sources?: Partial<Record<Period, ChangeSource>>;
  change_30d: number | null;
  change_90d: number | null;
  change_reasons: Record<string, string>;
  sales_7d: number;
  volume_7d: number;
  sales_30d: number;
  volume_30d: number;
  last_sale: number | null;
  last_sale_at: string | null;
  population: number | null;
  population_source: string | null;
  population_as_of: string | null;
  market_cap: number | null;
  watchers: number;
  trend?: { score: number; x_mentions: number; move_30d: number | null; why: string };
  /** the last 90 days of GENUINE observations of the shown price's source (null: fewer than two; sparkline_note says why) */
  sparkline: { points: [number, number][]; source: "sales" | "scrydex"; from: string; to: string; span_days: number; observations: number } | null;
  sparkline_note?: string | null;
}

export type SortKey = "rank" | "name" | "price" | "change_30d" | "change_90d" | "volume" | "market_cap" | "population" | "sales";
/** Vintage cards are illiquid collectibles: movements are read over months, not hours. */
export type Period = "30d" | "90d";
/** where a movement came from: verified sales (sales-priced cards), Scrydex's published trend, or Scrydex's recorded history */
export type ChangeSource = "sales" | "scrydex" | "scrydex_history";

export interface MarketQuery {
  grade?: string;
  q?: string;
  sort?: SortKey;
  dir?: "asc" | "desc";
  limit?: number;
  offset?: number;
  set?: string;
  year?: string;
  edition?: string;
  language?: string;
  era?: "vintage" | "modern" | "all";
  ids?: string;
  view?: "all" | "top100";
}

export interface MarketResp {
  grade: string;
  view?: "all" | "top100";
  top100?: Top100Summary;
  total: number;
  offset: number;
  limit: number;
  rows: RankRow[];
  as_of: string;
  hero: {
    total_market_cap: number | null;
    /** price-driven: today's caps vs each card's cap at its price at the period start (population held), over the cards with a move */
    market_cap_change_30d: number | null;
    market_cap_change_90d: number | null;
    market_cap_change_30d_cards: number;
    market_cap_change_90d_cards: number;
    cards_with_cap: number;
    cards_priced: number;
    cards_tracked: number;
    volume_30d: number;
    grade_sales_30d: number;
    /** the earliest verified sale on record */
    sales_records_from: string | null;
    all_sales_30d: number;
    population_total: number | null;
    cards_with_population: number;
    lowest_population: RankRow | null;
  };
  sections: { trending: RankRow[]; gainers: RankRow[]; losers: RankRow[]; recently_sold: Sale[] };
  options: { sets: string[]; years: number[]; editions: string[]; languages: string[]; grades: string[] };
  providers: { sales_connected: string[]; population_connected: string[]; sales_records: number };
  has_market_data: boolean;
}

export interface ChartPoint { t: string; v: number; kind?: "carried" | "sale_day" | "current" | "history" | "provider"; confidence?: Confidence | null; population?: number }
export interface ChartSale { t: string; v: number; id: number; source: string; verified: boolean; excluded: boolean; url: string | null; sale_type: string | null }
export interface PeriodChange { value: number | null; reason: string | null; base: number | null; base_day: string | null; source?: ChangeSource | null }
export type ChartRange = "30d" | "90d" | "all";

export interface ChartResp {
  card_id: number;
  as_of: string;
  grade: string;
  graded: boolean;
  range: ChartRange;
  rank: number | null;
  provider_line?: ChartPoint[];
  summary: {
    market_price: number | null;
    price_source?: PriceSource;
    provider?: ProviderPrice | null;
    reference?: ReferencePrice | null;
    confidence: Confidence | null;
    confidence_reasons: string[];
    sales_count: number;
    window_days: number;
    changes: Record<Period, PeriodChange>;
    population: number | null;
    population_source: string | null;
    population_as_of: string | null;
    market_cap: number | null;
    last_verified_sale: Sale | null;
    sales_30d: number;
    volume_30d: number;
    median_30d: number | null;
    highest_sale: { price: number; at: string; source: string } | null;
    lowest_sale: { price: number; at: string; source: string } | null;
    highest_estimate: { price: number; day: string } | null;
    lowest_estimate: { price: number; day: string } | null;
  };
  line: ChartPoint[];
  cap_line: ChartPoint[];
  sales: ChartSale[];
  volume: { t: string; count: number; usd: number }[];
  population_history: { t: string; count: number; source: string }[];
}

export interface CompareResp {
  range: string;
  series: { card: PCard; grade: string; market_price: number | null; confidence: Confidence | null; source?: "sales" | "scrydex" | null; line: ChartPoint[];
            sales: { t: string; v: number }[] }[];
}

/** Market cap of one release year at one grade (all cards released that year, or one language). */
export interface YearRow {
  year: number;
  rank?: number;
  era: "vintage" | "modern";
  cards: number;
  cards_priced: number;
  cards_with_cap: number;
  market_cap: number | null;
  market_cap_change_30d: number | null;
  market_cap_change_90d: number | null;
  share: number | null;
  population: number | null;
  sets: number;
  languages: string[];
  top: RankRow[];
}

export interface SetRow {
  rank: number | null;
  set_name: string;
  set_code: string | null;
  language: string;
  year: number;
  cards: number;
  cards_priced: number;
  market_cap: number | null;
  market_cap_change_30d: number | null;
  volume_30d: number;
  sales_30d: number;
  population: number | null;
  cover: PCard;
  top: PCard[];
}

export interface GradeRow {
  grade: string;
  company: string;
  graded: boolean;
  cards_priced: number;
  market_cap: number | null;
  sales_30d: number;
  volume_30d: number;
  population: number | null;
  cards_with_population: number;
  top_card: RankRow | null;
}

export interface CardMeta {
  source: string | null;
  language?: string;
  reason?: string;
  url?: string | null;
  meta: {
    name?: string; illustrator?: string; rarity?: string; hp?: number; types?: string[]; stage?: string; evolveFrom?: string;
    description?: string; dexId?: number[]; category?: string; trainerType?: string; effect?: string; retreat?: number;
    attacks?: { name: string; cost?: string[]; damage?: number | string; effect?: string }[];
    abilities?: { name: string; type?: string; effect?: string }[];
    weaknesses?: { type: string; value?: string }[];
    resistances?: { type: string; value?: string }[];
    set?: { id: string; name: string };
  } | null;
}

export interface QualityRow {
  card: PCard;
  scrydex: number | null;
  scrydex_range: [number | null, number | null] | null;
  pricecharting: number | null;
  sales_price: number | null;
  sales_30d: number;
  gap_pct: number | null;
  age_days: { scrydex: number | null; pricecharting: number | null };
  population: number | null;
  flags: string[];
}

export interface QualityResp {
  grade: string;
  as_of: string;
  rows: QualityRow[];
  total?: number;
  offset?: number;
  limit?: number;
  summary: { cards: number; both_sources: number; disagree: number; within_10: number; within_25: number; median_ratio: number | null; no_price: number;
             single_source: number; stale: number; no_population: number; stale_after_days: number };
}

/** USD -> JPY display rate (ECB reference rate). Prices stay in USD; yen is a conversion shown next to them. */
export interface FxRate { rate: number; rate_date: string; fetched_at: string; source: string; stale: boolean }

export const marketApi = {
  fx: () => request<{ usd_jpy: FxRate | null }>("/pokemon/fx"),
  quality: (grade: string, flag?: string, offset = 0) => request<QualityResp>(`/pokemon/quality${qs({ grade, flag, offset: offset || undefined })}`),
  top100Quality: (p: { language?: string; reason?: string; offset?: number; limit?: number }) =>
    request<Top100QualityResp>(`/pokemon/top100/quality${qs({ ...p })}`),
  cardQuality: (id: number) => request<CardQuality>(`/pokemon/cards/${id}/quality`),
  cardEbay: (id: number, grade: string, refresh = false) =>
    request<CardEbay>(`/pokemon/cards/${id}/ebay${qs({ grade, refresh: refresh ? "true" : undefined })}`),
  market: (p: MarketQuery) => request<MarketResp>(`/pokemon/market${qs({ ...p })}`),
  chart: (id: number, grade: string, range: ChartRange) => request<ChartResp>(`/pokemon/cards/${id}/chart${qs({ grade, range })}`),
  compare: (series: string[], range: ChartRange) =>
    request<CompareResp>(`/pokemon/compare?${series.map((s) => `series=${encodeURIComponent(s)}`).join("&")}&range=${range}`),
  movers: (direction: "gainers" | "losers", grade: string, period: "30d" | "90d") =>
    request<{ rows: RankRow[]; cards_with_change: number; period: string }>(`/pokemon/movers${qs({ direction, grade, period })}`),
  trending: (grade: string) => request<{ rows: RankRow[]; basis: string }>(`/pokemon/trending${qs({ grade })}`),
  years: (grade: string, language?: string) =>
    request<{ grade: string; language: string | null; years: YearRow[]; total_market_cap: number | null }>(`/pokemon/years${qs({ grade, language })}`),
  sets: (grade: string) => request<{ grade: string; sets: SetRow[] }>(`/pokemon/sets${qs({ grade })}`),
  grades: () => request<{ grades: GradeRow[]; cards_tracked: number }>("/pokemon/grades"),
  recent: (limit: number, offset: number, grade?: string) =>
    request<{ sales: Sale[]; total: number; limit: number; offset: number }>(`/pokemon/sales/recent${qs({ limit, offset, grade })}`),
  meta: (id: number) => request<CardMeta>(`/pokemon/cards/${id}/meta`),
};
