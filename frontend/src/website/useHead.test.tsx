import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import indexHtml from "../../index.html?raw";
import { useHead, type HeadInfo } from "./useHead";

// Audit UX-18: index.html carries the home page's link-preview tags; a page's own tags must replace them, never sit after them.

function Page(p: HeadInfo) {
  useHead(p);
  return null;
}
const staticHead = () => new DOMParser().parseFromString(indexHtml, "text/html").head.innerHTML;
const all = (sel: string) => [...document.head.querySelectorAll<HTMLMetaElement>(sel)].map((m) => m.content);

beforeEach(() => { document.head.innerHTML = staticHead(); document.title = "Price Chat"; });
afterEach(() => { cleanup(); document.head.innerHTML = ""; });

describe("useHead with index.html's static tags", () => {
  const card: HeadInfo = { title: "Blastoise · PSA 10 Price · Price Chat", description: "Blastoise card", path: "/pokemon/cards/5",
    siteUrl: "https://pricechat.live", image: "/cards/u/b5292d9dcf0b2022.webp" };

  it("keeps exactly one of each OG / X tag, with the page's own values", () => {
    const before = document.head.innerHTML;
    const { unmount } = render(<Page {...card} />);
    for (const key of ["og:title", "og:description", "og:url", "og:image", "og:type", "og:site_name"]) expect(all(`meta[property="${key}"]`)).toHaveLength(1);
    for (const key of ["twitter:title", "twitter:description", "twitter:image", "twitter:card"]) expect(all(`meta[name="${key}"]`)).toHaveLength(1);
    expect(all('meta[property="og:title"]')).toEqual([card.title]);
    expect(all('meta[property="og:url"]')).toEqual(["https://pricechat.live/pokemon/cards/5"]);
    expect(all('meta[property="og:image"]')).toEqual(["https://pricechat.live/cards/u/b5292d9dcf0b2022.webp"]);
    expect(all('meta[name="twitter:image"]')).toEqual(["https://pricechat.live/cards/u/b5292d9dcf0b2022.webp"]);
    expect(all('meta[property="og:image:width"], meta[property="og:image:alt"]')).toEqual([]);  // the home picture's size isn't this one's
    expect(document.title).toBe(card.title);
    unmount();
    expect(document.head.innerHTML).toBe(before);  // leaving the page puts index.html's tags back exactly
  });

  it("a page without its own picture keeps the site picture (as the server's previews do), titled for this page", () => {
    render(<Page title="All content · Price Chat" description="Every video" path="/content" siteUrl={null} />);
    expect(all('meta[property="og:image"]')).toEqual(["https://pricechat.live/og.jpg"]);
    expect(all('meta[property="og:image:alt"]')).toEqual(["All content · Price Chat"]);
    expect(all('meta[property="og:url"]')).toEqual([]);  // no address decided yet: never the home page's
  });
});
