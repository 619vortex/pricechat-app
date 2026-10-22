import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { openLogin } from "../../../website/emailLogin";
import { burst } from "../../../website/fx";
import { memberApi, useSession } from "../../../website/members";
import { playSound, warmSounds, type Sound } from "../../../website/sound";
import { MarketShell } from "../mx";
import "./quicktrade.css";

/**
 * The Marketplace, one click each (test mode). Everything is paid in Play USDT, a test currency with no value (not real
 * USDT; can't be bought, deposited or withdrawn). Every member starts with 10,000. Cards are virtual copies in the
 * member's binder. Buy = the market price. Sell = instantly, 5% below market (or to a better member bid). Bid = a
 * standing offer, held from the wallet until it fills or is cancelled. Sell all = everything at 10% below market.
 * The verified graded-slab marketplace lives on at /pokemon/marketplace/graded.
 */

type Tier = "common" | "uncommon" | "rare" | "holo" | "ultra";
interface CardLite { id: number; name: string; set: string; year: number; edition: string; image: string | null; tier?: Tier }
interface ShopCard extends CardLite { tier: Tier; price_cents: number; value_floor?: boolean; sell_cents?: number | null; best_bid_cents?: number | null }
interface Listing { id: number; card: CardLite & { tier: Tier }; price_cents: number; estimate_cents?: number | null; seller: string; mine: boolean; listed_at: string }
interface Bid { id: number; price_cents: number; created_at: string; card: CardLite }
interface Play { note: string; balance_cents: number; sell_pct?: number; market: Listing[]; mine: Listing[]; bids?: Bid[];
  activity: { delta_cents: number; kind: string; note: string; at: string }[] }
interface BinderCard extends CardLite { tier: Tier; value: number | null; value_floor?: boolean; count?: number }
interface Quote { market_cents: number | null; sell_cents: number | null; best_bid_cents: number | null; cheapest_listing_cents: number | null; owned: number;
  my_bids: { id: number; price_cents: number }[] }
type Tab = "market" | "inventory" | "bids" | "activity";
type SheetMode = "trade" | "bid" | "list";

const money = (c: number | null | undefined) => (c == null ? "—" : `$${(c / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
const STARTERS = ["Charizard", "Pikachu", "Blastoise", "Mewtwo", "Lugia", "Gengar", "Venusaur", "Dragonite"];
const sellAt = (market: number | null | undefined, pct = 95) => (market ? Math.floor((market * pct) / 100) : null);
const ago = (iso: string) => {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)}m ago` : s < 86400 ? `${Math.round(s / 3600)}h ago` : `${Math.round(s / 86400)}d ago`;
};

/** The balance rolls to its new value, flashing green (up) or red (down). */
function useRolling(target: number, ready: boolean): [number, "up" | "down" | null] {
  const [shown, setShown] = useState(target);
  const [dir, setDir] = useState<"up" | "down" | null>(null);
  const from = useRef(target);
  const loaded = useRef(false);
  useEffect(() => {
    const start = from.current;
    if (start === target) return;
    if (!loaded.current) {  // the first balance just appears; only changes after that roll
      if (ready) loaded.current = true;
      from.current = target;
      setShown(target);
      return;
    }
    setDir(target > start ? "up" : "down");
    const reduce = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce) { setShown(target); from.current = target; return; }
    const t0 = performance.now();
    let raf = 0;
    const step = () => {
      const k = Math.max(0, Math.min(1, (performance.now() - t0) / 700));
      const v = Math.round(start + (target - start) * (1 - Math.pow(1 - k, 3)));
      setShown(v);
      if (k < 1) raf = requestAnimationFrame(step);
      else { from.current = target; window.setTimeout(() => setDir(null), 500); }
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target]);
  return [shown, dir];
}

/** The bought card: it lifts out of the page, pops up big and glowing in the middle of the screen, then arcs into
 *  the Inventory button (which bounces with "+1"). Calls onLand when it arrives. */
function flyToInventory(img: HTMLElement | null, target: HTMLElement | null, onLand: () => void) {
  const calm = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  if (!img || !target || typeof document === "undefined" || calm || typeof img.animate !== "function") { onLand(); return; }
  const a = img.getBoundingClientRect(), b = target.getBoundingClientRect();
  const ghost = img.cloneNode(true) as HTMLElement;
  Object.assign(ghost.style, { position: "fixed", left: `${a.left}px`, top: `${a.top}px`, width: `${a.width}px`, height: `${a.height}px`, zIndex: "90",
    pointerEvents: "none", borderRadius: "10px", objectFit: "cover", transformOrigin: "center", willChange: "transform, opacity, filter" });
  document.body.appendChild(ghost);
  const cx = window.innerWidth / 2 - (a.left + a.width / 2), cy = window.innerHeight * 0.42 - (a.top + a.height / 2);
  const big = Math.min(2.2, (window.innerHeight * 0.42) / a.height);
  const tx = b.left + b.width / 2 - (a.left + a.width / 2), ty = b.top + b.height / 2 - (a.top + a.height / 2);
  const anim = ghost.animate([
    { transform: "translate(0, 0) scale(1) rotate(0)", filter: "drop-shadow(0 0 0 rgba(0,229,143,0))", offset: 0 },
    { transform: `translate(${cx}px, ${cy}px) scale(${big}) rotate(-3deg)`, filter: "drop-shadow(0 0 28px rgba(0,229,143,.9))", offset: 0.35 },
    { transform: `translate(${cx}px, ${cy - 8}px) scale(${big * 1.04}) rotate(2deg)`, filter: "drop-shadow(0 0 36px rgba(255,210,77,.9))", offset: 0.55 },
    { transform: `translate(${(cx + tx) / 2}px, ${Math.min(cy, ty) - 120}px) scale(${big * 0.5}) rotate(14deg)`, opacity: 1, offset: 0.78 },
    { transform: `translate(${tx}px, ${ty}px) scale(0.12) rotate(24deg)`, opacity: 0.4, offset: 1 },
  ], { duration: 1500, easing: "cubic-bezier(.45,.05,.35,1)", fill: "forwards" });
  anim.onfinish = () => { ghost.remove(); onLand(); };
}

function CardArt({ c, onClick }: { c: CardLite; onClick?: () => void }) {
  const move = (e: React.PointerEvent<HTMLButtonElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    e.currentTarget.style.setProperty("--rx", `${((e.clientY - r.top) / r.height - 0.5) * -10}deg`);
    e.currentTarget.style.setProperty("--ry", `${((e.clientX - r.left) / r.width - 0.5) * 12}deg`);
  };
  const leave = (e: React.PointerEvent<HTMLButtonElement>) => { e.currentTarget.style.setProperty("--rx", "0deg"); e.currentTarget.style.setProperty("--ry", "0deg"); };
  return (
    <button type="button" className="mq-art" onClick={onClick} onPointerMove={move} onPointerLeave={leave} aria-label={`${c.name}: details`}>
      {c.image ? <img src={c.image} alt={c.name} loading="lazy" decoding="async" /> : <span>{c.name}</span>}
    </button>
  );
}

const PAPER = [
  { face: "😱", title: "Sell EVERYTHING?", text: "Every card you own goes at once, 10% below market. Paper hands detected.", yes: "Yes, dump it all", no: "No, diamond hands 💎" },
  { face: "🫣", title: "Are you SURE sure?", text: "Your Charizard is looking at you. It trusted you. Last chance to hold.", yes: "YES. SURE SURE. 💀", no: "Okay okay, I'll hold" },
];

export function MarketplaceHome() {
  const member = useSession();
  const [play, setPlay] = useState<Play | null>(null);
  const [binder, setBinder] = useState<BinderCard[]>([]);
  const [tab, setTab] = useState<Tab>("market");
  const [bump, setBump] = useState(0);  // the Inventory button bounces with "+1" when a card lands in it
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<"rel" | "asc" | "desc">("rel");
  const [results, setResults] = useState<ShopCard[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<{ text: string; bad?: boolean; key: number } | null>(null);
  const [sheet, setSheet] = useState<{ card: CardLite; mode: SheetMode } | null>(null);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [amount, setAmount] = useState("");
  const [dump, setDump] = useState(0);  // 0 closed · 1 "are you sure?" · 2 "sure sure?"
  const starter = useRef(STARTERS[Math.floor(Math.random() * STARTERS.length)]);
  const invBtn = useRef<HTMLButtonElement>(null);
  const wallet = useRef<HTMLSpanElement>(null);

  const [balance, flash] = useRolling(play?.balance_cents ?? 0, play !== null);
  const worth = useMemo(() => binder.reduce((t, c) => t + (c.value ?? 0) * (c.count ?? 1), 0), [binder]);
  const copies = useMemo(() => binder.reduce((t, c) => t + (c.count ?? 1), 0), [binder]);

  const refresh = useCallback(async () => {
    const [p, b] = await Promise.all([memberApi<Play>("/members/play"), memberApi<{ cards: BinderCard[] }>("/members/packs/binder")]);
    setPlay(p);
    setBinder(b.cards);
  }, []);
  useEffect(() => { if (member) void refresh().catch(() => undefined); }, [member, refresh]);

  useEffect(() => {
    if (!member) return;
    const term = q.trim() || starter.current;
    if (term.length < 2) return;
    const t = window.setTimeout(() => {
      void memberApi<{ cards: ShopCard[] }>(`/members/play/shop?q=${encodeURIComponent(term)}`).then((r) => setResults(r.cards)).catch(() => setResults([]));
    }, 250);
    return () => window.clearTimeout(t);
  }, [q, member]);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 2800);
    return () => window.clearTimeout(t);
  }, [toast]);

  const sorted = useMemo(() => {
    if (!results) return null;
    if (sort === "rel") return results;
    return [...results].sort((a, b) => (sort === "asc" ? a.price_cents - b.price_cents : b.price_cents - a.price_cents));
  }, [results, sort]);

  async function openSheet(card: CardLite, mode: SheetMode = "trade") {
    setSheet({ card, mode });
    setQuote(null);
    playSound("swish");
    try {
      const qd = await memberApi<Quote>(`/members/play/quote/${card.id}`);
      setQuote(qd);
      const base = mode === "bid" ? Math.floor((qd.market_cents ?? 0) * 0.9) : qd.market_cents ?? 0;
      setAmount(base ? (base / 100).toFixed(2) : "");
    } catch { /* the sheet still shows the card */ }
  }

  /** One press, one trade: sound, animation, toast, fresh balance. */
  async function go(key: string, path: string, body: unknown | undefined, done: (r: Record<string, unknown>) => string, sound: Sound, from?: HTMLElement | null) {
    if (busy) return;
    warmSounds();
    setBusy(key);
    try {
      const r = await memberApi<Record<string, unknown>>(path, { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) });
      playSound(sound);
      if (sound === "coin") flyToInventory((from?.querySelector("img") ?? from) as HTMLElement | null, invBtn.current, () => {
        setBump((n) => n + 1);
        playSound("listed");
        if (invBtn.current) burst(invBtn.current, 12, 60, true);
      });
      if (sound === "cash" && wallet.current) { burst(wallet.current, 16, 90, true); }
      setToast({ text: done(r), key: Date.now() });
      await refresh();
      if (sheet) setSheet(null);
    } catch (e) {
      playSound("nope");
      setToast({ text: e instanceof Error ? e.message : "That didn't work. Try again.", bad: true, key: Date.now() });
    } finally {
      setBusy(null);
    }
  }

  async function dumpAll() {
    setDump(0);
    await go("dump", "/members/play/sell-all", undefined,
      (r) => `💸 Dumped ${r.copies} cards for ${money(r.total_cents as number)}. Paper hands!`, "cash");
    if (wallet.current) { burst(wallet.current, 30, 160, true); window.setTimeout(() => wallet.current && burst(wallet.current, 24, 120, true), 300); }
  }

  const bids = play?.bids ?? [];
  const tabs: [Tab, string, number | null][] = [["market", "Market", null], ["bids", "Bids", bids.length || null], ["activity", "Activity", null]];

  return (
    <MarketShell crumbs={[["Markets", "/pokemon"], ["Marketplace", null]]} compact>
      <section className="mq-hero">
        <div>
          <h1>Marketplace</h1>
          <p>Buy at market. Sell instantly. Bid on anything. One tap each.</p>
        </div>
      </section>

      {member === null && (
        <section className="mq-signin">
          <p><b>Sign in to start trading.</b> Every member starts with <b>$10,000</b> in Play USDT (a test currency).</p>
          <button type="button" className="mq-btn mq-buy" onClick={openLogin}>Sign in</button>
        </section>
      )}

      {member && (
        <>
          <div className="mq-bar" role="region" aria-label="Your wallet">
            <span ref={wallet} className={`mq-bal${flash ? ` is-${flash}` : ""}`} title={play?.note}>
              <small>Balance</small>{money(balance)}
            </span>
            <span className="mq-bal is-cards"><small>Inventory</small>{money(worth)}</span>
            <span className="mq-bal is-total"><small>Net worth</small>{money(balance + worth + bids.reduce((t, b) => t + b.price_cents, 0))}</span>
            <button ref={invBtn} key={bump} type="button" className={`mq-inv${tab === "inventory" ? " is-on" : ""}${bump ? " is-bump" : ""}`}
                    onClick={() => { setTab(tab === "inventory" ? "market" : "inventory"); playSound("swish"); }} aria-pressed={tab === "inventory"}>
              <span aria-hidden>🎒</span> Inventory <i>{copies}</i>
              {bump > 0 && <b className="mq-plus" aria-hidden>+1</b>}
            </button>
          </div>
          <p className="mq-note">🧪 Test mode: <b>Play USDT</b> is a test currency (not real USDT, no value, can't be bought or withdrawn). Cards are virtual copies.</p>

          <nav className="mq-tabs" role="tablist" aria-label="Marketplace">
            {tabs.map(([k, label, n]) => (
              <button key={k} type="button" role="tab" aria-selected={tab === k}
                      className={tab === k ? "is-on" : ""} onClick={() => { setTab(k); playSound("tick"); }}>
                {label}{n ? <i>{n}</i> : null}
              </button>
            ))}
          </nav>

          {tab === "market" && (
            <section aria-label="Buy">
              <div className="mq-tools">
                <input className="mq-search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search any card: Charizard, Pikachu, Lugia…"
                       aria-label="Search cards" />
                <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)} aria-label="Sort">
                  <option value="rel">Best match</option><option value="asc">Price: low → high</option><option value="desc">Price: high → low</option>
                </select>
              </div>
              <h2 className="mq-h">{q.trim() ? `Results for “${q.trim()}”` : `Popular · ${starter.current}`}</h2>
              {sorted === null ? <ul className="mq-grid">{Array.from({ length: 6 }, (_, i) => <li key={i} className="mq-skel" />)}</ul>
                : sorted.length === 0 ? <p className="pf-muted">No priced cards found. Try another name.</p> : (
                <ul className="mq-grid">
                  {sorted.map((c, i) => (
                    <li key={c.id} className={`tier-${c.tier}`} style={{ "--i": i } as CSSProperties}>
                      <CardArt c={c} onClick={() => void openSheet(c)} />
                      <b>{c.name}</b>
                      <small>{c.year} · {c.set}{c.edition && c.edition !== "—" ? ` · ${c.edition}` : ""}</small>
                      <div className="mq-prices"><span className="mq-price">{money(c.price_cents)}{c.value_floor ? "+" : ""}</span>
                        {c.best_bid_cents ? <span className="mq-bidtag">bid {money(c.best_bid_cents)}</span> : null}</div>
                      <div className="mq-pair">
                        <button type="button" className="mq-btn mq-buy" disabled={busy !== null || balance < c.price_cents}
                                onClick={(e) => void go(`b${c.id}`, "/members/play/buy-card", { card_id: c.id }, () => `✓ Bought ${c.name} for ${money(c.price_cents)}`,
                                  "coin", (e.currentTarget.closest("li") as HTMLElement))}>
                          {busy === `b${c.id}` ? "…" : "Buy"}</button>
                        <button type="button" className="mq-btn mq-bid" disabled={busy !== null} onClick={() => void openSheet(c, "bid")}>Bid</button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          {tab === "inventory" && (
            <section aria-label="Inventory">
              <button type="button" className="mq-back" onClick={() => setTab("market")}>← Back to the market</button>
              <div className="mq-mine-head">
                <p className="mq-h">🎒 {copies} {copies === 1 ? "card" : "cards"} · worth {money(worth)} · instant sale pays {play?.sell_pct ?? 95}%</p>
                {copies > 0 && <button type="button" className="mq-btn mq-panic" onClick={() => { setDump(1); playSound("nope"); }}>😱 Sell all</button>}
              </div>
              {binder.length === 0 ? <p className="pf-muted">Your inventory is empty. Buy a card, or <a href="/giveaway">open a free pack</a>.</p> : (
                <ul className="mq-grid">
                  {binder.map((c, i) => (
                    <li key={c.id} className={`tier-${c.tier}`} style={{ "--i": i } as CSSProperties}>
                      <div className="mq-art-wrap"><CardArt c={c} onClick={() => void openSheet(c)} />{(c.count ?? 1) > 1 && <i className="mq-count">×{c.count}</i>}</div>
                      <b>{c.name}</b>
                      <small>{c.year} · {c.set}</small>
                      <div className="mq-prices"><span className="mq-price">{money(c.value)}</span></div>
                      <div className="mq-pair">
                        <button type="button" className="mq-btn mq-sell" disabled={busy !== null || !c.value}
                                onClick={() => void go(`s${c.id}`, "/members/play/sell", { card_id: c.id },
                                  (r) => `✓ Sold ${c.name} for ${money(r.price_cents as number)}${r.to === "bid" ? " to a bid" : ""}`, "cash")}>
                          {busy === `s${c.id}` ? "…" : c.value ? `Sell ${money(sellAt(c.value, play?.sell_pct))}` : "No price"}</button>
                        <button type="button" className="mq-btn mq-bid" disabled={busy !== null} onClick={() => void openSheet(c, "list")}>List</button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          {tab === "market" && (
            <section aria-label="Listings">
              <h2 className="mq-h">For sale by members</h2>
              {!play || play.market.length === 0 ? <p className="pf-muted">No member listings right now. List a card from your Inventory.</p> : (
                <ul className="mq-grid">
                  {[...play.market].sort((a, b) => deal(b) - deal(a)).map((l, i) => {
                    const d = deal(l);
                    return (
                      <li key={l.id} className={`tier-${l.card.tier}`} style={{ "--i": i } as CSSProperties}>
                        <CardArt c={l.card} onClick={() => void openSheet(l.card)} />
                        <b>{l.card.name}</b>
                        <small>by {l.seller} · {ago(l.listed_at)}</small>
                        <div className="mq-prices"><span className="mq-price">{money(l.price_cents)}</span>
                          {l.estimate_cents ? <span className={`mq-deal${d > 0 ? " is-good" : d < 0 ? " is-bad" : ""}`}>
                            {d > 0 ? `${d}% below market` : d < 0 ? `${-d}% above` : "at market"}</span> : null}</div>
                        <button type="button" className="mq-btn mq-buy" disabled={busy !== null || balance < l.price_cents}
                                onClick={(e) => void go(`l${l.id}`, `/members/play/listings/${l.id}/buy`, undefined, () => `✓ Bought ${l.card.name} from ${l.seller}`,
                                  "coin", (e.currentTarget.closest("li") as HTMLElement))}>
                          {busy === `l${l.id}` ? "…" : `Buy ${money(l.price_cents)}`}</button>
                      </li>
                    );
                  })}
                </ul>
              )}
              {play && play.mine.length > 0 && (
                <>
                  <h2 className="mq-h">Your listings</h2>
                  <ul className="mq-rows">
                    {play.mine.map((l) => (
                      <li key={l.id}><b>{l.card.name}</b> <span>{money(l.price_cents)} · {ago(l.listed_at)}</span>
                        <button type="button" className="mq-btn mq-ghost" disabled={busy !== null}
                                onClick={() => void go(`c${l.id}`, `/members/play/listings/${l.id}/cancel`, undefined, () => `${l.card.name} is back in your inventory`, "listed")}>Cancel</button>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </section>
          )}

          {tab === "bids" && (
            <section aria-label="Your bids">
              <h2 className="mq-h">Your open bids <small>· the amount is held until the bid fills or you cancel</small></h2>
              {bids.length === 0 ? <p className="pf-muted">No open bids. Tap Bid on any card.</p> : (
                <ul className="mq-rows">
                  {bids.map((b) => (
                    <li key={b.id}><b>{b.card.name}</b> <span>{b.card.year} {b.card.set} · {money(b.price_cents)} · {ago(b.created_at)}</span>
                      <button type="button" className="mq-btn mq-ghost" disabled={busy !== null}
                              onClick={() => void go(`x${b.id}`, `/members/play/bids/${b.id}/cancel`, undefined, () => `Bid cancelled: ${money(b.price_cents)} released`, "listed")}>Cancel</button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          {tab === "activity" && (
            <section aria-label="Activity">
              <h2 className="mq-h">Activity</h2>
              {!play || play.activity.length === 0 ? <p className="pf-muted">Nothing yet.</p> : (
                <ul className="mq-rows mq-activity">
                  {play.activity.map((a, i) => (
                    <li key={i}><span className={a.delta_cents >= 0 ? "is-up" : "is-down"}>{a.delta_cents >= 0 ? "+" : "−"}{money(Math.abs(a.delta_cents))}</span>
                      <b>{a.note}</b> <small>{ago(a.at)}</small></li>
                  ))}
                </ul>
              )}
            </section>
          )}
        </>
      )}

      <p className="mq-graded"><Link to="/pokemon/marketplace/graded">Graded slab marketplace (verified, vaulted · preview) →</Link></p>

      {sheet && (
        <div className="mq-sheet-bg" onClick={() => setSheet(null)}>
          <div className="mq-sheet" role="dialog" aria-label={sheet.card.name} onClick={(e) => e.stopPropagation()}>
            <button type="button" className="mq-x" onClick={() => setSheet(null)} aria-label="Close">×</button>
            <div className="mq-sheet-art">{sheet.card.image ? <img src={sheet.card.image} alt={sheet.card.name} /> : null}</div>
            <div className="mq-sheet-body">
              <h3>{sheet.card.name}</h3>
              <p className="pf-muted">{sheet.card.year} · {sheet.card.set}{sheet.card.edition && sheet.card.edition !== "—" ? ` · ${sheet.card.edition}` : ""}</p>
              <dl className="mq-quote">
                <div><dt>Market (buy)</dt><dd>{money(quote?.market_cents)}</dd></div>
                <div><dt>Instant sale</dt><dd>{money(quote?.sell_cents)}</dd></div>
                <div><dt>Best bid</dt><dd>{money(quote?.best_bid_cents)}</dd></div>
                <div><dt>Cheapest listing</dt><dd>{money(quote?.cheapest_listing_cents)}</dd></div>
              </dl>
              {quote && quote.owned > 0 && <p className="mq-owned">You own {quote.owned}</p>}
              <div className="mq-modes" role="tablist">
                {(["trade", "bid", ...(quote && quote.owned > 0 ? ["list"] : [])] as SheetMode[]).map((m) => (
                  <button key={m} type="button" className={sheet.mode === m ? "is-on" : ""}
                          onClick={() => { setSheet({ ...sheet, mode: m }); const base = m === "bid" ? Math.floor((quote?.market_cents ?? 0) * 0.9) : quote?.market_cents ?? 0;
                            setAmount(base ? (base / 100).toFixed(2) : ""); }}>
                    {m === "trade" ? "Buy / Sell" : m === "bid" ? "Bid" : "List"}</button>
                ))}
              </div>
              {sheet.mode === "trade" && (
                <div className="mq-pair">
                  <button type="button" className="mq-btn mq-buy" disabled={busy !== null || !quote?.market_cents || balance < (quote?.market_cents ?? 0)}
                          onClick={() => void go("sb", "/members/play/buy-card", { card_id: sheet.card.id }, () => `✓ Bought ${sheet.card.name}`, "coin",
                            document.querySelector(".mq-sheet-art") as HTMLElement)}>Buy {money(quote?.market_cents)}</button>
                  {quote && quote.owned > 0 && (
                    <button type="button" className="mq-btn mq-sell" disabled={busy !== null || !quote.sell_cents}
                            onClick={() => void go("ss", "/members/play/sell", { card_id: sheet.card.id }, (r) => `✓ Sold ${sheet.card.name} for ${money(r.price_cents as number)}`, "cash")}>
                      Sell {money(quote.sell_cents)}</button>
                  )}
                </div>
              )}
              {(sheet.mode === "bid" || sheet.mode === "list") && (
                <>
                  <div className="mq-chips">
                    {(sheet.mode === "bid" ? [["−20%", 80], ["−10%", 90], ["−5%", 95], ["Market", 100]] : [["Market", 100], ["+5%", 105], ["+10%", 110], ["+25%", 125]])
                      .map(([label, pct]) => (
                        <button key={label as string} type="button" disabled={!quote?.market_cents}
                                onClick={() => setAmount((Math.floor(((quote?.market_cents ?? 0) * (pct as number)) / 100) / 100).toFixed(2))}>{label}</button>
                      ))}
                  </div>
                  <label className="mq-amount"><span>$</span>
                    <input type="number" inputMode="decimal" min="0.01" step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)}
                           aria-label={sheet.mode === "bid" ? "Your bid" : "Your price"} /></label>
                  <button type="button" className={`mq-btn ${sheet.mode === "bid" ? "mq-buy" : "mq-sell"} mq-wide`} disabled={busy !== null || !(Number(amount) > 0)}
                          onClick={() => sheet.mode === "bid"
                            ? void go("bid", "/members/play/bid", { card_id: sheet.card.id, price_cents: Math.round(Number(amount) * 100) },
                                (r) => (r.filled ? `✓ Bid filled: ${sheet.card.name} is yours for ${money(r.price_cents as number)}` : `✓ Bid placed: ${money(r.price_cents as number)} held`),
                                "listed")
                            : void go("lst", "/members/play/list", { card_id: sheet.card.id, price_cents: Math.round(Number(amount) * 100) },
                                (r) => (r.filled_bid ? `✓ Sold instantly to a bid for ${money(r.price_cents as number)}` : `✓ ${sheet.card.name} listed for ${money(r.price_cents as number)}`),
                                "listed")}>
                    {sheet.mode === "bid" ? `Place bid $${Number(amount || 0).toFixed(2)}` : `List for $${Number(amount || 0).toFixed(2)}`}</button>
                  {quote && quote.my_bids.length > 0 && sheet.mode === "bid" && <p className="pf-muted">Your open bids here: {quote.my_bids.map((b) => money(b.price_cents)).join(", ")}</p>}
                </>
              )}
              <Link className="mq-more" to={`/pokemon/cards/${sheet.card.id}`}>Price history & sales →</Link>
            </div>
          </div>
        </div>
      )}

      {dump > 0 && (
        <div className="mq-sheet-bg mq-panic-bg" onClick={() => setDump(0)}>
          <div className={`mq-panic-box step-${dump}`} role="alertdialog" aria-label={PAPER[dump - 1].title} onClick={(e) => e.stopPropagation()}>
            <span className="mq-face" aria-hidden>{PAPER[dump - 1].face}</span>
            <h3>{PAPER[dump - 1].title}</h3>
            <p>{PAPER[dump - 1].text}</p>
            <p className="mq-panic-sum">{copies} {copies === 1 ? "card" : "cards"} · worth {money(worth)} · you'd get about {money(Math.floor(worth * 0.9))}</p>
            <div className="mq-pair">
              <button type="button" className="mq-btn mq-ghost" onClick={() => { setDump(0); playSound("shine"); }}>{PAPER[dump - 1].no}</button>
              <button type="button" className="mq-btn mq-panic" onClick={() => { if (dump === 1) { setDump(2); playSound("nope"); } else void dumpAll(); }}>
                {PAPER[dump - 1].yes}</button>
            </div>
          </div>
        </div>
      )}

      {toast && <p key={toast.key} className={`mq-toast${toast.bad ? " is-bad" : ""}`} role="status">{toast.text}</p>}
    </MarketShell>
  );
}

function deal(l: Listing): number {
  return l.estimate_cents ? Math.round((1 - l.price_cents / l.estimate_cents) * 100) : 0;
}
