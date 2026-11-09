import { act, cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ContentArchive, Website } from "./Website";
import type { HomeData, WebsiteCard } from "./websiteLogic";

const card = (id: string, title: string, published: string, over: Partial<WebsiteCard> = {}): WebsiteCard => ({
  platform: "youtube", external_id: id, type: "short", type_label: "Short", title, url: `https://www.youtube.com/shorts/${id}`,
  thumbnail: `https://i.ytimg.com/vi/${id}/maxresdefault.jpg`, duration: "0:45", published_at: published, featured: false,
  views: 1326, series: null, category: null, ...over,
});

const baseHome = (over: Partial<HomeData> = {}): HomeData => ({
  sections: { latest: true, trending: true, whats_it_worth: true, price_board: true, originals: true, community: true, story: true },
  latest: [
    card("new", "Brand new upload 🤯 #crypto #trading", "2026-10-04T09:00:00+00:00"),
    card("mid", "The quietest millionaire... #story", "2026-10-02T09:00:00+00:00"),
    card("old", "Older one", "2026-10-01T09:00:00+00:00"),
  ],
  trending: [], whats_it_worth: [], originals: [], price_board: [], categories: [],
  links: { telegram: null, x: "https://x.com/pricechat", youtube: "https://www.youtube.com/@pricechat" }, site_url: null, ...over,
});

function serve(routes: Record<string, unknown>) {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => {
    const body = routes[path.split("?")[0]];
    return body === undefined ? new Response("", { status: 404 }) : new Response(JSON.stringify(body), { status: 200 });
  }));
}

async function renderSite(ui: React.ReactElement) {
  const r = render(ui);
  await act(async () => {
    await new Promise((res) => setTimeout(res, 0));
  });
  return r;
}

beforeEach(() => {
  localStorage.clear();
  window.matchMedia = ((q: string) => ({ matches: q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {} })) as never;
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.head.querySelectorAll("[data-site-head]").forEach((el) => el.remove());
});

describe("homepage", () => {
  it("features the newest upload and links every item to the real video", async () => {
    serve({ "/api/website/home": baseHome() });
    const { container } = await renderSite(<Website />);
    const feature = container.querySelector<HTMLAnchorElement>(".site-feature")!;
    expect(feature.href).toBe("https://www.youtube.com/shorts/new");
    expect(feature.target).toBe("_blank");
    expect(within(feature).getByText("Brand new upload 🤯")).toBeTruthy(); // hashtags hidden for display only
    expect(within(feature).getByText("1.3K views")).toBeTruthy();
    const rows = [...container.querySelectorAll<HTMLAnchorElement>(".site-row")].map((a) => a.href);
    expect(rows).toEqual(["https://www.youtube.com/shorts/mid", "https://www.youtube.com/shorts/old"]);
  });

  it("shows the hero statement and supporting line", async () => {
    serve({ "/api/website/home": baseHome() });
    await renderSite(<Website />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/Everything\s*has a price\./i);
    expect(screen.getByText("Markets. Money. Culture.")).toBeTruthy();
  });

  it("renders no section that lacks real data, and no placeholder text", async () => {
    serve({ "/api/website/home": baseHome() });
    const { container } = await renderSite(<Website />);
    for (const id of ["whats-it-worth", "price-board", "originals"]) expect(container.querySelector(`#${id}`), id).toBeNull();
    expect(container.querySelector(".site-ticker")).toBeNull();
    expect(container.textContent).not.toMatch(/placeholder|lorem/i);
    // the nav only links to places that exist
    const nav = within(container.querySelector(".site-nav")!);
    expect(nav.getAllByRole("link").map((a) => a.textContent)).toEqual(["Latest", "About", "Open Packs"]);
    // Telegram has no link yet, so it is not offered
    expect(container.querySelector(".site-join-telegram")).toBeNull();
    expect(container.querySelector<HTMLAnchorElement>(".site-join-x")!.href).toBe("https://x.com/pricechat");
  });

  it("shows franchise sections, trending topics and category links once real data exists", async () => {
    serve({
      "/api/website/home": baseHome({
        trending: ["Bitcoin", "Gold"],
        whats_it_worth: [card("w", "What is this card worth?", "2026-10-03T09:00:00+00:00", { series: "whats_it_worth" })],
        originals: [card("o", "An original story", "2026-09-30T09:00:00+00:00", { series: "originals" })],
        categories: ["crypto"],
      }),
    });
    const { container } = await renderSite(<Website />);
    expect(container.querySelector("#whats-it-worth a")!.getAttribute("href")).toBe("https://www.youtube.com/shorts/w");
    expect(container.querySelector("#originals")).not.toBeNull();
    expect(within(container.querySelector(".site-ticker")!).getAllByText("Bitcoin").length).toBeGreaterThan(0);
    expect(within(container.querySelector(".site-nav")!).getByText("Crypto").getAttribute("href")).toBe("/content?category=crypto");
    expect(container.querySelector("#price-board")).toBeNull(); // no market data, ever invented
  });

  it("respects section switches from the dashboard", async () => {
    serve({ "/api/website/home": baseHome({ sections: { ...baseHome().sections, latest: false, community: false } }) });
    const { container } = await renderSite(<Website />);
    expect(container.querySelector("#latest")).toBeNull();
    expect(container.querySelector("#community")).toBeNull();
    expect(container.querySelector("#story")).not.toBeNull();
  });

  it("sets page metadata and adds a canonical URL only when a public address is configured", async () => {
    serve({ "/api/website/home": baseHome() });
    await renderSite(<Website />);
    expect(document.title).toMatch(/Price Chat/);
    expect(document.head.querySelector('meta[property="og:title"]')).not.toBeNull();
    expect(document.head.querySelector('meta[property="og:image"]')!.getAttribute("content")).toBe("https://i.ytimg.com/vi/new/maxresdefault.jpg");
    expect(document.head.querySelector('link[rel="canonical"]')).toBeNull();
    cleanup();
    localStorage.clear();
    serve({ "/api/website/home": baseHome({ site_url: "https://pricechat.example" }) });
    await renderSite(<Website />);
    expect(document.head.querySelector('link[rel="canonical"]')!.getAttribute("href")).toBe("https://pricechat.example/");
  });

  it("keeps the last synced content if the backend is briefly unavailable", async () => {
    serve({ "/api/website/home": baseHome() });
    await renderSite(<Website />);
    cleanup();
    serve({});
    const { container } = await renderSite(<Website />);
    expect(container.querySelector<HTMLAnchorElement>(".site-feature")!.href).toBe("https://www.youtube.com/shorts/new");
  });
});

describe("content archive", () => {
  it("lists everything newest first and filters Shorts / Videos", async () => {
    const items = [card("s1", "Short one", "2026-10-03T00:00:00+00:00"), card("v1", "Long one", "2026-10-01T00:00:00+00:00", { type: "long_form", type_label: "Video" })];
    serve({ "/api/website/home": baseHome(), "/api/website/content": { items } });
    const { container } = await renderSite(<ContentArchive />);
    const hrefs = () => [...container.querySelectorAll<HTMLAnchorElement>(".site-grid .site-card")].map((a) => a.href.split("/").pop());
    expect(hrefs()).toEqual(["s1", "v1"]);
    await act(async () => screen.getByRole("tab", { name: "Videos" }).click());
    expect(hrefs()).toEqual(["v1"]);
    await act(async () => screen.getByRole("tab", { name: "Shorts" }).click());
    expect(hrefs()).toEqual(["s1"]);
  });
});

describe("brand and community", () => {
  it("sells the Kiwi VPN member perk next to the sign-up: three points, linking to /vpn", async () => {
    serve({ "/api/website/home": baseHome() });
    const { container } = await renderSite(<Website />);
    const perk = container.querySelector("#community .site-vpnperk")!;
    expect(perk.querySelector("h3")!.textContent).toContain("Kiwi VPN");
    expect([...perk.querySelectorAll("li b")].map((b) => b.textContent)).toEqual(["Your address stays hidden", "We don't log the sites you visit", "30-day free trial"]);
    expect(perk.querySelector("a")!.getAttribute("href")).toBe("/vpn");
  });

  it("puts the sign-up and the market in the first viewport, inside the hero (audit UX-3)", async () => {
    serve({ "/api/website/home": baseHome() });
    const { container } = await renderSite(<Website />);
    const actions = container.querySelector("#top .site-hero-actions")!;
    const links = [...actions.querySelectorAll<HTMLAnchorElement>("a")];
    expect(links.map((a) => [a.textContent!.replace("→", "").trim(), a.getAttribute("href")])).toEqual([
      ["Get your free Kiwi Passport", "/join"], ["Explore the Pokémon market", "/pokemon"],
    ]);
  });

  it("only names the VPN price while payments are open (audit UX-6)", async () => {
    serve({ "/api/website/home": baseHome(), "/api/members/vpn/public": { enabled: true, payments: false, trial: true, free: false, billing: null } });
    const off = await renderSite(<Website />);
    const line = () => [...off.container.querySelectorAll("#community .site-vpnperk li")].map((li) => li.textContent).join(" | ");
    expect(line()).toContain("30-day free trial");
    expect(line()).toContain("The paid plan isn't open yet");
    expect(line()).not.toMatch(/\$/);
    cleanup();
    serve({ "/api/website/home": baseHome(), "/api/members/vpn/public": { enabled: true, payments: true, trial: true, free: false, billing: { price_usd: 9.99, period_days: 30 } } });
    const on = await renderSite(<Website />);
    expect(on.container.querySelector("#community .site-vpnperk")!.textContent).toContain("Then $9.99 for 30 days, renewed by hand");
  });

  it("renders YouTube, X and Telegram in that order, with handles and real links", async () => {
    serve({ "/api/website/home": baseHome({ links: { telegram: "https://t.me/thepricechat", x: "https://x.com/pricechat", youtube: "https://www.youtube.com/@pricechat" } }) });
    const { container } = await renderSite(<Website />);
    const rows = [...container.querySelectorAll<HTMLAnchorElement>(".site-join a")];
    expect(rows.map((a) => a.href)).toEqual(["https://www.youtube.com/@pricechat", "https://x.com/pricechat", "https://t.me/thepricechat"]);
    expect(rows.map((a) => a.querySelector(".site-join-handle")?.textContent)).toEqual(["@pricechat", "@pricechat", "@thepricechat"]);
    expect(rows[2].textContent).toContain("Telegram");
    expect(rows.every((a) => a.target === "_blank" && a.rel.includes("noopener"))).toBe(true);
  });

  it("shows each linked platform's real count, its change and a bare sparkline, without turning the rows into cards", async () => {
    const day = (d: string, value: number) => ({ day: d, value });
    serve({ "/api/website/home": baseHome({
      links: { telegram: "https://t.me/thepricechat", x: "https://x.com/pricechat", youtube: "https://www.youtube.com/@pricechat" },
      growth: {
        youtube: { count: 36, unit: "subscribers", as_of: "", tracking_since: "2026-10-06", change_24h: null, change_7d: null, change_30d: null,
                   change_window: 0, change_window_pct: 0, window_start: "2026-10-06", history: [day("2026-10-06", 36)] },
        x: { count: 40, unit: "followers", as_of: "", tracking_since: "2026-10-03", change_24h: 7, change_7d: null, change_30d: null,
             change_window: 22, change_window_pct: 122.2, window_start: "2026-10-03",
             history: [day("2026-10-03", 18), day("2026-10-04", 33), day("2026-10-05", 37), day("2026-10-06", 40)] },
        telegram: null, // not measured: the row stays exactly as before
      },
    }) });
    const { container } = await renderSite(<Website />);
    const row = (n: string) => container.querySelector(`.site-join-${n}`)!;
    expect(row("x").querySelector(".site-join-count")!.getAttribute("aria-label")).toBe("40 followers");
    expect(row("x").querySelector(".site-join-change")!.textContent).toBe("+122.2% ↗ since 3 Oct");
    expect(row("x").querySelector(".site-join-spark path")).not.toBeNull();
    expect(row("x").querySelector(".site-join-spark")!.querySelectorAll("text, line, rect, g")).toHaveLength(0); // no axes, labels, grid or box
    expect(row("youtube").querySelector(".site-join-change")!.textContent).toBe("tracking since 6 Oct"); // nothing invented
    expect(row("youtube").querySelector(".site-join-spark path")).toBeNull();
    expect(row("telegram").querySelector(".site-join-stat, .site-join-spark")).toBeNull();
    expect(row("telegram").textContent).toContain("@thepricechat");
  });

  it("uses only the canonical Kiwi asset: never a drawn or substitute Kiwi", async () => {
    serve({ "/api/website/home": baseHome() });
    const { container } = await renderSite(<Website />);
    const kiwis = [...container.querySelectorAll("img")].filter((i) => /kiwi/i.test(i.getAttribute("src") ?? "") || /kiwi/i.test(i.className + i.parentElement!.className));
    // header logo, 4 rank Kiwis in the join CTA, footer logo, and two Kiwi rigs made of layers of the same image:
    // hero (body, crest, eye) and Story flight (body, crest, 2 legs, eye, panic eye).
    expect(kiwis.length).toBe(15);
    expect(new Set(kiwis.map((i) => i.getAttribute("src")))).toEqual(new Set(["/brand/kiwi.webp"]));
    expect(container.querySelector(".site-hero-kiwi img")).not.toBeNull();
    expect(container.querySelector("#story .site-horizon-kiwi")).not.toBeNull();
    // only social/play icons are SVG, plus the Story Kiwi's small overlays (two wing stubs, three stars) and the rank
    // decoration drawn AROUND the rank Kiwis (backdrop, gear, sparks: .rkd): never a drawn Kiwi
    const overlays = container.querySelectorAll("#story .kr-wing path, #story .kf-star path, .rkd path").length;
    expect(container.querySelectorAll("#story .kr-wing path")).toHaveLength(2); // the flight Kiwi's only drawn parts
    expect(container.querySelectorAll("svg path").length).toBe(container.querySelectorAll(".site-icon path, .site-play path").length + overlays);
  });
});
