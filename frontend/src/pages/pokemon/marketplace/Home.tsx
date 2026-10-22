import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { errorText } from "../portfolio/shared";
import { mkApi, usdt, type Filters, type Home, type Page } from "./api";
import { ListingGrid, MarketplaceFrame, ModeBanner, useMarketStatus } from "./shared";

const SORTS: [string, string][] = [["recent", "Recently listed"], ["price_asc", "Price: low to high"], ["price_desc", "Price: high to low"],
  ["value", "Most valuable"], ["discount", "Biggest discount to estimate"], ["sold", "Most recently sold"]];
const GRADES = ["10", "9.5", "9", "8.5", "8", "7.5", "7", "6", "5", "4", "3", "2", "1"];
const CONDITIONS: [string, string][] = [["NM", "Near Mint"], ["LP", "Lightly Played"], ["MP", "Moderately Played"], ["HP", "Heavily Played"], ["DMG", "Damaged"]];

/** The verified graded-slab marketplace (preview): /pokemon/marketplace/graded. The one-click Marketplace is QuickTrade.tsx. */
export function GradedMarketplace() {
  const status = useMarketStatus();
  const [home, setHome] = useState<Home | null>(null);
  const [page, setPage] = useState<Page | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sp, setSp] = useSearchParams();
  const filters: Filters = useMemo(() => {
    const f: Filters = {};
    for (const [k, v] of sp.entries()) (f as Record<string, unknown>)[k] = ["year", "min_price", "max_price", "min_discount", "page"].includes(k) ? Number(v) : k === "vaulted" ? v === "1" : v;
    return f;
  }, [sp]);
  const searching = Object.keys(filters).length > 0;
  useEffect(() => { void mkApi.home().then(setHome).catch((e) => setErr(errorText(e))); }, []);
  useEffect(() => {
    if (!searching) { setPage(null); return; }
    void mkApi.browse({ ...filters, status: filters.sort === "sold" ? "sold" : filters.status }).then(setPage).catch((e) => setErr(errorText(e)));
  }, [filters, searching]);
  const set = (k: keyof Filters, v: string | number | boolean | undefined) => {
    const n = new URLSearchParams(sp);
    if (v === undefined || v === "" || v === false) n.delete(k); else n.set(k, v === true ? "1" : String(v));
    if (k !== "page") n.delete("page");
    setSp(n, { replace: true });
  };
  const s = home?.stats;
  return (
    <MarketplaceFrame crumbs={[["Markets", "/pokemon"], ["Marketplace", "/pokemon/marketplace"], ["Graded & raw cards", null]]}>
      <section className="mk-hero">
        <div>
          <p className="pf-eyebrow">Graded & raw Pokémon · verified sellers · priced in USDT</p>
          <h1>Card Marketplace</h1>
          <p className="pf-muted">Graded slabs and raw cards from verified sellers, checked before they list and held by a vault partner. Raw cards show an AI condition
            review and members' own grades; every asking price sits next to Price Chat's market estimate.</p>
          <div className="pf-actions">
            <Link className="pf-shiny pf-big" to="/pokemon/marketplace/sell">＋ Sell a card</Link>
            <a className="pf-btn pf-big" href="#browse">Browse cards</a>
          </div>
        </div>
        <dl className="mk-stats">
          <div><dt>Active listings</dt><dd>{s ? s.active_listings : "—"}</dd></div>
          <div><dt>Vaulted inventory</dt><dd>{home ? home.vaulted_inventory : "—"}</dd></div>
          <div><dt>Volume (30D)</dt><dd>{s ? usdt(s.volume_30d) : "—"}</dd></div>
          <div><dt>Completed sales</dt><dd>{s ? s.sales : "—"}</dd></div>
        </dl>
      </section>
      <ModeBanner status={status} />
      {err && <p className="pf-error">{err}</p>}

      <section className="mk-filters" id="browse" aria-label="Search and filter">
        <input className="mk-search" placeholder="Search Pokémon, set or card number" defaultValue={filters.q ?? ""}
               onKeyDown={(e) => { if (e.key === "Enter") set("q", (e.target as HTMLInputElement).value.trim()); }} aria-label="Search" />
        <div className="mk-kind-tabs" role="tablist" aria-label="Card type">
          {([["", "All cards"], ["graded", "💎 Graded"], ["raw", "🃏 Raw"]] as const).map(([k, label]) => (
            <button key={k} type="button" role="tab" aria-selected={(filters.kind ?? "") === k} className={(filters.kind ?? "") === k ? "is-on" : ""}
                    onClick={() => { const n = new URLSearchParams(sp); n.delete("company"); n.delete("grade"); n.delete("page");
                                     if (k) n.set("kind", k); else n.delete("kind"); setSp(n, { replace: true }); }}>{label}</button>
          ))}
        </div>
        <div className="mk-filter-row">
          {filters.kind !== "raw" && (
            <select value={filters.company ?? ""} onChange={(e) => set("company", e.target.value)} aria-label="Grading company">
              <option value="">All graders</option><option>PSA</option><option>BGS</option><option>CGC</option></select>
          )}
          {filters.kind === "raw" ? (
            <select value={filters.grade ?? ""} onChange={(e) => set("grade", e.target.value)} aria-label="Condition">
              <option value="">Any condition</option>{CONDITIONS.map(([k, label]) => <option key={k} value={k}>{label} ({k})</option>)}</select>
          ) : (
            <select value={filters.grade ?? ""} onChange={(e) => set("grade", e.target.value)} aria-label="Grade">
              <option value="">Any grade</option>{GRADES.map((g) => <option key={g}>{g}</option>)}</select>
          )}
          <select value={filters.language ?? ""} onChange={(e) => set("language", e.target.value)} aria-label="Language">
            <option value="">Any language</option><option>English</option><option>Japanese</option></select>
          <select value={filters.edition ?? ""} onChange={(e) => set("edition", e.target.value)} aria-label="Edition">
            <option value="">Any edition</option><option>1st Edition</option><option>Unlimited</option><option>Shadowless</option></select>
          <input inputMode="numeric" placeholder="Year" defaultValue={filters.year ?? ""} onBlur={(e) => set("year", e.target.value ? Number(e.target.value) : undefined)} aria-label="Year" />
          <input placeholder="Card #" defaultValue={filters.number ?? ""} onBlur={(e) => set("number", e.target.value.trim())} aria-label="Card number" />
          <input inputMode="decimal" placeholder="Min USDT" defaultValue={filters.min_price ?? ""} onBlur={(e) => set("min_price", e.target.value ? Number(e.target.value) : undefined)} aria-label="Minimum price" />
          <input inputMode="decimal" placeholder="Max USDT" defaultValue={filters.max_price ?? ""} onBlur={(e) => set("max_price", e.target.value ? Number(e.target.value) : undefined)} aria-label="Maximum price" />
          <select value={filters.min_discount ?? ""} onChange={(e) => set("min_discount", e.target.value ? Number(e.target.value) : undefined)} aria-label="Discount to market">
            <option value="">Any price vs estimate</option><option value="5">≥ 5% below estimate</option><option value="10">≥ 10% below</option><option value="20">≥ 20% below</option></select>
          <label className="pf-check"><input type="checkbox" checked={!!filters.vaulted} onChange={(e) => set("vaulted", e.target.checked)} /> Vaulted only</label>
          <select value={filters.sort ?? "recent"} onChange={(e) => set("sort", e.target.value)} aria-label="Sort">
            {SORTS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}</select>
          {searching && <button type="button" className="pf-link" onClick={() => setSp({}, { replace: true })}>Clear</button>}
        </div>
      </section>

      {searching ? (
        <section className="mk-section">
          <h2>{page ? `${page.total} card${page.total === 1 ? "" : "s"}` : "Searching…"}</h2>
          {page && <ListingGrid items={page.items} empty={<p>No listings match. Try fewer filters.</p>} />}
          {page && page.total > page.per_page && (
            <div className="pf-actions pf-center">
              <button type="button" className="pf-btn" disabled={page.page <= 1} onClick={() => set("page", page.page - 1)}>← Previous</button>
              <span className="pf-muted">Page {page.page} of {Math.ceil(page.total / page.per_page)}</span>
              <button type="button" className="pf-btn" disabled={page.page * page.per_page >= page.total} onClick={() => set("page", page.page + 1)}>Next →</button>
            </div>
          )}
        </section>
      ) : home && (
        <>
          {home.featured.length > 0 && <Section title="✨ Featured vaulted cards" items={home.featured} />}
          <Section title="Newly listed" items={home.newest} empty={<EmptyMarket />} />
          {home.cheapest.length > 0 && <Section title="Lowest priced" items={home.cheapest} />}
          {home.valuable.length > 0 && <Section title="Most valuable" items={home.valuable} />}
          <Section title="Recently sold" items={home.sold} empty={<p>No completed marketplace sales yet. Sales appear here only once they've settled.</p>} />
          {home.trending.length > 0 && (
            <section className="mk-section"><h2>Trending on the marketplace</h2>
              <div className="mk-chips">{home.trending.map((n) => <button key={n} type="button" className="mk-chip" onClick={() => set("q", n)}>{n}</button>)}</div>
            </section>
          )}
          <p className="pf-fine">Volume counts completed sales between different collectors only (never listings, cancelled orders, transfers or sandbox trades).
            Estimates are Price Chat market data for the exact grade, in USD; asking prices are in USDT.</p>
        </>
      )}
    </MarketplaceFrame>
  );
}

function Section({ title, items, empty }: { title: string; items: Home["newest"]; empty?: React.ReactNode }) {
  if (!items.length && !empty) return null;
  return <section className="mk-section"><h2>{title}</h2><ListingGrid items={items} empty={empty} /></section>;
}

function EmptyMarket() {
  return (
    <div>
      <div className="pf-holo-card" aria-hidden><span>🛒</span></div>
      <p><b>No cards are listed yet.</b> Listings appear once a card has been verified and its vault custody confirmed. Nothing here is invented.</p>
      <Link className="pf-shiny" to="/pokemon/marketplace/sell">Be the first to list a card</Link>
    </div>
  );
}
