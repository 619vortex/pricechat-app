/**
 * GET /api/seo-shell?path=<website path>&<the page's own query>: what search crawlers get for a website page (audit UX-1).
 * vercel.json rewrites crawler requests here by user agent; people never reach it. See api/_lib/seoShell.ts.
 *
 * index.html from this same deployment + the page's head from the backend (/api/website/seo/og/<path>, public, cached at the
 * edge for an hour). Either fetch failing never leaves a crawler with an error: no head -> the plain app; no index.html -> the
 * backend's page without its redirect. Read-only.
 */
import { cleanPath, headOnly, injectHead } from "./_lib/seoShell";

const API = "https://api-production-b1be.up.railway.app";  // the same backend vercel.json's /api rewrite uses
const WAIT_MS = 4000;

async function text(url: string): Promise<{ body: string; robots: string | null } | null> {
  try {
    const r = await fetch(url, { headers: { Accept: "text/html" }, signal: AbortSignal.timeout(WAIT_MS) });
    return r.ok ? { body: await r.text(), robots: r.headers.get("x-robots-tag") } : null;
  } catch {
    return null;
  }
}

export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const path = cleanPath(url.searchParams.get("path"));
  url.searchParams.delete("path");
  const query = url.searchParams.toString();
  const [shell, og] = await Promise.all([
    text(new URL("/index.html", url.origin).toString()),
    path === null ? Promise.resolve(null) : text(`${API}/api/website/seo/og/${path}${query ? `?${query}` : ""}`),
  ]);
  const html = shell && og ? injectHead(shell.body, og.body) : shell ? shell.body : og ? headOnly(og.body) : null;
  if (html === null) return new Response("Temporarily unavailable", { status: 503, headers: { "Retry-After": "60" } });
  const headers: Record<string, string> = { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "public, max-age=0, s-maxage=600, stale-while-revalidate=3600" };
  if (og?.robots) headers["X-Robots-Tag"] = og.robots;  // private / owner-only / unknown addresses stay out of the index
  return new Response(html, { headers });
}
