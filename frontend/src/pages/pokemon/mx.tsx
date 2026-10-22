/** Market V2 building blocks: the CoinGecko-style shell (global stats bar, market nav, search, breadcrumbs) and the small
 * pieces every view shares (sparkline, change cell, card cell, watch star, grade selector). Unknown is always "—". */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Link, NavLink } from "react-router-dom";
import { marketApi, pokemonApi, type FxRate, type MarketResp, type PCard } from "./api";
import { SearchBox } from "./components";
import { DASH, num, usd } from "./format";
import "./pokemon.css";
import "./market.css";
import "./market-motion.css";
import { play, useMarketSound } from "./marketSound";

export const GRADES = ["PSA 10", "PSA 9", "PSA 8", "PSA 7", "BGS 10", "BGS 9.5", "CGC 10", "RAW NM"];

// --- public website mode ----------------------------------------------------------------------------------------------

/** True on the public website (pricechat.live/pokemon): owner-only controls (watchlist, syncs, data sources, Pulse
 * settings, eBay lookups, PSA links) are hidden; the server refuses them anyway. */
const PublicCtx = createContext(false);
export const PublicMarketProvider = PublicCtx.Provider;
export function usePublicMarket(): boolean {
  return useContext(PublicCtx);
}

// --- watchlist (shared across views) -------------------------------------------------------------------------------

const WatchCtx = createContext<{ ids: Set<number>; toggle: (id: number) => Promise<void> }>({ ids: new Set(), toggle: async () => {} });

export function WatchProvider({ children }: { children: ReactNode }) {
  const [ids, setIds] = useState<Set<number>>(new Set());
  const isPublic = usePublicMarket();
  useEffect(() => {
    if (isPublic) return; // the watchlist is the owner's
    void pokemonApi.watchlist().then((w) => setIds(new Set(w.cards.map((c) => c.card.id)))).catch(() => undefined);
  }, [isPublic]);
  const toggle = useCallback(async (id: number) => {
    const on = !ids.has(id);
    const r = await pokemonApi.star(id, on);
    setIds((prev) => {
      const next = new Set(prev);
      if (r.watched) next.add(id);
      else next.delete(id);
      return next;
    });
  }, [ids]);
  const value = useMemo(() => ({ ids, toggle }), [ids, toggle]);
  return <WatchCtx.Provider value={value}>{children}</WatchCtx.Provider>;
}

export function useWatch() {
  return useContext(WatchCtx);
}

export function Star({ id, label }: { id: number; label: string }) {
  if (usePublicMarket()) return null;
  return <StarButton id={id} label={label} />;
}

function StarButton({ id, label }: { id: number; label: string }) {
  const { ids, toggle } = useWatch();
  const on = ids.has(id);
  const [pop, setPop] = useState(false);
  return (
    <button type="button" className={`mx-star${on ? " is-on" : ""}${pop ? " is-popping" : ""}`} aria-pressed={on}
            aria-label={`${on ? "Remove" : "Add"} ${label} ${on ? "from" : "to"} watchlist`} onAnimationEnd={() => setPop(false)}
            title={on ? "On your watchlist" : "Add to watchlist"} onClick={(e) => { e.stopPropagation(); if (!on) { setPop(true); play("star"); } else play("tick"); void toggle(id); }}>
      {on ? "★" : "☆"}
    </button>
  );
}

// --- shell ---------------------------------------------------------------------------------------------------------

const NAV: [string, string][] = [
  ["/pokemon", "Markets"], ["/pokemon/vault", "◆ Vault"], ["/pokemon/marketplace", "🛒 Marketplace"], ["/pokemon/trending", "Trending"], ["/pokemon/gainers", "Top Gainers"], ["/pokemon/losers", "Top Losers"],
  ["/pokemon/recent", "Recently Sold"], ["/pokemon/years", "Years"], ["/pokemon/sets", "Sets"], ["/pokemon/grades", "Grades"],
  ["/pokemon/portfolio", "✨ Portfolio"], ["/pokemon/portfolios", "Collectors"], ["/pokemon/watchlist", "Watchlist"],
  ["/pokemon/quality", "Data Quality"], ["/pokemon/sources", "Data Sources"],
];

/** The page frame of every market view: global stats, market nav + search, breadcrumbs. */
// --- light / dark ------------------------------------------------------------------------------------------------------

export type MarketTheme = "dark" | "light";
const THEME_KEY = "pc-market-theme";
const ThemeCtx = createContext<{ theme: MarketTheme; setTheme: (t: MarketTheme) => void }>({ theme: "dark", setTheme: () => undefined });

function readTheme(): MarketTheme {
  try {
    const v = window.localStorage.getItem(THEME_KEY);
    if (v === "light" || v === "dark") return v;
  } catch {
    /* per-viewer convenience only */
  }
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

/** The market's colour theme (charts redraw when it changes). */
export function useMarketTheme(): MarketTheme {
  return useContext(ThemeCtx).theme;
}

export function ThemeToggle() {
  const { theme, setTheme } = useContext(ThemeCtx);
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button type="button" className="mx-soundbtn mx-themebtn" aria-label={`Switch to ${next} mode`} title={`${next === "light" ? "Light" : "Dark"} mode`}
            onClick={() => { play("tick"); setTheme(next); }}>
      <span aria-hidden="true">{theme === "dark" ? "☀" : "☾"}</span>
    </button>
  );
}

// owner-only pages, and the simulated marketplace (retired from the public site)
const OWNER_ONLY = new Set(["/pokemon/watchlist", "/pokemon/quality", "/pokemon/sources", "/pokemon/marketplace"]);

/** `compact`: member pages (portfolio, marketplace) drop the market totals and card search on phones: their own content first. */
export function MarketShell({ crumbs, children, compact = false }: { crumbs?: [string, string | null][]; children: ReactNode; compact?: boolean }) {
  const isPublic = usePublicMarket();
  const [theme, setThemeState] = useState<MarketTheme>(readTheme);
  const setTheme = useCallback((t: MarketTheme) => {
    setThemeState(t);
    try {
      window.localStorage.setItem(THEME_KEY, t);
    } catch {
      /* per-viewer convenience only */
    }
  }, []);
  const ctx = useMemo(() => ({ theme, setTheme }), [theme, setTheme]);
  return (
    <ThemeCtx.Provider value={ctx}>
    <WatchProvider>
      <div className={`page mx${compact ? " mx-compact" : ""}`} data-theme={theme}>
        <CardPreviewLayer />
        <div className="mx-page">
        <GlobalStats />
        <div className="mx-top">
          <Link to="/pokemon" className="mx-brand"><span className="mx-brand-dot" aria-hidden="true" />Pokémon Market Cap</Link>
          <nav className="mx-nav" aria-label="Market">
            {NAV.filter(([to]) => !isPublic || !OWNER_ONLY.has(to)).map(([to, label]) => <NavLink key={to} to={to} end={to === "/pokemon"}>{label}</NavLink>)}
          </nav>
          <SearchBox />
          <ThemeToggle />
          <SoundToggle />
        </div>
        {crumbs && crumbs.length > 0 && (
          <nav className="mx-crumbs" aria-label="Breadcrumb">
            {crumbs.map(([label, to], i) => (
              <span key={`${label}-${i}`}>
                {i > 0 && <span aria-hidden="true">› </span>}
                {to ? <Link to={to}>{label}</Link> : <span aria-current="page">{label}</span>}
              </span>
            ))}
          </nav>
        )}
        {children}
        </div>
      </div>
    </WatchProvider>
    </ThemeCtx.Provider>
  );
}

/** The thin stats strip above everything (CoinGecko's global bar). PSA 10 totals; "—" when unknown. */
function GlobalStats() {
  const [m, setM] = useState<MarketResp | null>(null);
  useEffect(() => {
    void marketApi.market({ grade: "PSA 10", limit: 1, era: "all" }).then(setM).catch(() => setM(null));
  }, []);
  const h = m?.hero;
  return (
    <div className="mx-stats" aria-label="Market totals">
      <span>Cards:<b>{h ? num(h.cards_tracked) : DASH}</b></span>
      <span className="mx-sep" />
      <span>PSA 10 market cap:<b>{h ? usd(h.total_market_cap, true) : DASH}</b>{h && h.market_cap_change_30d !== null && <> <Change v={h.market_cap_change_30d} /> <small>30D</small></>}</span>
      <span className="mx-sep" />
      <span>PSA 10 verified volume (30D):<b>{h && h.grade_sales_30d ? usd(h.volume_30d, true) : DASH}</b></span>
      <span className="mx-sep" />
      <span>Verified sales (30d):<b>{h ? num(h.all_sales_30d) : DASH}</b></span>
      <span className="mx-sep" />
      <span>PSA 10 population:<b>{h && h.population_total !== null ? num(h.population_total) : DASH}</b></span>
    </div>
  );
}

// --- cells ---------------------------------------------------------------------------------------------------------

/** ▲ 12.4% green / ▼ 3.1% red / — grey. `title` explains a missing value. */
export const CHANGE_SOURCE: Record<string, string> = {
  sales: "Verified sales: today's estimate vs the estimate at the start of the period",
  scrydex: "Scrydex's published trend for this period (provider estimate, not sales)",
  scrydex_history: "Scrydex's valuation today vs its recorded valuation at the start of the period",
};

/** A movement, or "—" with the reason it is unknown (never 0% for missing data). `src` names where the number came from. */
export function Change({ v, why, src }: { v: number | null | undefined; why?: string; src?: string | null }) {
  if (v === null || v === undefined) return <span className="mx-flat" title={why ?? "No movement on record for this period"}>{DASH}</span>;
  const cls = v > 0 ? "mx-up" : v < 0 ? "mx-down" : "mx-flat";
  return (
    <span className={cls} title={src ? CHANGE_SOURCE[src] ?? src : undefined}>
      {v !== 0 && <span className="mx-caret" aria-hidden="true">{v > 0 ? "▲" : "▼"}</span>}
      {Math.abs(v).toFixed(1)}%
      <span className="sr-only">{v > 0 ? " up" : v < 0 ? " down" : ""}</span>
    </span>
  );
}

let sparkId = 0;

const SPARK_SOURCE: Record<string, string> = { sales: "verified-sales estimates", scrydex: "Scrydex valuations (provider estimates)" };
const shortDay = (sec: number) => new Date(sec * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

/** Table sparkline (last 90 days): thin straight segments between the REAL observations (no smoothing, nothing carried
 * forward, repeated or interpolated). Few observations -> each one is marked, so a sparse line never looks like a detailed
 * one. Green when the last observation is above the first, red when below, grey when equal; fewer than two observations
 * -> no chart, with the reason. The hover title names the source and the actual date range covered. */
export function Sparkline({ points, height = 40, carried = false, source, note }: {
  points: [number, number][] | null | undefined; width?: number; height?: number; carried?: boolean; source?: string; note?: string;
}) {
  const [gid] = useState(() => `mx-sp-${++sparkId}`);
  if (!points || points.length < 2)
    return <span className="mx-spark-empty" title={note ? `No chart: ${note}` : "Fewer than two price observations in the last 90 days: no chart is drawn"}>{DASH}</span>;
  const W = 120, H = 40, pad = 3;
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const sx = (x: number) => (x1 === x0 ? W / 2 : ((x - x0) / (x1 - x0)) * (W - 2 * pad) + pad);
  const sy = (y: number) => (y1 === y0 ? H / 2 : H - pad - ((y - y0) / (y1 - y0)) * (H - 2 * pad));
  const pts = points.map((p) => [sx(p[0]), sy(p[1])] as [number, number]);
  const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`).join("");
  const first = ys[0], last = ys[ys.length - 1];
  const dir = last > first ? "up" : last < first ? "down" : "flat";
  const color = dir === "up" ? "var(--up)" : dir === "down" ? "var(--down)" : "var(--muted)";
  const area = `${d}L${pts[pts.length - 1][0].toFixed(2)},${H}L${pts[0][0].toFixed(2)},${H}Z`;
  const sparse = points.length <= 6;
  const change = first ? ((last - first) / first) * 100 : 0;
  return (
    <svg className={`mx-spark mx-spark-${dir}`} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" width="100%" height={height} role="img"
         aria-label={`${dir === "flat" ? "Flat" : dir === "up" ? "Up" : "Down"} ${Math.abs(change).toFixed(1)}% across ${points.length} price observations${carried ? " (no sale in the window: last estimate carried)" : ""}`}>
      <title>{`${points.length} observations · ${shortDay(x0)} → ${shortDay(x1)}${source ? ` · ${SPARK_SOURCE[source] ?? source}` : ""}`
}</title>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.16} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <path className="mx-spark-area" d={area} fill={`url(#${gid})`} stroke="none" />
      <path className="mx-spark-line" d={d} fill="none" stroke={color} strokeWidth={1.3} strokeLinecap="round" strokeLinejoin="round"
            vectorEffect="non-scaling-stroke" pathLength={carried ? undefined : 1} strokeDasharray={carried ? "3 3" : undefined} />
      {sparse && (
        <path className="mx-spark-obs" d={pts.map(([x, y]) => `M${x.toFixed(2)},${y.toFixed(2)}l0,0`).join("")} stroke={color} strokeWidth={3.2}
              strokeLinecap="round" vectorEffect="non-scaling-stroke" fill="none" />
      )}
    </svg>
  );
}

export function Thumb({ card, className }: { card: PCard; className?: string }) {
  return card.image_url
    ? (
      <span className="mx-thumbwrap" onMouseEnter={(e) => showPreview(card.image_url!, e.currentTarget)} onMouseLeave={hidePreview}>
        <img className={className} src={card.image_url} alt="" loading="lazy" width={30} height={42} />
      </span>
    )
    : <span className={`mx-noimg ${className ?? ""}`} aria-hidden="true">no<br />image</span>;
}

export function Tags({ card }: { card: PCard }) {
  return (
    <>
      {card.edition !== "—" && <span className={`mx-tag${card.edition === "1st Edition" ? " mx-tag-first" : ""}`}>{card.edition === "1st Edition" ? "1st Ed" : card.edition}</span>}
      {card.language !== "English" && <span className="mx-tag mx-tag-jp">{card.language === "Japanese" ? "JP" : card.language}</span>}
      {card.variant && <span className="mx-tag">{card.variant}</span>}
    </>
  );
}

/** Thumbnail, name (link), "Base Set · 1999 · 4/102" and edition / language chips. `inline` = one line (rankings table). */
export function CardCell({ card, extra, inline = false }: { card: PCard; extra?: ReactNode; inline?: boolean }) {
  const meta = `${card.set_name} · ${card.year}${card.card_number ? ` · ${card.card_number}` : ""}`;
  if (inline)
    return (
      <span className="mx-cardcell is-inline" title={`${card.name} · ${meta}`}>
        <Thumb card={card} />
        <span className="mx-cardtext">
          <Link to={`/pokemon/cards/${card.id}`} className="mx-cardname">{card.name}</Link>
          <span className="mx-cardsub">
            <span className="mx-cardmeta">{meta}</span>
            <Tags card={card} />
            {extra}
          </span>
        </span>
      </span>
    );
  return (
    <span className="mx-cardcell">
      <Thumb card={card} />
      <span className="mx-cardtext">
        <Link to={`/pokemon/cards/${card.id}`} className="mx-cardname" title={card.name}>{card.name}</Link>
        <span className="mx-cardsub">
          <span>{card.set_name} · {card.year}{card.card_number ? ` · ${card.card_number}` : ""}</span>
          <Tags card={card} />
          {extra}
        </span>
      </span>
    </span>
  );
}

export function GradeSelect({ value, onChange, label = "Grade", className = "mx-select", options = GRADES }: {
  value: string; onChange: (g: string) => void; label?: string; className?: string; options?: string[];
}) {
  return (
    <select className={className} value={value} aria-label={label} onChange={(e) => { play("tick"); onChange(e.target.value); }} onClick={(e) => e.stopPropagation()}>
      {options.map((g) => <option key={g} value={g}>{g}</option>)}
    </select>
  );
}

export function Pills<T extends string>({ value, options, onChange, label }: { value: T; options: [T, string][]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="mx-pills" role="group" aria-label={label}>
      {options.map(([v, text]) => (
        <button key={v} type="button" className={`mx-pill${v === value ? " is-on" : ""}`} aria-pressed={v === value} onClick={() => { if (v !== value) play("tick"); onChange(v); }}>{text}</button>
      ))}
    </div>
  );
}

let fxOnce: Promise<FxRate | null> | null = null;
/** Tests only: forget the fetched rate. */
export function resetFx(): void {
  fxOnce = null;
}

/** The USD -> JPY display rate, fetched once per page load. null while loading or when no rate is available. */
export function useFx(): FxRate | null {
  const [fx, setFx] = useState<FxRate | null>(null);
  useEffect(() => {
    let live = true;
    fxOnce ??= marketApi.fx().then((r) => r.usd_jpy).catch(() => { fxOnce = null; return null; });
    void fxOnce.then((r) => { if (live) setFx(r); });
    return () => { live = false; };
  }, []);
  return fx;
}

/** "$1 = ¥149.25 · ECB rate of 7 Oct 2026 · fetched 8 Oct 2026, 10:02 UTC" */
export function fxNote(fx: FxRate): string {
  const d = (s: string) => new Date(s.length === 10 ? `${s}T00:00:00Z` : s);
  const day = (s: string) => d(s).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  const time = d(fx.fetched_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  return `$1 = ¥${fx.rate.toLocaleString("en-US", { maximumFractionDigits: 2 })} · ECB rate of ${day(fx.rate_date)} · fetched ${day(fx.fetched_at)}, ${time} UTC${fx.stale ? " (last known rate)" : ""}`;
}

/** Data Confidence of a card's PSA 10 record: a coloured pill; the tooltip lists what keeps it out of the Top 100. */
export function Confidence({ q, compact = false }: { q: { confidence: string; score: number; eligible: boolean; reasons: string[]; history_points: number } | null | undefined; compact?: boolean }) {
  if (!q) return null;
  const label = q.confidence === "high" ? "High" : q.confidence === "medium" ? "Medium" : "Low";
  const why = q.eligible ? `Top 100 eligible · score ${q.score}/100 · ${q.history_points} price observations`
    : `Not Top 100 eligible (score ${q.score}/100): ${q.reasons.join("; ")}`;
  return (
    <span className={`mx-confpill mx-confpill-${q.confidence}${q.eligible ? "" : " is-out"}`} title={why} aria-label={`Data confidence ${label}`}>
      <i aria-hidden="true" />{compact ? null : label}
    </span>
  );
}

export function Conf({ c, reasons }: { c: string | null | undefined; reasons?: string[] }) {
  if (!c) return null;
  return <span className={`mx-conf mx-conf-${c}`} title={reasons?.join(" · ")}>{c}</span>;
}


// --- sound + motion helpers ------------------------------------------------------------------------------------------

export function SoundToggle() {
  const [on, setOn] = useMarketSound();
  return (
    <button type="button" className="mx-soundbtn" aria-pressed={on} aria-label={on ? "Sound on: turn off" : "Sound off: turn on"}
            title={on ? "Sounds on" : "Sounds off"} onClick={() => setOn(!on)}>
      {on ? "🔊" : "🔇"}
    </button>
  );
}

function reducedMotion(): boolean {
  return typeof window === "undefined" || !window.matchMedia || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Counts a number up from 0 (or from its previous value) over `ms`. Reduced motion: the final value at once. */
export function useCountUp(target: number | null | undefined, ms = 700): number | null {
  const [v, setV] = useState<number | null>(target ?? null);
  const from = useRef<number>(0);
  useEffect(() => {
    if (target === null || target === undefined) {
      setV(null);
      return;
    }
    if (reducedMotion() || typeof requestAnimationFrame === "undefined") {
      setV(target);
      from.current = target;
      return;
    }
    const start = performance.now();
    const a = from.current;
    let raf = 0;
    const step = (now: number) => {
      const k = Math.min(1, (now - start) / ms);
      const eased = 1 - Math.pow(1 - k, 3);
      setV(Math.round(a + (target - a) * eased));
      if (k < 1) raf = requestAnimationFrame(step);
      else from.current = target;
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return v;
}

export function Skeleton({ rows = 8, boxes = false }: { rows?: number; boxes?: boolean }) {
  return (
    <div className="mx-skel" aria-busy="true" aria-label="Loading">
      {boxes && <div className="mx-skel-grid"><div className="mx-skel-box" /><div className="mx-skel-box" /><div className="mx-skel-box" /></div>}
      {Array.from({ length: rows }, (_, i) => <div key={i} className="mx-skel-row" style={{ opacity: 1 - i * 0.08 }} />)}
    </div>
  );
}

/** The card image tilts towards the pointer with a holo shine (pointer devices, motion allowed). */
export function Tilt({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLSpanElement>(null);
  function move(e: React.PointerEvent<HTMLSpanElement>) {
    const el = ref.current;
    if (!el || reducedMotion() || e.pointerType !== "mouse") return;
    const r = el.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
    el.style.transform = `perspective(900px) rotateY(${(x - 0.5) * 14}deg) rotateX(${(0.5 - y) * 14}deg)`;
    el.style.setProperty("--mx-x", `${x * 100}%`);
    el.style.setProperty("--mx-y", `${y * 100}%`);
  }
  function leave() {
    if (ref.current) ref.current.style.transform = "";
  }
  return (
    <span ref={ref} className="mx-tilt" onPointerMove={move} onPointerLeave={leave}>
      {children}
      <span className="mx-tilt-shine" aria-hidden="true" />
    </span>
  );
}


// --- card hover preview (one floating layer for the whole page) ---------------------------------------------------------

type PreviewState = { src: string; x: number; y: number } | null;
const previewListeners = new Set<(p: PreviewState) => void>();

function showPreview(src: string, el: HTMLElement): void {
  if (typeof window === "undefined" || !window.matchMedia?.("(hover: hover) and (pointer: fine)").matches) return;
  const r = el.getBoundingClientRect();
  const w = 190, h = 265;
  const x = r.right + 12 + w > window.innerWidth ? r.left - 12 - w : r.right + 12;
  const y = Math.min(Math.max(8, r.top + r.height / 2 - h / 2), window.innerHeight - h - 8);
  previewListeners.forEach((l) => l({ src, x, y }));
}

function hidePreview(): void {
  previewListeners.forEach((l) => l(null));
}

/** Mounted once by MarketShell: the enlarged card next to the hovered thumbnail, above everything, never clipped. */
export function CardPreviewLayer() {
  const theme = useMarketTheme();
  const [p, setP] = useState<PreviewState>(null);
  useEffect(() => {
    previewListeners.add(setP);
    const off = () => setP(null);
    window.addEventListener("scroll", off, true);
    return () => {
      previewListeners.delete(setP);
      window.removeEventListener("scroll", off, true);
    };
  }, []);
  if (!p || typeof document === "undefined") return null;
  return createPortal(<img className="mx-preview-layer mx-portal" data-theme={theme} src={p.src} alt="" style={{ left: p.x, top: p.y }} />, document.body);
}
