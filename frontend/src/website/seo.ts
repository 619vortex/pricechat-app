/**
 * Search and share heads for every public page: title, description, picture, robots and JSON-LD.
 *
 * ONE set of strings, written twice: link-preview bots (Telegram, X, WhatsApp, Discord...) never run this app; vercel.json
 * sends them to the backend's /api/website/seo/og/... which builds the same heads in backend/app/seo/heads.py. Both test
 * suites check their builders against seoHeads.golden.json (this folder), so a title changed here and not there fails
 * a test. Change the three together.
 *
 * Rules shared with the backend: titles end in "Price Chat"; prices come from the page's own (public, cleaned) data and
 * are left out when unknown; private or owner-only pages are noindex and never get a share card of their own.
 */
import type { CardDetail } from "../pages/pokemon/api";
import { num, usd } from "../pages/pokemon/format";
import type { VaultDetail } from "../pages/pokemon/vault/api";
import { cardName, money } from "../pages/pokemon/vault/api";

export interface Head { title: string; description: string }
export type JsonLd = Record<string, unknown>;

/** The fixed public pages, by path without the leading "/". Titles are complete (SitePage adds nothing to them). */
export const PAGE_HEADS = {
  "": { title: "Price Chat · Everything has a price.", description: "Markets. Money. Culture. Everything has a price. Short stories and conversations from Price Chat." },
  content: { title: "All content · Price Chat", description: "Every Price Chat video and Short, newest first." },
  join: { title: "Join Price Chat", description: "Sign in with Telegram, X or Google. Earn XP. Level up." },
  leaderboard: { title: "Kiwi Leaderboard · Price Chat", description: "Price Chat members by Kiwi XP and invites." },
  giveaway: { title: "Pack Giveaway · Price Chat", description: "Open free virtual Pokémon packs for fun and earn Kiwi XP. Virtual cards only: no money, no real cards." },
  earn: { title: "Earn: Kiwi Trading Bot · Price Chat", description: "Free for every Kiwi Passport member: connect Hyperliquid and start in paper trading." },
  "earn/brain": { title: "Kiwi Brain · Price Chat", description: "How the Kiwi trading bot reads the market, signal by signal, live." },
  "earn/lunch": { title: "Earn: Lunch Bot · Price Chat", description: "Lunch Bot paper-trades pump.fun launches at real on-chain prices." },
  "earn/funding": { title: "Earn: Funding Arb · Price Chat", description: "Live perp funding rates across Hyperliquid, Lighter, Orderly and Backpack, and the biggest gaps between them." },
  "earn/kalshi": { title: "Earn: Kalshi · Price Chat", description: "Watch Kiwi's Kalshi bot scan every market against Kalshi's fees, and follow your own Kalshi positions marked at the current bid after Kalshi's fees." },
  learn: { title: "Beginner content · Price Chat", description: "Exclusive beginner lessons for Kiwi Chicks and above." },
  vpn: { title: "Kiwi VPN · Price Chat", description: "A WireGuard VPN from Price Chat. We don't log the sites you visit. 30-day free trial, then $9.99 for 30 days." },
  "vpn/privacy": { title: "Kiwi VPN Privacy · Price Chat", description: "What Kiwi VPN and its browser extension store, what they don't, and why they need their permissions." },
  "vpn/terms": { title: "Kiwi VPN Terms · Price Chat", description: "What you get with Kiwi VPN, what it costs, how renewal works and when access ends." },
  "vpn/refunds": { title: "Kiwi VPN Refund Policy · Price Chat", description: "When a Kiwi VPN payment can be corrected or refunded, and what to do if a payment goes wrong." },
  "website/privacy": { title: "Privacy · Price Chat", description: "How Price Chat handles member data, connected accounts and Kiwi XP." },
  "website/terms": { title: "Terms · Price Chat", description: "The rules of the Price Chat community and Kiwi XP." },
  "website/disclaimer": { title: "Disclaimer · Price Chat", description: "Price Chat content is for information and entertainment." },
  "website/contact": { title: "Contact · Price Chat", description: "How to reach Price Chat." },
  pokemon: { title: "Pokémon Market · Price Chat", description: "Vintage Pokémon card prices, market caps and verified sales, and the Price Chat Vault of vintage Japanese cards." },
  "pokemon/trending": { title: "Trending Pokémon Cards · Price Chat", description: "The vintage Pokémon cards collectors are talking about and trading right now, with prices, 30-day moves and the reason each one is trending." },
  "pokemon/gainers": { title: "Pokémon Card Top Gainers · Price Chat", description: "The vintage Pokémon cards whose estimated price rose the most over the last 30 and 90 days, by grade." },
  "pokemon/losers": { title: "Pokémon Card Top Losers · Price Chat", description: "The vintage Pokémon cards whose estimated price fell the most over the last 30 and 90 days, by grade." },
  "pokemon/recent": { title: "Recently Sold Pokémon Cards · Price Chat", description: "Completed, verified sales of vintage Pokémon cards, newest first: date, grade, price and where each card sold." },
  "pokemon/years": { title: "Pokémon Card Market Cap by Year · Price Chat", description: "The estimated market cap of Pokémon cards by release year, its share of the whole market, 30- and 90-day moves and each year's top cards." },
  "pokemon/sets": { title: "Pokémon Card Sets by Market Cap · Price Chat", description: "Every tracked Pokémon card set ranked by estimated market cap, with 30-day moves, verified sales volume, population and its most valuable cards." },
  "pokemon/grades": { title: "Pokémon Card Market by Grade · Price Chat", description: "PSA, BGS and CGC grades side by side: cards priced, market cap, verified sales volume, known population and the most valuable card at each grade." },
  "pokemon/vault": { title: "Price Chat Vault · Vintage Japanese Pokémon Cards", description: "Hand-picked 1996–2002 Japanese Pokémon cards (Base, Jungle, Fossil, Rocket Gang, Gym, Neo, e-Card) with the seller's real photos and prices." },
  "pokemon/portfolios": { title: "Explore Pokémon Card Collectors · Price Chat", description: "Public Pokémon card portfolios from the Price Chat community: top collections, most cards, most valuable cards and featured collectors." },
} satisfies Record<string, Head>;
export type PagePath = keyof typeof PAGE_HEADS;

/** SitePage props for a fixed page: `<SitePage {...pageHead("vpn")}>`. */
export function pageHead(p: PagePath): Head & { path: string } {
  return { ...PAGE_HEADS[p], path: `/${p}` };
}

/** Owner-only or personal addresses: noindex, and the link-preview server gives them no card of their own. */
export const NOINDEX_PATHS = [/^\/passport\/?$/, /^\/website\/profile\/?$/, /^\/website\/preview\/?$/,
  /^\/pokemon\/portfolio(\/add|\/phone\/[^/]+)?\/?$/, /^\/pokemon\/wallet\/?$/, /^\/pokemon\/vault\/requests\/?$/];
export function isNoindexPath(pathname: string): boolean {
  return NOINDEX_PATHS.some((r) => r.test(pathname));
}

const SITE = "Price Chat";
const crumbs = (items: [string, string][]): JsonLd => ({
  "@context": "https://schema.org", "@type": "BreadcrumbList",
  itemListElement: items.map(([name, item], i) => ({ "@type": "ListItem", position: i + 1, name, item })),
});

// --- card page (/pokemon/cards/:id?grade=) ------------------------------------------------------------------------------

function cardIdent(d: CardDetail): { name: string; ident: string } {
  const c = d.card;
  return { name: `${c.name}${c.edition !== "—" ? ` ${c.edition}` : ""}`,
           ident: `${c.year} ${c.language === "English" ? "" : `${c.language} `}${c.set_name}${c.card_number ? ` ${c.card_number}` : ""}` };
}

/** "Charizard 1st Edition · 1999 Base Set 4/102 · PSA 10 Price · Price Chat". The grade is the page's (?grade=, PSA 10). */
export function cardHead(d: CardDetail, grade: string): Head & { image: string | null } {
  const { name, ident } = cardIdent(d);
  const v = [...d.valuations, ...d.other_grades].find((x) => x.label === grade);
  const facts = [v?.market_price !== null && v?.market_price !== undefined ? `estimated ${grade} price ${usd(v.market_price)}` : null,
    v?.population !== null && v?.population !== undefined ? `${grade} population ${num(v.population)}` : null].filter(Boolean).join(", ");
  return {
    title: `${name} · ${ident} · ${grade} Price · ${SITE}`,
    description: `${name} (${ident})${facts ? `: ${facts}` : ""}. Verified sales, price history, population and every grade on the Price Chat Pokémon Market.`,
    image: d.card.image_url,
  };
}

/** A Product (the card printing) with its known facts, and the page's breadcrumbs. No offers: the public page shows
 * estimates and past sales, not anything for sale, so none are claimed. Paths are made absolute by useHead. */
export function cardJsonLd(d: CardDetail, grade: string): JsonLd[] {
  const c = d.card;
  const { name, ident } = cardIdent(d);
  const v = [...d.valuations, ...d.other_grades].find((x) => x.label === grade);
  const props: JsonLd[] = [];
  if (v?.market_price !== null && v?.market_price !== undefined)
    props.push({ "@type": "PropertyValue", name: `Estimated ${grade} price (USD)`, value: v.market_price / 100 });
  if (v?.population !== null && v?.population !== undefined) props.push({ "@type": "PropertyValue", name: `${grade} population`, value: v.population });
  const product: JsonLd = {
    "@context": "https://schema.org", "@type": "Product", name: `${name} (${ident})`, url: `/pokemon/cards/${c.id}`,
    category: "Pokémon trading card", brand: { "@type": "Brand", name: "Pokémon" }, description: cardHead(d, grade).description,
  };
  if (c.image_url) product.image = c.image_url;
  if (c.card_number) product.mpn = c.card_number;
  if (props.length) product.additionalProperty = props;
  return [product, crumbs([["Markets", "/pokemon"], [c.set_name, `/pokemon?set=${encodeURIComponent(c.set_name)}`], [`${c.name} ${grade}`, `/pokemon/cards/${c.id}`]])];
}

// --- market home filtered to one set or one year (/pokemon?set=…, /pokemon?year=…) -------------------------------------

/** The market table narrowed to one set or one year is a landing page of its own (its own canonical address) only when
 * that is the ONLY thing in the address and the value is one the market lists. */
export function landingHead(search: string, sets: string[] | null, years: number[] | null): (Head & { path: string }) | null {
  const sp = new URLSearchParams(search);
  const keys = [...new Set(sp.keys())];
  if (keys.length !== 1) return null;
  const set = sp.get("set"), year = sp.get("year");
  if (keys[0] === "set" && set && sets?.includes(set))
    return { title: `${set} Pokémon Card Prices · ${SITE}`, path: `/pokemon?set=${encodeURIComponent(set)}`,
             description: `Every ${set} Pokémon card ranked by estimated market value, with verified sales, population and 30-day moves.` };
  if (keys[0] === "year" && year && /^\d{4}$/.test(year) && years?.includes(Number(year)))
    return { title: `${year} Pokémon Card Prices · ${SITE}`, path: `/pokemon?year=${year}`,
             description: `Every Pokémon card released in ${year} ranked by estimated market value, with verified sales, population and 30-day moves.` };
  return null;
}

// --- Vault card (/pokemon/vault/:id) -------------------------------------------------------------------------------------

type VaultHeadIn = Pick<VaultDetail, "id" | "title" | "pokemon" | "set_name" | "year" | "condition" | "price" | "currency" | "availability" | "url" | "images">;

export function vaultHead(c: VaultHeadIn): Head & { image: string | null } {
  return {
    title: `${cardName(c)}${c.set_name ? ` · ${c.set_name}` : ""}${c.price !== null ? ` · ${money(c.price, c.currency)}` : ""} · Price Chat Vault`,
    description: `${c.title}. ${c.condition ?? "Ungraded"} ${c.year ? `${c.year} ` : ""}Japanese Pokémon card in the Price Chat Vault: the seller's own photos and price, re-checked every few hours.`,
    image: c.images[0] ?? null,
  };
}

const AVAILABILITY_LD: Record<string, string> = { available: "https://schema.org/InStock", sold_out: "https://schema.org/SoldOut", ended: "https://schema.org/Discontinued" };

/** The seller's real listing: a Product with its Offer (the seller's own price and availability, as last checked). */
export function vaultJsonLd(c: VaultHeadIn): JsonLd[] {
  const product: JsonLd = { "@context": "https://schema.org", "@type": "Product", name: cardName(c), description: c.title, url: `/pokemon/vault/${c.id}`,
                            category: "Pokémon trading card" };
  if (c.images.length) product.image = c.images.slice(0, 4);
  if (c.price !== null) {
    const offer: JsonLd = { "@type": "Offer", price: (c.price / 100).toFixed(2), priceCurrency: c.currency || "USD", url: c.url || `/pokemon/vault/${c.id}` };
    if (AVAILABILITY_LD[c.availability]) offer.availability = AVAILABILITY_LD[c.availability];
    product.offers = offer;
  }
  return [product, crumbs([["Markets", "/pokemon"], ["Vault", "/pokemon/vault"], [cardName(c), `/pokemon/vault/${c.id}`]])];
}

// --- public collector portfolio (/pokemon/portfolio/u/:username) and Kiwi Passport (/u/:username) -----------------------

export function portfolioHead(p: { username: string; cards: number; unique_cards: number; graded: number; bio: string }): Head {
  return { title: `${p.username}'s Pokémon Card Collection · ${SITE}`,
           description: `${p.username}'s Pokémon portfolio on Price Chat: ${num(p.cards)} cards, ${num(p.unique_cards)} unique, ${num(p.graded)} graded.${p.bio ? ` ${p.bio}` : ""}` };
}

export function portfolioJsonLd(username: string): JsonLd[] {
  return [crumbs([["Markets", "/pokemon"], ["Collectors", "/pokemon/portfolios"], [username, `/pokemon/portfolio/u/${encodeURIComponent(username)}`]])];
}

export function profileHead(p: { username: string; joined_at: string; rank: { name: string; level: number }; badges: unknown[] }): Head {
  return { title: `${p.username} · Kiwi Passport · ${SITE}`,
           description: `${p.rank.name} · Kiwi Rank ${p.rank.level}${p.badges.length ? ` · ${p.badges.length} badges` : ""} · in Price Chat since ${p.joined_at.slice(0, 7)}.` };
}
