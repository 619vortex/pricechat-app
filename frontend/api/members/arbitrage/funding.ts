/**
 * Earn › Funding Arb board, served by Vercel (GET /api/members/arbitrage/funding). READ-ONLY: public market data only.
 *
 * The board of backend/app/arbitrage/funding.py plus the Phase 2 signal statistics of backend/fundarb/signals.py, so the page
 * works without the API server. For every perp on 2+ venues: each venue's funding normalized to hourly and APR with the
 * interval the venue itself reports (never hard-coded); the gross spread (short the higher hourly rate, long the lower);
 * then, for the leaders, the spread's own 7-day history from each venue's settled funding (expanded to hourly so 1 h / 4 h /
 * 8 h venues line up): mean and standard deviation (APR), the current reading's z-score, the share of hours it paid, the
 * capital-adjusted APR (each leg posts its own margin: 2 legs x 1/leverage x (1 + buffer) per $1 of notional) and a
 * stability score (capital-adjusted mean - 2 std), which orders the board.
 * Endpoints (verified live): Hyperliquid POST /info metaAndAssetCtxs + fundingHistory · Backpack GET /api/v1/markets,
 * /markPrices, /fundingRates · Orderly GET /v1/public/info, /funding_rates, /funding_rate_history (ms) · Lighter GET
 * /api/v1/orderBooks, /fundings (rate in percent, signed by direction). A venue that fails is named with its reason.
 */

type Rate = { rate: number; hours: number; source: string; kind: "predicted" | "last_settled"; native: string };
type Board = Record<string, Rate>;
type History = Array<[number, number]>;  // [unix seconds, rate per interval]

const HOURS_PER_YEAR = 8760;
const MAX_LIGHTER = 40;
const TOP = 40;
const WITH_STATS = 20;  // leaders whose 7-day history is read
const LEVERAGE = 2, BUFFER = 0.25, STABILITY_K = 2, MIN_HOURS = 72;
const CAPITAL_PER_NOTIONAL = 2 * (1 / LEVERAGE) * (1 + BUFFER);
const CACHE_MS = 120_000;
let cache: { at: number; body: unknown } | null = null;

function canonical(raw: string): string {
  const s = raw.trim();
  return /^k[A-Z0-9]{2,}$/.test(s) ? `1000${s.slice(1)}-PERP` : `${s.toUpperCase()}-PERP`;
}

async function getJson(url: string, init?: RequestInit): Promise<any> {
  const r = await fetch(url, { ...init, headers: { Accept: "application/json", "User-Agent": "PriceChat-FundingArb/1.0", ...(init?.headers ?? {}) } });
  if (!r.ok) throw new Error(`${new URL(url).host} answered ${r.status}`);
  return r.json();
}

const hlInfo = (body: unknown) => getJson("https://api.hyperliquid.xyz/info", { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } });
const LIGHTER = "https://mainnet.zklighter.elliot.ai/api/v1";

function intervalFromSeconds(ts: number[]): number {
  const t = [...new Set(ts)].sort((a, b) => a - b);
  if (t.length < 3) throw new Error("not enough funding history to tell the interval");
  const gaps = t.slice(1).map((v, i) => v - t[i]).sort((a, b) => a - b);
  return Math.round((gaps[Math.floor(gaps.length / 2)] / 3600) * 4) / 4;
}

async function hyperliquid(): Promise<Board> {
  const [[meta, ctxs], hist] = await Promise.all([hlInfo({ type: "metaAndAssetCtxs" }), hlInfo({ type: "fundingHistory", coin: "BTC", startTime: Date.now() - 12 * 3600_000 })]);
  const hours = intervalFromSeconds((hist as any[]).map((h) => Number(h.time) / 1000));
  const out: Board = {};
  (meta.universe as any[]).forEach((u, i) => {
    const f = ctxs[i]?.funding;
    if (!u.isDelisted && f != null) out[canonical(u.name)] = { rate: Number(f), hours, source: "derived: spacing of fundingHistory timestamps (BTC)", kind: "predicted", native: u.name };
  });
  return out;
}

async function backpack(): Promise<Board> {
  const [markets, marks] = await Promise.all([getJson("https://api.backpack.exchange/api/v1/markets"), getJson("https://api.backpack.exchange/api/v1/markPrices")]);
  const byNative: Record<string, { sym: string; ms: number }> = {};
  for (const m of markets as any[]) {
    const p = String(m.symbol ?? "").split("_");
    if (m.marketType === "PERP" && p.length === 3 && p[1] === "USDC" && p[2] === "PERP" && Number(m.fundingInterval) > 0) byNative[m.symbol] = { sym: canonical(p[0]), ms: Number(m.fundingInterval) };
  }
  const out: Board = {};
  for (const r of marks as any[]) {
    const m = byNative[r.symbol];
    if (m && r.fundingRate != null) out[m.sym] = { rate: Number(r.fundingRate), hours: m.ms / 3_600_000, source: "api: /api/v1/markets fundingInterval", kind: "predicted", native: r.symbol };
  }
  return out;
}

async function orderly(): Promise<Board> {
  const [info, rates] = await Promise.all([getJson("https://api.orderly.org/v1/public/info"), getJson("https://api.orderly.org/v1/public/funding_rates")]);
  const period: Record<string, { sym: string; hours: number }> = {};
  for (const r of info?.data?.rows ?? []) {
    const p = String(r.symbol ?? "").split("_");
    if (p.length === 3 && p[0] === "PERP" && p[2] === "USDC" && Number(r.funding_period) > 0) period[r.symbol] = { sym: canonical(p[1]), hours: Number(r.funding_period) };
  }
  const out: Board = {};
  for (const r of rates?.data?.rows ?? []) {
    const m = period[r.symbol];
    if (m && r.est_funding_rate != null) out[m.sym] = { rate: Number(r.est_funding_rate), hours: m.hours, source: "api: /v1/public/info funding_period", kind: "predicted", native: r.symbol };
  }
  return out;
}

const lighterSign = (r: any) => (String(r.direction ?? "long").toLowerCase() === "long" ? 1 : -1);

async function lighter(wanted: string[]): Promise<Board> {
  const books = await getJson(`${LIGHTER}/orderBooks`);
  const ids: Record<string, number> = {};
  for (const b of books.order_books ?? []) if (b.market_type === "perp" && b.status === "active") ids[canonical(b.symbol)] = Number(b.market_id);
  const pick = wanted.filter((s) => s in ids).slice(0, MAX_LIGHTER);
  const now = Math.floor(Date.now() / 1000);
  const out: Board = {};
  await limited(pick.map((sym) => async () => {
    const d = await getJson(`${LIGHTER}/fundings?market_id=${ids[sym]}&resolution=1h&start_timestamp=${now - 24 * 3600}&end_timestamp=${now}&count_back=24`);
    const rows = ((d.fundings ?? []) as any[]).sort((a, b) => Number(a.timestamp) - Number(b.timestamp));
    if (rows.length < 3) return;
    const last = rows[rows.length - 1];
    out[sym] = { rate: (lighterSign(last) * Number(last.rate)) / 100, hours: intervalFromSeconds(rows.map((r) => Number(r.timestamp))),
                 source: "derived: spacing of /fundings timestamps", kind: "last_settled", native: String(ids[sym]) };
  }));
  return out;
}

async function limited(jobs: Array<() => Promise<void>>, n = 8): Promise<void> {
  for (let i = 0; i < jobs.length; i += n) await Promise.all(jobs.slice(i, i + n).map((j) => j().catch(() => undefined)));
}

/** The venue's settled funding over the last 7 days. */
async function history(venue: string, native: string, hours: number): Promise<History> {
  const now = Date.now();
  if (venue === "hyperliquid") return ((await hlInfo({ type: "fundingHistory", coin: native, startTime: now - 7 * 86_400_000 })) as any[]).map((r) => [Number(r.time) / 1000, Number(r.fundingRate)]);
  if (venue === "backpack") return ((await getJson(`https://api.backpack.exchange/api/v1/fundingRates?symbol=${native}&limit=${Math.ceil((7 * 24) / hours) + 2}`)) as any[])
    .map((r) => [Date.parse(`${r.intervalEndTimestamp}Z`) / 1000, Number(r.fundingRate)]);
  if (venue === "orderly") return ((await getJson(`https://api.orderly.org/v1/public/funding_rate_history?symbol=${native}&start_t=${now - 7 * 86_400_000}&end_t=${now}&page=1&size=500`))
    ?.data?.rows ?? []).map((r: any) => [Number(r.funding_rate_timestamp) / 1000, Number(r.funding_rate)]);
  const s = Math.floor(now / 1000);
  return ((await getJson(`${LIGHTER}/fundings?market_id=${native}&resolution=1h&start_timestamp=${s - 7 * 86400}&end_timestamp=${s}&count_back=170`)).fundings ?? [])
    .map((r: any) => [Number(r.timestamp), (lighterSign(r) * Number(r.rate)) / 100]);
}

/** {unix hour: hourly rate}: each settlement spread over the hours of its interval. */
function hourly(h: History, hours: number): Map<number, number> {
  const out = new Map<number, number>();
  const span = Math.max(1, Math.round(hours));
  for (const [ts, rate] of h) {
    const end = Math.floor(ts / 3600);
    for (let x = end - span + 1; x <= end; x++) out.set(x, rate / hours);
  }
  return out;
}

async function build() {
  const settle = async (name: string, p: Promise<Board>) => { try { return [name, await p, null] as const; } catch (e) { return [name, {} as Board, `${(e as Error).name}: ${(e as Error).message}`.slice(0, 200)] as const; } };
  const bulk = await Promise.all([settle("hyperliquid", hyperliquid()), settle("backpack", backpack()), settle("orderly", orderly())]);
  const counts: Record<string, number> = {};
  for (const [, rates] of bulk) for (const s of Object.keys(rates)) counts[s] = (counts[s] ?? 0) + 1;
  const wanted = Object.keys(counts).sort((a, b) => counts[b] - counts[a] || a.localeCompare(b));
  const all = [...bulk, await settle("lighter", lighter(wanted))];
  const boards = Object.fromEntries(all.map(([n, r]) => [n, r])) as Record<string, Board>;
  const symbols = new Set(all.flatMap(([, r]) => Object.keys(r)));
  const rows: any[] = [];
  for (const sym of symbols) {
    const legs = all.flatMap(([venue, rates]) => {
      const f = rates[sym];
      return f ? [{ venue, rate_raw: f.rate, interval_hours: f.hours, interval_source: f.source, kind: f.kind, hourly: f.rate / f.hours,
                    apr_pct: Math.round((f.rate / f.hours) * HOURS_PER_YEAR * 100 * 1000) / 1000 }] : [];
    });
    if (legs.length < 2) continue;
    legs.sort((a, b) => b.hourly - a.hourly);
    const hi = legs[0], lo = legs[legs.length - 1];
    const gross = (hi.hourly - lo.hourly) * HOURS_PER_YEAR * 100;
    rows.push({ symbol: sym, legs, short: hi.venue, long: lo.venue, gross_spread_apr_pct: Math.round(gross * 1000) / 1000,
                capital_apr_pct: Math.round((gross / CAPITAL_PER_NOTIONAL) * 1000) / 1000, stats: null });
  }
  rows.sort((a, b) => b.gross_spread_apr_pct - a.gross_spread_apr_pct);
  // the leaders' 7-day spread statistics
  await limited(rows.slice(0, WITH_STATS).map((row) => async () => {
    const [s, l] = [boards[row.short][row.symbol], boards[row.long][row.symbol]];
    const [hs, hl] = await Promise.all([history(row.short, s.native, s.hours), history(row.long, l.native, l.hours)]);
    const a = hourly(hs, s.hours), b = hourly(hl, l.hours);
    const series = [...a.keys()].filter((h) => b.has(h)).sort((x, y) => x - y).map((h) => a.get(h)! - b.get(h)!);
    if (series.length < MIN_HOURS) { row.stats = { hours: series.length }; return; }
    const mean = series.reduce((x, y) => x + y, 0) / series.length;
    const std = Math.sqrt(series.reduce((x, y) => x + (y - mean) ** 2, 0) / series.length);
    const now = row.legs[0].hourly - row.legs[row.legs.length - 1].hourly;
    const apr = (v: number) => Math.round(v * HOURS_PER_YEAR * 100 * 100) / 100;
    row.stats = { hours: series.length, mean_apr_pct: apr(mean), std_apr_pct: apr(std), z: std > 0 ? Math.round(((now - mean) / std) * 10) / 10 : null,
                  paid_share: Math.round((series.filter((x) => x > 0).length / series.length) * 100) / 100,
                  score_pct: Math.round(((apr(mean) - STABILITY_K * apr(std)) / CAPITAL_PER_NOTIONAL) * 100) / 100 };
  }));
  const score = (r: any) => (r.stats?.score_pct ?? -Infinity);
  rows.sort((a, b) => score(b) - score(a) || b.gross_spread_apr_pct - a.gross_spread_apr_pct);
  return {
    as_of: new Date().toISOString(),
    venues: Object.fromEntries(all.map(([name, rates, err]) => {
      const vals = Object.values(rates);
      return [name, { ok: err === null, symbols: vals.length, error: err, interval_hours: [...new Set(vals.map((v) => v.hours))].sort((a, b) => a - b),
                      interval_source: vals[0]?.source ?? null }];
    })),
    rows: rows.slice(0, TOP),
    pairs_found: rows.length,
    assumptions: { leverage: LEVERAGE, buffer: BUFFER, capital_per_notional: CAPITAL_PER_NOTIONAL, stability_k: STABILITY_K, history_days: 7 },
    note: "Gross funding spread before fees, slippage, depth and the chance the spread closes. Read-only: no trades.",
  };
}

export async function GET(): Promise<Response> {
  if (!cache || Date.now() - cache.at > CACHE_MS) cache = { at: Date.now(), body: await build() };
  return new Response(JSON.stringify(cache.body), {
    headers: { "Content-Type": "application/json", "Cache-Control": "public, s-maxage=120, stale-while-revalidate=240" },
  });
}
