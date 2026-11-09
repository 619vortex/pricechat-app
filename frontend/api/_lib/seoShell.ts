/**
 * Search crawlers (Googlebot, Bingbot, AI search bots...) and the app's one HTML page (audit UX-1).
 *
 * The website is a single-page app, so every address serves the same index.html: before rendering, Google saw
 * "<title>Price Chat</title>" on 25k+ card pages. vercel.json sends crawler requests for website pages to /api/seo-shell, which
 * serves the SAME index.html (same scripts, so a rendering crawler still gets the real page) with that page's own head from
 * the backend's /api/website/seo/og/<path> (app.seo.heads, golden-tested against seo.ts): title, description, canonical,
 * robots, OG / X tags and JSON-LD, plus a short text summary inside #root that React replaces on start. The link-preview
 * page's <meta http-equiv=refresh> is dropped: it is for social bots only.
 *
 * The injected tags carry data-seo-shell; main.tsx removes them on start, so the app's own head (useHead) is the only one
 * left once it runs. Pure string work here (tested in src/api/seoShell.test.ts); the fetching is in api/seo-shell.ts.
 */

/** Search and AI crawlers that read a page's own HTML. Link-preview bots keep vercel.json's older rule (the og page itself). */
export const CRAWLER_UA =
  "Googlebot|Google-InspectionTool|GoogleOther|Storebot-Google|AdsBot-Google|bingbot|BingPreview|msnbot|adidxbot|Applebot|DuckDuckBot|YandexBot|Baiduspider|Sogou|Exabot|SeznamBot|Qwantbot|PetalBot|Yeti|GPTBot|OAI-SearchBot|ChatGPT-User|PerplexityBot|Perplexity-User|ClaudeBot|Claude-SearchBot|Claude-User|anthropic-ai|Amazonbot|CCBot|YouBot|cohere-ai|meta-externalagent|ia_archiver";

/** The website path a crawler asked for, from the rewrite's ?path=: plain path characters only, no "..", at most 400 long. */
export function cleanPath(raw: string | null): string | null {
  const p = (raw ?? "").replace(/^\/+|\/+$/g, "");
  if (p.length > 400 || !/^[A-Za-z0-9/_\-.~%@]*$/.test(p) || p.split("/").includes("..")) return null;
  return p;
}

const mark = (tags: string) => tags.replace(/<(meta|link|script)\b/gi, "<$1 data-seo-shell");

/** index.html with the page's own head (from the backend's link-preview page) in place of the home page's tags. */
export function injectHead(index: string, og: string): string {
  const head = /<head[^>]*>([\s\S]*?)<\/head>/i.exec(og)?.[1] ?? "";
  const body = /<body[^>]*>([\s\S]*?)<\/body>/i.exec(og)?.[1] ?? "";
  const title = /<title>[\s\S]*?<\/title>/i.exec(head)?.[0] ?? "";
  const tags = head
    .replace(/<title>[\s\S]*?<\/title>/i, "")
    .replace(/<meta\s+charset=[^>]*>/gi, "")
    .replace(/<meta\s+http-equiv="refresh"[^>]*>/gi, "")
    .trim();
  if (!title) return index;  // not a head we understand: the plain app
  const cleaned = index
    .replace(/<!--\s*link previews[\s\S]*?-->\s*/i, "")
    .replace(/<title>[\s\S]*?<\/title>\s*/i, "")
    .replace(/<meta\s+name="description"[^>]*>\s*/gi, "")
    .replace(/<link\s+rel="canonical"[^>]*>\s*/gi, "")  // one canonical only: the page's own
    .replace(/<meta\s+property="og:[^"]*"[^>]*>\s*/gi, "")
    .replace(/<meta\s+name="twitter:[^"]*"[^>]*>\s*/gi, "");
  return cleaned
    .replace(/<\/head>/i, `${title}\n${mark(tags)}\n</head>`)
    .replace(/<div id="root"><\/div>/i, `<div id="root"><main data-seo-shell>${body}</main></div>`);
}

/** No index.html (the deployment couldn't be read): the backend's page without the redirect, still the page's own head. */
export function headOnly(og: string): string {
  return og.replace(/<meta\s+http-equiv="refresh"[^>]*>/gi, "");
}
