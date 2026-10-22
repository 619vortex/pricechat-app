import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { marketApi, type RankRow, type SortKey } from "./api";
import { DASH, num, usd, yen } from "./format";
import { play } from "./marketSound";
import { CardCell, Change, GradeSelect, Sparkline, Star, Confidence } from "./mx";

type Col = { key: SortKey | null; label: string; num?: boolean; title?: string; className?: string; short?: string };

const COLS: Col[] = [
  { key: null, label: "", className: "mx-c-star" },
  { key: "rank", label: "#", className: "mx-c-rank" },
  { key: "name", label: "Card" },
  { key: null, label: "Grade" },
  { key: "price", label: "Price", num: true, title: "Estimated market price: recency-weighted median of verified sales at this grade" },
  { key: "change_30d", label: "30D", num: true, title: "Price movement over 30 days (source in each value's tooltip)" },
  { key: "change_90d", label: "90D", num: true, title: "Price movement over 90 days" },
  { key: "volume", label: "Volume (30D)", short: "Vol 30D", num: true, title: "Verified completed sales at this grade in the last 30 days (USD). Never asking prices or estimates.", className: "mx-c-vol" },
  { key: "market_cap", label: "Market Cap", short: "Mkt Cap", num: true, title: "Estimated: market price × known population of this grade" },
  { key: "population", label: "Population", short: "Pop.", num: true, title: "Population at this grade", className: "mx-c-pop" },
  { key: null, label: "Last 90 Days", short: "90 Days", num: true, className: "mx-c-spark", title: "Genuine observations of the shown price's source in the last 90 days: nothing carried forward or filled in" },
];

/** The CoinGecko rankings table: sticky header, sortable columns (unknowns always last), per-row grade dropdown. */
export function RankTable({ rows, sort, dir, onSort, grade, caption, reason, jpy }: {
  rows: RankRow[]; sort?: SortKey; dir?: "asc" | "desc"; onSort?: (k: SortKey) => void; grade: string; caption?: string;
  reason?: (r: RankRow) => string | undefined;
  /** USD -> JPY display rate: when set, each price also shows in yen */
  jpy?: number | null;
}) {
  const nav = useNavigate();
  const [override, setOverride] = useState<Record<number, RankRow>>({});
  const [flash, setFlash] = useState<Record<number, "up" | "down">>({});
  async function regrade(r: RankRow, g: string) {
    if (g === grade) {
      setOverride(({ [r.card.id]: _, ...rest }) => rest);
      return;
    }
    const res = await marketApi.market({ ids: String(r.card.id), grade: g, era: "all" });
    const next = res.rows[0];
    if (next) {
      const before = (override[r.card.id] ?? r).market_price;
      setOverride((o) => ({ ...o, [r.card.id]: next }));
      if (before !== null && next.market_price !== null && before !== next.market_price) {
        const dir = next.market_price > before ? "up" : "down";
        setFlash((f) => ({ ...f, [r.card.id]: dir }));
        play(dir);
        window.setTimeout(() => setFlash(({ [r.card.id]: _, ...rest }) => rest), 1200);
      }
    }
  }
  return (
    <div className="mx-table-wrap">
      <table className="mx-table">
        {caption && <caption className="sr-only">{caption}</caption>}
        {/* fixed layout: numeric columns get compact widths, the card column takes the rest, so the table always fits */}
        <colgroup>{COLS.map((c, i) => <col key={i} className={`mx-col-${i === 2 ? "card" : (c.className ?? "x").replace("mx-c-", "") || "x"} mx-col-${i}`} />)}</colgroup>
        <thead>
          <tr>
            {COLS.map((c, i) => {
              const active = c.key && sort === c.key;
              const label = c.label;
              return (
                <th key={i} scope="col" className={`mx-th ${c.num ? "mx-num" : ""} ${c.className ?? ""} ${i < 3 ? "mx-sticky-col" : ""} ${i === 2 ? "mx-c-card" : ""}`}
                    aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : undefined} title={c.title}>
                  {c.key && onSort ? (
                    <button type="button" onClick={() => { play("tick"); onSort(c.key as SortKey); }}>
                      <span className="mx-th-label" data-short={c.short && label === c.label ? c.short : undefined}>{label}</span>
                      <span className="mx-sorticon" aria-hidden="true">{active ? (dir === "asc" ? "▲" : "▼") : "↕"}</span>
                    </button>
                  ) : <span><span className="mx-th-label" data-short={c.short && label === c.label ? c.short : undefined}>{label}</span></span>}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody className="mx-anim-rows" key={`${sort}-${dir}-${grade}-${rows[0]?.card.id ?? "none"}-${rows.length}`}>
          {rows.length === 0 && <tr><td colSpan={COLS.length} className="mx-muted" style={{ textAlign: "center" }}>No cards match.</td></tr>}
          {rows.map((base, i) => {
            const r = override[base.card.id] ?? base;
            const fl = flash[base.card.id];
            return (
              // a click on the card's own link (or a control) is handled there: navigating twice would make Back need two presses
              <tr key={base.card.id} className={fl ? `mx-flash-${fl}` : undefined}
                  onClick={(e) => { if (!(e.target as HTMLElement).closest("a, button, select, input")) nav(cardHref(r)); }}
                  style={{ cursor: "pointer", ["--i" as string]: i } as React.CSSProperties}>
                <td className="mx-c-star mx-sticky-col"><Star id={r.card.id} label={r.card.name} /></td>
                <td className="mx-c-rank mx-sticky-col">{base.rank ?? DASH}</td>
                <td className="mx-sticky-col mx-c-card"><CardCell card={r.card} inline to={cardHref(r)} extra={reason?.(base) ? <span className="mx-why">{reason(base)}</span> : undefined} /></td>
                <td><GradeSelect className="mx-grade-select" value={r.label} label={`Grade for ${r.card.name}`} onChange={(g) => void regrade(base, g)} /></td>
                <td className="mx-num mx-price" title={priceTitle(r)}>
                  <span className="mx-price-main">
                    {r.reference?.disagree && <span className="mx-warn" aria-label="Sources disagree">⚠</span>}
                    {usd(r.market_price)}
                  </span>
                  <span className="mx-price-sub">
                    {r.price_source === "scrydex" && <span className="mx-est" aria-label="Scrydex market estimate">est.</span>}
                    {r.price_source === "pricecharting" && <span className="mx-est mx-est-pc" aria-label="PriceCharting value">PC</span>}
                    <Confidence q={base.quality} compact />
                  </span>
                  {jpy && r.market_price !== null && <div className="mx-jpy">{yen(r.market_price, jpy)}</div>}
                </td>
                {(["30d", "90d"] as const).map((k) => (
                  <td key={k} className="mx-num"><Change v={r[`change_${k}`]} why={r.change_reasons[k]} src={r.change_sources?.[k]} /></td>
                ))}
                <td className="mx-num mx-c-vol">{r.sales_30d ? <>{usd(r.volume_30d, true)}<div className="mx-note">{r.sales_30d} sale{r.sales_30d === 1 ? "" : "s"}</div></> : <span className="mx-dash">{DASH}</span>}</td>
                <td className="mx-num">{usd(r.market_cap, true)}</td>
                <td className="mx-num mx-c-pop" title={r.population !== null ? num(r.population) : undefined}>{r.population !== null ? compactNum(r.population) : <span className="mx-dash">{DASH}</span>}</td>
                <td className="mx-num mx-c-spark"><Sparkline points={r.sparkline?.points} height={42} source={r.sparkline?.source} note={r.sparkline_note ?? undefined} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** The card page at the row's grade (PSA 10 is the page's default, so it stays out of the URL). */
function cardHref(r: RankRow): string {
  return `/pokemon/cards/${r.card.id}${r.label && r.label !== "PSA 10" ? `?grade=${encodeURIComponent(r.label)}` : ""}`;
}

/** 1,234 / 12.3K / 1.2M: populations stay readable in a compact column (the exact count is in the tooltip). */
function compactNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toLocaleString("en-US", { maximumFractionDigits: 1 })}M`;
  if (n >= 10_000) return `${(n / 1_000).toLocaleString("en-US", { maximumFractionDigits: 1 })}K`;
  return num(n);
}

function priceTitle(r: RankRow): string | undefined {
  const parts: string[] = [];
  if (r.price_source === "scrydex" && r.provider) parts.push(`Scrydex market estimate as of ${r.provider.as_of} (not a verified sale)`);
  if (r.price_source === "pricecharting" && r.reference) parts.push(`PriceCharting value as of ${r.reference.as_of} (reference, not a verified sale)`);
  if (r.price_source === "sales" && r.confidence) parts.push(`Verified sales · ${r.confidence} confidence`);
  if (r.reference && r.price_source !== "pricecharting")
    parts.push(`PriceCharting: ${usd(r.reference.price)}${r.reference.diff_pct !== null ? ` (shown price ${r.reference.diff_pct > 0 ? "+" : ""}${r.reference.diff_pct}% vs PriceCharting)` : ""}`
      + (r.reference.disagree ? " · sources disagree by more than 25%" : ""));
  return parts.length ? parts.join("\n") : undefined;
}

export function Pager({ total, offset, limit, onPage }: { total: number; offset: number; limit: number; onPage: (offset: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / limit));
  const cur = Math.floor(offset / limit) + 1;
  const show = Array.from(new Set([1, cur - 1, cur, cur + 1, pages].filter((p) => p >= 1 && p <= pages))).sort((a, b) => a - b);
  return (
    <div className="mx-pager">
      <span>Showing {total ? offset + 1 : 0} to {Math.min(offset + limit, total)} of {num(total)} cards</span>
      <nav className="mx-pages" aria-label="Pages">
        <button type="button" disabled={cur <= 1} onClick={() => onPage((cur - 2) * limit)} aria-label="Previous page">‹</button>
        {show.map((p, i) => (
          <span key={p} style={{ display: "contents" }}>
            {i > 0 && p - show[i - 1] > 1 && <button type="button" disabled>…</button>}
            <button type="button" aria-current={p === cur ? "page" : undefined} onClick={() => onPage((p - 1) * limit)}>{p}</button>
          </span>
        ))}
        <button type="button" disabled={cur >= pages} onClick={() => onPage(cur * limit)} aria-label="Next page">›</button>
      </nav>
    </div>
  );
}
