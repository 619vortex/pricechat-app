import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, useSearchParams } from "react-router-dom";
import { MarketShell, usePageHead } from "../mx";
import { PAGE_HEADS } from "../../../website/seo";
import { AVAILABILITY, SORTS, ago, cardName, money, vaultApi, type VaultCard, type VaultFilters, type VaultHome } from "./api";

/** The Vault is a tab of the Pokémon Market: the market's frame (stats, tabs), then the Vault's own strip + nav. */
export function VaultShell({ children, wide = false, crumb }: { children: ReactNode; wide?: boolean; crumb?: string }) {
  return (
    <MarketShell crumbs={[["Markets", "/pokemon"], ["Vault", crumb ? "/pokemon/vault" : null], ...(crumb ? [[crumb, null] as [string, null]] : [])]} compact>
      <div className={`vt${wide ? " vt-wide" : ""}`}>
        <nav className="vt-nav" aria-label="Vault">
          <Link to="/pokemon/vault" className="vt-brand"><span className="vt-brand-mark" aria-hidden="true">◆</span>Price Chat Vault</Link>
          <div className="vt-nav-links">
            <NavLink to="/pokemon/vault" end>Store</NavLink>
            <NavLink to="/pokemon/vault/requests">My requests</NavLink>
          </div>
        </nav>
        {children}
        <VaultFootnote />
      </div>
    </MarketShell>
  );
}

export function VaultFootnote() {
  return (
    <aside className="vt-foot" aria-label="About these listings">
      <p><b>How the Vault works today.</b> Each card is listed by an independent seller (Mayumi Card Shop on eBay). Prices, photos,
        condition and availability are the seller's own and are re-checked with eBay every few hours. Price Chat doesn't own, hold or
        reserve these cards. A Request to Buy is not an order and takes no payment.</p>
      <p className="vt-foot-small">eBay and the eBay logo are trademarks of eBay Inc. Price Chat is not affiliated with or endorsed by eBay or Mayumi Card Shop.</p>
    </aside>
  );
}

const FILTER_KEYS = ["q", "pokemon", "set", "year", "min_price", "max_price", "sort", "sold"] as const;

export function VaultStore() {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => {
    const f: VaultFilters = {};
    for (const k of FILTER_KEYS) { const v = params.get(k); if (v) f[k] = v; }
    return f;
  }, [params]);
  const [home, setHome] = useState<VaultHome | null>(null);
  const [items, setItems] = useState<VaultCard[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState(filters.q ?? "");
  const seq = useRef(0);

  useEffect(() => { void vaultApi.home().then(setHome).catch(() => setHome(null)); }, []);
  useEffect(() => {
    const mine = ++seq.current;
    setLoading(true);
    setError(null);
    vaultApi.browse({ ...filters, page: 1 })
      .then((r) => { if (mine !== seq.current) return; setItems(r.items); setTotal(r.total); setPage(1); setPages(r.pages); })
      .catch((e: Error) => { if (mine === seq.current) setError(e.message); })
      .finally(() => { if (mine === seq.current) setLoading(false); });
  }, [filters]);

  const set = (k: (typeof FILTER_KEYS)[number], v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v); else next.delete(k);
    setParams(next, { replace: true });
  };
  const more = () => {
    const mine = ++seq.current;
    setLoading(true);
    vaultApi.browse({ ...filters, page: page + 1 })
      .then((r) => { if (mine !== seq.current) return; setItems((xs) => [...xs, ...r.items]); setPage(r.page); setPages(r.pages); })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };
  usePageHead(PAGE_HEADS["pokemon/vault"]);
  const active = FILTER_KEYS.filter((k) => k !== "sort" && filters[k]).length;
  const seller = home?.sources[0];

  return (
    <VaultShell wide>
      <header className="vt-hero">
        <p className="vt-eyebrow">Japanese originals · 1996–2002</p>
        <h1>Vintage Japanese Pokémon</h1>
        <p className="vt-lede">Hand-picked 1996–2002 Japanese cards: Base, Jungle, Fossil, Rocket Gang, Gym, Neo and e-Card. Real listings, real photos, the seller's
          actual prices.</p>
        <div className="vt-hero-meta">
          {seller && (
            <span className="vt-source">Curated from <a href={seller.url ?? undefined} target="_blank" rel="noopener noreferrer">{seller.name}</a>
              {seller.platform === "eBay" ? " on eBay" : ""}{seller.checked_at ? ` · checked ${ago(seller.checked_at)}` : ""}</span>
          )}
          {home && <span className="vt-count"><b>{home.count.toLocaleString()}</b> {home.count === 1 ? "card" : "cards"} available</span>}
        </div>
      </header>

      <section className="vt-tools" aria-label="Search and filters">
        <form className="vt-search" role="search" onSubmit={(e) => { e.preventDefault(); set("q", q.trim()); }}>
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search Pokémon, set or title…" aria-label="Search the Vault" maxLength={80} />
          <button type="submit" className="vt-btn">Search</button>
        </form>
        <details className="vt-filters" open={active > 0 || undefined}>
          <summary>Filters{active ? ` · ${active}` : ""}</summary>
          <div className="vt-filter-grid">
            <label>Pokémon
              <select value={filters.pokemon ?? ""} onChange={(e) => set("pokemon", e.target.value)}>
                <option value="">All Pokémon</option>
                {home?.facets.pokemon.map(([n, c]) => <option key={n} value={n}>{n} ({c})</option>)}
              </select>
            </label>
            <label>Set
              <select value={filters.set ?? ""} onChange={(e) => set("set", e.target.value)}>
                <option value="">All sets</option>
                {home?.facets.sets.map(([n, c]) => <option key={n} value={n}>{n} ({c})</option>)}
              </select>
            </label>
            <label>Year
              <select value={filters.year ?? ""} onChange={(e) => set("year", e.target.value)}>
                <option value="">Any year</option>
                {home?.facets.years.map(([y, c]) => <option key={y} value={String(y)}>{y} ({c})</option>)}
              </select>
            </label>
            <label>Min price
              <PriceInput value={filters.min_price ?? ""} placeholder="$ min" onCommit={(v) => set("min_price", v)} />
            </label>
            <label>Max price
              <PriceInput value={filters.max_price ?? ""} placeholder="$ max" onCommit={(v) => set("max_price", v)} />
            </label>
            <label className="vt-check"><input type="checkbox" checked={filters.sold === "true"} onChange={(e) => set("sold", e.target.checked ? "true" : "")} /> Show recently sold</label>
            {active > 0 && <button type="button" className="vt-link" onClick={() => { setQ(""); setParams(new URLSearchParams(filters.sort ? { sort: filters.sort } : {}), { replace: true }); }}>Clear filters</button>}
          </div>
        </details>
        <label className="vt-sort">Sort
          <select value={filters.sort ?? "featured"} onChange={(e) => set("sort", e.target.value === "featured" ? "" : e.target.value)}>
            {SORTS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
        </label>
      </section>

      {error && <p className="vt-error" role="alert">Couldn't load the Vault: {error}</p>}
      {!error && !loading && items.length === 0 && (
        <div className="vt-empty">
          {active ? <><h2>No cards match</h2><p>Try fewer filters or a different search.</p></>
            : <><h2>The first cards are being curated</h2><p>Every card is reviewed by hand before it appears here. Check back soon.</p></>}
        </div>
      )}
      <p className="vt-results" aria-live="polite">{!loading || items.length ? `${total.toLocaleString()} ${total === 1 ? "card" : "cards"}` : "Loading…"}</p>
      <ul className="vt-grid" aria-busy={loading}>
        {items.map((c) => <li key={c.id}><CardTile c={c} /></li>)}
        {loading && items.length === 0 && Array.from({ length: 8 }, (_, i) => <li key={`s${i}`} className="vt-skel" aria-hidden="true" />)}
      </ul>
      {page < pages && !error && (
        <div className="vt-more"><button className="vt-btn vt-btn-ghost" onClick={more} disabled={loading}>{loading ? "Loading…" : "Show more cards"}</button></div>
      )}
    </VaultShell>
  );
}

/** A price bound that applies once typing pauses: "1500" is one search, not four. */
function PriceInput({ value, placeholder, onCommit }: { value: string; placeholder: string; onCommit: (v: string) => void }) {
  const [v, setV] = useState(value);
  const commit = useRef(onCommit);
  commit.current = onCommit; // the latest filters, not the ones from when typing started
  useEffect(() => setV(value), [value]);
  useEffect(() => {
    if (v === value) return;
    const t = window.setTimeout(() => commit.current(v), 500);
    return () => window.clearTimeout(t);
  }, [v]); // eslint-disable-line react-hooks/exhaustive-deps
  return <input inputMode="decimal" value={v} placeholder={placeholder} onChange={(e) => setV(e.target.value.replace(/[^\d.]/g, ""))}
                onKeyDown={(e) => { if (e.key === "Enter" && v !== value) onCommit(v); }} />;
}

export function CardTile({ c }: { c: VaultCard }) {
  const out = c.availability !== "available";
  return (
    <Link to={`/pokemon/vault/${c.id}`} className={`vt-tile${out ? " is-out" : ""}`} aria-label={`${cardName(c)}, ${money(c.price, c.currency)}`}>
      <div className="vt-tile-art">
        {c.image ? <img src={c.image} alt={c.title} loading="lazy" decoding="async" referrerPolicy="no-referrer" /> : <span className="vt-noimg">No photo</span>}
        {c.featured && !out && <span className="vt-flag vt-flag-feat">Featured</span>}
        {out && <span className="vt-flag vt-flag-out">{AVAILABILITY[c.availability]}</span>}
      </div>
      <div className="vt-tile-body">
        <h3>{cardName(c)}</h3>
        <p className="vt-tile-set">{[c.set_name, c.year ? `${c.year}` : null].filter(Boolean).join(" · ") || "Japanese vintage"}</p>
        <p className="vt-chips">
          <span className="vt-chip">Japanese</span>
          <span className="vt-chip">{c.condition ?? "Ungraded"}{c.seller_grade ? ` · ${c.seller_grade}` : ""}</span>
        </p>
        <div className="vt-tile-foot">
          <span className="vt-price">{money(c.price, c.currency)}</span>
          <span className={`vt-avail is-${c.availability}`}>{AVAILABILITY[c.availability]}</span>
        </div>
        <span className="vt-btn vt-btn-sm vt-tile-cta">View details</span>
      </div>
    </Link>
  );
}
