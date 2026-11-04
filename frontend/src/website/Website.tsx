import { useEffect, useRef, useState, type ReactNode } from "react";
import { NETWORK_LABEL, site, type Network } from "./websiteData";
import {
  displayTitle, fallbackThumb, filterArchive, formatDate, formatViews, growthChange, handleFromUrl, sectionStates, sparkPoints,
  type ArchiveFilter, type Growth, type HomeData, type Quote, type SectionKey, type WebsiteCard,
} from "./websiteLogic";
import { PAGE_HEADS } from "./seo";
import { useHead } from "./useHead";
import { noteContentOpened, useSession } from "./members";
import { RankKiwi } from "./RankKiwi";
import { LoginHost, openLogin } from "./emailLogin";
import { ChatBubble } from "./chatBubble";
import "@fontsource-variable/archivo/wdth.css";
import "./website.css";
import "./motion.css";
import { KiwiFlight } from "./KiwiFlight";
import { KiwiRig, useKiwiIdle } from "./KiwiRig";
import { reducedMotion, useInViewOnce, usePointerParallax, useReveal } from "./motion";

/**
 * The public Price Chat website. Copy comes from websiteData.ts; everything else (videos, franchises,
 * trending topics, links, section switches) from the Price Chat backend, which syncs YouTube on its own.
 * The newest public upload is always the featured item. Sections without real data are not rendered.
 */
export function Website() {
  const home = useStored<HomeData>("/api/website/home");
  const states = home ? sectionStates(home) : null;
  const show = (k: SectionKey) => !!states?.[k].visible;
  useHead({
    ...PAGE_HEADS[""], // seo.ts (the link previews match it)
    path: site.publicPaths.home,
    siteUrl: home?.site_url ?? null,
    image: site.shareImage ?? home?.latest[0]?.thumbnail ?? null,
  });
  const root = useReveal();
  return (
    <div className="site" ref={root}>
      <SiteHeader home={home} base="" />
      <main>
        <Hero />
        {show("trending") && <TrendingStrip topics={home!.trending} />}
        {home === null || show("latest") ? <LatestSection items={home?.latest ?? null} /> : null}
        {show("whats_it_worth") && <WhatsItWorth items={home!.whats_it_worth} />}
        {show("price_board") && <PriceBoard quotes={home!.price_board} />}
        {show("originals") && <Originals items={home!.originals} />}
        {(home === null || show("story")) && <Story />}
        {show("community") && <Community links={home!.links} growth={home!.growth} />}
      </main>
      <SiteFooter home={home} />
    </div>
  );
}

/** "View all": every published item, newest first, filterable by format (and by category from the nav). */
export function ContentArchive() {
  const home = useStored<HomeData>("/api/website/home");
  const items = useStored<{ items: WebsiteCard[] }>("/api/website/content?limit=200")?.items ?? null;
  const params = new URLSearchParams(typeof location !== "undefined" ? location.search : "");
  const category = home?.categories.includes(params.get("category") ?? "") ? params.get("category") : null;
  const [filter, setFilter] = useState<ArchiveFilter>("all");
  const shown = items ? filterArchive(items, filter, category) : null;
  const heading = category ? navLabel(category) : "All content";
  useHead({
    title: category ? `${heading} · Price Chat` : PAGE_HEADS.content.title,
    description: PAGE_HEADS.content.description,
    path: site.publicPaths.content,
    siteUrl: home?.site_url ?? null,
    image: site.shareImage ?? items?.[0]?.thumbnail ?? null,
  });
  const root = useReveal();
  // Later formats (articles, X posts) get a tab automatically once such content exists.
  const extra = items ? [...new Set(items.map((i) => i.type))].filter((t) => t !== "short" && t !== "long_form") : [];
  const tabs: { key: ArchiveFilter; label: string }[] = [
    { key: "all", label: "All" },
    { key: "short", label: "Shorts" },
    { key: "long_form", label: "Videos" },
    ...extra.map((t) => ({ key: t as ArchiveFilter, label: items!.find((i) => i.type === t)!.type_label })),
  ];
  return (
    <div className="site" ref={root}>
      <SiteHeader home={home} base="/" solid />
      <main className="site-archive-page site-page-anim">
        <div className="site-wrap">
          <div className="site-archive-head">
            <h1 className="site-display site-h1">{heading}</h1>
            <div className="site-filters" role="tablist" aria-label="Format">
              {tabs.map((t) => (
                <button key={t.key} role="tab" aria-selected={filter === t.key} className="site-filter" onClick={() => setFilter(t.key)}>
                  {t.label}
                </button>
              ))}
            </div>
          </div>
          {shown === null ? (
            <div className="site-grid">{[0, 1, 2, 3, 4, 5].map((n) => <CardSkeleton key={n} />)}</div>
          ) : shown.length === 0 ? (
            <p className="site-empty">{filter === "long_form" ? "No long-form videos yet." : filter === "short" ? "No Shorts yet." : site.latest.empty}</p>
          ) : (
            <div className="site-grid">
              {shown.map((c) => <Card key={c.url} c={c} />)}
            </div>
          )}
        </div>
      </main>
      <SiteFooter home={home} />
    </div>
  );
}

// --- data --------------------------------------------------------------------------------------------

/** Stored content from the backend. The last good answer is kept in the browser, so a hiccup never empties the page. */
export function useStored<T>(path: string): T | null {
  const key = `pc-site:${path}`;
  const [data, setData] = useState<T | null>(() => {
    try {
      const cached = localStorage.getItem(key);
      return cached ? (JSON.parse(cached) as T) : null;
    } catch {
      return null;
    }
  });
  useEffect(() => {
    let alive = true;
    fetch(path, { headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((body: T) => {
        if (!alive) return;
        setData(body);
        try {
          localStorage.setItem(key, JSON.stringify(body));
        } catch {
          /* storage unavailable: fine */
        }
      })
      .catch(() => undefined); // keep the cached copy (or the loading state)
    return () => {
      alive = false;
    };
  }, [path, key]);
  return data;
}

/** Sections fade up once as they enter the viewport. Skipped entirely for reduced motion or old browsers. */
export { useReveal };

// --- brand -------------------------------------------------------------------------------------------

/** The one Kiwi: always the canonical asset (websiteData `site.kiwi`), only ever placed, scaled, lit or mirrored. */
export function Kiwi({ className, alt = "", eager = false }: { className?: string; alt?: string; eager?: boolean }) {
  const { src, width, height } = site.kiwi;
  return (
    <img className={className} src={src} width={width} height={height} alt={alt} decoding="async"
         loading={eager ? "eager" : "lazy"} {...(eager ? { fetchPriority: "high" as const } : {})} />
  );
}

function LogoMark({ size = 36 }: { size?: number }) {
  return (
    <span className="site-logo-mark" style={{ width: size, height: size }} aria-hidden>
      <Kiwi eager />
    </span>
  );
}

function Wordmark() {
  return (
    <span className="site-wordmark">
      PRICE <span className="site-green">CHAT</span>
    </span>
  );
}

const ICON: Record<Network, string> = {
  telegram: "M21.4 3.6 2.9 10.7c-1.3.5-1.2 1.2-.2 1.5l4.7 1.5 1.8 5.6c.2.6.4.8.9.8.4 0 .6-.2.9-.5l2.3-2.2 4.7 3.5c.9.5 1.5.2 1.7-.8l3.1-14.6c.3-1.3-.5-1.9-1.4-1.5ZM9.6 14.3l-.4 4-1.5-4.9 10.2-6.5-8.3 7.4Z",
  youtube: "M22.5 7.2a2.8 2.8 0 0 0-2-2C18.8 4.7 12 4.7 12 4.7s-6.8 0-8.5.5a2.8 2.8 0 0 0-2 2C1 8.9 1 12 1 12s0 3.1.5 4.8a2.8 2.8 0 0 0 2 2c1.7.5 8.5.5 8.5.5s6.8 0 8.5-.5a2.8 2.8 0 0 0 2-2c.5-1.7.5-4.8.5-4.8s0-3.1-.5-4.8ZM9.8 15.1V8.9l5.7 3.1-5.7 3.1Z",
  x: "M17.8 3h3.1l-6.8 7.7L22 21h-6.2l-4.9-6.4L5.3 21H2.2l7.3-8.3L1.8 3h6.4l4.4 5.8L17.8 3Zm-1.1 16.2h1.7L7.4 4.7H5.6l11.1 14.5Z",
};
export const NETWORK_ORDER: Network[] = ["youtube", "x", "telegram"];

export function Icon({ network }: { network: Network }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className="site-icon">
      <path d={ICON[network]} />
    </svg>
  );
}

export function socials(home: HomeData | null): { network: Network; href: string }[] {
  if (!home) return [];
  return NETWORK_ORDER.flatMap((n) => (home.links[n] ? [{ network: n, href: home.links[n]! }] : []));
}

function navLabel(key: string): string {
  return site.nav.find((n) => n.key === key)?.label ?? key;
}

// --- header ------------------------------------------------------------------------------------------

export function SiteHeader({ home, base, solid = false }: { home: HomeData | null; base: string; solid?: boolean }) {
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(solid);
  useEffect(() => {
    if (solid) return;
    const onScroll = () => {
      const s = window.scrollY > 24;
      setScrolled(s);
      document.documentElement.classList.toggle("pc-scrolled", s); // idle motion rests while the glass header is on
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [solid]);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const toDesktop = () => window.innerWidth > 760 && setOpen(false);
    const onNav = () => setOpen(false);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", toDesktop);
    window.addEventListener("hashchange", onNav);
    window.addEventListener("popstate", onNav);
    // lock the page behind the menu: overflow alone doesn't stop iPhone Safari, so the body is pinned where it was
    const y = window.scrollY;
    const body = document.body.style;
    const prev = { position: body.position, top: body.top, left: body.left, right: body.right, width: body.width, overflow: body.overflow };
    document.documentElement.classList.add("site-menu-open");
    Object.assign(body, { position: "fixed", top: `-${y}px`, left: "0", right: "0", width: "100%", overflow: "hidden" });
    return () => {
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", toDesktop);
      window.removeEventListener("hashchange", onNav);
      window.removeEventListener("popstate", onNav);
      document.documentElement.classList.remove("site-menu-open");
      Object.assign(body, prev);
      const wanted = window.location.hash ? null : y;  // a #section link scrolls itself; otherwise back to where they were
      if (wanted !== null) window.scrollTo(0, wanted);
    };
  }, [open]);

  const states = home ? sectionStates(home) : null;
  // A link only appears when its destination has real content.
  const links = site.nav.flatMap((n): { label: string; href: string }[] => {
    if (n.key === "latest") return states && !states.latest.visible ? [] : [{ label: n.label, href: `${base}#latest` }];
    if (n.key === "about") return states && !states.story.visible ? [] : [{ label: n.label, href: `${base}#story` }];
    return home?.categories.includes(n.key) ? [{ label: n.label, href: `/content?category=${n.key}` }] : [];
  });
  const social = socials(home);
  const onGive = typeof location !== "undefined" && location.pathname.startsWith("/giveaway");
  const onBot = typeof location !== "undefined" && (location.pathname.startsWith("/earn") || location.pathname.startsWith("/trading-bot"));
  const showBot = !!home?.trading_bot || onBot;  // the tab appears once the owner opens the funnel
  const onVpn = typeof location !== "undefined" && location.pathname.startsWith("/vpn");
  const member = useSession();
  const showVpn = !!member || onVpn;  // Kiwi VPN is a member perk: signed-in members only
  return (
    <header className={`site-header${scrolled || open ? " is-solid" : ""}${open ? " is-open" : ""}`}>
      <div className="site-header-inner">
        <a className="site-brand" href={base || "#top"} aria-label="Price Chat home">
          <LogoMark />
          <Wordmark />
        </a>
        <nav className="site-nav" aria-label="Sections">
          {links.map((l) => (
            <a key={l.label} href={l.href} className="site-link" onClick={() => setOpen(false)}>
              {l.label}
            </a>
          ))}
          <a href="/giveaway" className={`site-link site-link-give${onGive ? " is-current" : ""}`} aria-current={onGive ? "page" : undefined}>
            Giveaway
          </a>
          {showBot && (
            <a href="/earn" className={`site-link site-link-bot${onBot ? " is-current" : ""}`} aria-current={onBot ? "page" : undefined}>
              Earn
            </a>
          )}
          {showVpn && (
            <a href="/vpn" className={`site-link site-link-vpn${onVpn ? " is-current" : ""}`} aria-current={onVpn ? "page" : undefined}>
              VPN
            </a>
          )}
        </nav>
        <div className="site-header-social">
          {social.map((s) => (
            <a key={s.network} href={s.href} target="_blank" rel="noopener noreferrer" aria-label={NETWORK_LABEL[s.network]} className="site-icon-link">
              <Icon network={s.network} />
            </a>
          ))}
        </div>
        <AccountLink />
        <button className="site-menu-btn" aria-label={open ? "Close menu" : "Open menu"} aria-expanded={open} aria-controls="site-menu" onClick={() => setOpen(!open && window.innerWidth <= 760)}>
          <span />
          <span />
        </button>
      </div>
      <div className="site-menu" id="site-menu" hidden={!open}>
        <nav aria-label="Menu">
          {links.map((l) => (
            <a key={l.label} href={l.href} onClick={() => setOpen(false)}>
              {l.label}
            </a>
          ))}
          {showBot && (
            <a href="/earn" onClick={() => setOpen(false)}>
              💰 Earn
            </a>
          )}
          {showVpn && (
            <a href="/vpn" onClick={() => setOpen(false)}>
              🛡️ Kiwi VPN
            </a>
          )}
          <a href="/giveaway" onClick={() => setOpen(false)}>
            🎁 Pack Giveaway
          </a>
          <a href="/website/leaderboard" onClick={() => setOpen(false)}>
            Leaderboard
          </a>
        </nav>
        {social.length > 0 && (
          <div className="site-menu-social">
            {social.map((s) => (
              <a key={s.network} href={s.href} target="_blank" rel="noopener noreferrer">
                <Icon network={s.network} />
                {NETWORK_LABEL[s.network]}
              </a>
            ))}
          </div>
        )}
      </div>
      <LoginHost />
      <ChatBubble />
    </header>
  );
}

/** LOGIN, or the signed-in member's Kiwi, name and rank (links to their Kiwi Passport). */
function AccountLink() {
  const member = useSession();
  if (member === undefined) return <span className="site-account site-account-loading" aria-hidden />;
  if (member === null)
    return (
      <a className="site-account site-login" href="/website/join" onClick={(e) => { e.preventDefault(); openLogin(); }}>
        Login
      </a>
    );
  return (
    <a className="site-account site-account-member" href="/passport" aria-label={`${member.username}, ${member.rank.name}: your Kiwi Passport`}>
      <RankKiwi art={member.rank.art} rankKey={member.rank.key} size={30} />
      {member.unread ? <span className="site-account-dot" aria-label={`${member.unread} new notifications`} /> : null}
      <span className="site-account-text">
        <span className="site-account-name">{member.username}</span>
        <span className="site-account-rank">{member.rank.name}</span>
      </span>
    </a>
  );
}

// --- hero --------------------------------------------------------------------------------------------

function Hero() {
  const { line1, line2, accent, support } = site.hero;
  const ref = useRef<HTMLElement>(null);
  const kiwi = useRef<HTMLDivElement>(null);
  usePointerParallax(ref);
  useKiwiIdle(kiwi);
  return (
    <section className="site-hero" id="top" ref={ref} data-pause-offscreen>
      <div className="site-hero-kiwi price-glow" ref={kiwi}>
        <span className="pc-parallax">
          <span className="pc-idle-kiwi" style={{ display: "block", position: "relative" }}>
            <span className="site-hero-kiwi-shadow" aria-hidden />
            <KiwiRig variant="hero" alt="Kiwi, the Price Chat mascot" eager />
          </span>
        </span>
      </div>
      <h1 className="site-display site-hero-title">
        <span className="site-hero-line">
          <span className="site-hero-word site-hero-w1">{line1}</span>
        </span>
        <span className="site-hero-line">
          <span className="site-hero-word site-hero-w2">{line2}</span>{" "}
          <span className="site-hero-word site-hero-w3 site-green">{accent}</span>
        </span>
      </h1>
      <p className="site-hero-support">
        {support.map((s) => (
          <span key={s}>{s}</span>
        ))}
      </p>
    </section>
  );
}

// --- trending ----------------------------------------------------------------------------------------

/** Human-approved community topics. Names only, no prices: nothing here is market data. */
function TrendingStrip({ topics }: { topics: string[] }) {
  const row = (hidden: boolean) => (
    <ul className="site-ticker-row" aria-hidden={hidden || undefined}>
      {topics.map((t) => (
        <li key={t}>{t}</li>
      ))}
    </ul>
  );
  return (
    <section className="site-ticker" aria-label="Trending in the Price Chat community">
      <span className="site-ticker-label">Trending</span>
      <div className={`site-ticker-track${topics.length > 3 ? " is-moving" : ""}`}>
        {row(false)}
        {topics.length > 3 && row(true)}
      </div>
    </section>
  );
}

// --- content cards -----------------------------------------------------------------------------------

/** YouTube thumbnails: a small 16:9 image for narrow slots, the large one where it is shown big. */
function thumbSources(c: WebsiteCard): { src: string | null; srcSet?: string } {
  const src = c.thumbnail ?? fallbackThumb(c);
  const m = src?.match(/^(https:\/\/i\.ytimg\.com\/vi\/[^/]+\/)maxresdefault\.jpg$/);
  return m ? { src, srcSet: `${m[1]}mqdefault.jpg 320w, ${src} 1280w` } : { src };
}

function Thumb({ c, sizes, eager = false }: { c: WebsiteCard; sizes: string; eager?: boolean }) {
  const initial = thumbSources(c);
  const [img, setImg] = useState(initial);
  const fallback = fallbackThumb(c);
  const fail = () => setImg((cur) => (cur.src && cur.src !== fallback && fallback ? { src: fallback } : { src: null }));
  return (
    <span className="site-thumb">
      {img.src ? (
        <img src={img.src} srcSet={img.srcSet} sizes={img.srcSet ? sizes : undefined} alt="" width={1280} height={720}
             loading={eager ? "eager" : "lazy"} decoding="async" onError={fail} {...(eager ? { fetchPriority: "high" as const } : {})} />
      ) : (
        <span className="site-thumb-empty" />
      )}
      <span className="site-play" aria-hidden>
        <svg viewBox="0 0 24 24">
          <path d="M8 5.5v13l11-6.5z" />
        </svg>
      </span>
    </span>
  );
}

function Meta({ c }: { c: WebsiteCard }) {
  const bits = [c.duration, formatDate(c.published_at)].filter(Boolean) as string[];
  const views = formatViews(c.views);
  return (
    <span className="site-meta">
      <span className="site-meta-type">{c.type_label}</span>
      {bits.map((b) => (
        <span key={b}>{b}</span>
      ))}
      {views && <span className="site-meta-views">{views}</span>}
    </span>
  );
}

/** Opens the real video on YouTube. The headline is tidied for display; the stored title is unchanged. */
function ext(c: WebsiteCard) {
  const opened = () => noteContentOpened(c.external_id);
  return /^https?:/.test(c.url) ? { target: "_blank", rel: "noopener noreferrer", onClick: opened, onAuxClick: opened } : {};
}

function Card({ c, sizes = "(max-width: 680px) 100vw, 400px" }: { c: WebsiteCard; sizes?: string }) {
  const title = displayTitle(c.title);
  return (
    <a className="site-card site-reveal" href={c.url} {...ext(c)} title={title !== c.title ? c.title : undefined}>
      <Thumb c={c} sizes={sizes} />
      <Meta c={c} />
      <span className="site-card-title">{title}</span>
    </a>
  );
}

function CardSkeleton() {
  return (
    <span className="site-card is-loading" aria-hidden>
      <span className="site-thumb">
        <span className="site-thumb-empty" />
      </span>
      <span className="site-meta">&nbsp;</span>
      <span className="site-card-title">&nbsp;</span>
    </span>
  );
}

function SectionHead({ title, children, id }: { title: ReactNode; children?: ReactNode; id?: string }) {
  return (
    <div className="site-section-head site-reveal">
      <h2 className="site-display site-h2" id={id}>
        {title}
      </h2>
      {children}
    </div>
  );
}

// --- latest ------------------------------------------------------------------------------------------

function LatestSection({ items }: { items: WebsiteCard[] | null }) {
  const [featured, ...rest] = items ?? [];
  return (
    <section className="site-section" id="latest" aria-labelledby="latest-title">
      <div className="site-wrap">
        <SectionHead title={site.latest.title} id="latest-title">
          <a className="site-more site-link" href="/content">
            {site.latest.viewAll} <span aria-hidden>→</span>
          </a>
        </SectionHead>
        {items === null ? (
          <div className="site-latest">
            <div className="site-feature is-loading" aria-hidden>
              <span className="site-thumb">
                <span className="site-thumb-empty" />
              </span>
            </div>
          </div>
        ) : (
          <div className={`site-latest${rest.length === 0 ? " is-single" : ""}`}>
            <a className="site-feature site-reveal" href={featured.url} {...ext(featured)}>
              <Thumb c={featured} sizes="(max-width: 960px) 100vw, 760px" eager />
              <span className="site-feature-text">
                <span className="site-label">New</span>
                <Meta c={featured} />
                <span className="site-feature-title">{displayTitle(featured.title)}</span>
              </span>
            </a>
            {rest.length > 0 && (
              <div className="site-latest-rest">
                {rest.map((c) => (
                  <a key={c.url} className="site-row site-reveal" href={c.url} {...ext(c)}>
                    <Thumb c={c} sizes="(max-width: 680px) 40vw, 400px" />
                    <span className="site-row-text">
                      <Meta c={c} />
                      <span className="site-card-title">{displayTitle(c.title)}</span>
                    </span>
                  </a>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

// --- what's it worth? --------------------------------------------------------------------------------

/** The franchise: one object, its price, its story. Only real content placed here in the dashboard. */
function WhatsItWorth({ items }: { items: WebsiteCard[] }) {
  const [lead, ...more] = items;
  return (
    <section className="site-section site-worth" id="whats-it-worth" aria-labelledby="worth-title">
      <div className="site-wrap">
        <a className="site-worth-lead site-reveal" href={lead.url} {...ext(lead)}>
          <Thumb c={lead} sizes="(max-width: 960px) 100vw, 700px" />
          <span className="site-worth-text">
            <span className="site-label">A Price Chat series</span>
            <h2 className="site-display site-worth-title" id="worth-title">
              {site.whatsItWorth.title}
            </h2>
            <span className="site-worth-subject">{displayTitle(lead.title)}</span>
            <Meta c={lead} />
            <span className="site-more site-link">
              Watch the story <span aria-hidden>→</span>
            </span>
          </span>
        </a>
        {more.length > 0 && (
          <div className="site-grid site-grid-3">
            {more.map((c) => <Card key={c.url} c={c} />)}
          </div>
        )}
      </div>
    </section>
  );
}

// --- price board -------------------------------------------------------------------------------------

/**
 * The Price Board. Renders only real quotes from /api/website/home (backend: price_board_quotes, the
 * market-data integration boundary). With no source connected the list is empty and this never renders.
 */
function PriceBoard({ quotes }: { quotes: Quote[] }) {
  const asOf = quotes.map((q) => q.as_of).sort().at(-1);
  return (
    <section className="site-section" id="price-board" aria-labelledby="board-title">
      <div className="site-wrap">
        <SectionHead title={site.priceBoard.title} id="board-title">
          {asOf && <span className="site-note">As of {new Date(asOf).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" })}</span>}
        </SectionHead>
        <ul className="site-board site-reveal">
          {quotes.map((q) => (
            <li key={q.symbol}>
              <span className="site-board-symbol">{q.symbol}</span>
              <span className="site-board-name">{q.name}</span>
              <span className="site-board-price">{q.price.toLocaleString("en-US", { style: "currency", currency: q.currency, maximumFractionDigits: 2 })}</span>
              {q.change_24h_pct != null && (
                <span className={`site-board-change ${q.change_24h_pct >= 0 ? "is-up" : "is-down"}`}>
                  {q.change_24h_pct >= 0 ? "+" : ""}
                  {q.change_24h_pct.toFixed(2)}%
                </span>
              )}
            </li>
          ))}
        </ul>
        <p className="site-note">Source: {[...new Set(quotes.map((q) => q.source))].join(", ")}. Not financial advice.</p>
      </div>
    </section>
  );
}

// --- originals ---------------------------------------------------------------------------------------

function Originals({ items }: { items: WebsiteCard[] }) {
  return (
    <section className="site-section" id="originals" aria-labelledby="originals-title">
      <div className="site-wrap">
        <SectionHead title={site.originals.title} id="originals-title">
          <span className="site-note">{site.originals.intro}</span>
        </SectionHead>
        <ol className="site-originals">
          {items.map((c, i) => (
            <li key={c.url} className="site-reveal">
              <a href={c.url} {...ext(c)}>
                <span className="site-originals-n">{String(i + 1).padStart(2, "0")}</span>
                <span className="site-originals-text">
                  <span className="site-originals-title">{displayTitle(c.title)}</span>
                  <Meta c={c} />
                </span>
                <Thumb c={c} sizes="(max-width: 680px) 100vw, 320px" />
              </a>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

// --- community ---------------------------------------------------------------------------------------

function Community({ links, growth }: { links: HomeData["links"]; growth?: HomeData["growth"] }) {
  const { title, lines } = site.community;
  const available = NETWORK_ORDER.filter((n) => links[n]);
  const member = useSession();
  const list = useRef<HTMLUListElement>(null);
  const seen = useInViewOnce(list, 0.4);
  return (
    <section className="site-section site-community" id="community" aria-labelledby="community-title">
      <div className="site-wrap site-community-inner site-reveal">
        <div>
          <h2 className="site-display site-h1" id="community-title">
            <span>{title[0]}</span>
            <span className="site-green">{title[1]}</span>
          </h2>
          <p className="site-community-lines">
            {lines.map((l) => (
              <span key={l}>{l}</span>
            ))}
          </p>
        </div>
        <ul className={`site-join${seen ? " is-seen" : ""}`} ref={list}>
          {available.map((n) => (
            <li key={n}>
              <a href={links[n]!} target="_blank" rel="noopener noreferrer" className={`site-join-${n}`}>
                <Icon network={n} />
                <span className="site-join-name">
                  <span className="site-join-title">
                    <span>{NETWORK_LABEL[n]}</span>
                    {handleFromUrl(links[n]) && <span className="site-join-handle">{handleFromUrl(links[n])}</span>}
                  </span>
                  {growth?.[n] && <JoinStat g={growth[n]!} seen={seen} />}
                </span>
                {growth?.[n] && <Spark g={growth[n]!} />}
                <span className="site-join-arrow" aria-hidden>
                  ↗
                </span>
              </a>
            </li>
          ))}
        </ul>
      </div>
      <JoinCta signedIn={!!member} />
      <VpnPerk />
    </section>
  );
}

const VPN_POINTS: [string, string][] = [
  ["Your address stays hidden", "Sites see our server's address instead of yours."],
  ["We don't log the sites you visit", "Our privacy page lists exactly what we do keep."],
  ["30-day free trial", "Then $9.99 for 30 days, renewed by hand. Free for Royal Kiwi and above."],
];

/** A member perk, sold briefly next to the sign-up: Kiwi VPN's three selling points and a link to /vpn. */
function VpnPerk() {
  return (
    <div className="site-wrap">
      <aside className="site-vpnperk site-reveal" aria-labelledby="vpnperk-title">
        <div className="site-vpnperk-head">
          <p className="site-eyebrow">Member perk</p>
          <h3 className="site-display" id="vpnperk-title"><span aria-hidden>🛡️ </span>Kiwi VPN</h3>
        </div>
        <ul className="site-vpnperk-points">
          {VPN_POINTS.map(([title, line]) => <li key={title}><b>{title}</b><span>{line}</span></li>)}
        </ul>
        <a className="site-more site-link" href="/vpn">Get Kiwi VPN <span aria-hidden>→</span></a>
      </aside>
    </div>
  );
}

/** "1,051 members  +1.1% ↗ since 4 Oct": the real count (counts up once when the rows come into view) and its change. */
function JoinStat({ g, seen }: { g: Growth; seen: boolean }) {
  const shown = useRiseTo(g.count, seen);
  const ch = growthChange(g);
  const up = ch && ch.abs > 0, down = ch && ch.abs < 0;
  const since = new Date(`${g.tracking_since}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  const unit = g.count === 1 ? g.unit.replace(/s$/, "") : g.unit;
  return (
    <span className="site-join-stat">
      <span className="site-join-count" aria-label={`${g.count.toLocaleString("en-GB")} ${unit}`}>
        <strong aria-hidden>{shown.toLocaleString("en-GB")}</strong> <span aria-hidden>{unit}</span>
      </span>
      {ch ? (
        <span className={`site-join-change${up ? " is-up" : down ? " is-down" : ""}`}
              title={ch.label === "7d" ? "Change over the last 7 days" : `Change since Price Chat started tracking (${since})`}>
          {up ? "+" : down ? "−" : "±"}{ch.pct !== null ? `${Math.abs(ch.pct).toLocaleString("en-GB")}%` : Math.abs(ch.abs).toLocaleString("en-GB")}
          {up ? " ↗" : down ? " ↘" : ""} <span className="site-join-period">{ch.label}</span>
        </span>
      ) : (
        <span className="site-join-change site-join-period">tracking since {since}</span>
      )}
    </span>
  );
}

/** The real daily history as a bare line (no axes, labels or box). One snapshot so far: just a dot. */
function Spark({ g }: { g: Growth }) {
  const W = 120, H = 28;
  const pts = sparkPoints(g.history, W, H);
  return (
    <svg className="site-join-spark" viewBox={`0 0 ${W} ${H}`} aria-hidden preserveAspectRatio="none">
      {pts.length ? (
        <>
          <path d={`M${pts.map(([x, y]) => `${x} ${y}`).join(" L")}`} pathLength={100} />
          <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r={2.2} />
        </>
      ) : (
        <circle className="is-first" cx={W - 3} cy={H / 2} r={2.2} />
      )}
    </svg>
  );
}

/** Counts up to `value` (from ~85% of it, ~0.7s) the first time `go` turns true. Reduced motion: just the value. */
function useRiseTo(value: number, go: boolean): number {
  const [shown, setShown] = useState(value);
  const done = useRef(false);
  useEffect(() => {
    if (!go || done.current) {
      if (done.current) setShown(value);
      return;
    }
    done.current = true;
    if (reducedMotion() || typeof requestAnimationFrame === "undefined" || value < 5) return setShown(value);
    const from = Math.floor(value * 0.85);
    let raf = 0, t0: number | null = null, finished = false;
    const tick = (t: number) => {
      t0 ??= t;
      const k = Math.min(1, (t - t0) / 700);
      setShown(Math.round(from + (value - from) * (1 - Math.pow(1 - k, 3))));
      if (k < 1) raf = requestAnimationFrame(tick);
      else finished = true;
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      if (!finished) done.current = false; // interrupted (e.g. a dev double-run): run it again next time
    };
  }, [go, value]);
  return shown;
}

const LADDER: { key: string; name: string; size: number; art: import("./members").RankArt }[] = [
  { key: "egg", name: "The Egg", size: 54, art: { treatment: "egg", overlay: null, placeholder: null, slot: null } },
  { key: "warrior", name: "Warrior", size: 62, art: { treatment: "none", overlay: null, placeholder: null, slot: null } },
  { key: "royal", name: "Royal", size: 70, art: { treatment: "none", overlay: null, placeholder: null, slot: null } },
  { key: "mythic", name: "Mythic", size: 80, art: { treatment: "mythic", overlay: null, placeholder: null, slot: null } },
];

/** The community sign-up: one identity, verified participation, a Kiwi that grows with you. */
function JoinCta({ signedIn }: { signedIn: boolean }) {
  const { joinTitle, joinLines, joinCta, joinNote } = site.community;
  return (
    <div className="site-wrap">
      <div className="site-joincta site-reveal">
        <ol className="site-joincta-ladder" aria-label="Kiwi ranks, from The Egg to Mythic Kiwi">
          {LADDER.map((r) => (
            <li key={r.key}>
              <RankKiwi art={r.art} rankKey={r.key} size={r.size} />
              <span>{r.name}</span>
            </li>
          ))}
        </ol>
        <div className="site-joincta-text">
          <h3 className="site-display">{joinTitle}</h3>
          <p>
            {joinLines.map((l) => (
              <span key={l}>{l}</span>
            ))}
          </p>
          {!signedIn && <p className="site-joincta-note">{joinNote}</p>}
        </div>
        <div className="site-joincta-actions">
          <a className="site-button" href={signedIn ? "/passport" : "/website/join"}
             onClick={signedIn ? undefined : (e) => { e.preventDefault(); openLogin(); }}>
            {signedIn ? "Your Kiwi Passport" : joinCta}
          </a>
          <a className="site-more site-link" href="/website/leaderboard">
            Leaderboard <span aria-hidden>→</span>
          </a>
        </div>
      </div>
    </div>
  );
}

// --- story -------------------------------------------------------------------------------------------

/** The brand's first chapter: where we came from. */
function Story() {
  const { eyebrow, title, lead, paragraphs } = site.story;
  const head = useRef<HTMLDivElement>(null);
  const playing = useInViewOnce(head, 0.6);
  return (
    <section className={`site-section site-story${playing ? " is-playing" : ""}`} id="story" aria-labelledby="story-title" data-pause-offscreen>
      <div className="site-wrap" ref={head}>
        <p className="site-eyebrow">{eyebrow}</p>
        <h2 className="site-display site-story-title" id="story-title">
          <span className="site-story-line">{title[0]}</span>
          <span className="site-story-line site-green">{title[1]}</span>
        </h2>
      </div>
      {/* Past → future: a dark scene, Kiwi on the horizon; the Price Line extends toward a distant green light.
          Now and then the Kiwi tries to fly toward it (KiwiFlight). */}
      <div className="site-wrap">
        <div className="site-horizon" aria-hidden>
          <span className="site-horizon-light" />
          <span className="site-horizon-line price-line" />
          <KiwiFlight />
        </div>
      </div>
      <div className="site-wrap">
        <div className="site-chapter site-story-copy">
          <div className="site-chapter-lead">
            {lead.map((p) => (
              <p key={p} className="site-lead">{p}</p>
            ))}
          </div>
          <div className="site-chapter-body">
            {paragraphs.map((p) => (
              <p key={p} className="site-body">
                {p}
              </p>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

// --- footer ------------------------------------------------------------------------------------------

export function SiteFooter({ home }: { home: HomeData | null }) {
  const social = socials(home);
  return (
    <footer className="site-footer">
      <div className="site-wrap site-footer-inner">
        <div className="site-brand">
          <LogoMark size={32} />
          <span>
            <Wordmark />
            <span className="site-footer-tagline">{site.tagline}</span>
          </span>
        </div>
        <nav className="site-footer-links" aria-label="Price Chat">
          {site.footer.links.map((l) => (
            <a key={l.label} href={l.href} className="site-link">
              {l.label}
            </a>
          ))}
        </nav>
        {social.length > 0 && (
          <nav className="site-footer-links site-footer-social" aria-label="Follow Price Chat">
            {social.map((s) => (
              <a key={s.network} href={s.href} target="_blank" rel="noopener noreferrer" className="site-link">
                {NETWORK_LABEL[s.network]}
              </a>
            ))}
          </nav>
        )}
        <p className="site-footer-small">
          © {new Date().getFullYear()} Price Chat. Nothing on this site is financial advice. <a className="site-link" href="/website/disclaimer">Disclaimer</a>
        </p>
      </div>
    </footer>
  );
}
