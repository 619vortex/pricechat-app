import { memberApi } from "../../../website/members";

/** Pokémon Paper Exchange (PAPER TRADING ONLY). Money in paper USDT cents, quantities in whole contracts. */

export interface ExMarket {
  symbol: string; title: string; grade: string; status: string; tradable: boolean; auto?: boolean;
  card: { id: number; name: string; set: string; number: string; edition: string; year: number; image: string | null } | null;
  reference: { contract: number | null; card: number | null; status: "ok" | "stale" | "insufficient"; confidence: string | null; at: string | null };
  last: number | null; bid: number | null; ask: number | null; premium_pct: number | null;
  rules: { multiplier: string; tick: number; min_qty: number; max_qty: number; taker_fee_bps: number; maker_fee_bps: number; limit_band_pct: number; market_slippage_pct: number };
  stats_24h: { trades: number; volume: number; turnover: number; high: number | null; low: number | null; change_pct: number | null };
}
export interface Level { price: number; qty: number; orders: number; simulated_qty: number }
export interface Trade { id: number; price: number; qty: number; side: "buy" | "sell"; at: string; simulated: boolean }
export interface Candle { t: number; o: number; h: number; l: number; c: number; v: number }
export interface Order { id: number; market: string; side: "buy" | "sell"; type: "limit" | "market"; price: number; qty: number; filled: number; status: string; reason: string | null; client_id: string; created_at: string }
export interface Position { market: string; title: string; qty: number; held: number; avg_cost: number | null; cost: number; mark: number; last: number | null; value: number; unrealized: number; realized: number }
export interface Me {
  account: { id: number; name: string }; usdt: { available: number; held: number }; equity: number; start: number; pnl: number; realized: number; unrealized: number;
  positions: Position[]; open_orders: Order[]; orders: Order[]; perps?: MePerps;
  history: { id: number; market: string; side: "buy" | "sell"; price: number; qty: number; fee: number; realized: number | null; maker: boolean; at: string }[];
  achievements: { key: string; name: string; how: string; earned: boolean }[];
}
export interface PerpMarket {
  symbol: string; spot: string; title: string; grade: string; tradable: boolean; auto?: boolean; card: ExMarket["card"]; reference: ExMarket["reference"];
  index: number | null; mark: number | null; last: number | null; bid: number | null; ask: number | null; premium_pct: number | null;
  funding: { last_ppm: number; predicted_ppm: number; next_at: string; every_minutes: number; cap_ppm: number };
  open_interest: number; max_leverage: number; mmr_bps: number; taker_fee_bps: number; maker_fee_bps: number;
  stats_24h: { trades: number; volume: number; high: number | null; low: number | null; change_pct: number | null };
}
export interface PerpOrder { id: number; market: string; side: "buy" | "sell"; type: "limit" | "market"; price: number; qty: number; filled: number; leverage: number; reduce_only: boolean; status: string; reason: string | null; created_at: string }
export interface PerpPosition {
  market: string; side: "long" | "short" | "flat"; size: number; entry: number; mark: number; liq_price: number | null; margin: number; leverage: number;
  unrealized: number; roe_pct: number | null; realized: number; funding: number; liquidations: number; value: number;
}
export interface MePerps {
  positions: PerpPosition[]; open_orders: PerpOrder[]; orders: PerpOrder[];
  history: { id: number; market: string; side: "buy" | "sell"; price: number; qty: number; kind: string; fee: number; realized: number | null; at: string }[];
  equity_in_perps: number;
}
export interface Board { board: string; rows: Record<string, unknown>[]; rules: string[] }

export const exApi = {
  markets: () => memberApi<{ paper_trading: boolean; markets: ExMarket[] }>("/members/exchange/markets"),
  market: (s: string) => memberApi<{ market: ExMarket; book: { bids: Level[]; asks: Level[] }; trades: Trade[] }>(`/members/exchange/markets/${s}`),
  candles: (s: string, interval: string) => memberApi<{ candles: Candle[]; reference: { t: number; v: number }[] }>(`/members/exchange/markets/${s}/candles?interval=${interval}`),
  me: () => memberApi<Me>("/members/exchange/me"),
  order: (body: { symbol: string; side: string; type: string; qty: number; price?: number; client_id: string }) =>
    memberApi<{ order: Order; fills: { price: number; qty: number }[]; duplicate: boolean }>("/members/exchange/orders", { method: "POST", body: JSON.stringify(body) }),
  cancel: (id: number) => memberApi<{ order: Order }>(`/members/exchange/orders/${id}/cancel`, { method: "POST" }),
  perps: () => memberApi<{ paper_trading: boolean; markets: PerpMarket[] }>("/members/exchange/perps"),
  perp: (s: string) => memberApi<{ market: PerpMarket; book: { bids: Level[]; asks: Level[] }; trades: (Trade & { kind: string })[] }>(`/members/exchange/perps/${s}`),
  perpCandles: (s: string, interval: string) => memberApi<{ candles: Candle[]; reference: { t: number; v: number }[] }>(`/members/exchange/perps/${s}/candles?interval=${interval}`),
  perpOrder: (body: { symbol: string; side: string; type: string; qty: number; price?: number; leverage: number; reduce_only: boolean; client_id: string }) =>
    memberApi<{ order: PerpOrder; fills: { price: number; qty: number }[]; duplicate: boolean }>("/members/exchange/perps/orders", { method: "POST", body: JSON.stringify(body) }),
  perpCancel: (id: number) => memberApi<{ order: PerpOrder }>(`/members/exchange/perps/orders/${id}/cancel`, { method: "POST" }),
  perpClose: (s: string) => memberApi<{ order: PerpOrder; fills: { price: number; qty: number }[] }>(`/members/exchange/perps/${s}/close`, { method: "POST", body: JSON.stringify({ client_id: newId() }) }),
  board: (b: string) => memberApi<Board>(`/members/exchange/leaderboard?board=${b}`),
};

/** paper USDT, from cents */
export const px = (c: number | null | undefined, d = 2) => (c == null ? "—" : (c / 100).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
export const usd = (c: number | null | undefined) => (c == null ? "—" : `$${(c / 100).toLocaleString("en-US", { maximumFractionDigits: 0 })}`);
export const signed = (c: number) => `${c >= 0 ? "+" : "−"}${px(Math.abs(c))}`;
export const newId = () => `w${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
