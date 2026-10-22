import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiErrorBanner } from "../../components/ui";
import { play } from "./marketSound";
import { marketApi, type MarketQuery, type MarketResp, type RankRow, type SortKey } from "./api";
import { DASH, num, usd } from "./format";

import { Change, fxNote, GradeSelect, MarketShell, Pills, Skeleton, Thumb, useCountUp, useFx, usePublicMarket } from "./mx";
import { MarketPulse } from "./pulse";
import { Pager, RankTable } from "./RankTable";

const PER: [string, string][] = [["50", "50"], ["100", "100"], ["250", "250"]];
const DEFAULT_DESC: Partial<Record<SortKey, boolean>> = { rank: false, name: false };
const HIGHLIGHTS_KEY = "pc.market.highlights";
const PULSE_KEY = "pc.market.pulse";

function readPulse(): boolean {
  try {
    const saved = window.localStorage.getItem(PULSE_KEY);
    if (saved) return saved !== "off";
  } catch {
    /* storage unavailable */
  }
  return true;
}

/** Category chips (CoinGecko's "All / Highlights / categories" row). Each is a plain URL filter, so it is shareable. */
const CHIPS: { label: string; icon?: string; params: Record<string, string> }[] = [
  { label: "All", params: {} },
  { label: "1st Edition", icon: "🥇", params: { edition: "1st Edition" } },
  { label: "Base Set", icon: "🔥", params: { set: "Base Set" } },
  { label: "Jungle", params: { set: "Jungle" } },
  { label: "Fossil", params: { set: "Fossil" } },
  { label: "Team Rocket", params: { set: "Team Rocket" } },
  { label: "Neo Genesis", params: { set: "Neo Genesis" } },
  { label: "Neo Destiny", params: { set: "Neo Destiny" } },
  { label: "Gold Star", icon: "★", params: { q: "star" } },
];
const FILTER_KEYS = ["set", "year", "edition", "language", "q"];
const CHIP_KEYS = ["set", "year", "edition", "q"]; // the market (language) switch is separate and survives chip clicks

function readHighlights(): boolean {
  try {
    const saved = window.localStorage.getItem(HIGHLIGHTS_KEY);
    if (saved) return saved !== "off";
  } catch {
    /* storage unavailable: fall through to the default */
  }
  return typeof window === "undefined" || !window.matchMedia || !window.matchMedia("(max-width: 640px)").matches;
}

/** Market homepage, laid out like CoinGecko's market overview: heading + summary, compact highlights, category chips, and
 * the rankings table as the main event. Every control lives in the URL. */
export function MarketHome() {
  const [sp, setSp] = useSearchParams();
  const grade = sp.get("grade") ?? "PSA 10";
  const sort = (sp.get("sort") as SortKey | null) ?? "rank";
  const dir = (sp.get("dir") as "asc" | "desc" | null) ?? (DEFAULT_DESC[sort] === false ? "asc" : "desc");
  const per = Number(sp.get("per") ?? 100);
  const page = Math.max(1, Number(sp.get("page") ?? 1));
  const q: MarketQuery = {
    grade, sort, dir, limit: per, offset: (page - 1) * per, q: sp.get("q") ?? undefined, set: sp.get("set") ?? undefined,
    year: sp.get("year") ?? undefined, edition: sp.get("edition") ?? undefined, language: sp.get("language") ?? undefined, era: "all",
    view: sp.get("view") === "all" ? "all" : "top100",
  };
  const top = q.view === "top100";
  const isPublic = usePublicMarket();
  const key = JSON.stringify(q);
  const [data, setData] = useState<MarketResp | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [filterText, setFilterText] = useState(sp.get("q") ?? "");
  const [showHighlights, setShowHighlights] = useState(readHighlights);
  const [showFilters, setShowFilters] = useState(false);
  const [valuable, setValuable] = useState<RankRow[]>([]);
  const [pulseOpen, setPulseOpen] = useState(readPulse);
  const [pulseMobile, setPulseMobile] = useState(false);
  function setPulse(open: boolean) {
    setPulseOpen(open);
    try {
      window.localStorage.setItem(PULSE_KEY, open ? "on" : "off");
    } catch {
      /* per-viewer convenience only */
    }
  }
  useEffect(() => {
    let live = true;
    marketApi.market(q).then((d) => { if (live) { setData(d); setError(null); } }).catch((e) => live && setError(e));
    return () => { live = false; };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    // "Trending" needs recent verified sales or watchlist stars; when there are none, the panel shows the highest prices instead
    if (!data || data.sections.trending.length) return;
    void marketApi.market({ grade, sort: "price", dir: "desc", limit: 3, era: "all" }).then((m) => setValuable(m.rows.filter((r) => r.market_price !== null)))
      .catch(() => setValuable([]));
  }, [data?.sections.trending.length, grade]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const t = window.setTimeout(() => { if ((sp.get("q") ?? "") !== filterText.trim()) update({ q: filterText.trim() || null, page: null }); }, 300);
    return () => window.clearTimeout(t);
  }, [filterText]); // eslint-disable-line react-hooks/exhaustive-deps

  function update(changes: Record<string, string | null>) {
    const next = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(changes)) {
      if (v === null || v === "") next.delete(k);
      else next.set(k, v);
    }
    setSp(next, { replace: false });
  }
  function onSort(k: SortKey) {
    const natural = DEFAULT_DESC[k] === false ? "asc" : "desc";
    const nextDir = sort === k ? (dir === "asc" ? "desc" : "asc") : natural;
    update({ sort: k === "rank" && nextDir === "asc" ? null : k, dir: nextDir === natural ? null : nextDir, page: null });
  }
  function toggleHighlights() {
    const next = !showHighlights;
    play("tick");
    setShowHighlights(next);
    try {
      window.localStorage.setItem(HIGHLIGHTS_KEY, next ? "on" : "off");
    } catch {
      /* per-viewer convenience only */
    }
  }
  function chip(params: Record<string, string>) {
    play("tick");
    const reset = Object.fromEntries(CHIP_KEYS.map((k) => [k, null as string | null]));
    setFilterText(params.q ?? "");
    update({ ...reset, ...params, page: null });
  }
  const activeChip = CHIPS.find((c) => CHIP_KEYS.every((k) => (sp.get(k) ?? "") === (c.params[k] ?? "")))?.label ?? null;
  const market = sp.get("language") ?? "";
  const fx = useFx();
  // yen next to USD: on request, and always on the Japanese market
  const showJpy = sp.get("cur") === "jpy" || (market === "Japanese" && sp.get("cur") !== "usd");
  const filtersOn = FILTER_KEYS.some((k) => sp.get(k));
  const h = data?.hero;
  const capShown = useCountUp(h?.total_market_cap, 900);
  const trendingRows = data?.sections.trending.length ? data.sections.trending.slice(0, 3) : valuable.slice(0, 3);

  return (
    <MarketShell>
      <header className="mx-pagehead">
        <div>
          <h1 className="mx-h1">{top ? "Top 100 Vintage Pokémon Cards by Market Cap" : "Pokémon Card Prices by Market Cap"}</h1>
          {h && (
            <p className="mx-lede">
              {h.total_market_cap !== null ? (
                <>The estimated {grade} market cap of {top ? "the Top 100" : "tracked cards"} today is <b>{usd(h.total_market_cap, true)}</b>
                  {h.market_cap_change_30d !== null && <>, <Change v={h.market_cap_change_30d} /> over 30 days from price moves
                    {h.market_cap_change_30d_cards < h.cards_with_cap && <> ({num(h.market_cap_change_30d_cards)} of {num(h.cards_with_cap)} cards have a 30-day price history)</>}</>}. </>
              ) : <>No {grade} market cap yet: it needs a price and a known {grade} population for the same card. </>}
              {top && data?.top100 ? (
                <>
                  <b>{num(data.top100.eligible)}</b> of <b>{num(data.top100.candidates)}</b> vintage PSA 10 records meet every data-quality check
                  {data.top100.eligible < data.top100.limit && <> (fewer than {data.top100.limit}: the list is not padded with unreliable cards)</>};
                  {" "}{isPublic ? <>{num(data.top100.excluded)} excluded</> : <Link to="/pokemon/quality" className="mx-readmore">{num(data.top100.excluded)} excluded, see why</Link>}.{" "}
                </>
              ) : <><b>{num(h.cards_priced)}</b> of <b>{num(h.cards_tracked)}</b> cards priced.{" "}</>}
              {!isPublic && <Link to="/pokemon/sources" className="mx-readmore">Data sources</Link>}
            </p>
          )}
        </div>
        <div className="mx-headside">
        <Link to="/pokemon/vault" className="pf-trade-btn">◆ Shop the Vault <small>Japanese vintage</small></Link>
        <Link to="/pokemon/portfolio" className="pf-shiny pf-portfolio-btn">✨ My Portfolio</Link>
        {!isPublic && <Link to="/pokemon/marketplace" className="pf-btn pf-portfolio-btn">🛒 Marketplace</Link>}
        <label className="mx-switch">
          <span>Highlights</span>
          <input type="checkbox" role="switch" checked={showHighlights} onChange={toggleHighlights} aria-label="Show highlights" />
          <i aria-hidden="true" />
        </label>
        </div>
      </header>

      <ApiErrorBanner error={error} />
      <div className={`mx-withside${pulseOpen ? "" : " is-collapsed"}`}>
      <div className="mx-maincol">
      <div className="mx-pulse-mobile">
        <button type="button" className="mx-customise" aria-expanded={pulseMobile} onClick={() => setPulseMobile(!pulseMobile)}>
          <span className="mx-pulse-dot" aria-hidden="true" /> Pokémon Market Pulse {pulseMobile ? "▴" : "▾"}
        </button>
        {pulseMobile && <MarketPulse />}
      </div>
      {!data ? <Skeleton rows={10} boxes={showHighlights} /> : (
        <>
          {showHighlights && (
            <section className="mx-highlights" aria-label="Highlights">
              <div className="mx-statstack">
                <div className="mx-box mx-kpi">
                  <strong aria-label={h!.total_market_cap !== null ? usd(h!.total_market_cap) : "unknown"}>{capShown !== null ? usd(capShown) : DASH}</strong>
                  <span title="30-day change from price moves (population held at today's count), over the cards with a 30-day price history">
                    {grade} Market Cap {h!.market_cap_change_30d !== null && <><Change v={h!.market_cap_change_30d} /> 30D</>}
                    <em> · {num(h!.cards_with_cap)} cards with population</em></span>
                </div>
                <div className="mx-box mx-kpi">
                  <strong>{h!.grade_sales_30d ? usd(h!.volume_30d) : DASH}</strong>
                  <span>30D verified trading volume <em>· {h!.grade_sales_30d ? `${num(h!.grade_sales_30d)} completed sales` : "no verified sales yet"}</em></span>

                </div>
              </div>
              <MiniBox title={data.sections.trending.length ? "🔥 Trending" : "💎 Highest Value"} to={data.sections.trending.length ? "/pokemon/trending" : "/pokemon?sort=price"}
                       rows={trendingRows} value={(r) => usd(r.market_price)} move={(r) => r.change_30d}
                       empty="Nothing to show yet." />
              <MiniBox title="🚀 Top Gainers" to="/pokemon/gainers" rows={data.sections.gainers.slice(0, 3)} value={(r) => usd(r.market_price)} move={(r) => r.change_30d}
                       empty="No card has a 30-day price rise on record." />
            </section>
          )}

          {!data.has_market_data && (
            <p className="mx-banner" role="status">
              <b>No market data yet.</b> Prices appear from Scrydex market valuations or verified sales; nothing is estimated in their place.{" "}
              {!isPublic && <Link to="/pokemon/sources">Data sources →</Link>}
            </p>
          )}

          <div className="mx-tabbar">
            <div className="mx-pills mx-viewpills" role="group" aria-label="Ranking">
              <button type="button" className={`mx-pill${top ? " is-on" : ""}`} aria-pressed={top} onClick={() => update({ view: null, page: null })}
                      title="Only cards that pass every data-quality check, ranked by PSA 10 market cap">Top 100</button>
              <button type="button" className={`mx-pill${top ? "" : " is-on"}`} aria-pressed={!top} onClick={() => update({ view: "all", page: null })}
                      title="Every card in the catalogue, including ones with incomplete data">All cards</button>
            </div>
            <nav className="mx-chips" aria-label="Categories">
              {CHIPS.map((c) => (
                <button key={c.label} type="button" className={`mx-chip${activeChip === c.label ? " is-on" : ""}`} aria-pressed={activeChip === c.label}
                        onClick={() => chip(c.params)}>
                  {c.icon && <span aria-hidden="true">{c.icon}</span>}{c.label}
                </button>
              ))}
            </nav>
            <div className="mx-tabbar-right">
              <button type="button" className={`mx-jp-toggle${market === "Japanese" ? " is-on" : ""}`} aria-pressed={market === "Japanese"}
                      title={market === "Japanese" ? "Showing Japanese cards only. Click to show all cards." : "Show Japanese cards only"}
                      onClick={() => { play("tick"); update({ language: market === "Japanese" ? null : "Japanese", page: null }); }}>
                <span aria-hidden="true">🇯🇵</span> Japanese
              </button>
              <Pills label="Currency" value={showJpy ? "jpy" : "usd"} options={[["usd", "USD"], ["jpy", "USD + ¥"]]}
                     onChange={(v) => update({ cur: v === (market === "Japanese" ? "jpy" : "usd") ? null : v })} />
              <GradeSelect value={grade} onChange={(g) => update({ grade: g === "PSA 10" ? null : g, page: null })} label="Grade for the whole table" />
              <button type="button" className={`mx-customise${showFilters || filtersOn ? " is-on" : ""}`} aria-expanded={showFilters} onClick={() => setShowFilters(!showFilters)}>
                <span aria-hidden="true">⚙</span> Filters{filtersOn ? " •" : ""}
              </button>
            </div>
          </div>

          {(showFilters || filtersOn) && (
            <div className="mx-filterrow">
              <input className="mx-select mx-filterinput" type="search" placeholder="Filter cards…" aria-label="Filter the table"
                     value={filterText} onChange={(e) => setFilterText(e.target.value)} />
              <FilterSelect label="Set" value={sp.get("set")} options={data.options.sets} onChange={(v) => update({ set: v, page: null })} />
              <FilterSelect label="Year" value={sp.get("year")} options={data.options.years.map(String)} onChange={(v) => update({ year: v, page: null })} />
              <FilterSelect label="Edition" value={sp.get("edition")} options={data.options.editions} onChange={(v) => update({ edition: v, page: null })} />
              <FilterSelect label="Language" value={sp.get("language")} options={data.options.languages} onChange={(v) => update({ language: v, page: null })} />
              {filtersOn && <button type="button" className="mx-clear" onClick={() => { setFilterText(""); update({ set: null, year: null, edition: null, language: null, q: null, page: null }); }}>Clear filters</button>}
            </div>
          )}

          {data.rows.some((r) => r.price_source === "scrydex" || r.price_source === "pricecharting") && (
            <p className="mx-sourcebar">
              <span className="mx-est">est.</span> = <b>Scrydex</b> valuation · <span className="mx-est mx-est-pc">PC</span> = <b>PriceCharting</b> value (no history) ·{" "}
              <span className="mx-warn">⚠</span> sources differ &gt;25% · none are sales. Volume = verified sales only.
            </p>
          )}
          {showJpy && (
            <p className="mx-sourcebar mx-fxbar" role="note">
              {fx ? <>¥ = USD price converted at {fxNote(fx)}. Prices are in USD; yen is a conversion, not a Japanese sale price.</>
                : <>No USD/JPY rate available right now, so prices are shown in USD only.</>}
            </p>
          )}
          {market === "Japanese" && (
            <p className="mx-sourcebar" role="note">
              Japanese market: Japanese prints only, priced from Japanese card data. English prices and populations are never applied to Japanese cards.
            </p>
          )}
          <RankTable rows={data.rows} sort={sort} dir={dir} onSort={onSort} grade={grade} jpy={showJpy ? fx?.rate ?? null : null}
                     caption={`Pokémon cards ranked by ${grade} market cap`} />
          <div className="mx-pagerbar">
            <Pager total={data.total} offset={data.offset} limit={data.limit} onPage={(o) => { update({ page: o ? String(o / per + 1) : null }); window.scrollTo({ top: 0 }); }} />
            <span className="mx-rows"><span className="mx-muted">Rows</span>
              <Pills label="Rows per page" value={String(per)} options={PER} onChange={(v) => update({ per: v === "100" ? null : v, page: null })} /></span>
          </div>
          <p className="mx-note">
            Price: recency-weighted median of verified sales at the selected grade; where there are none, the Scrydex market valuation, labelled
            “Scrydex est.” (a provider estimate, never a sale). Volume counts verified sales only. Market cap = price × known population of that grade.
          </p>
        </>
      )}
      </div>
      <div className="mx-sidecol">
        {pulseOpen ? <MarketPulse onCollapse={() => setPulse(false)} /> : (
          <button type="button" className="mx-pulse-rail" onClick={() => setPulse(true)} aria-label="Open Pokémon Market Pulse">
            <span className="mx-pulse-dot" aria-hidden="true" /><span>Market Pulse</span>
          </button>
        )}
      </div>
      </div>
    </MarketShell>
  );
}

function FilterSelect({ label, value, options, onChange }: { label: string; value: string | null; options: string[]; onChange: (v: string | null) => void }) {
  return (
    <select className={`mx-select${value ? " is-set" : ""}`} aria-label={label} value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}>
      <option value="">{label}: all</option>
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}

export function MiniBox({ title, to, rows, value, move, empty }: {
  title: string; to: string; rows: RankRow[]; value: (r: RankRow) => string; move: (r: RankRow) => number | null; empty: string;
}) {
  return (
    <section className="mx-box" aria-label={title}>
      <div className="mx-box-head">
        <h2 className="mx-box-title"><Link to={to}>{title}</Link></h2>
        <Link to={to} className="mx-box-link">View more ›</Link>
      </div>
      {rows.length === 0 ? <p className="mx-empty">{empty}</p> : (
        <ul className="mx-mini">
          {rows.map((r) => (
            <li key={r.card.id}>
              <Link to={`/pokemon/cards/${r.card.id}`} title={r.trend?.why ? `${r.card.name}: ${r.trend.why}` : undefined}>
                <Thumb card={r.card} />
                <span className="mx-mini-name">{r.card.name}<small>{r.trend?.why || `${r.card.year} ${r.card.set_name}${r.card.edition === "1st Edition" ? " · 1st Ed" : ""}`}</small></span>
                <span className="mx-mini-price">{value(r)}</span>
                <Change v={move(r)} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
