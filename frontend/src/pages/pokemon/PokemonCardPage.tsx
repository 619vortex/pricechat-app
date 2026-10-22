import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Card, Loading } from "../../components/ui";
import { marketApi, pokemonApi, type CardDetail, type CardMeta, type ChartRange, type ChartResp, type CompareResp, type Provenance, type RankRow, type Valuation, type CardEbay, type CardQuality } from "./api";
import { MarketChart, MultiLineChart, compareSeries, pricePopSeries, type ChartMode, type ChartStyle } from "./charts";
import { DASH, day, num, sourceLabel, usd, yen } from "./format";
import { play } from "./marketSound";
import { Change, Conf, GRADES, MarketShell, Pills, Skeleton, Tags, Thumb, Tilt, useCountUp, useWatch, fxNote, useFx, Confidence, usePageHead, usePublicMarket } from "./mx";
import { PsaLinkBox } from "./psa";
import { cardHead, cardJsonLd } from "../../website/seo";

const RANGES: [ChartRange, string][] = [["30d", "30D"], ["90d", "90D"], ["all", "ALL"]];
const RANGE_DAYS: Partial<Record<ChartRange, number>> = { "30d": 30, "90d": 90 };
const PERIODS = ["30d", "90d"] as const;
const PERIOD_LABEL: Record<(typeof PERIODS)[number], string> = { "30d": "30D", "90d": "90D" };
const TABS = [["overview", "Overview"], ["sales", "Sales"], ["grades", "Grades"], ["population", "Population"], ["history", "Historical Data"], ["about", "About"]] as const;
type Tab = (typeof TABS)[number][0];
const BASE_GRADES = ["PSA 10", "PSA 9", "PSA 8", "PSA 7"];

/** A card's own page, CoinGecko coin-page style: identity + key facts on the left, chart and tabs on the right. */
export function PokemonCardPage() {
  const id = Number(useParams().id);
  const [sp, setSp] = useSearchParams();
  const grade = sp.get("grade") ?? "PSA 10";
  const range = RANGES.some(([k]) => k === sp.get("range")) ? (sp.get("range") as ChartRange) : "90d";
  const tab = (sp.get("tab") as Tab | null) ?? "overview";
  const { data, failure, loading, setData } = useApi(() => pokemonApi.card(id));
  const [chart, setChart] = useState<ChartResp | null>(null);
  const [chartErr, setChartErr] = useState<unknown>(null);
  useEffect(() => {
    let live = true; // a quick grade / range switch must never show the previous request's chart
    setChart(null);
    marketApi.chart(id, grade, range).then((c) => { if (live) { setChart(c); setChartErr(null); } }).catch((e) => live && setChartErr(e));
    return () => { live = false; };
  }, [id, grade, range]);
  usePageHead(data && data.card.id === id ? { ...cardHead(data, grade), jsonLd: cardJsonLd(data, grade) } : null);
  const announced = useRef<string>("");
  useEffect(() => {
    // one soft blip when a card / grade opens: rising if its 30-day move is up, falling if down
    const move = chart?.summary.changes["30d"]?.value;
    const key = `${id}-${grade}`;
    if (!chart || announced.current === key || move === null || move === undefined || move === 0) return;
    announced.current = key;
    play(move > 0 ? "up" : "down");
  }, [chart, id, grade]);
  const set = (k: string, v: string | null, dflt: string) => {
    const next = new URLSearchParams(sp);
    if (v === null || v === dflt) next.delete(k);
    else next.set(k, v);
    setSp(next, { replace: k !== "tab" });
  };

  if (loading && !data) return <MarketShell><Skeleton rows={6} boxes /></MarketShell>;
  if (!data) return <MarketShell><ApiErrorBanner error={failure} /></MarketShell>;
  const c = data.card;
  const known = [...data.valuations, ...data.other_grades].filter((v) => v.market_price !== null || v.population !== null).map((v) => v.label);
  const grades = [...BASE_GRADES, ...GRADES.filter((g) => !BASE_GRADES.includes(g) && known.includes(g))];
  if (!grades.includes(grade)) grades.push(grade);

  return (
    <MarketShell crumbs={[["Markets", "/pokemon"], [c.set_name, `/pokemon?set=${encodeURIComponent(c.set_name)}`], [`${c.name} ${grade}`, null]]}>
      <div className="mx-coin">
        <Side detail={data} chart={chart} grade={grade} grades={grades} onGrade={(g) => set("grade", g, "PSA 10")} />
        <div className="mx-main">
          <div className="mx-tabs" role="tablist" aria-label="Card sections">
            {TABS.map(([k, label]) => (
              <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => { if (tab !== k) play("tick"); set("tab", k, "overview"); }}>{label}</button>
            ))}
          </div>
          <ApiErrorBanner error={chartErr} />
          {tab === "overview" && <Overview detail={data} chart={chart} grade={grade} range={range} onRange={(r) => set("range", r, "90d")} />}
          {tab === "sales" && <SalesTab detail={data} grade={grade} />}
          {tab === "grades" && <GradesTab detail={data} range={range} />}
          {tab === "population" && <PopulationTab detail={data} chart={chart} grade={grade} onChanged={() => void pokemonApi.card(id).then(setData)} />}
          {tab === "history" && <HistoryTab chart={chart} />}
          {tab === "about" && <AboutTab cardId={id} detail={data} />}
        </div>
      </div>
    </MarketShell>
  );
}

/** Search / share head ("Charizard 1st Edition · 1999 Base Set 4/102 · PSA 10 Price · Price Chat") and structured data:
 * built in website/seo.ts, which the backend's link previews match. */
export { cardHead };

// --- left column -----------------------------------------------------------------------------------------------------

function Side({ detail, chart, grade, grades, onGrade }: { detail: CardDetail; chart: ChartResp | null; grade: string; grades: string[]; onGrade: (g: string) => void }) {
  const c = detail.card;
  const isPublic = usePublicMarket();
  const { ids, toggle } = useWatch();
  const watched = ids.has(c.id);
  const s = chart?.summary;
  const [shared, setShared] = useState<"copied" | "failed" | null>(null);
  const share = async () => {
    const url = window.location.href;
    // phones: the system share sheet; elsewhere (or if it's unavailable): copy the link, and say when that fails
    if (typeof navigator.share === "function" && window.matchMedia?.("(pointer: coarse)").matches) {
      try {
        await navigator.share({ title: document.title, url });
        return;
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
      }
    }
    try {
      await navigator.clipboard.writeText(url);
      setShared("copied");
    } catch {
      setShared("failed");
    }
    window.setTimeout(() => setShared(null), 2000);
  };
  const fx = useFx();
  const priceShown = useCountUp(s?.market_price, 650);
  const ch = s?.changes;
  const lead = ch ? PERIODS.find((k) => ch[k].value !== null) ?? "30d" : "30d";
  const company = grade.split(" ")[0];
  return (
    <aside className="mx-side" aria-label="Card summary">
      {c.image_url ? <Tilt><img className="mx-coin-img" src={c.image_url} alt={`${c.name}, ${c.set_name} ${c.card_number ?? ""}`} /></Tilt>
        : <div className="mx-coin-img mx-coin-noimg">No image for this card yet (TCGdex has none for most early Japanese cards).</div>}
      <div>
        <h1 className="mx-coin-name">{c.name} <span className="mx-muted" style={{ fontWeight: 600 }}>{grade}</span></h1>
        <div className="mx-coin-sub">{c.year} {c.set_name}{c.card_number ? ` · ${c.card_number}` : ""} <Tags card={c} /></div>
        <p style={{ margin: "8px 0 0" }}><span className="mx-rankpill" title={`Rank by ${grade} market cap`}>{chart?.rank ? `Rank #${chart.rank}` : "Unranked"}</span></p>
      </div>
      <div className="mx-gradebar"><Pills label="Grade" value={grade} options={grades.map((g) => [g, g] as [string, string])} onChange={onGrade} /></div>
      <div>
        <div className="mx-coin-price">
          <strong aria-label={s ? usd(s.market_price) : "unknown"}>{s && priceShown !== null ? usd(priceShown) : DASH}</strong>
          {ch && <Change v={ch[lead].value} why={ch[lead].reason ?? undefined} />}
          {ch && ch[lead].value !== null && <span className="mx-muted" style={{ fontSize: 12 }}>{PERIOD_LABEL[lead]}</span>}
        </div>
        {fx && s && priceShown !== null && (
          <div className="mx-coin-sub mx-jpy-line" title={`${fxNote(fx)} · ${fx.source}`}>
            ≈ <b>{yen(priceShown, fx.rate)}</b> <span className="mx-muted">· {fxNote(fx)}</span>
          </div>
        )}
        {s?.price_source === "pricecharting" && s.reference ? (
          <div className="mx-coin-sub">
            <span className="mx-src mx-src-pc">PriceCharting</span> {grade} value (reference, not a sale; no price history) · as of {day(s.reference.as_of)}
          </div>
        ) : s?.price_source === "scrydex" && s.provider ? (
          <div className="mx-coin-sub">
            <span className="mx-src">Scrydex</span> {grade} market price (provider estimate, not a sale) · as of {day(s.provider.as_of)}
            {s.provider.low !== null && s.provider.high !== null && <> · range {usd(s.provider.low)}–{usd(s.provider.high)}</>}
          </div>
        ) : <div className="mx-coin-sub">Estimated {grade} market price <Conf c={s?.confidence} reasons={s?.confidence_reasons} /></div>}
      </div>
      <div className="mx-btnrow">
        {!isPublic && (
          <button type="button" className={`mx-btn${watched ? " is-on" : ""}`} aria-pressed={watched} onClick={() => void toggle(c.id).catch(() => undefined)}>
            {watched ? "★ On watchlist" : "☆ Add to watchlist"}
          </button>
        )}
        <button type="button" className="mx-btn" onClick={() => void share()} aria-live="polite">
          {shared === "copied" ? "Link copied" : shared === "failed" ? "Copy the address bar link" : "Share"}
        </button>
        <Link className="pf-shiny pf-sm" to={`/pokemon/portfolio/add?card=${c.id}`}>＋ Add to Portfolio</Link>
      </div>
      <dl className="mx-facts">
        <Fact k="Market cap" v={company === "RAW" ? "n/a (raw)" : usd(s?.market_cap)} hint="Estimated: price × known population" />
        <Fact k="Population" v={s?.population !== null && s?.population !== undefined ? num(s.population) : DASH}
              sub={s?.population_source ? `${sourceLabel(s.population_source).toUpperCase()} · ${day(s.population_as_of)}` : undefined} />
        <Fact k="30d sales volume" v={s?.sales_30d ? usd(s.volume_30d) : DASH} sub={s?.sales_30d ? `${s.sales_30d} verified sale${s.sales_30d === 1 ? "" : "s"}` : undefined} />
        <Fact k="Last verified sale" v={s?.last_verified_sale ? usd(s.last_verified_sale.price_usd) : DASH}
              sub={s?.last_verified_sale ? `${day(s.last_verified_sale.sale_date)} · ${sourceLabel(s.last_verified_sale.source)}` : undefined}
              href={s?.last_verified_sale?.url ?? undefined} />
        <Fact k="Highest sale" v={s?.highest_sale ? usd(s.highest_sale.price) : DASH} sub={s?.highest_sale ? day(s.highest_sale.at) : undefined} />
        <Fact k="Lowest sale" v={s?.lowest_sale ? usd(s.lowest_sale.price) : DASH} sub={s?.lowest_sale ? day(s.lowest_sale.at) : undefined} />
        <Fact k="Grading company" v={company === "RAW" ? "Ungraded" : company} />
        <Fact k="Grade" v={grade.split(" ").slice(1).join(" ")} />
        <Fact k="Set / number" v={`${c.set_name}${c.card_number ? ` · ${c.card_number}` : ""}`} />
        <Fact k="Language / edition" v={`${c.language}${c.edition !== "—" ? ` · ${c.edition}` : ""}`} />
        <Fact k="Price source" v={s?.price_source === "scrydex" ? "Scrydex (provider estimate)" : s?.price_source === "pricecharting" ? "PriceCharting (reference)"
              : s?.price_source === "sales" ? "Verified sales" : "No data"}
              sub={s?.price_source === "scrydex" && s.provider ? `${s.provider.variant} · updated ${day(s.provider.fetched_at)}` : undefined} />
        {s?.reference && s.price_source !== "pricecharting" && (
          <Fact k="PriceCharting" v={usd(s.reference.price)} hint="Second source, same exact grade"
                sub={`${s.reference.diff_pct !== null ? `shown price ${s.reference.diff_pct > 0 ? "+" : ""}${s.reference.diff_pct}% vs PriceCharting · ` : ""}as of ${day(s.reference.as_of)}${s.reference.disagree ? " · ⚠ sources disagree" : ""}`} />
        )}
        <Fact k="Data confidence" v={s?.price_source === "scrydex" || s?.price_source === "pricecharting" ? "Provider estimate" : s?.confidence ?? "No data"} sub={s?.confidence_reasons?.join(" · ") || undefined} />
      </dl>
    </aside>
  );
}

function Fact({ k, v, sub, hint, href }: { k: string; v: string; sub?: string; hint?: string; href?: string }) {
  return (
    <div>
      <dt title={hint}>{k}</dt>
      <dd>{href ? <a href={href} target="_blank" rel="noopener noreferrer">{v} ↗</a> : v}{sub && <small>{sub}</small>}</dd>
    </div>
  );
}

// --- overview ----------------------------------------------------------------------------------------------------------

function Overview({ detail, chart, grade, range, onRange }: { detail: CardDetail; chart: ChartResp | null; grade: string; range: ChartRange; onRange: (r: ChartRange) => void }) {
  const isPublic = usePublicMarket();
  const [mode, setMode] = useState<ChartMode>("price");
  const [style, setStyle] = useState<ChartStyle>("area");
  const reset = useRef<() => void>(() => undefined);
  const s = chart?.summary;
  return (
    <>
      <div className="mx-chartbar">
        <div className="mx-controls-left">
          <Pills label="Chart value" value={mode} options={[["price", "Price"], ["cap", "Market Cap"]]} onChange={setMode} />
          <Pills label="Chart style" value={style} options={[["area", "Area"], ["line", "Line"]]} onChange={setStyle} />
        </div>
        <div className="mx-controls-right">
          <Pills label="Time range" value={range} options={RANGES} onChange={onRange} />
          <button type="button" className="mx-btn" style={{ height: 30 }} onClick={() => reset.current()} title="Fit the whole range">Reset zoom</button>
        </div>
      </div>
      <MarketChart data={chart} mode={mode} style={style} onReset={(fn) => { reset.current = fn; }} />
      <Coverage chart={chart} mode={mode} />
      <div className="mx-legend">
        <span><i />{mode === "price" && chart?.line.length === 0 && chart?.provider_line?.length ? `Scrydex market price (${grade}, provider estimate)` : `Estimated ${mode === "cap" ? "market cap" : "valuation"} (${grade})`}</span>
        {chart?.line.some((p) => p.kind === "carried") && <span><i className="mx-k-carry" />Carried estimate (no sale that day)</span>}
        {mode === "price" && chart?.sales.some((x) => x.verified && !x.excluded) && <span><i className="mx-k-sale" />Completed verified sale</span>}
        {mode === "price" && chart?.sales.some((x) => !x.verified || x.excluded) && <span><i className="mx-k-excl" />Unverified / excluded sale</span>}
        {!!chart?.volume.length && <span><i className="mx-k-vol" />Daily verified volume</span>}
        {chart && !chart.sales.length && <span>No verified sales on record for {grade}</span>}
        <span>Scroll to zoom · drag to pan</span>
      </div>
      <section className="mx-statgrid" aria-label="Price changes">
        {PERIODS.map((k) => (
          <div key={k} className="mx-stat">
            <span>{PERIOD_LABEL[k]} change</span>
            <strong>{s ? <Change v={s.changes[k].value} why={s.changes[k].reason ?? undefined} src={s.changes[k].source} /> : DASH}</strong>
            {s && s.changes[k].value === null && <small>{s.changes[k].reason}</small>}
          </div>
        ))}
      </section>
      <SourceCompare detail={detail} grade={grade} />
      <LatestSales chart={chart} grade={grade} />
      <DataConfidence cardId={detail.card.id} />
      {!isPublic && <EbayListings cardId={detail.card.id} grade={grade} />}
      <div className="mx-two">
        <Card title="Price statistics">
          <dl className="mx-facts">
            <Fact k="Estimated price" v={usd(s?.market_price)} />
            <Fact k="30-day median sale" v={usd(s?.median_30d)} />
            <Fact k="Highest estimate" v={s?.highest_estimate ? usd(s.highest_estimate.price) : DASH} sub={s?.highest_estimate ? day(s.highest_estimate.day) : undefined} />
            <Fact k="Lowest estimate" v={s?.lowest_estimate ? usd(s.lowest_estimate.price) : DASH} sub={s?.lowest_estimate ? day(s.lowest_estimate.day) : undefined} />
            <Fact k="Verified sales used" v={s ? num(s.sales_count) : DASH} sub={s?.sales_count ? `window ${s.window_days} days` : undefined} />
          </dl>
        </Card>
        <AboutSnippet cardId={detail.card.id} />
      </div>
      <Related detail={detail} grade={grade} />
    </>
  );
}

/** What the chart is built from: source, first / last date, number of points. Never implies more data than exists. */
function Coverage({ chart, mode }: { chart: ChartResp | null; mode: ChartMode }) {
  if (!chart) return null;
  const providerOnly = mode === "price" && chart.line.length === 0 && (chart.provider_line?.length ?? 0) > 0;
  const pts = mode === "cap" ? chart.cap_line : providerOnly ? chart.provider_line ?? [] : chart.line;
  if (!pts.length) return null;
  const src = mode === "cap" ? "price × PSA population on record" : providerOnly ? "Scrydex daily market valuations (provider estimates)" : "Price Chat estimates from verified sales";
  const asOf = chart.summary.provider?.fetched_at;
  // the selected range is longer than the history on record: say so instead of implying a full range
  const want = RANGE_DAYS[chart.range];
  const have = Math.round((Date.parse(chart.as_of) - Date.parse(pts[0].t)) / 86_400_000);
  const short = want && pts.length > 1 && have < want - 7
    ? `${PERIOD_LABEL[chart.range as (typeof PERIODS)[number]] ?? chart.range} selected, but history on record covers only ${have} days` : "";
  return (
    <p className="mx-coverage">
      <b>{pts.length}</b> point{pts.length === 1 ? "" : "s"} · {day(pts[0].t)} → {day(pts[pts.length - 1].t)} · source: {src} · all prices in USD
      {providerOnly && asOf ? ` · Scrydex data fetched ${day(asOf)}` : ""}
      {pts.length === 1 ? " · only one valuation on record for this grade so far" : ""}
      {short ? ` · ${short}` : ""}
    </p>
  );
}

/** The same exact grade from every source, side by side: where they agree, where they don't. Nothing here is a sale. */
function SourceCompare({ detail, grade }: { detail: CardDetail; grade: string }) {
  const vals = [...detail.valuations, ...detail.other_grades].filter((v) => v.provider || v.reference || v.price_source === "sales");
  if (!vals.length) return null;
  const order = ["PSA 10", "PSA 9", "PSA 8", "PSA 7", "BGS 10", "BGS 9.5", "CGC 10"];
  vals.sort((a, b) => (order.indexOf(a.label) + 99 * +(order.indexOf(a.label) < 0)) - (order.indexOf(b.label) + 99 * +(order.indexOf(b.label) < 0)));
  return (
    <Card title="Price sources (same card, same exact grade)">
      <div style={{ overflowX: "auto" }}>
        <table className="mx-list mx-srcs">
          <thead><tr><th>Grade</th><th className="mx-num">Verified sales</th><th className="mx-num">Scrydex (range)</th><th className="mx-num">PriceCharting</th><th className="mx-num">Difference</th></tr></thead>
          <tbody>
            {vals.map((v) => {
              const sx = v.provider?.market ?? null;
              const pc = v.reference?.price ?? null;
              const diff = sx !== null && pc ? Math.round(((sx - pc) / pc) * 1000) / 10 : null;
              const bad = sx !== null && pc !== null && Math.abs(sx - pc) / Math.min(sx, pc) > 0.25;
              return (
                <tr key={v.label} className={v.label === grade ? "mx-row-current" : undefined}>
                  <td><b>{v.label}</b></td>
                  <td className="mx-num">{v.price_source === "sales" ? usd(v.market_price) : DASH}</td>
                  <td className="mx-num">{sx !== null ? <>{usd(sx)}{v.provider?.low !== null && v.provider?.high !== null && v.provider && <div className="mx-note">{usd(v.provider.low)}–{usd(v.provider.high)}</div>}</> : DASH}</td>
                  <td className="mx-num">{pc !== null ? usd(pc) : DASH}</td>
                  <td className="mx-num">{diff !== null ? <span className={bad ? "mx-down" : "mx-muted"} title="Scrydex vs PriceCharting">{bad ? "⚠ " : ""}{diff > 0 ? "+" : ""}{diff}%</span> : DASH}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mx-note" style={{ marginTop: 6 }}>Scrydex: daily market valuation per exact variant. PriceCharting: its published value for the exact product (as of the last sync).
        A difference over 25% is flagged. Neither is a sale; “Grade 9” style any-grader values from PriceCharting are not shown as PSA grades.</p>
    </Card>
  );
}

/** Completed, verified sales: shown apart from every estimate, never mixed into the valuation line's label. */
function LatestSales({ chart, grade }: { chart: ChartResp | null; grade: string }) {
  const sales = (chart?.sales ?? []).filter((s) => s.verified && !s.excluded).slice(-5).reverse();
  return (
    <Card title={`Latest verified ${grade} sales`}>
      {!chart ? <Loading /> : sales.length === 0 ? (
        <p className="mx-empty">No verified {grade} sales on record in this range. Prices shown above are estimates, not sales; completed sales appear here once imported or synced.</p>
      ) : (
        <table className="mx-list" style={{ minWidth: 0 }}>
          <thead><tr><th>Date</th><th className="mx-num">Price (USD)</th><th>Source</th></tr></thead>
          <tbody>
            {sales.map((s) => (
              <tr key={s.id}><td>{day(s.t)}</td><td className="mx-num"><b>{usd(s.v)}</b></td>
                <td>{s.url ? <a href={s.url} target="_blank" rel="noopener noreferrer">{sourceLabel(s.source)} ↗</a> : sourceLabel(s.source)}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function Related({ detail, grade }: { detail: CardDetail; grade: string }) {
  const c = detail.card;
  const [rows, setRows] = useState<RankRow[] | null>(null);
  useEffect(() => {
    Promise.all([
      marketApi.market({ q: c.pokemon_name, grade, limit: 12, era: "all" }),
      marketApi.market({ set: c.set_name, grade, limit: 12, era: "all" }),
    ]).then(([a, b]) => {
      const seen = new Set<number>([c.id]);
      setRows([...a.rows, ...b.rows].filter((r) => !seen.has(r.card.id) && seen.add(r.card.id)).slice(0, 10));
    }).catch(() => setRows([]));
  }, [c.id, c.pokemon_name, c.set_name, grade]);
  if (!rows?.length) return null;
  return (
    <section aria-label="Related cards">
      <h2 className="mx-h2">Related cards</h2>
      <div className="mx-related">
        {rows.map((r) => (
          <Link key={r.card.id} to={`/pokemon/cards/${r.card.id}?grade=${encodeURIComponent(grade)}`}>
            <Thumb card={r.card} />
            <span><b>{r.card.name}</b><br /><span className="mx-muted">{r.card.year} {r.card.set_name}</span><br />{usd(r.market_price)}</span>
          </Link>
        ))}
      </div>
    </section>
  );
}

// --- sales -------------------------------------------------------------------------------------------------------------

function SalesTab({ detail, grade }: { detail: CardDetail; grade: string }) {
  const isPublic = usePublicMarket();
  const [all, setAll] = useState<ChartResp | null>(null);
  useEffect(() => {
    void marketApi.chart(detail.card.id, grade, "all").then(setAll).catch(() => setAll(null));
  }, [detail.card.id, grade]);
  const sales = useMemo(() => [...(all?.sales ?? [])].reverse(), [all]);
  return (
    <>
      <Card title={`Historical ${grade} sales (completed, newest first)`}>
        {!all ? <Loading /> : sales.length === 0 ? (
          <p className="mx-empty">No completed {grade} sales on record for this card.{isPublic ? " Verified sales appear here as soon as they're recorded."
            : " Sales appear when imported (CSV) or synced from a connected sales source."}</p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="mx-list">
              <thead><tr><th>Date</th><th className="mx-num">Price (USD)</th><th>Source</th><th>Type</th><th>Status</th></tr></thead>
              <tbody>
                {sales.map((s) => (
                  <tr key={s.id}>
                    <td>{day(s.t)}</td>
                    <td className="mx-num"><b>{usd(s.v)}</b></td>
                    <td>{s.url ? <a href={s.url} target="_blank" rel="noopener noreferrer">{sourceLabel(s.source)} ↗</a> : sourceLabel(s.source)}</td>
                    <td>{s.sale_type ?? DASH}</td>
                    <td>{s.excluded ? <span className="mx-down">Excluded (outlier)</span> : s.verified ? <span className="mx-up">Verified</span> : <span className="mx-muted">Unverified: not priced</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <ActiveListings d={detail} />
    </>
  );
}

// --- grades ------------------------------------------------------------------------------------------------------------

function GradesTab({ detail, range }: { detail: CardDetail; range: ChartRange }) {
  const [cmp, setCmp] = useState<CompareResp | null>(null);
  useEffect(() => {
    void marketApi.compare(BASE_GRADES.map((g) => `${detail.card.id}:${g}`), range).then(setCmp).catch(() => setCmp(null));
  }, [detail.card.id, range]);
  const vals = [...detail.valuations, ...detail.other_grades];
  return (
    <>
      <Card title="Grade comparison: estimated price over time">
        <MultiLineChart series={compareSeries(cmp)} empty="No grade of this card has a price history yet (verified sales or Scrydex valuations)." />
        {cmp?.series.some((s) => s.source === "scrydex") && (
          <p className="mx-note" style={{ marginTop: 8 }}>Lines marked “Scrydex est.” are Scrydex's published market valuations for that grade (provider estimates, not sales).
            A grade with a single point has only today's valuation on record.</p>
        )}
      </Card>
      <Card title="Every grade">
        <div style={{ overflowX: "auto" }}>
          <table className="mx-list">
            <thead><tr><th>Grade</th><th className="mx-num">Price</th><th className="mx-num">30D</th><th className="mx-num">90D</th>
              <th className="mx-num">PriceCharting</th><th className="mx-num">Population</th><th className="mx-num">Market cap</th><th className="mx-num">Sales (30d)</th><th>Source</th><th /></tr></thead>
            <tbody>
              {vals.map((v) => <GradeRowView key={v.label} v={v} cardId={detail.card.id} />)}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

function GradeRowView({ v, cardId }: { v: Valuation; cardId: number }) {
  const [why, setWhy] = useState(false);
  return (
    <>
      <tr>
        <td><Link to={`/pokemon/cards/${cardId}?grade=${encodeURIComponent(v.label)}`}><b>{v.label}</b></Link></td>
        <td className="mx-num"><b>{usd(v.market_price)}</b></td>
        <td className="mx-num"><Change v={v.change_30d} why={v.change_reasons?.["30d"]} src={v.change_sources?.["30d"]} /></td>
        <td className="mx-num"><Change v={v.change_90d} why={v.change_reasons?.["90d"]} src={v.change_sources?.["90d"]} /></td>
        <td className="mx-num">{v.reference ? <span title={`as of ${v.reference.as_of}`}>{v.reference.disagree ? "⚠ " : ""}{usd(v.reference.price)}</span> : DASH}</td>
        <td className="mx-num">{v.graded ? num(v.population) : "n/a"}</td>
        <td className="mx-num">{v.graded ? usd(v.market_cap, true) : "n/a"}</td>
        <td className="mx-num">{v.stats.sales_30d || DASH}</td>
        <td>{v.price_source === "sales" ? (v.confidence ? <Conf c={v.confidence} reasons={v.stats.confidence_reasons} /> : "Sales")
          : v.price_source === "scrydex" ? <span className="mx-src">Scrydex</span> : v.price_source === "pricecharting" ? <span className="mx-src mx-src-pc">PriceCharting</span> : DASH}</td>
        <td>{!usePublicMarket() && (v.stats.sales_count > 0 || v.stats.last_sale !== null) && <button type="button" className="mx-clear" onClick={() => setWhy(!why)}>Why this price?</button>}</td>
      </tr>
      {why && <tr><td colSpan={11}><WhyPrice cardId={cardId} grade={v.label} onClose={() => setWhy(false)} /></td></tr>}
    </>
  );
}

// --- population -------------------------------------------------------------------------------------------------------

function PopulationTab({ detail, chart, grade, onChanged }: { detail: CardDetail; chart: ChartResp | null; grade: string; onChanged: () => void }) {
  const isPublic = usePublicMarket();
  const companies = Object.keys(detail.population_totals);
  return (
    <>
      <div className="mx-two">
        <Card title="Population by grade (card-wide)">
          {detail.population.length === 0 ? (
            <p className="mx-empty">{isPublic ? "No grading population on record for this card yet. Market cap needs one, so it stays blank rather than guessed."
              : <>No population on record. Link a PSA cert of this card below: PSA's SpecID brings in its card-wide population per grade
              (once PSA has approved API access for the key).</>}</p>
          ) : companies.map((co) => (
            <div key={co} style={{ marginBottom: 10, overflowX: "auto" }}>
              <table className="mx-list" style={{ minWidth: 0 }}>
                <thead><tr><th>{co} grade</th><th className="mx-num">Population</th><th>Source</th><th>As of</th></tr></thead>
                <tbody>
                  {detail.population.filter((p) => p.grading_company === co).map((p) => (
                    <tr key={p.grade}><td>{co} {p.grade}</td><td className="mx-num"><b>{num(p.count)}</b></td><td>{sourceLabel(p.source).toUpperCase()}</td><td>{day(p.as_of)}</td></tr>
                  ))}
                  <tr><td><b>Total graded (known)</b></td><td className="mx-num"><b>{num(detail.population_totals[co])}</b></td><td /><td /></tr>
                </tbody>
              </table>
            </div>
          ))}
        </Card>
        <Card title={`${grade}: price versus population`}>
          <MultiLineChart small series={pricePopSeries(chart)}
                          empty="Needs population history (reports over time) and a price history for this grade. Neither is estimated." />
        </Card>
      </div>
      {!isPublic && <PsaLinkBox card={detail.card} onChanged={onChanged} />}
    </>
  );
}

// --- historical data ------------------------------------------------------------------------------------------------

function HistoryTab({ chart }: { chart: ChartResp | null }) {
  if (!chart) return <Loading />;
  const s = chart.summary;
  return (
    <Card title={`${chart.grade} price history by period`}>
      <div style={{ overflowX: "auto" }}>
        <table className="mx-list">
          <thead><tr><th>Period</th><th className="mx-num">Estimate at start</th><th>Start date</th><th className="mx-num">Estimate now</th><th className="mx-num">Change</th><th>Note</th></tr></thead>
          <tbody>
            {PERIODS.map((k) => {
              const p = s.changes[k];
              return (
                <tr key={k}>
                  <td><b>{PERIOD_LABEL[k]}</b>{p.source && <div className="mx-note">{p.source === "sales" ? "verified sales" : p.source === "scrydex" ? "Scrydex trend" : "Scrydex history"}</div>}</td><td className="mx-num">{usd(p.base)}</td><td>{p.base_day ? day(p.base_day) : DASH}</td>
                  <td className="mx-num">{usd(s.market_price)}</td><td className="mx-num"><Change v={p.value} /></td><td className="mx-muted">{p.reason ?? ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <h3 className="mx-h2" style={{ marginTop: 14 }}>Estimate points in this range ({chart.range})</h3>
      {chart.line.length === 0 ? <p className="mx-empty">No estimate points: no verified sales for this grade yet.</p> : (
        <div style={{ overflowX: "auto", maxHeight: 360 }}>
          <table className="mx-list">
            <thead><tr><th>Date</th><th className="mx-num">Estimated price</th><th>Point</th><th>Confidence</th></tr></thead>
            <tbody>
              {[...chart.line].reverse().map((p) => (
                <tr key={p.t}><td>{day(p.t)}</td><td className="mx-num">{usd(p.v)}</td>
                  <td>{p.kind === "sale_day" ? "Day with verified sales" : p.kind === "carried" ? "Carried (no sale)" : "Today's estimate"}</td>
                  <td><Conf c={p.confidence} /></td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

// --- about --------------------------------------------------------------------------------------------------------------

function useMeta(cardId: number) {
  const [m, setM] = useState<CardMeta | null>(null);
  useEffect(() => {
    void marketApi.meta(cardId).then(setM).catch(() => setM({ source: null, meta: null, reason: "Card details couldn't be loaded." }));
  }, [cardId]);
  return m;
}

function describe(d: CardDetail, m: CardMeta["meta"]): string {
  const c = d.card;
  let s = `${c.name} is card ${c.card_number ?? "(unnumbered)"} from the ${c.year} ${c.language} ${c.set_name}${/\bset$/i.test(c.set_name) ? "" : " set"}${c.edition !== "—" ? `, ${c.edition} printing` : ""}.`;
  if (m?.illustrator) s += ` Illustrated by ${m.illustrator}.`;
  if (c.rarity ?? m?.rarity) s += ` Rarity: ${c.rarity ?? m?.rarity}.`;
  return s;
}

function AboutSnippet({ cardId }: { cardId: number }) {
  const m = useMeta(cardId);
  return (
    <Card title="About this card">
      {!m ? <Loading /> : !m.meta ? <p className="mx-empty">{m.reason}</p> : (
        <div className="mx-about">
          {m.meta.description && <p>{m.meta.description}</p>}
          <dl className="mx-facts">
            {m.meta.illustrator && <Fact k="Illustrator" v={m.meta.illustrator} />}
            {m.meta.hp && <Fact k="HP" v={String(m.meta.hp)} />}
            {m.meta.types && <Fact k="Type" v={m.meta.types.join(", ")} />}
            {m.meta.stage && <Fact k="Stage" v={m.meta.stage} />}
            {m.meta.dexId && <Fact k="Pokédex" v={m.meta.dexId.map((n) => `#${n}`).join(", ")} />}
          </dl>
          <p className="mx-note">Card details: TCGdex ({m.language}). Catalog data only.</p>
        </div>
      )}
    </Card>
  );
}

function AboutTab({ cardId, detail }: { cardId: number; detail: CardDetail }) {
  const isPublic = usePublicMarket();
  const m = useMeta(cardId);
  const meta = m?.meta ?? null;
  const c = detail.card;
  return (
    <div className="mx-two">
      <Card title={`About ${c.name}`}>
        <div className="mx-about">
          <p>{describe(detail, meta)}</p>
          {meta?.description && <p>{meta.description}</p>}
          {meta?.effect && <p>{meta.effect}</p>}
          {meta?.abilities?.map((a) => <div key={a.name} className="mx-attack"><b>{a.type ?? "Ability"}: {a.name}</b><br />{a.effect}</div>)}
          {meta?.attacks?.map((a) => (
            <div key={a.name} className="mx-attack">
              <b>{a.name}</b>{a.damage !== undefined && <span className="mx-muted"> · {a.damage}</span>}
              {a.cost?.length ? <span className="mx-muted"> · cost {a.cost.join(", ")}</span> : null}
              {a.effect && <><br />{a.effect}</>}
            </div>
          ))}
          {!meta && m && <p className="mx-empty">{m.reason}</p>}
        </div>
      </Card>
      <Card title="Identity & sources">
        <dl className="mx-facts">
          <Fact k="Set" v={`${c.set_name}${c.set_code ? ` (${c.set_code})` : ""}`} />
          <Fact k="Number" v={c.card_number ?? DASH} />
          <Fact k="Year / language" v={`${c.year} · ${c.language}`} />
          <Fact k="Edition / variant" v={[c.edition !== "—" ? c.edition : null, c.variant || null].filter(Boolean).join(" · ") || DASH} />
          <Fact k="Rarity" v={c.rarity ?? DASH} />
          {meta?.illustrator && <Fact k="Illustrator" v={meta.illustrator} />}
          {meta?.hp && <Fact k="HP" v={String(meta.hp)} />}
          {meta?.types && <Fact k="Type" v={meta.types.join(", ")} />}
          {meta?.weaknesses && <Fact k="Weakness" v={meta.weaknesses.map((w) => `${w.type} ${w.value ?? ""}`).join(", ")} />}
          {meta?.retreat !== undefined && <Fact k="Retreat cost" v={String(meta.retreat)} />}
          <Fact k="Catalog & image" v={m?.source === "tcgdex" ? "TCGdex" : c.image_source ?? DASH} href={m?.url ?? undefined} />
          {(c.psa_spec || !isPublic) && <Fact k="PSA spec" v={c.psa_spec ?? "Not linked"} />}
        </dl>
      </Card>
    </div>
  );
}

// --- kept from the previous card page ---------------------------------------------------------------------------------

/** WHY IS THIS PRICE $X? Sales used (weights) and left out (reasons), the algorithm and the confidence. */
function WhyPrice({ cardId, grade, onClose }: { cardId: number; grade: string; onClose: () => void }) {
  const [p, setP] = useState<Provenance | null>(null);
  useEffect(() => {
    void pokemonApi.provenance(cardId, grade).then(setP).catch(() => setP(null));
  }, [cardId, grade]);
  return (
    <div className="pm-why" role="dialog" aria-label={`Why is the ${grade} price what it is?`}>
      <div className="pm-why-head">
        <strong>Why is {grade} {p ? usd(p.market_price) : "…"}?</strong>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onClose}>Close</button>
      </div>
      {!p ? <p className="muted pm-small">Loading…</p> : (
        <>
          <p className="muted pm-small">{p.algorithm} · window {p.window_days} days · computed {day(p.computed_at)}</p>
          <p className="pm-small">Confidence: <strong>{p.confidence.level ?? DASH}</strong> {p.confidence.reasons.length ? `(${p.confidence.reasons.join(" · ")})` : ""}</p>
          <table className="mx-list" style={{ minWidth: 0 }}>
            <thead><tr><th>Used</th><th>Date</th><th className="mx-num">Price</th><th>Source</th><th className="mx-num">Weight / reason</th></tr></thead>
            <tbody>
              {p.included.map((r) => (
                <tr key={`i${r.sale_id}`}><td>✓</td><td>{day(r.sale_date)}</td><td className="mx-num">{usd(r.price)}</td><td>{sourceLabel(r.source)}</td><td className="mx-num">{r.weight}</td></tr>
              ))}
              {p.excluded.map((r) => (
                <tr key={`x${r.sale_id}`}><td>✕</td><td>{day(r.sale_date)}</td><td className="mx-num">{usd(r.price)}</td><td>{sourceLabel(r.source)}</td><td className="mx-num">{r.reason}</td></tr>
              ))}
              {!p.included.length && !p.excluded.length && <tr><td colSpan={5} className="mx-muted">No sales recorded for this grade.</td></tr>}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

/** Asking prices on marketplaces: shown for context, never sales, never in the price. */
/** The card's PSA 10 data-quality checks, its Top 100 status, and the market cap worked out in the open. */
export function DataConfidence({ cardId }: { cardId: number }) {
  const [q, setQ] = useState<CardQuality | null>(null);
  useEffect(() => {
    let live = true;
    marketApi.cardQuality(cardId).then((r) => live && setQ(r)).catch(() => live && setQ(null));
    return () => { live = false; };
  }, [cardId]);
  if (!q || !q.quality) return null;
  const m = q.market_cap;
  return (
    <Card title="Data confidence (PSA 10)">
      <div className="mx-dc-head">
        <Confidence q={q.quality} />
        <span>{q.top100_rank ? <>Top 100 rank <b>#{q.top100_rank}</b></> : !q.in_scope ? "Modern card: the Top 100 is vintage (1996–2003) only"
          : q.quality.eligible ? `Eligible, outside the Top 100 by market cap` : "Not in the Top 100 until the checks below pass"}</span>
        <span className="mx-muted">score {q.quality.score}/100 · {q.quality.history_points} price observations (needs {q.min_history})</span>
      </div>
      <ul className="mx-checks">
        {q.quality.checks.map((c) => (
          <li key={c.code} className={c.ok === true ? "is-ok" : c.ok === false ? "is-bad" : "is-na"}>
            <span aria-hidden="true">{c.ok === true ? "✓" : c.ok === false ? "✗" : "–"}</span> <b>{c.label}</b> <span className="mx-muted">{c.detail}</span>
          </li>
        ))}
      </ul>
      {m && (
        <p className="mx-audit">
          <b>Market cap</b> = {usd(m.price)} PSA 10 price ({m.price_source === "scrydex" ? "Scrydex estimate" : m.price_source === "sales" ? "verified sales" : m.price_source}
          {m.price_as_of ? `, ${day(m.price_as_of)}` : ""}) × {m.population !== null ? num(m.population) : "unknown"} PSA 10 population
          {m.population_source ? ` (${sourceLabel(m.population_source)}${m.population_as_of ? `, ${day(m.population_as_of)}` : ""})` : ""} = <b>{m.market_cap !== null ? usd(m.market_cap) : "—"}</b>
        </p>
      )}
    </Card>
  );
}

/** Live eBay listings for this exact card + grade (Browse API). Asking prices, never sales; sold data only with eBay's
 * Marketplace Insights approval. */
export function EbayListings({ cardId, grade }: { cardId: number; grade: string }) {
  const [d, setD] = useState<CardEbay | null>(null);
  const [busy, setBusy] = useState(false);
  async function load(refresh = false) {
    setBusy(true);
    try {
      setD(await marketApi.cardEbay(cardId, grade, refresh));
    } catch {
      setD(null);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => { void load(); }, [cardId, grade]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Card title={`eBay listings · ${grade} (asking prices, not sales)`}>
      {!d ? (busy ? <Loading /> : <p className="mx-note">eBay is unavailable right now.</p>) : !d.connected ? (
        <p className="mx-note">eBay isn't connected: add the eBay App ID and Cert ID under Settings › API keys. {d.message}</p>
      ) : (
        <>
          <div className="mx-ebay-head">
            <span className="mx-muted">Search: “{d.query.split(" -")[0]}”{d.counts ? ` · ${d.counts.seen} results, ${d.counts.matched} matched exactly, ${d.counts.review} for review, ${d.counts.rejected} other cards` : ""}</span>
            <button type="button" className="mx-customise" disabled={busy} onClick={() => void load(true)}>{busy ? "Searching…" : "Refresh"}</button>
          </div>
          {d.message && <p className="mx-banner">{d.message}</p>}
          {!d.listings.length ? <p className="mx-note">No active eBay listing matches this exact card and grade right now.</p> : (
            <ul className="mx-ebay">
              {d.listings.map((l) => (
                <li key={l.id}>
                  {l.image_url ? <img src={l.image_url} alt="" loading="lazy" width={64} height={64} /> : <span className="mx-ebay-noimg" />}
                  <div>
                    <a href={l.url ?? "#"} target="_blank" rel="noopener noreferrer">{l.title}</a>
                    <div className="mx-note">{l.seller ? `Seller ${l.seller}` : "Seller —"}{l.listing_type ? ` · ${l.listing_type.replace(/_/g, " ")}` : ""}{l.condition ? ` · ${l.condition}` : ""} · seen {day(l.observed_at)}</div>
                  </div>
                  <strong>{l.currency === "USD" ? usd(l.price) : `${(l.price / 100).toLocaleString("en-US")} ${l.currency}`}</strong>
                  {l.url && <a className="mx-ebay-go" href={l.url} target="_blank" rel="noopener noreferrer">View on eBay ↗</a>}
                </li>
              ))}
            </ul>
          )}
          <p className="mx-note">{d.sold === null ? "Sold-history access is checked on the first search."
            : d.sold.access ? `Sold history: eBay Marketplace Insights is granted · ${d.sold.verified_sales ?? 0} verified sales imported from this search.`
            : d.sold.message ?? "Sold history unavailable: eBay has not granted Marketplace Insights to these keys. Asking prices are never used as sales."}</p>
        </>
      )}
    </Card>
  );
}

export function ActiveListings({ d }: { d: CardDetail }) {
  if (!d.reference_prices.length) return null;
  return (
    <div className="mx-two">
      {d.reference_prices.length > 0 && (
        <Card title="Reference prices (internal, not sales)">
          <ul className="pm-sales-mini">
            {d.reference_prices.map((r) => (
              <li key={`${r.provider}-${r.provider_grade}-${r.as_of}`}>
                <span>{sourceLabel(r.provider)} · {r.provider_grade}</span>
                <span className="muted">{r.grade ?? "any grader"}</span>
                <strong>{usd(r.price)}</strong>
                <span className="muted">as of {day(r.as_of)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
