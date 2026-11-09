import { describe, expect, it } from "vitest";
import raw from "../../vercel.json?raw";
import indexHtml from "../../index.html?raw";
import { CRAWLER_UA, cleanPath, headOnly, injectHead } from "../../api/_lib/seoShell";

// Audit UX-1 / UX-17: search crawlers get each page's own head (not the 2 KB empty shell), legacy /website/* legal addresses 301.

type Has = { type: string; key: string; value: string };
type Rewrite = { source: string; destination: string; has?: Has[] };
type Redirect = { source: string; destination: string; permanent?: boolean };
const config = JSON.parse(raw) as { rewrites: Rewrite[]; redirects?: Redirect[] };

/** path-to-regexp's "/:page(a|b)/:rest*" for the pages these tests use. */
function matches(source: string, path: string): boolean {
  const m = /^\/:page\(([^)]+)\)\/:rest\*$/.exec(source);
  if (!m) return false;
  return new RegExp(`^/(${m[1]})(/.*)?$`).test(path);
}
const uaRule = (r: Rewrite) => r.has?.find((h) => h.type === "header" && h.key === "user-agent");
const ruleFor = (path: string, ua: string) => config.rewrites.find((r) => matches(r.source, path) && (!uaRule(r) || new RegExp(uaRule(r)!.value).test(ua)));

const GOOGLEBOT = "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.204 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)";
const BING = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm) Chrome/116.0.1938.76 Safari/537.36";
const CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";
const TWITTER = "Twitterbot/1.0";

const OG = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Blastoise Unlimited · PSA 10 Price · Price Chat</title>
<meta name="description" content="Blastoise: estimated PSA 10 price $1,200."><link rel="canonical" href="https://pricechat.live/pokemon/cards/5">
<meta property="og:type" content="website"><meta property="og:site_name" content="Price Chat"><meta property="og:url" content="https://pricechat.live/pokemon/cards/5">
<meta property="og:title" content="Blastoise Unlimited · PSA 10 Price · Price Chat"><meta property="og:image" content="https://pricechat.live/cards/u/b.webp">
<meta name="twitter:card" content="summary"><meta name="twitter:title" content="Blastoise">
<script type="application/ld+json">{"@type":"Product"}</script><meta http-equiv="refresh" content="0;url=https://pricechat.live/pokemon/cards/5"></head><body><a href="https://pricechat.live/pokemon/cards/5">Blastoise Unlimited · PSA 10 Price · Price Chat</a><p>Blastoise: estimated PSA 10 price $1,200.</p></body></html>`;

describe("vercel.json: crawlers get the page's own head", () => {
  it("sends Googlebot, Bingbot and AI crawlers on a card page to /api/seo-shell with the path", () => {
    for (const ua of [GOOGLEBOT, BING, "Mozilla/5.0 (compatible; GPTBot/1.2)", "ClaudeBot/1.0", "PerplexityBot/1.0", "Applebot/0.1"]) {
      const r = ruleFor("/pokemon/cards/5", ua)!;
      expect(r.destination, ua).toBe("/api/seo-shell?path=:page/:rest*");
    }
    for (const p of ["/pokemon", "/earn", "/vpn", "/u/kiwi", "/privacy", "/giveaway"]) expect(ruleFor(p, GOOGLEBOT)!.destination).toContain("/api/seo-shell");
    expect(new RegExp(uaRule(ruleFor("/pokemon", GOOGLEBOT)!)!.value).test(GOOGLEBOT)).toBe(true);
    // the list in vercel.json is the one documented next to the injector
    expect(uaRule(ruleFor("/pokemon", GOOGLEBOT)!)!.value).toBe(`.*(${CRAWLER_UA}).*`);
  });

  it("leaves people on the plain app and link-preview bots on their own preview page", () => {
    expect(ruleFor("/pokemon/cards/5", CHROME)!.destination).toBe("/index.html");
    expect(ruleFor("/pokemon/cards/5", TWITTER)!.destination).toContain("/api/website/seo/og/");
    expect(new RegExp(`.*(${CRAWLER_UA}).*`).test(CHROME)).toBe(false);
    expect(new RegExp(`.*(${CRAWLER_UA}).*`).test(TWITTER)).toBe(false);
  });

  it("serves the legal pages at their own addresses and 301s the old /website/* ones", () => {
    expect(ruleFor("/privacy", CHROME)!.destination).toBe("/index.html");
    const r = config.redirects!.find((x) => x.source.startsWith("/website/:page("))!;
    expect(r.permanent).toBe(true);
    expect(r.destination).toBe("/:page");
    for (const k of ["privacy", "terms", "disclaimer", "contact", "leaderboard"]) expect(r.source).toContain(k);
    expect(r.source).not.toContain("join");  // the sign-in / invite flow keeps /website/join
  });
});

describe("injectHead", () => {
  const html = injectHead(indexHtml, OG);
  const doc = new DOMParser().parseFromString(html, "text/html");
  const all = (sel: string) => [...doc.querySelectorAll(sel)];

  it("puts the card's title, description, canonical, OG tags and JSON-LD in index.html, once each", () => {
    expect(doc.title).toBe("Blastoise Unlimited · PSA 10 Price · Price Chat");
    expect(all("title")).toHaveLength(1);
    expect(all('meta[name="description"]').map((m) => m.getAttribute("content"))).toEqual(["Blastoise: estimated PSA 10 price $1,200."]);
    expect(all('link[rel="canonical"]').map((l) => l.getAttribute("href"))).toEqual(["https://pricechat.live/pokemon/cards/5"]);
    expect(all('meta[property="og:title"]')).toHaveLength(1);
    expect(all('meta[property="og:image"]').map((m) => m.getAttribute("content"))).toEqual(["https://pricechat.live/cards/u/b.webp"]);
    expect(all('meta[name="twitter:title"]')).toHaveLength(1);
    expect(all('script[type="application/ld+json"]')).toHaveLength(1);
    expect(all("head [data-seo-shell]").length).toBeGreaterThan(5);  // main.tsx removes these once the app's own head takes over
  });

  it("keeps the app (its script and #root) and never the social bots' redirect", () => {
    expect(html).not.toMatch(/http-equiv="refresh"/);
    expect(all('script[type="module"]').map((s) => s.getAttribute("src"))).toEqual(["/src/main.tsx"]);
    expect(doc.querySelector("#root main")!.textContent).toContain("estimated PSA 10 price $1,200");
    expect(all('meta[name="viewport"]')).toHaveLength(1);
    expect(all('meta[charset], meta[charSet]')).toHaveLength(1);
  });

  it("falls back safely", () => {
    expect(injectHead(indexHtml, "<html>nope</html>")).toBe(indexHtml);
    expect(headOnly(OG)).not.toMatch(/refresh/);
    expect(cleanPath("pokemon/cards/5")).toBe("pokemon/cards/5");
    expect(cleanPath("pokemon/")).toBe("pokemon");
    expect(cleanPath("../admin")).toBeNull();
    expect(cleanPath("a/<script>")).toBeNull();
    expect(cleanPath(null)).toBe("");
  });
});
