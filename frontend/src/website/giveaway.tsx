import { useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import { SitePage } from "./community";
import { pageHead } from "./seo";
import { openLogin } from "./emailLogin";
import { burst } from "./fx";
import { memberApi, refreshSession, useSession } from "./members";
import { reducedMotion } from "./motion";
import { playSound, warmSounds } from "./sound";
import "./giveaway.css";

/** Pack Giveaway: open free virtual Pokémon packs for fun. Virtual cards only: no real money, no real cards, no prizes. */

type Tier = "common" | "uncommon" | "rare" | "holo" | "ultra";
interface Pack { key: string; name: string; set: string; year: number; tagline: string; theme: string[]; mascot: string; logo?: string | null; image?: string | null; cover: string | null; size: number; chase: number; owned: number }
interface Shelf { packs: Pack[]; card_back?: string; per_day: number | null; opens_left: number | null; xp_per_day: number; xp_packs_left: number | null; xp_today?: number; xp_cap?: number; level_packs: number;
  rank_level: number | null; resets_at: string; signed_in: boolean }
interface Pulled { id: number; name: string; set: string; number: string | null; year: number; rarity: string | null; edition: string; variant: string; image: string | null; tier: Tier; value: number | null; value_floor?: boolean; new: boolean; count?: number; pack?: string;
  auto_sold?: number | null }
interface Opened { pack: string; bonus: boolean; print?: string; cards: Pulled[]; xp: number; xp_packs_left: number; xp_today?: number; xp_cap?: number; level_packs: number; resets_at: string; value: number; best: number }

const TIER_LABEL: Record<Tier, string> = { common: "Common", uncommon: "Uncommon", rare: "Rare", holo: "Holo Rare", ultra: "Chase card" };
const usd = (c: number | null | undefined) => {
  if (!c) return "—";
  const d = c >= 10_000 ? 0 : 2;  // $52.50, $1,234
  return `$${(c / 100).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}`;
};
const wait = (ms: number) => new Promise((r) => window.setTimeout(r, ms));
/** A card's estimate; "$35+" when it's a floor (a rarer print shown at least at the commoner print's price). */
const est = (c: { value: number | null; value_floor?: boolean }) => (c.value ? `${usd(c.value)}${c.value_floor ? "+" : ""}` : "—");
const RARE_PRINT = (p?: string) => p === "1st Edition" || p === "Shadowless";
interface Binder { cards: Pulled[]; unique: number; pulls: number; value: number; priced?: number; best?: Pulled | null; in_portfolio?: boolean;
  auto_sell_dupes?: boolean }
interface PlayListing { id: number; card: { id: number; name: string; set: string; year: number; edition: string; image: string | null; tier: Tier };
  price_cents: number; seller: string; mine: boolean }
interface Play { currency: string; note: string; balance_cents: number; auto_sell_dupes: boolean; market: PlayListing[]; mine: PlayListing[];
  activity: { delta_cents: number; kind: string; note: string; at: string }[] }
/** Play USDT: a test currency with no value (never real USDT). */
const pusd = (c: number) => `${(c / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} Play USDT`;

const RANK_TIER: Tier[] = ["common", "uncommon", "rare", "holo", "ultra"];
/** The best card in a pack: the highest USD estimate, then the highest tier. */
function bestCard(o: Opened): Pulled {
  return [...o.cards].sort((a, b) => (b.value ?? 0) - (a.value ?? 0) || RANK_TIER.indexOf(b.tier) - RANK_TIER.indexOf(a.tier))[0];
}
const hitTier = (o: Opened): Tier => o.cards.reduce<Tier>((t, c) => (RANK_TIER.indexOf(c.tier) > RANK_TIER.indexOf(t) ? c.tier : t), "common");

const SHARE_URL = "https://pricechat.live/giveaway";

/** What a shared pull says: the best card, its USD estimate and the whole pack's. Always says it's virtual, for fun. */
function pullText(o: Opened, packName: string): string {
  const c = bestCard(o);
  const what = `${c.name}${c.edition && c.edition !== "—" && c.edition !== "Unlimited" ? ` (${c.edition})` : ""}`;
  const hype = hitTier(o) === "ultra" ? "✨ CHASE PULL! " : hitTier(o) === "holo" ? "🌈 Holo! " : "🎴 ";
  const worth = c.value ? ` worth ${est(c)}` : "";
  const pack = o.value > 0 && o.cards.length > 1 ? ` Whole pack: ${usd(o.value)}${o.cards.some((x) => x.value_floor) ? "+" : ""}.` : "";
  return `${hype}I just pulled ${what}${worth} from a virtual ${packName} pack on Price Chat!${pack} Free vintage pack giveaway, just for fun (virtual cards, not real money):`;
}

/** Shown automatically after a pack: the best card, big, with its USD value, and one-tap sharing (Telegram, X, copy,
 *  the phone's share sheet). Sharing never pays XP. */
function ShareCard({ o, packName }: { o: Opened; packName: string }) {
  const c = bestCard(o);
  const text = pullText(o, packName);
  const [copied, setCopied] = useState(false);
  const go = (url: string) => { playSound("tick"); window.open(url, "_blank", "noopener,noreferrer"); };
  async function native() {
    try { await navigator.share({ text, url: SHARE_URL }); } catch { /* cancelled */ }
  }
  async function copy() {
    try { await navigator.clipboard.writeText(`${text} ${SHARE_URL}`); setCopied(true); window.setTimeout(() => setCopied(false), 2000); } catch { /* blocked */ }
  }
  return (
    <section className={`gw-sharecard tier-${c.tier}`} aria-label="Share my pull">
      <div className="gw-sharecard-art">{c.image ? <img src={c.image} alt={c.name} /> : <span>{c.name}</span>}</div>
      <div className="gw-sharecard-body">
        <small>Your best pull</small>
        <b className="gw-sharecard-name">{c.name}</b>
        <span className="gw-sharecard-meta">{TIER_LABEL[c.tier]}{c.edition && c.edition !== "—" ? ` · ${c.edition}` : ""} · {c.set}</span>
        <strong className="gw-sharecard-usd">{est(c)}</strong>
        {o.value > 0 && o.cards.length > 1 && <span className="gw-sharecard-pack">Whole pack {usd(o.value)}{o.cards.some((x) => x.value_floor) ? "+" : ""} <i>(virtual estimates)</i></span>}
        <div className="gw-sharebtns">
          <button type="button" className="gw-sharebtn is-tg" onClick={() => go(`https://t.me/share/url?url=${encodeURIComponent(SHARE_URL)}&text=${encodeURIComponent(text)}`)}>✈️ Share on Telegram</button>
          <button type="button" className="gw-sharebtn is-x" onClick={() => go(`https://x.com/intent/post?text=${encodeURIComponent(text)}&url=${encodeURIComponent(SHARE_URL)}`)}>𝕏 Post</button>
          <button type="button" className="gw-sharebtn" onClick={() => void copy()}>{copied ? "✓ Copied" : "📋 Copy"}</button>
          {typeof navigator !== "undefined" && "share" in navigator && typeof navigator.share === "function" && (
            <button type="button" className="gw-sharebtn" onClick={() => void native()}>↗ More</button>)}
        </div>
      </div>
    </section>
  );
}

function Countdown({ to }: { to: string }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(t); }, []);
  const s = Math.max(0, Math.round((Date.parse(to) - now) / 1000));
  return <>{String(Math.floor(s / 3600)).padStart(2, "0")}:{String(Math.floor((s % 3600) / 60)).padStart(2, "0")}:{String(s % 60).padStart(2, "0")}</>;
}

/** A foil booster pack, drawn in CSS: its set's colours, the mascot in a window, a holographic sheen that follows the pointer. */
function PackArt({ p, big = false, state = "idle", bonus = false }: { p: Pack; big?: boolean; state?: string; bonus?: boolean }) {
  const el = useRef<HTMLDivElement>(null);
  const move = (e: PointerEvent<HTMLDivElement>) => {
    if (!el.current || reducedMotion()) return;
    const r = el.current.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
    el.current.style.setProperty("--rx", `${(0.5 - y) * 14}deg`);
    el.current.style.setProperty("--ry", `${(x - 0.5) * 18}deg`);
    el.current.style.setProperty("--sx", `${x * 100}%`);
    el.current.style.setProperty("--sy", `${y * 100}%`);
  };
  const leave = () => { el.current?.style.setProperty("--rx", "0deg"); el.current?.style.setProperty("--ry", "0deg"); };
  const [broken, setBroken] = useState(false);
  if (p.image && !broken) {
    return (
      <div ref={el} className={`gw-pack gw-real${big ? " is-big" : ""}${bonus ? " is-bonus" : ""} is-${state}`} onPointerMove={move} onPointerLeave={leave}
           style={{ "--c1": p.theme[0], "--c2": p.theme[1], "--c3": p.theme[2] } as CSSProperties}>
        <img className="gw-real-body" src={p.image} alt={`${p.name} booster pack`} decoding="async" onError={() => setBroken(true)} draggable={false} />
        <img className="gw-real-top" src={p.image} alt="" aria-hidden decoding="async" draggable={false} />
        <span className="gw-real-light" aria-hidden />
        <span className="gw-real-glint" aria-hidden />
        {bonus && <span className="gw-real-bonus">★ LEVEL-UP · HOLO GUARANTEED</span>}
      </div>
    );
  }
  return (
    <div ref={el} className={`gw-pack${big ? " is-big" : ""}${bonus ? " is-bonus" : ""} is-${state}`} onPointerMove={move} onPointerLeave={leave}
         style={{ "--c1": p.theme[0], "--c2": p.theme[1], "--c3": p.theme[2] } as CSSProperties}>
      <div className="gw-pack-crimp gw-pack-top" />
      <div className="gw-pack-body">
        <span className="gw-pack-brand">PRICE CHAT · {p.year}</span>
        <div className="gw-pack-window">{p.cover ? <img src={p.cover} alt="" loading="lazy" decoding="async" /> : <span>✨</span>}</div>
        {p.logo ? <img className="gw-pack-logo" src={p.logo} alt={p.name} decoding="async" /> : <strong className="gw-pack-name">{p.name}</strong>}
        <span className="gw-pack-tag">{p.tagline}</span>
        <span className="gw-pack-count">{bonus ? "★ LEVEL-UP · HOLO GUARANTEED" : "5 CARDS · VIRTUAL"}</span>
      </div>
      <div className="gw-pack-crimp gw-pack-bottom" />
      <span className="gw-pack-sheen" aria-hidden />
    </div>
  );
}

function CardFace({ c, flipped, onFlip, index, hit, charging, spotlight }: {
  c: Pulled; flipped: boolean; onFlip: () => void; index: number; hit: boolean; charging: boolean; spotlight: boolean;
}) {
  return (
    <button type="button" className={`gw-card tier-${c.tier}${flipped ? " is-flipped" : ""}${hit && !flipped ? " is-hit" : ""}${charging ? " is-charging" : ""}${spotlight ? " is-spot" : ""}`}
            onClick={onFlip} disabled={flipped || charging}
            style={{ "--i": index, "--fx": `${(2 - index) * 105}%` } as CSSProperties} aria-label={flipped ? `${c.name}, ${TIER_LABEL[c.tier]}` : index === 4 ? "Tap to reveal the hit card" : "Tap to reveal"}>
      <span className="gw-card-inner">
        <span className="gw-card-back" aria-hidden />
        <span className="gw-card-front">
          {c.image ? <img src={c.image} alt="" decoding="async" /> : <span className="gw-card-noimg">{c.name}</span>}
          {(c.tier === "holo" || c.tier === "ultra") && <span className="gw-foil" aria-hidden />}
          {c.new && <span className="gw-new">NEW</span>}
        </span>
      </span>
    </button>
  );
}

export function GiveawayPage() {
  const member = useSession();
  const [shelf, setShelf] = useState<Shelf | null>(null);
  const [sel, setSel] = useState<string>("base");
  const [bonus, setBonus] = useState(false);
  const [phase, setPhase] = useState<"idle" | "charging" | "rip" | "reveal" | "done">("idle");
  const [opened, setOpened] = useState<Opened | null>(null);
  const [flipped, setFlippedState] = useState<number>(0);
  const flippedRef = useRef(0);  // the live count: "Reveal all" and fast taps must never read a stale value
  const setFlipped = (n: number) => { flippedRef.current = n; setFlippedState(n); };
  const [banner, setBanner] = useState<string | null>(null);
  const [charging, setCharging] = useState<number | null>(null);  // the hit card building up before it turns
  const [spot, setSpot] = useState<number | null>(null);  // the chase card in the spotlight
  const [err, setErr] = useState<string | null>(null);
  const [binder, setBinder] = useState<Binder | null>(null);
  const [firstPack, setFirstPack] = useState<string | null>(null);
  const [play, setPlay] = useState<Play | null>(null);
  const [pick, setPick] = useState<Pulled | null>(null);  // a binder card picked to sell or list
  const [price, setPrice] = useState("");
  const [playMsg, setPlayMsg] = useState<string | null>(null);  // "1st Edition" / "Shadowless": announced before the cards
  const stage = useRef<HTMLDivElement>(null);
  const xpEl = useRef<HTMLSpanElement>(null);

  const [shelfErr, setShelfErr] = useState(false);
  const load = useCallback(() => {
    if (member === undefined) return;  // wait for the session: the shelf is loaded once, with the member's own numbers
    setShelfErr(false);
    void memberApi<Shelf>("/members/packs").then(setShelf).catch(() => setShelfErr(true));
    if (member) void memberApi<Binder>("/members/packs/binder").then(setBinder).catch(() => setBinder(null));
    if (member) void memberApi<Play>("/members/play").then(setPlay).catch(() => setPlay(null));
  }, [member]);
  useEffect(load, [load]);

  const pack = shelf?.packs.find((p) => p.key === sel) ?? shelf?.packs[0];
  const xpLeft = shelf?.xp_packs_left ?? 0;
  const levelPacks = shelf?.level_packs ?? 0;

  async function refreshPlay() {
    const [b, pl] = await Promise.all([memberApi<Binder>("/members/packs/binder"), memberApi<Play>("/members/play")]);
    setBinder(b);
    setPlay(pl);
  }

  /** Play Market moves: instant sell, list, buy, cancel. Play USDT only (a test currency: not real, no value). */
  async function playAct(path: string, body?: unknown, ok?: string) {
    setPlayMsg(null);
    try {
      await memberApi(path, { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) });
      playSound("xp");
      if (ok) setPlayMsg(ok);
      setPick(null);
      await refreshPlay();
    } catch (e) {
      setPlayMsg(e instanceof Error ? e.message : "That didn't work. Try again.");
    }
  }

  async function toggleAutoSell(on: boolean) {
    setPlay((p) => (p ? { ...p, auto_sell_dupes: on } : p));
    try {
      await memberApi("/members/play/auto-sell", { method: "POST", body: JSON.stringify({ on }) });
      playSound("tick");
    } catch {
      setPlay((p) => (p ? { ...p, auto_sell_dupes: !on } : p));
    }
  }

  async function toggleInPortfolio(show: boolean) {
    setBinder((b) => (b ? { ...b, in_portfolio: show } : b));
    try {
      const r = await memberApi<{ in_portfolio: boolean }>("/members/packs/portfolio", { method: "POST", body: JSON.stringify({ show }) });
      setBinder((b) => (b ? { ...b, in_portfolio: r.in_portfolio } : b));
      playSound("tick");
    } catch {
      setBinder((b) => (b ? { ...b, in_portfolio: !show } : b));
    }
  }

  async function open(useBonus = bonus) {
    if (!member) { openLogin(); return; }
    if (!pack || (phase !== "idle" && phase !== "done") || (useBonus && levelPacks <= 0)) return;
    setBonus(useBonus);
    warmSounds();
    setErr(null); setOpened(null); setFlipped(0); setBanner(null); setSpot(null); setCharging(null); setFirstPack(null);
    const calm = reducedMotion();
    setPhase("charging");
    const req = memberApi<Opened>(`/members/packs/${pack.key}/open${useBonus ? "?bonus=true" : ""}`, { method: "POST" });
    if (!calm) for (let i = 0; i < 4; i++) { playSound("tick"); await wait(210 - i * 30); }
    let r: Opened;
    try { r = await req; } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't open the pack.");
      setPhase("idle");
      return;
    }
    setPhase("rip");
    playSound("rip");
    await wait(calm ? 0 : 320);  // the tear lands with the sound
    if (stage.current) burst(stage.current, 22, 170, true);
    await wait(calm ? 0 : 520);
    if (RARE_PRINT(r.print)) {  // a rare print run: the whole pack is special, announced before the cards come out
      setFirstPack(r.print!);
      playSound("first");
      if (stage.current) { burst(stage.current, 30, 220, true); window.setTimeout(() => stage.current && burst(stage.current, 24, 160, true), 380); }
      await wait(calm ? 0 : 1500);
    }
    setOpened(r);
    setShelf((s) => (s ? { ...s, xp_packs_left: r.xp_packs_left, xp_today: r.xp_today ?? s.xp_today, level_packs: r.level_packs } : s));
    if (r.cards.some((c) => c.auto_sold)) void memberApi<Play>("/members/play").then(setPlay).catch(() => undefined);
    setPhase("reveal");
    if (!calm) for (let i = 0; i < 5; i++) { window.setTimeout(() => playSound("swish"), 60 + i * 95); }  // dealt out of the pack
  }

  async function flip(i: number) {
    if (!opened || i !== flippedRef.current) return;
    const c = opened.cards[i];
    const calm = reducedMotion();
    const last = i === opened.cards.length - 1;
    if (last && !calm) {  // the hit: a rising whoosh while the card trembles and glows, then it turns
      flippedRef.current = -1;  // no double taps while it builds up
      setCharging(i);
      playSound("rise");
      await wait(620);
      setCharging(null);
    }
    setFlipped(i + 1);
    playSound("swish");
    const el = document.querySelectorAll(".gw-card")[i] as HTMLElement | undefined;
    if (c.tier === "ultra") {
      await wait(calm ? 0 : 260);
      playSound("chase");
      setSpot(i);
      setBanner(`✨ CHASE CARD! ${c.edition && c.edition !== "—" && c.edition !== "Unlimited" ? `${c.edition} ` : ""}${c.name}`);
      if (el) { burst(el, 30, 190, true); window.setTimeout(() => el && burst(el, 22, 140, true), 300); window.setTimeout(() => el && burst(el, 16, 220, true), 650); }
      window.setTimeout(() => setSpot(null), calm ? 0 : 1900);
    } else if (c.tier === "holo") {
      await wait(calm ? 0 : 220);
      playSound("shine");
      setBanner(`🌈 Holo! ${c.name}`);
      if (el) burst(el, 16, 120, true);
    }
    if (i + 1 === opened.cards.length) {
      await wait(c.tier === "ultra" ? 2100 : 800);
      setPhase("done");
      if (opened.xp > 0) {
        playSound("coin");
        window.setTimeout(() => playSound("xp"), 180);
        await wait(30);
        if (xpEl.current) { burst(xpEl.current, 16, 90, true); window.setTimeout(() => xpEl.current && burst(xpEl.current, 10, 60, true), 260); }
        refreshSession();
      }
      load();
    }
  }

  async function revealAll() {
    if (!opened || flippedRef.current < 0) return;
    for (let i = flippedRef.current; i < opened.cards.length; i++) { await flip(i); await wait(reducedMotion() ? 0 : 260); }
  }

  return (
    <SitePage {...pageHead("giveaway")}>
      <section className="gw">
        <div className="gw-warning" role="note">
          <strong>⚠️ Just for fun: test feature.</strong> These are <b>virtual</b> packs and <b>virtual</b> cards. No real money is involved, nothing can be bought,
          and you <b>don't win real cards or prizes</b>: nothing is shipped, sold, traded or withdrawn. Card values shown are public market estimates for fun only.
        </div>

        <header className="gw-head">
          <h1 className="site-display">Pack <span className="site-green">Giveaway</span> 🎁</h1>
          {member && levelPacks > 0 && (
            <div className="gw-levelup" role="status">
              <span className="gw-levelup-badge">★ {levelPacks}</span>
              <div><b>Level-up {levelPacks === 1 ? "pack" : "packs"} waiting!</b><small>One for every Kiwi Passport level you reach: holo guaranteed, 30% chase card.</small></div>
              <button type="button" className="gw-open gw-gold" disabled={phase !== "idle" && phase !== "done"}
                      onClick={() => { setPhase("idle"); setOpened(null); void open(true); }}>Open Level-up pack</button>
            </div>
          )}
        </header>

        {!shelf && (shelfErr ? (
          <div className="site-empty" role="alert">
            <p>Couldn't load the packs right now.</p>
            <button type="button" className="site-button" onClick={load}>Try again</button>
          </div>
        ) : <p className="site-empty">Loading the packs…</p>)}

        {shelf && (
          <div className="gw-shelf" role="listbox" aria-label="Choose a pack">
            {shelf.packs.map((p) => (
              <button key={p.key} type="button" role="option" aria-selected={p.key === pack?.key} className={`gw-shelf-item${p.key === pack?.key ? " is-on" : ""}`}
                      onClick={() => { if (phase === "idle" || phase === "done") { setSel(p.key); setPhase("idle"); setOpened(null); playSound("tick"); } }}>
                <PackArt p={p} />
                <span className="gw-shelf-meta">{p.owned}/{p.size} collected · {p.chase} chase cards</span>
              </button>
            ))}
          </div>
        )}

        {pack && (
          <div className={`gw-stage is-${phase}${spot !== null ? " is-spotlight" : ""}${firstPack || RARE_PRINT(opened?.print) ? " is-firstpack" : ""}`} ref={stage}
               style={shelf?.card_back ? ({ "--back": `url("${shelf.card_back}")` } as CSSProperties) : undefined}>
            {(phase === "idle" || phase === "charging" || phase === "rip") && (
              <>
                <PackArt p={pack} big state={phase} bonus={bonus && phase !== "idle"} />
                {phase === "rip" && <span className="gw-flash" aria-hidden />}
                {firstPack && <p className="gw-firstpack" role="status"><span>🔥</span> {firstPack} pack! <span>🔥</span></p>}
                <button type="button" className="gw-open" onClick={() => void open(false)} disabled={phase !== "idle"}>
                  {!member ? "Sign in to open packs" : phase === "idle" ? `Open ${pack.name}` : "Opening…"}
                </button>
              </>
            )}
            {(phase === "reveal" || phase === "done") && opened && (
              <>
                {RARE_PRINT(opened.print) && <p className="gw-print-badge">🔥 {opened.print} pack</p>}
                {banner && <p key={banner} className={`gw-banner${banner.startsWith("✨") ? " is-ultra" : ""}`} role="status">{banner}</p>}
                <div className="gw-cards">
                  {opened.cards.map((c, i) => <CardFace key={i} c={c} index={i} flipped={i < flipped} onFlip={() => void flip(i)}
                                                        hit={i === opened.cards.length - 1} charging={charging === i} spotlight={spot === i} />)}
                </div>
                {phase === "reveal" ? (
                  <div className="gw-row">
                    <p className="gw-hint">Tap the cards to reveal them, last one is the hit 👀</p>
                    <button type="button" className="gw-ghost" onClick={() => void revealAll()}>Reveal all</button>
                  </div>
                ) : (
                  <div className="gw-summary">
                    <ul className="gw-pulled">
                      {opened.cards.map((c, i) => (
                        <li key={i} className={`tier-${c.tier}`}><b>{c.name}</b> <span>{TIER_LABEL[c.tier]}{c.edition && c.edition !== "—" ? ` · ${c.edition}` : ""}</span>
                          <em>{est(c)}</em>{c.new && <small>NEW</small>}
                          {c.auto_sold ? <small className="gw-autosold">auto-sold +{pusd(c.auto_sold)}</small> : null}</li>
                      ))}
                    </ul>
                    {opened.value > 0 && <p className="gw-total">Virtual pull value <b>{usd(opened.value)}{opened.cards.some((c) => c.value_floor) ? "+" : ""}</b> <small>(market estimates for fun: not money, not real cards)</small></p>}
                    <ShareCard o={opened} packName={pack.name} />
                    {opened.xp > 0 && <span ref={xpEl} className="gw-xp">+{opened.xp} Kiwi XP</span>}
                    <div className="gw-row">
                      <button type="button" className="gw-open" onClick={() => { setBonus(false); setPhase("idle"); setOpened(null); void open(false); }}>Open another {pack.name}</button>
                      <a className="gw-ghost" href="#binder">View binder</a>
                    </div>
                  </div>
                )}
              </>
            )}
            {err && <p className="site-error" role="alert">{err}</p>}
          </div>
        )}

        {/* about the packs: under the pack opening (only the disclaimer and the title stay on top) */}
        <div className="gw-about">
          <p>Rip open original <b>vintage</b> packs (1999-2003), free and unlimited for now. Chase 1st Edition holos, Shining Pokémon and secret rares,
            fill your binder and keep your daily streak alive.</p>
          {member && shelf && (
            <p className="gw-left">
              <span>{xpLeft > 0 ? <><b>+5 Kiwi XP</b> every pack · <b>{shelf.xp_today ?? 0}</b> / {shelf.xp_cap ?? 100} XP today</> : <>Today's 100 pack XP collected · more in <b><Countdown to={shelf.resets_at} /></b></>}</span>
              <span className="gw-meter" role="progressbar" aria-label="Pack XP today" aria-valuemin={0} aria-valuemax={shelf.xp_cap ?? 100} aria-valuenow={shelf.xp_today ?? 0}>
                <i style={{ ["--p" as string]: Math.min(1, (shelf.xp_today ?? 0) / (shelf.xp_cap ?? 100)) }} />
              </span>
            </p>
          )}
          {member && levelPacks === 0 && shelf?.rank_level != null && (
            <p className="gw-hint">🎁 Reach the next Kiwi Passport level to earn a boosted Level-up pack.</p>
          )}
          <a className="gw-market-link" href="/pokemon">📈 What are these cards worth? Open the Pokémon Market →</a>
        </div>

        {member && binder && (
          <section className="gw-binder" id="binder" aria-labelledby="gw-binder-title">
            <div className="gw-binder-head">
              <h2 className="site-h3" id="gw-binder-title">Your binder</h2>
              {play && <span className="gw-wallet" title={play.note}>💰 {pusd(play.balance_cents)}</span>}
            </div>
            {binder.cards.length > 0 && <p className="gw-hint">Tap a card to sell it now or list it on the Play Market.</p>}
            <p className="gw-binder-stats">{binder.unique} unique cards · {binder.pulls} pulled{binder.value > 0 ? ` · virtual value ${usd(binder.value)}` : ""}</p>
            {binder.cards.length === 0 ? <p className="site-note">Open your first pack: every card you pull lands here.</p> : (
              <ul className="gw-binder-grid">
                {binder.cards.map((c) => (
                  <li key={c.id} className={`tier-${c.tier}${pick?.id === c.id ? " is-picked" : ""}`} title={`${c.name} · ${c.set}${c.number ? ` #${c.number}` : ""} · ${TIER_LABEL[c.tier]}`}
                      role="button" tabIndex={0} aria-label={`${c.name}: sell or list`}
                      onClick={() => { setPick(c); setPrice(c.value ? (c.value / 100).toFixed(2) : ""); setPlayMsg(null); }}
                      onKeyDown={(e) => { if (e.key === "Enter") { setPick(c); setPrice(c.value ? (c.value / 100).toFixed(2) : ""); } }}>
                    {c.image ? <img src={c.image} alt={c.name} loading="lazy" decoding="async" /> : <span>{c.name}</span>}
                    {(c.count ?? 1) > 1 && <b>×{c.count}</b>}
                    {c.value ? <em className="gw-binder-val">{est(c)}</em> : null}
                  </li>
                ))}
              </ul>
            )}
            {pick && (
              <div className="gw-sell" role="dialog" aria-label={`Sell ${pick.name}`}>
                {pick.image && <img src={pick.image} alt="" />}
                <div className="gw-sell-body">
                  <b>{pick.name}</b>
                  <small>{pick.set} · {pick.edition !== "—" ? pick.edition : ""} · est. {est(pick)}</small>
                  <div className="gw-sell-actions">
                    <button type="button" className="gw-open gw-sell-now" disabled={!pick.value}
                            onClick={() => void playAct("/members/play/sell", { card_id: pick.id }, `Sold ${pick.name} for ${pusd(Math.floor((pick.value ?? 0) * 0.95))}`)}>
                      ⚡ Sell now {pick.value ? `+${pusd(Math.floor(pick.value * 0.95))}` : ""}</button>
                    <span className="gw-sell-list">
                      <input type="number" inputMode="decimal" min="0.01" step="0.01" value={price} onChange={(e) => setPrice(e.target.value)}
                             aria-label="Your price in Play USDT" />
                      <button type="button" className="gw-ghost" disabled={!(Number(price) > 0)}
                              onClick={() => void playAct("/members/play/list", { card_id: pick.id, price_cents: Math.round(Number(price) * 100) },
                                                          `${pick.name} listed for ${pusd(Math.round(Number(price) * 100))}`)}>List</button>
                    </span>
                    <button type="button" className="site-textbutton" onClick={() => setPick(null)}>Cancel</button>
                  </div>
                  {!pick.value && <small>No market estimate yet: list it with your own price.</small>}
                </div>
              </div>
            )}
            {playMsg && <p className="gw-play-msg" role="status">{playMsg}</p>}
            {binder.cards.length > 0 && play && (
              <label className="gw-optin">
                <input type="checkbox" checked={play.auto_sell_dupes} onChange={(e) => void toggleAutoSell(e.target.checked)} />
                <span><b>Auto-sell duplicates</b>
                  <small>Extra copies of cards you already have are sold instantly (5% below market), for Play USDT.</small></span>
              </label>
            )}
            {binder.cards.length > 0 && (
              <label className="gw-optin">
                <input type="checkbox" checked={!!binder.in_portfolio} onChange={(e) => void toggleInPortfolio(e.target.checked)} />
                <span><b>Add my pulls to my Pokémon Portfolio</b>
                  <small>They count in your portfolio's value and chart, marked 🎁 Virtual (not owned, not real), and never on public leaderboards.
                    {binder.in_portfolio && <> <a href="/pokemon/portfolio">Open my portfolio →</a></>}</small></span>
              </label>
            )}
            {binder.cards.length > 0 && (
              <div className="gw-binder-total" role="status">
                <span className="gw-binder-total-label">Your binder's market value</span>
                <strong>{binder.value > 0 ? usd(binder.value) : "—"}</strong>
                <span className="gw-binder-total-meta">{binder.priced ?? 0} of {binder.unique} cards priced · all {binder.pulls} pulls counted
                  {binder.best?.value ? <> · best pull <b>{binder.best.name}</b> {est(binder.best)}</> : null}</span>
                <small>Ungraded market estimates (Price Chat market data), for fun only: virtual cards, not money and not real cards.
                  Cards without a known price aren't counted. "+" means at least: a 1st Edition or Shadowless card with no price of its own
                  is shown at its Unlimited price.</small>
              </div>
            )}
          </section>
        )}

        {member && play && (
          <section className="gw-market" aria-labelledby="gw-market-title">
            <div className="gw-binder-head">
              <h2 className="site-h3" id="gw-market-title">Play Market</h2>
              <span className="gw-wallet">💰 {pusd(play.balance_cents)}</span>
            </div>
            <p className="gw-play-note">⚠️ {play.note}</p>
            {play.market.length === 0 ? <p className="site-note">No cards for sale right now. List one from your binder.</p> : (
              <ul className="gw-market-grid">
                {play.market.map((l) => (
                  <li key={l.id} className={`tier-${l.card.tier}`}>
                    {l.card.image ? <img src={l.card.image} alt={l.card.name} loading="lazy" decoding="async" /> : <span>{l.card.name}</span>}
                    <div><b>{l.card.name}</b><small>by {l.seller}</small></div>
                    <button type="button" className="gw-open gw-buy" disabled={play.balance_cents < l.price_cents}
                            onClick={() => void playAct(`/members/play/listings/${l.id}/buy`, undefined, `Bought ${l.card.name}: it's in your binder.`)}>
                      Buy · {pusd(l.price_cents)}</button>
                  </li>
                ))}
              </ul>
            )}
            {play.mine.length > 0 && (
              <>
                <h3 className="gw-sub">Your listings</h3>
                <ul className="gw-mine">
                  {play.mine.map((l) => (
                    <li key={l.id}><b>{l.card.name}</b> <span>{pusd(l.price_cents)}</span>
                      <button type="button" className="site-textbutton" onClick={() => void playAct(`/members/play/listings/${l.id}/cancel`, undefined, `${l.card.name} is back in your binder.`)}>
                        Cancel</button></li>
                  ))}
                </ul>
              </>
            )}
          </section>
        )}

        <details className="site-details gw-rules">
          <summary>How it works & odds</summary>
          <ul>
            <li>Original vintage sets only (1999-2003). Free and unlimited for now; nothing to buy, ever.</li>
            <li>Each pack has 5 cards from the real set list: 2 commons, 1 uncommon, 1 uncommon-or-rare, and a final <b>hit</b> card.</li>
            <li>Print runs, like the real thing: each pack is Unlimited, or rarely a <b>1st Edition</b> pack (1% for Base Set, 2% for the other sets)
              or a <b>Shadowless</b> pack (4%, Base Set only). All five cards come from that print, and its holo is the chase.</li>
            <li>Hit card odds: Rare 65% · Holo 27% · Chase 8% (a Shining Pokémon, a secret rare or a reverse foil, where the set has them).</li>
            <li>Level-up packs: one for every Kiwi Passport level you reach. The hit is always a holo, with a 30% chase chance, and three times the chance of a 1st Edition pack.</li>
            <li>+5 Kiwi XP for every pack, up to 100 XP a day, and opening a pack counts as your daily task for the streak.</li>
            <li>Virtual only: cards can't be exchanged for anything real. Values are public market estimates shown for fun.</li>
            <li>Play Market: buy any card at market, sell instantly at 5% below market, bid, or list it at your price for other members. Paid in <b>Play USDT</b>,
              a test currency: not real USDT, no value, can't be bought, deposited or withdrawn, and it never buys packs.</li>
          </ul>
        </details>
      </section>
    </SitePage>
  );
}
