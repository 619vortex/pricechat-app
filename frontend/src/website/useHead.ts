import { useEffect } from "react";

export interface HeadInfo {
  title: string;
  description: string;
  path: string; // public path of this page, e.g. "/" or "/content"
  siteUrl: string | null; // public origin from the dashboard; null = not decided yet (no canonical/og:url)
  image?: string | null; // absolute URL, or a path that is made absolute with siteUrl
  noindex?: boolean; // private / owner-only page: <meta name="robots" content="noindex">
  jsonLd?: Record<string, unknown>[] | null; // structured data with site paths ("/pokemon/…"), made absolute with siteUrl
}

/** JSON-LD is built with site paths ("/pokemon/…"); the origin is added once it is known. */
export function absolutize<T>(v: T, origin: string): T {
  if (typeof v === "string") return (v.startsWith("/") ? `${origin.replace(/\/$/, "")}${v}` : v) as T;
  if (Array.isArray(v)) return v.map((x) => absolutize(x, origin)) as T;
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, absolutize(x, origin)])) as T;
  return v;
}

/**
 * Page title, description, canonical URL, OpenGraph and X card tags for a website page. Tags are added on
 * mount and removed on unmount, so the dashboard (same app) is never left with website metadata.
 * No production domain is assumed: without a configured public address there is no canonical or og:url.
 */
export function useHead({ title, description, path, siteUrl, image, noindex, jsonLd }: HeadInfo) {
  const ld = jsonLd?.length ? JSON.stringify(jsonLd) : ""; // compared as text so a new array each render changes nothing
  useEffect(() => {
    const prevTitle = document.title;
    document.title = title;
    const url = siteUrl ? new URL(path, siteUrl.endsWith("/") ? siteUrl : `${siteUrl}/`).toString() : null;
    const img = image && !/^https?:/.test(image) ? (siteUrl ? new URL(image, siteUrl).toString() : null) : image ?? null;
    const added: HTMLElement[] = [];
    const restore: (() => void)[] = [];
    // the home picture from index.html: the fallback for a page without its own (as the server's link previews do)
    const homeImage = document.head.querySelector<HTMLMetaElement>('meta[property="og:image"]:not([data-site-head])')?.content || null;
    const meta = (attr: "name" | "property", key: string, content: string | null) => {
      // index.html's own link-preview tags (the home page's) are changed in place, never duplicated (audit UX-18): a reader
      // that takes the first og:title / og:image would otherwise see the home card on every page
      const own = document.head.querySelector<HTMLMetaElement>(`meta[${attr}="${key}"]:not([data-site-head])`);
      if (own) {
        const prev = own.content, next = own.nextSibling, parent = own.parentNode;
        if (content) own.content = content;
        else own.remove();  // nothing for this page (no address yet, another picture's size): the home value must not stay
        restore.push(() => {
          own.content = prev;
          if (!own.isConnected && parent) parent.insertBefore(own, next && next.parentNode === parent ? next : null);
        });
        return;
      }
      if (!content) return;
      const el = document.createElement("meta");
      el.setAttribute(attr, key);
      el.content = content;
      el.dataset.siteHead = "";
      document.head.appendChild(el);
      added.push(el);
    };
    // the static description in index.html is replaced while a website page is open
    const base = document.head.querySelector<HTMLMetaElement>('meta[name="description"]:not([data-site-head])');
    const prevDescription = base?.content;
    if (base) base.content = description;
    else meta("name", "description", description);
    meta("property", "og:type", "website");
    meta("property", "og:site_name", "Price Chat");
    meta("property", "og:title", title);
    meta("property", "og:description", description);
    meta("property", "og:url", url);
    const shown = img ?? homeImage;
    const home = !!shown && shown === homeImage;
    meta("property", "og:image", shown);
    meta("property", "og:image:width", home ? "1200" : null);  // index.html's size and alt text describe the home picture only
    meta("property", "og:image:height", home ? "630" : null);
    meta("property", "og:image:alt", home ? title : null);
    meta("name", "twitter:card", shown ? "summary_large_image" : "summary");
    meta("name", "twitter:title", title);
    meta("name", "twitter:description", description);
    meta("name", "twitter:image", shown);
    if (noindex) meta("name", "robots", "noindex");
    if (ld) {
      const origin = siteUrl ?? (typeof location !== "undefined" ? location.origin : "");
      for (const block of JSON.parse(ld) as Record<string, unknown>[]) {
        const s = document.createElement("script");
        s.type = "application/ld+json";
        s.textContent = JSON.stringify(absolutize(block, origin)).replace(/</g, "\\u003c");
        s.dataset.siteHead = "";
        document.head.appendChild(s);
        added.push(s);
      }
    }
    if (url) {
      const link = document.createElement("link");
      link.rel = "canonical";
      link.href = url;
      link.dataset.siteHead = "";
      document.head.appendChild(link);
      added.push(link);
    }
    return () => {
      document.title = prevTitle;
      if (base && prevDescription !== undefined) base.content = prevDescription;
      added.forEach((el) => el.remove());
      restore.reverse().forEach((undo) => undo());  // last changed first, so removed neighbours go back in their places
    };
  }, [title, description, path, siteUrl, image, noindex, ld]);
}
