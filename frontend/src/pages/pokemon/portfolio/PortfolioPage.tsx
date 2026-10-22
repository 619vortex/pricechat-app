import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { MultiLineChart } from "../charts";
import { DASH, day, num, usd } from "../format";
import { useCountUp } from "../mx";
import { portfolioApi, type Dashboard, type Holding, type PeriodChange } from "./api";
import { GiveawayPulls } from "./GiveawayPulls";
import { PhoneScanButton } from "./PhoneHandoff";
import {
  CardArt, CompositionBars, GradeBadge, HoldingSheet, HoldingTile, PortfolioFrame, SignInPrompt, ValueCell, errorText, useMember, useStatus,
} from "./shared";

type Range = "30d" | "90d" | "1y" | "all";
const RANGE_DAYS: Record<Range, number | null> = { "30d": 30, "90d": 90, "1y": 365, all: null };
type Sort = "value_desc" | "value_asc" | "recent" | "name" | "set" | "grade";

export function PortfolioPage() {
  const member = useMember();
  const [data, setData] = useState<Dashboard | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<Holding | null>(null);
  const status = useStatus();
  // the floating Scan Card button appears once the hero's own Scan / Add buttons have scrolled away
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 520);
    on();
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);
  const load = useCallback(() => {
    void portfolioApi.dashboard().then((d) => { setData(d); setErr(null); }).catch((e) => setErr(errorText(e)));
  }, []);
  useEffect(() => {
    if (member) load();
  }, [member, load]);
  if (member === undefined) return <PortfolioFrame crumbs={[["Markets", "/pokemon"], ["My Portfolio", null]]}><p className="pf-muted">Loading…</p></PortfolioFrame>;
  if (member === null) return <PortfolioFrame crumbs={[["Markets", "/pokemon"], ["My Portfolio", null]]}><SignInPrompt /></PortfolioFrame>;
  return (
    <PortfolioFrame crumbs={[["Markets", "/pokemon"], ["My Portfolio", null]]}>
      {err && <p className="pf-error" role="alert">{err}</p>}
      {!data ? <p className="pf-muted">Loading your portfolio…</p> : data.summary.copies === 0 ? <Empty onAdded={load} /> : (
        <>
          <Hero d={data} onOpen={setOpen} onAdded={load} />
          <HistoryChart d={data} />
          <div className="pf-grid2">
            <section className="pf-box">
              <h2>Top holdings</h2>
              <ol className="pf-toplist">
                {data.top.map((h) => (
                  <li key={h.id}><button type="button" onClick={() => setOpen(h)}>
                    <CardArt card={h.card} photoId={h.photos.find((p) => p.side === "front")?.id} className="pf-toplist-art" />
                    <span className="pf-toplist-name"><b>{h.card.name}</b><small>{h.card.year} {h.card.set_name}</small></span>
                    <GradeBadge h={h} /><ValueCell h={h} total />
                    {h.share != null && <span className="pf-share" title="Share of priced portfolio value">{h.share}%</span>}
                  </button></li>
                ))}
                {data.top.length === 0 && <li className="pf-muted">No priced cards yet.</li>}
              </ol>
            </section>
            <section className="pf-box">
              <h2>Composition</h2>
              <div className="pf-comps">
                <CompositionBars title="By set" groups={data.composition.set} total={data.summary.value} />
                <CompositionBars title="Raw vs graded" groups={data.composition.kind ?? []} total={data.summary.value} />
                <CompositionBars title="By grade" groups={data.composition.grade} total={data.summary.value} />
                <CompositionBars title="By language" groups={data.composition.language} total={data.summary.value} />
                <CompositionBars title="By rarity" groups={data.composition.rarity} total={data.summary.value} />
              </div>
            </section>
          </div>
          <Gallery holdings={data.holdings} onOpen={setOpen} />
          <SettingsBox d={data} onSaved={load} />
        </>
      )}
      {data && <GiveawayPulls onChanged={load} />}
      {open && <HoldingSheet h={open} owner status={status} onClose={() => { setOpen(null); load(); }} onChanged={load} />}
      {data && data.summary.copies > 0 && status?.scanner !== false && !open && scrolled && (
        <Link className="pf-shiny pf-fab" to="/pokemon/portfolio/add?scan=1" aria-label="Scan a card">📷 Scan Card</Link>
      )}
    </PortfolioFrame>
  );
}

function Empty({ onAdded }: { onAdded: () => void }) {
  return (
    <section className="pf-empty">
      <div className="pf-holo-card" aria-hidden><span>✨</span></div>
      <h1>Your portfolio is empty</h1>
      <p>Scan a card with your camera, or search the catalogue. Every exact printing (1st Edition, Shadowless, Japanese…) has its own market value.</p>
      <div className="pf-actions pf-center">
        <PhoneScanButton className="pf-shiny pf-big" onClosed={onAdded} />
        <Link className="pf-shiny pf-big pf-phone-only" to="/pokemon/portfolio/add?scan=1">📷 Scan Pokémon Card</Link>
        <Link className="pf-btn pf-big" to="/pokemon/portfolio/add">🔎 Search & add</Link>
      </div>
    </section>
  );
}

function ChangeChip({ label, c }: { label: string; c: PeriodChange }) {
  if (c.value === null) return <span className="pf-chip is-na" title={c.reason ?? undefined}>{label} {DASH}</span>;
  const up = (c.market ?? 0) >= 0;
  return (
    <span className={`pf-chip ${up ? "is-up" : "is-down"}`}
          title={`Total change ${usd(c.value)}: market move ${usd(c.market)}${c.pct_market !== null ? ` (${c.pct_market > 0 ? "+" : ""}${c.pct_market}%)` : ""}, cards added/removed ${usd(c.flows)}. Since ${day(c.base_day)}.`}>
      {label} <b>{c.pct_market !== null ? `${c.pct_market > 0 ? "▲" : c.pct_market < 0 ? "▼" : ""}${Math.abs(c.pct_market)}%` : usd(c.market)}</b>
      <small>market</small>
    </span>
  );
}

function Hero({ d, onOpen, onAdded }: { d: Dashboard; onOpen: (h: Holding) => void; onAdded: () => void }) {
  const s = d.summary;
  const shown = useCountUp(s.value, 700);
  return (
    <section className="pf-hero">
      <div className="pf-hero-main">
        <p className="pf-eyebrow">Estimated portfolio value{s.unpriced_holdings > 0 ? " (priced cards)" : ""}</p>
        <h1 className="pf-total">{shown !== null ? usd(shown) : DASH}</h1>
        {(s.virtual_copies ?? 0) > 0 && (
          <p className="pf-virtual-line">🎁 Includes {usd(s.virtual_value ?? 0)} in {s.virtual_copies} virtual Pack Giveaway {s.virtual_copies === 1 ? "card" : "cards"}
            <small> (not owned, not real; never on public leaderboards)</small></p>
        )}
        {s.unpriced_holdings > 0 && <p className="pf-unpriced-line">+ {s.unpriced_holdings} unpriced card{s.unpriced_holdings === 1 ? "" : "s"} <small>(not included)</small></p>}
        <div className="pf-chips">
          <ChangeChip label="30D" c={s.changes["30d"]} /><ChangeChip label="90D" c={s.changes["90d"]} /><ChangeChip label="1Y" c={s.changes["1y"]} />
        </div>
        <p className="pf-fine">Estimates from market data (exact grade; raw cards as conservative ranges), not guaranteed sale proceeds.
          {s.unpriced_holdings > 0 && ` ${s.unpriced_holdings} card${s.unpriced_holdings === 1 ? " has" : "s have"} no price yet and ${s.unpriced_holdings === 1 ? "is" : "are"} left out of the total (open a card to see why).`}
          {s.valued_at && ` Updated ${day(s.valued_at)}.`}</p>
        <div className="pf-actions">
          <PhoneScanButton className="pf-shiny pf-big" onClosed={onAdded} />
          <Link className="pf-shiny pf-big pf-phone-only" to="/pokemon/portfolio/add?scan=1">📷 Scan Card</Link>
          <Link className="pf-btn pf-big" to="/pokemon/portfolio/add">＋ Add Cards</Link>
        </div>
      </div>
      <dl className="pf-stats">
        <div><dt>Unique cards</dt><dd>{num(s.unique_cards)}</dd></div>
        <div><dt>Total cards</dt><dd>{num(s.copies)}</dd></div>
        <div><dt>Graded</dt><dd>{num(s.graded)}</dd></div>
        <div><dt>Raw</dt><dd>{num(s.raw)}</dd></div>
        {s.most_valuable && (
          <div className="pf-mvp"><dt>Most valuable</dt><dd><button type="button" className="pf-link" onClick={() => onOpen(s.most_valuable!)}>{s.most_valuable.card.name}</button>
            <GradeBadge h={s.most_valuable} /> <ValueCell h={s.most_valuable} /></dd></div>
        )}
      </dl>
    </section>
  );
}

function HistoryChart({ d }: { d: Dashboard }) {
  const [range, setRange] = useState<Range>("all");
  const pts = useMemo(() => {
    const days = RANGE_DAYS[range];
    const since = days ? Date.now() - days * 86_400_000 : 0;
    return d.history.filter((p) => Date.parse(p.t) >= since).map((p) => ({ t: p.t, v: p.value / 100 }));
  }, [d.history, range]);
  const first = d.history[0]?.t;
  return (
    <section className="pf-box">
      <div className="pf-boxhead">
        <h2>Valuation history</h2>
        <div className="pf-seg pf-seg-sm" role="radiogroup" aria-label="Range">
          {(["30d", "90d", "1y", "all"] as Range[]).map((r) => <button key={r} type="button" role="radio" aria-checked={range === r} className={range === r ? "is-on" : ""} onClick={() => setRange(r)}>{r.toUpperCase()}</button>)}
        </div>
      </div>
      {pts.length >= 2 ? <MultiLineChart series={[{ name: "Estimated value", points: pts }]} empty="" small /> : (
        <p className="pf-muted pf-chart-empty">Your portfolio's history starts {first ? `on ${day(first)}` : "today"}: one value is recorded each day from then on
          (nothing is back-filled). The chart appears once two days are on record.</p>
      )}
      <p className="pf-fine">Daily recorded values. Changes above count only market moves; cards you add or remove are shown separately.</p>
    </section>
  );
}

function Gallery({ holdings, onOpen }: { holdings: Holding[]; onOpen: (h: Holding) => void }) {
  const [view, setView] = useState<"grid" | "list">("grid");
  const [sort, setSort] = useState<Sort>("value_desc");
  const [f, setF] = useState({ lang: "", kind: "", set: "", year: "", q: "", company: "" });
  const opts = useMemo(() => ({
    sets: [...new Set(holdings.map((h) => h.card.set_name))].sort(), years: [...new Set(holdings.map((h) => h.card.year))].sort(),
    langs: [...new Set(holdings.map((h) => h.card.language))].sort(),
  }), [holdings]);
  const shown = useMemo(() => {
    const val = (h: Holding) => h.value?.total ?? -1;
    const rows = holdings.filter((h) => (!f.lang || h.card.language === f.lang) && (!f.kind || h.kind === f.kind) && (!f.set || h.card.set_name === f.set)
      && (!f.year || String(h.card.year) === f.year) && (!f.company || h.grading_company === f.company)
      && (!f.q || `${h.card.name} ${h.card.pokemon_name}`.toLowerCase().includes(f.q.toLowerCase())));
    const by: Record<Sort, (a: Holding, b: Holding) => number> = {
      value_desc: (a, b) => val(b) - val(a), value_asc: (a, b) => val(a) - val(b), recent: (a, b) => Date.parse(b.added_at) - Date.parse(a.added_at),
      name: (a, b) => a.card.name.localeCompare(b.card.name), set: (a, b) => a.card.year - b.card.year || a.card.set_name.localeCompare(b.card.set_name),
      grade: (a, b) => a.label.localeCompare(b.label),
    };
    return [...rows].sort(by[sort]);
  }, [holdings, sort, f]);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <section className="pf-box">
      <div className="pf-boxhead">
        <h2>Collection <small>{shown.length} of {holdings.length}</small></h2>
        <div className="pf-seg pf-seg-sm" role="radiogroup" aria-label="View">
          <button type="button" role="radio" aria-checked={view === "grid"} className={view === "grid" ? "is-on" : ""} onClick={() => setView("grid")}>Grid</button>
          <button type="button" role="radio" aria-checked={view === "list"} className={view === "list" ? "is-on" : ""} onClick={() => setView("list")}>List</button>
        </div>
      </div>
      <div className="pf-filters">
        <input placeholder="Pokémon" value={f.q} onChange={set("q")} aria-label="Filter by Pokémon" />
        <select value={sort} onChange={(e) => setSort(e.target.value as Sort)} aria-label="Sort">
          <option value="value_desc">Highest value</option><option value="value_asc">Lowest value</option><option value="recent">Recently added</option>
          <option value="name">Name</option><option value="set">Set</option><option value="grade">Grade</option>
        </select>
        <select value={f.kind} onChange={set("kind")} aria-label="Raw or graded"><option value="">Raw & graded</option><option value="raw">Raw</option><option value="graded">Graded</option></select>
        <select value={f.company} onChange={set("company")} aria-label="Grading company"><option value="">Any company</option><option>PSA</option><option>BGS</option><option>CGC</option></select>
        <select value={f.lang} onChange={set("lang")} aria-label="Language"><option value="">All languages</option>{opts.langs.map((l) => <option key={l}>{l}</option>)}</select>
        <select value={f.set} onChange={set("set")} aria-label="Set"><option value="">All sets</option>{opts.sets.map((s) => <option key={s}>{s}</option>)}</select>
        <select value={f.year} onChange={set("year")} aria-label="Year"><option value="">All years</option>{opts.years.map((y) => <option key={y}>{y}</option>)}</select>
      </div>
      {view === "grid" ? (
        <div className="pf-gallery">{shown.map((h) => <HoldingTile key={h.id} h={h} onOpen={onOpen} />)}</div>
      ) : (
        <div className="pf-listwrap">
          <table className="pf-list">
            <thead><tr><th>Card</th><th>Grade</th><th className="pf-num">Qty</th><th className="pf-num">Value</th><th className="pf-num">Total</th></tr></thead>
            <tbody>{shown.map((h) => (
              <tr key={h.id} onClick={() => onOpen(h)}>
                <td><b>{h.card.name}</b><br /><small>{h.card.year} · {h.card.set_name}{h.card.edition !== "—" ? ` · ${h.card.edition}` : ""}</small></td>
                <td><GradeBadge h={h} /></td><td className="pf-num">{h.quantity}</td><td className="pf-num"><ValueCell h={h} /></td>
                <td className="pf-num">{h.value?.total != null ? usd(h.value.total) : DASH}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function SettingsBox({ d, onSaved }: { d: Dashboard; onSaved: () => void }) {
  const p = d.portfolio;
  const nav = useNavigate();
  const [bio, setBio] = useState(p.bio);
  const [err, setErr] = useState<string | null>(null);
  const save = async (s: Parameters<typeof portfolioApi.settings>[0]) => {
    setErr(null);
    try {
      await portfolioApi.settings(s);
      onSaved();
    } catch (e) {
      setErr(errorText(e));
    }
  };
  return (
    <details className="pf-box pf-settings">
      <summary>Sharing & privacy <span className={`pf-tag ${p.visibility === "public" ? "is-public" : ""}`}>{p.visibility === "public" ? "Public" : "Private"}</span></summary>
      <label className="pf-check"><input type="checkbox" checked={p.visibility === "public"} onChange={(e) => void save({ visibility: e.target.checked ? "public" : "private" })} />
        Make my portfolio public (only cards marked public are shown)</label>
      <label className="pf-check"><input type="checkbox" checked={p.show_value} disabled={p.visibility !== "public"} onChange={(e) => void save({ show_value: e.target.checked })} />
        Show values publicly (needed to appear in Top Portfolios)</label>
      <label className="pf-check"><input type="checkbox" checked={p.show_purchase} disabled={p.visibility !== "public"} onChange={(e) => void save({ show_purchase: e.target.checked })} />
        Show what I paid (off by default)</label>
      <label className="pf-check"><input type="checkbox" checked={p.comments_enabled} onChange={(e) => void save({ comments_enabled: e.target.checked })} /> Allow comments</label>
      <label className="pf-field">Short bio <span className="pf-muted">(no real names or addresses needed)</span>
        <input value={bio} maxLength={280} onChange={(e) => setBio(e.target.value)} onBlur={() => bio !== p.bio && void save({ bio })} /></label>
      {p.visibility === "public" && <p><button type="button" className="pf-link" onClick={() => nav(p.public_url)}>View my public profile →</button></p>}
      {p.review_status === "review" && <p className="pf-fine">Your collection is waiting for a quick review before it can appear in Top Portfolios (very valuable or fast-growing self-reported collections are checked by a person). It stays visible on your profile.</p>}
      {err && <p className="pf-error">{err}</p>}
    </details>
  );
}
