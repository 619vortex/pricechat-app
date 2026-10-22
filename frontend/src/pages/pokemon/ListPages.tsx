/** The market's secondary views: Trending, Top Gainers / Losers, Recently Sold, Sets, Grades. Shareable URLs (grade, period, page). */
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiErrorBanner, Loading } from "../../components/ui";
import { marketApi, type GradeRow, type QualityRow, type RankRow, type Sale, type SetRow, type YearRow } from "./api";
import { DASH, day, num, sourceLabel, usd } from "./format";
import { CardCell, Change, Confidence, GradeSelect, MarketShell, Pills, Thumb } from "./mx";
import { Pager, RankTable } from "./RankTable";

function useParam(name: string, dflt: string): [string, (v: string) => void] {
  const [sp, setSp] = useSearchParams();
  const v = sp.get(name) ?? dflt;
  return [v, (next: string) => {
    const p = new URLSearchParams(sp);
    if (next === dflt) p.delete(name);
    else p.set(name, next);
    p.delete("page");
    setSp(p);
  }];
}

function useLoad<T>(load: () => Promise<T>, deps: unknown[]): { data: T | null; error: unknown } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    let live = true;
    setData(null);
    load().then((d) => { if (live) { setData(d); setError(null); } }).catch((e) => live && setError(e));
    return () => { live = false; };
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps
  return { data, error };
}

export function TrendingPage() {
  const [grade, setGrade] = useParam("grade", "PSA 10");
  const { data, error } = useLoad(() => marketApi.trending(grade), [grade]);
  return (
    <MarketShell crumbs={[["Markets", "/pokemon"], ["Trending", null]]}>
      <header>
        <h1 className="mx-h1">🔥 Trending Pokémon</h1>
        <p className="mx-lede">{data?.basis ?? "X mentions + price momentum"}. One card per Pokémon; the reason is shown under each name.</p>
      </header>
      <div className="mx-controls"><div className="mx-controls-left"><GradeSelect value={grade} onChange={setGrade} /></div></div>
      <ApiErrorBanner error={error} />
      {!data ? <Loading /> : data.rows.length === 0
        ? <p className="mx-banner"><b>Nothing trending yet.</b> No X mentions, price moves, verified sales or stars to rank.</p>
        : <RankTable rows={data.rows} grade={grade} reason={(r) => r.trend?.why} />}
    </MarketShell>
  );
}

export function MoversPage({ direction }: { direction: "gainers" | "losers" }) {
  const [grade, setGrade] = useParam("grade", "PSA 10");
  const [period, setPeriod] = useParam("period", "30d");
  const { data, error } = useLoad(() => marketApi.movers(direction, grade, period as "30d" | "90d"), [direction, grade, period]);
  const title = direction === "gainers" ? "🚀 Top Gainers" : "📉 Top Losers";
  return (
    <MarketShell crumbs={[["Markets", "/pokemon"], [direction === "gainers" ? "Top Gainers" : "Top Losers", null]]}>
      <header>
        <h1 className="mx-h1">{title}</h1>
        <p className="mx-lede">Cards whose estimated {grade} price {direction === "gainers" ? "rose" : "fell"} the most over the period. A change exists only when
          verified sales happened inside the period; {data ? <b>{num(data.cards_with_change)}</b> : "…"} cards have one for {period}.</p>
      </header>
      <div className="mx-controls">
        <div className="mx-controls-left">
          <GradeSelect value={grade} onChange={setGrade} />
          <Pills label="Period" value={period} options={[["30d", "30D"], ["90d", "90D"]]} onChange={setPeriod} />
        </div>
      </div>
      <ApiErrorBanner error={error} />
      {!data ? <Loading /> : data.rows.length === 0
        ? <p className="mx-banner"><b>No {direction} for {period}.</b> No card has a verified-sales-based {grade} price {direction === "gainers" ? "rise" : "decline"} over {period}.</p>
        : <RankTable rows={data.rows} grade={grade} />}
    </MarketShell>
  );
}

export function RecentPage() {
  const [grade, setGrade] = useParam("grade", "all");
  const [sp, setSp] = useSearchParams();
  const page = Math.max(1, Number(sp.get("page") ?? 1));
  const per = 50;
  const { data, error } = useLoad(() => marketApi.recent(per, (page - 1) * per, grade === "all" ? undefined : grade), [grade, page]);
  return (
    <MarketShell crumbs={[["Markets", "/pokemon"], ["Recently Sold", null]]}>
      <header>
        <h1 className="mx-h1">Recently Sold</h1>
        <p className="mx-lede">Completed, verified sales, newest first. Asking prices and unverified listings never appear here.</p>
      </header>
      <div className="mx-controls"><div className="mx-controls-left">
        <GradeSelect value={grade} onChange={setGrade} options={["all", "PSA 10", "PSA 9", "PSA 8", "PSA 7", "BGS 10", "BGS 9.5", "CGC 10", "RAW NM"]} />
      </div></div>
      <ApiErrorBanner error={error} />
      {!data ? <Loading /> : data.sales.length === 0 ? (
        <p className="mx-banner"><b>No verified sales on record yet.</b> They appear here as soon as sales are imported (Data sources › CSV) or synced from a connected
          sales provider.</p>
      ) : (
        <>
          <div className="mx-table-wrap">
            <table className="mx-list mx-recent">
              <thead><tr><th>Date</th><th>Card</th><th>Grade</th><th className="mx-num">Price</th><th>Source</th><th>Type</th></tr></thead>
              <tbody>
                {data.sales.map((s: Sale) => (
                  <tr key={s.id}>
                    <td>{day(s.sale_date)}</td>
                    <td>{s.card ? <CardCell card={s.card} /> : DASH}
                      <div className="mx-note mx-phone-only">{day(s.sale_date)} · {s.url ? <a href={s.url} target="_blank" rel="noopener noreferrer">{sourceLabel(s.source)} ↗</a> : sourceLabel(s.source)}</div></td>
                    <td><b>{s.label}</b></td>
                    <td className="mx-num"><b>{usd(s.price_usd)}</b>{s.currency !== "USD" && <div className="mx-note">{(s.price / 100).toLocaleString("en-US")} {s.currency}</div>}</td>
                    <td>{s.url ? <a href={s.url} target="_blank" rel="noopener noreferrer">{sourceLabel(s.source)} ↗</a> : sourceLabel(s.source)}</td>
                    <td>{s.sale_type ?? DASH}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager total={data.total} offset={data.offset} limit={per} onPage={(o) => { const p = new URLSearchParams(sp); if (o) p.set("page", String(o / per + 1)); else p.delete("page"); setSp(p); }} />
        </>
      )}
    </MarketShell>
  );
}

/** Market cap by release year: one row per year, a bar for its share of the whole market, the top cards of that year.
 * A year links to the market table filtered to that year (and language). */
export function YearsPage() {
  const [grade, setGrade] = useParam("grade", "PSA 10");
  const [language, setLanguage] = useParam("language", "");
  const { data, error } = useLoad(() => marketApi.years(grade, language || undefined), [grade, language]);
  const max = Math.max(1, ...(data?.years ?? []).map((y) => y.market_cap ?? 0));
  const jp = language === "Japanese";
  const link = (y: YearRow) => `/pokemon?year=${y.year}${language ? `&language=${encodeURIComponent(language)}` : ""}${grade !== "PSA 10" ? `&grade=${encodeURIComponent(grade)}` : ""}`;
  return (
    <MarketShell crumbs={[["Markets", "/pokemon"], ["Years", null]]}>
      <header>
        <h1 className="mx-h1">Pokémon Card Market Cap by Year</h1>
        <p className="mx-lede">
          {data?.total_market_cap ? <>The estimated {grade} market cap of {jp ? "Japanese" : "all"} cards is <b>{usd(data.total_market_cap, true)}</b>, split by release year. </> : null}
          A year's cap is the sum over its cards that have both a {grade} price and a known {grade} population; prices are provider estimates, not sales.
        </p>
      </header>
      <div className="mx-controls">
        <div className="mx-controls-left">
          <GradeSelect value={grade} onChange={setGrade} />
          <button type="button" className={`mx-jp-toggle${jp ? " is-on" : ""}`} aria-pressed={jp} onClick={() => setLanguage(jp ? "" : "Japanese")}
                  title={jp ? "Showing Japanese cards only. Click to show all cards." : "Show Japanese cards only"}>
            <span aria-hidden="true">🇯🇵</span> Japanese
          </button>
        </div>
      </div>
      <ApiErrorBanner error={error} />
      {!data ? <Loading /> : !data.years.length ? <p className="mx-banner">No cards in the catalog yet.</p> : (
        <div className="mx-table-wrap">
          <table className="mx-list mx-years">
            <thead><tr><th>#</th><th>Year</th><th className="mx-years-capcol">Market Cap</th><th className="mx-num">Share</th><th className="mx-num">30D</th><th className="mx-num">90D</th>
              <th className="mx-num">Cards</th><th className="mx-num">Priced</th><th className="mx-num">{grade} pop.</th><th className="mx-num">Sets</th><th>Top cards</th></tr></thead>
            <tbody>
              {data.years.map((y) => (
                <tr key={y.year}>
                  <td className="mx-muted">{y.rank ?? DASH}</td>
                  <td><Link to={link(y)} className="mx-year-link"><b>{y.year}</b></Link>
                    <div className="mx-note">{y.era === "vintage" ? "Vintage" : "Modern"}{y.languages.length === 1 ? ` · ${y.languages[0]}` : ""}</div></td>
                  <td className="mx-years-capcol">
                    <div className="mx-yearbar" title={y.market_cap ? `${usd(y.market_cap)} from ${num(y.cards_with_cap)} cards` : "No card of this year has both a price and a population"}>
                      <span className="mx-yearbar-fill" style={{ width: `${Math.max(y.market_cap ? 1.5 : 0, ((y.market_cap ?? 0) / max) * 100)}%` }} />
                      <b>{y.market_cap ? usd(y.market_cap, true) : DASH}</b>
                    </div>
                  </td>
                  <td className="mx-num">{y.share !== null && y.market_cap ? `${y.share.toFixed(y.share < 1 ? 2 : 1)}%` : DASH}</td>
                  <td className="mx-num"><Change v={y.market_cap_change_30d} /></td>
                  <td className="mx-num"><Change v={y.market_cap_change_90d} /></td>
                  <td className="mx-num">{num(y.cards)}</td>
                  <td className="mx-num">{num(y.cards_priced)}</td>
                  <td className="mx-num">{num(y.population)}</td>
                  <td className="mx-num">{y.sets}</td>
                  <td className="mx-years-top">{y.top.map((r) => (
                    <Link key={r.card.id} to={`/pokemon/cards/${r.card.id}${grade !== "PSA 10" ? `?grade=${encodeURIComponent(grade)}` : ""}`}
                          title={`${r.card.name} · ${r.card.set_name} · ${usd(r.market_price)}`}><Thumb card={r.card} className="mx-inline-thumb" /></Link>
                  ))}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </MarketShell>
  );
}

export function SetsPage() {
  const [grade, setGrade] = useParam("grade", "PSA 10");
  const { data, error } = useLoad(() => marketApi.sets(grade), [grade]);
  return (
    <MarketShell crumbs={[["Markets", "/pokemon"], ["Sets", null]]}>
      <header>
        <h1 className="mx-h1">Pokémon Card Sets by Market Cap</h1>
        <p className="mx-lede">Each set's estimated {grade} market cap is the sum over its tracked cards that have both a verified price and a known population.</p>
      </header>
      <div className="mx-controls"><div className="mx-controls-left"><GradeSelect value={grade} onChange={setGrade} /></div></div>
      <ApiErrorBanner error={error} />
      {!data ? <Loading /> : (
        <div className="mx-table-wrap">
          <table className="mx-list mx-sets">
            <thead><tr><th>#</th><th>Set</th><th>Year</th><th>Language</th><th className="mx-num">Cards</th><th className="mx-num">Priced</th>
              <th className="mx-num">Market Cap</th><th className="mx-num">30D</th><th className="mx-num">Volume (30D)</th><th className="mx-num">{grade} pop.</th><th>Top cards</th></tr></thead>
            <tbody>
              {data.sets.map((s: SetRow) => (
                <tr key={`${s.set_name}-${s.language}`}>
                  <td className="mx-muted">{s.rank ?? DASH}</td>
                  <td><Link to={`/pokemon?set=${encodeURIComponent(s.set_name)}${grade !== "PSA 10" ? `&grade=${encodeURIComponent(grade)}` : ""}`} className="mx-cardcell" style={{ textDecoration: "none" }}>
                    <Thumb card={s.cover} /><b className="mx-cardname">{s.set_name}</b></Link></td>
                  <td>{s.year}</td>
                  <td>{s.language}</td>
                  <td className="mx-num">{s.cards}</td>
                  <td className="mx-num">{s.cards_priced}</td>
                  <td className="mx-num"><b>{usd(s.market_cap, true)}</b></td>
                  <td className="mx-num"><Change v={s.market_cap_change_30d} /></td>
                  <td className="mx-num">{s.sales_30d ? <>{usd(s.volume_30d, true)}<div className="mx-note">{s.sales_30d} sales</div></> : DASH}</td>
                  <td className="mx-num">{num(s.population)}</td>
                  <td>{s.top.map((c) => <Link key={c.id} to={`/pokemon/cards/${c.id}`} style={{ marginRight: 6 }}><Thumb card={c} className="mx-inline-thumb" /></Link>)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </MarketShell>
  );
}

export function GradesPage() {
  const { data, error } = useLoad(() => marketApi.grades(), []);
  return (
    <MarketShell crumbs={[["Markets", "/pokemon"], ["Grades", null]]}>
      <header>
        <h1 className="mx-h1">Market by Grade</h1>
        <p className="mx-lede">Every grade is its own market: prices, populations and market caps are never combined across grades or grading companies.</p>
      </header>
      <ApiErrorBanner error={error} />
      {!data ? <Loading /> : (
        <div className="mx-table-wrap">
          <table className="mx-list">
            <thead><tr><th>Grade</th><th className="mx-num">Cards priced</th><th className="mx-num">Market Cap</th><th className="mx-num">Sales (30d)</th>
              <th className="mx-num">Volume (30d)</th><th className="mx-num">Known population</th><th>Most valuable card</th><th /></tr></thead>
            <tbody>
              {data.grades.map((g: GradeRow) => (
                <tr key={g.grade}>
                  <td><b>{g.grade}</b>{!g.graded && <div className="mx-note">ungraded: no population or market cap</div>}</td>
                  <td className="mx-num">{num(g.cards_priced)} <span className="mx-muted">/ {num(data.cards_tracked)}</span></td>
                  <td className="mx-num"><b>{g.graded ? usd(g.market_cap, true) : "n/a"}</b></td>
                  <td className="mx-num">{g.sales_30d || DASH}</td>
                  <td className="mx-num">{g.sales_30d ? usd(g.volume_30d, true) : DASH}</td>
                  <td className="mx-num">{g.graded ? num(g.population) : "n/a"}</td>
                  <td>{g.top_card ? <TopCard r={g.top_card} /> : <span className="mx-muted">{DASH}</span>}</td>
                  <td><Link to={`/pokemon${g.grade !== "PSA 10" ? `?grade=${encodeURIComponent(g.grade)}` : ""}`} className="mx-box-link">View market ›</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </MarketShell>
  );
}

function TopCard({ r }: { r: RankRow }) {
  return <span className="mx-cardcell" style={{ minWidth: 0 }}><Thumb card={r.card} /><span><Link to={`/pokemon/cards/${r.card.id}?grade=${encodeURIComponent(r.label)}`}>{r.card.name}</Link> · {usd(r.market_price)}</span></span>;
}


const FLAG_FILTERS: [string, string][] = [["all", "All cards"], ["sources disagree", "Sources disagree"], ["single source", "Single source"],
  ["no price", "No price"], ["no population", "No population"], ["stale", "Stale"]];

/** Every price source side by side for one exact grade. Nothing is averaged or corrected: conflicts are shown for review. */
/** Top 100 eligibility: how many vintage PSA 10 records qualify, why the others don't (by check), the largest excluded
 * records with their failed checks, and recent entries / exits. Excluded cards are never deleted. */
function EligibilitySection() {
  const [reason, setReason] = useParam("reason", "");
  const [language, setLanguage] = useParam("language", "");
  const [offset, setOffset] = useState(0);
  const { data, error } = useLoad(() => marketApi.top100Quality({ reason: reason || undefined, language: language || undefined, offset, limit: 50 }),
    [reason, language, offset]);
  const s = data?.summary;
  const jp = language === "Japanese";
  return (
    <section aria-label="Top 100 eligibility" className="mx-elig">
      <h2 className="mx-h2">Top 100 eligibility</h2>
      <p className="mx-lede">A vintage card enters the Top 100 only when its PSA 10 record passes every check below; eligible cards are then ranked purely by
        market cap (PSA 10 price × PSA 10 population). Nothing is filled in to make a card qualify, and excluded cards stay searchable.</p>
      <div className="mx-controls"><div className="mx-controls-left">
        <button type="button" className={`mx-jp-toggle${jp ? " is-on" : ""}`} aria-pressed={jp} onClick={() => { setLanguage(jp ? "" : "Japanese"); setOffset(0); }}>
          <span aria-hidden="true">🇯🇵</span> Japanese
        </button>
      </div></div>
      <ApiErrorBanner error={error} />
      {!data || !s ? <Loading /> : (
        <>
          <section className="mx-statgrid" aria-label="Eligibility summary">
            <div className="mx-box mx-kpi"><strong>{num(s.eligible)}</strong><span>qualify for the Top 100{s.eligible < s.limit ? ` (shown: ${s.shown}, not padded)` : ""}</span></div>
            <div className="mx-box mx-kpi"><strong>{num(s.excluded)}</strong><span>excluded of {num(s.candidates)} vintage records</span></div>
            <div className="mx-box mx-kpi"><strong>{num(data.missing.price)}</strong><span>no reliable PSA 10 price</span></div>
            <div className="mx-box mx-kpi"><strong>{num(data.missing.population)}</strong><span>no PSA 10 population</span></div>
            <div className="mx-box mx-kpi"><strong>{num(data.missing.history)}</strong><span>under {s.min_history} price observations</span></div>
          </section>
          <div className="mx-reasons" role="group" aria-label="Filter by reason">
            <button type="button" className={`mx-chip${!reason ? " is-on" : ""}`} aria-pressed={!reason} onClick={() => { setReason(""); setOffset(0); }}>All excluded</button>
            {Object.entries(s.excluded_by_reason).map(([k, n]) => (
              <button key={k} type="button" className={`mx-chip${reason === k ? " is-on" : ""}`} aria-pressed={reason === k}
                      onClick={() => { setReason(k); setOffset(0); }}>{s.labels[k]} <b>{num(n)}</b></button>
            ))}
          </div>
          <div className="mx-table-wrap">
            <table className="mx-list">
              <thead><tr><th>Card</th><th className="mx-num">PSA 10 price</th><th className="mx-num">Pop.</th><th className="mx-num">Market cap</th>
                <th className="mx-num">History</th><th>Confidence</th><th>Why it is excluded</th></tr></thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={r.card.id}>
                    <td><CardCell card={r.card} inline /></td>
                    <td className="mx-num">{usd(r.market_price)}</td>
                    <td className="mx-num">{num(r.population)}</td>
                    <td className="mx-num">{usd(r.market_cap, true)}</td>
                    <td className="mx-num">{r.quality.history_points}</td>
                    <td><Confidence q={r.quality} /></td>
                    <td className="mx-why-list">{r.quality.reasons.join(" · ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager total={data.total} offset={data.offset} limit={data.limit} onPage={setOffset} />
          {data.events.length > 0 && (
            <>
              <h3 className="mx-h3">Recent Top 100 changes</h3>
              <ul className="mx-events">
                {data.events.map((e, i) => (
                  <li key={i} className={`mx-event-${e.kind}`}>
                    <b>{e.kind === "entered" ? "▲ Entered" : "▼ Left"}</b> <Link to={`/pokemon/cards/${e.card.id}`}>{e.card.name}</Link>
                    <span className="mx-muted"> {e.card.set_name} · {e.card.edition}{e.rank ? ` · rank ${e.rank}` : ""} · {day(e.at)}</span>
                    <div className="mx-note">{e.reasons.join(" · ")}</div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </section>
  );
}

export function QualityPage() {
  const [grade, setGrade] = useParam("grade", "PSA 10");
  const [flag, setFlag] = useParam("flag", "all");
  const [qOffset, setQOffset] = useState(0);
  useEffect(() => setQOffset(0), [grade, flag]);
  const { data, error } = useLoad(() => marketApi.quality(grade, flag === "all" ? undefined : flag, qOffset), [grade, flag, qOffset]);
  const rows = data ? data.rows : []; // paged on the server: the catalogue has ~50k cards
  const s = data?.summary;
  return (
    <MarketShell crumbs={[["Markets", "/pokemon"], ["Data Quality", null]]}>
      <header>
        <h1 className="mx-h1">Data Quality</h1>
      </header>
      <EligibilitySection />
      <header>
        <h2 className="mx-h2">Sources side by side</h2>
        <p className="mx-lede">Scrydex, PriceCharting and verified sales for the same exact card and grade, side by side. A gap over 25% is flagged; values are never
          averaged or corrected. Provider data older than {s?.stale_after_days ?? 3} days is marked stale.</p>
      </header>
      <div className="mx-controls"><div className="mx-controls-left">
        <GradeSelect value={grade} onChange={setGrade} options={["PSA 10", "PSA 9", "PSA 8", "BGS 10", "CGC 10"]} />
        <Pills label="Show" value={flag} options={FLAG_FILTERS} onChange={setFlag} />
      </div></div>
      <ApiErrorBanner error={error} />
      {!data || !s ? <Loading /> : (
        <>
          <section className="mx-statgrid" aria-label="Summary">
            <div className="mx-stat"><span>Both sources</span><strong>{s.both_sources} / {s.cards}</strong></div>
            <div className="mx-stat"><span>Within ±10%</span><strong>{s.within_10}</strong></div>
            <div className="mx-stat"><span>Within ±25%</span><strong>{s.within_25}</strong></div>
            <div className="mx-stat"><span>Disagree &gt;25%</span><strong className={s.disagree ? "mx-down" : undefined}>{s.disagree}</strong></div>
            <div className="mx-stat"><span>Median Scrydex ÷ PriceCharting</span><strong>{s.median_ratio ?? DASH}</strong></div>
            <div className="mx-stat"><span>Single source</span><strong>{s.single_source}</strong></div>
            <div className="mx-stat"><span>No price</span><strong>{s.no_price}</strong></div>
            <div className="mx-stat"><span>No population</span><strong>{s.no_population}</strong></div>
          </section>
          <div className="mx-table-wrap">
            <table className="mx-list">
              <thead><tr><th>Card</th><th className="mx-num">Scrydex (range)</th><th className="mx-num">PriceCharting</th><th className="mx-num">Gap</th>
                <th className="mx-num">Verified sales</th><th className="mx-num">Population</th><th className="mx-num">Data age</th><th>Flags</th></tr></thead>
              <tbody>
                {rows.map((r: QualityRow) => (
                  <tr key={r.card.id}>
                    <td><CardCell card={r.card} /></td>
                    <td className="mx-num">{usd(r.scrydex)}{r.scrydex_range && r.scrydex_range[0] !== null && <div className="mx-note">{usd(r.scrydex_range[0])}–{usd(r.scrydex_range[1])}</div>}</td>
                    <td className="mx-num">{usd(r.pricecharting)}</td>
                    <td className="mx-num">{r.gap_pct === null ? DASH : <span className={Math.abs(r.gap_pct) > 25 ? "mx-down" : "mx-muted"}>{r.gap_pct > 0 ? "+" : ""}{r.gap_pct}%</span>}</td>
                    <td className="mx-num">{r.sales_price !== null ? <>{usd(r.sales_price)}<div className="mx-note">{r.sales_30d} in 30d</div></> : DASH}</td>
                    <td className="mx-num">{num(r.population)}</td>
                    <td className="mx-num mx-note">{r.age_days.scrydex !== null ? `SX ${r.age_days.scrydex}d` : ""}{r.age_days.scrydex !== null && r.age_days.pricecharting !== null ? " · " : ""}
                      {r.age_days.pricecharting !== null ? `PC ${r.age_days.pricecharting}d` : ""}</td>
                    <td>{r.flags.length ? r.flags.map((f) => <span key={f} className={`mx-tag${f === "sources disagree" || f === "no price" ? " mx-tag-warn" : ""}`}>{f}</span>) : <span className="mx-up">✓</span>}</td>
                  </tr>
                ))}
                {!rows.length && <tr><td colSpan={8} className="mx-muted">No cards with this flag.</td></tr>}
              </tbody>
            </table>
          </div>
          {data.total !== undefined && <Pager total={data.total} offset={data.offset ?? 0} limit={data.limit ?? 100} onPage={setQOffset} />}
        </>
      )}
    </MarketShell>
  );
}
