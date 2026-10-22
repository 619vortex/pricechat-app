/** The website's heads and the backend's link previews (backend/app/seo/heads.py) must say the same thing: both test
 * suites check their builders against seoHeads.golden.json. */
import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { CardDetail } from "../pages/pokemon/api";
import type { VaultDetail } from "../pages/pokemon/vault/api";
import golden from "./seoHeads.golden.json";
import { cardHead, cardJsonLd, isNoindexPath, landingHead, PAGE_HEADS, portfolioHead, portfolioJsonLd, profileHead, vaultHead, vaultJsonLd } from "./seo";
import { absolutize, useHead } from "./useHead";

describe("page heads match the link previews", () => {
  it("fixed pages", () => {
    expect(PAGE_HEADS).toEqual(golden.pages);
  });

  it("owner-only pages are noindex", () => {
    for (const [path, no] of Object.entries(golden.noindex)) expect([path, isNoindexPath(path)]).toEqual([path, no]);
  });

  it("card pages", () => {
    for (const c of golden.cards) {
      const d = c.detail as unknown as CardDetail;
      const head = cardHead(d, c.grade);
      expect({ title: head.title, description: head.description, image: head.image ?? null }).toEqual(c.head);
      expect(absolutize(cardJsonLd(d, c.grade), golden.base)).toEqual(c.jsonLd);
    }
  });

  it("one-set / one-year landing pages", () => {
    for (const c of golden.landing.cases) expect([c.search, landingHead(c.search, golden.landing.sets, golden.landing.years)]).toEqual([c.search, c.head]);
  });

  it("Vault cards", () => {
    for (const c of golden.vault) {
      const v = c.card as unknown as VaultDetail;
      expect(vaultHead(v)).toEqual(c.head);
      expect(absolutize(vaultJsonLd(v), golden.base)).toEqual(c.jsonLd);
    }
  });

  it("collector portfolios and Kiwi Passports", () => {
    for (const c of golden.portfolios) {
      expect(portfolioHead(c.portfolio)).toEqual(c.head);
      expect(absolutize(portfolioJsonLd(c.portfolio.username), golden.base)).toEqual(c.jsonLd);
    }
    for (const c of golden.profiles) expect(profileHead(c.profile)).toEqual(c.head);
  });
});

describe("useHead", () => {
  it("adds robots noindex and JSON-LD with absolute addresses, and removes them on leave", () => {
    const ld = [{ "@type": "BreadcrumbList", itemListElement: [{ "@type": "ListItem", position: 1, name: "</script>", item: "/pokemon" }] }];
    const { unmount } = renderHook(() => useHead({ title: "T", description: "D", path: "/pokemon/portfolio", siteUrl: "https://pricechat.live", noindex: true, jsonLd: ld }));
    expect(document.head.querySelector('meta[name="robots"]')?.getAttribute("content")).toBe("noindex");
    const script = document.head.querySelector('script[type="application/ld+json"]');
    expect(script?.textContent).not.toContain("</script>");
    expect(JSON.parse(script!.textContent!).itemListElement[0].item).toBe("https://pricechat.live/pokemon");
    unmount();
    expect(document.head.querySelector('meta[name="robots"]')).toBeNull();
    expect(document.head.querySelector('script[type="application/ld+json"]')).toBeNull();
  });

  it("a public page is indexable", () => {
    const { unmount } = renderHook(() => useHead({ title: "T", description: "D", path: "/earn", siteUrl: "https://pricechat.live" }));
    expect(document.head.querySelector('meta[name="robots"]')).toBeNull();
    expect(document.head.querySelector('link[rel="canonical"]')?.getAttribute("href")).toBe("https://pricechat.live/earn");
    unmount();
  });
});
