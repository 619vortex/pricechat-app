/** Market formatting. Unknown is always "—" (never 0, never a guess). Money arrives in cents (USD). */
import type { PCard } from "./api";

export const DASH = "—";

export function usd(cents: number | null | undefined, compact = false): string {
  if (cents === null || cents === undefined) return DASH;
  const v = cents / 100;
  if (compact && Math.abs(v) >= 1_000_000)
    return `$${(v / 1_000_000).toLocaleString("en-US", { maximumFractionDigits: v >= 100_000_000 ? 0 : 2 })}M`;
  if (compact && Math.abs(v) >= 10_000) return `$${(v / 1_000).toLocaleString("en-US", { maximumFractionDigits: 1 })}K`;
  return v.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: v >= 1000 ? 0 : 2 });
}

export function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return DASH;
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
}

export function moveClass(v: number | null | undefined): string {
  return v === null || v === undefined ? "pm-flat" : v > 0 ? "pm-up" : v < 0 ? "pm-down" : "pm-flat";
}

export function num(v: number | null | undefined): string {
  return v === null || v === undefined ? DASH : v.toLocaleString("en-US");
}

export function day(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : DASH;
}

/** "1999 Base Set" */
export function yearSet(c: PCard): string {
  return `${c.year} ${c.set_name}`;
}

/** "4/102 — 1st Edition Holo" (only the parts the card has) */
export function cardLine(c: PCard): string {
  const parts = [c.edition !== "—" ? c.edition : null, c.variant || null, c.holo_type && c.holo_type !== c.variant ? c.holo_type : null].filter(Boolean).join(" ");
  return [c.card_number, parts].filter(Boolean).join(" — ");
}

export function sourceLabel(s: string): string {
  return ({ ebay: "eBay", goldin: "Goldin", heritage: "Heritage", pwcc: "PWCC", fanatics: "Fanatics Collect", pricecharting: "PriceCharting", scrydex: "Scrydex", psa: "PSA", gemrate: "GemRate", manual: "Manual" } as Record<string, string>)[s] ?? s;
}

/** A USD amount (cents) shown in yen at a display rate: "¥4,830,000", or compact "¥48.3M". */
export function yen(cents: number | null | undefined, rate: number | null | undefined, compact = false): string {
  if (cents === null || cents === undefined || !rate) return DASH;
  const v = Math.round((cents / 100) * rate);
  if (compact && Math.abs(v) >= 1_000_000) return `¥${(v / 1_000_000).toLocaleString("en-US", { maximumFractionDigits: 1 })}M`;
  return `¥${v.toLocaleString("en-US")}`;
}
