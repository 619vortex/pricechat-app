import { describe, expect, it } from "vitest";
import { displayTitle, filterArchive, formatDate, formatViews, growthChange, handleFromUrl, sectionStates, sparkPoints, type Growth, type HomeData, type WebsiteCard } from "./websiteLogic";

const card = (over: Partial<WebsiteCard> = {}): WebsiteCard => ({
  platform: "youtube", external_id: "a", type: "short", type_label: "Short", title: "T", url: "https://www.youtube.com/shorts/a",
  thumbnail: null, duration: "0:45", published_at: "2026-10-03T05:50:02+00:00", featured: false, views: null, series: null, category: null, ...over,
});

const home = (over: Partial<HomeData> = {}): HomeData => ({
  sections: { latest: true, trending: true, whats_it_worth: true, price_board: true, originals: true, community: true, story: true },
  latest: [], trending: [], whats_it_worth: [], originals: [], price_board: [], categories: [],
  links: { telegram: null, x: null, youtube: null }, site_url: null, ...over,
});

describe("displayTitle (display only)", () => {
  it("drops trailing hashtags from the real Price Chat titles", () => {
    expect(displayTitle("He Put His Allowance Into Bitcoin… Then Became a Millionaire 🤯 #crypto #trading")).toBe(
      "He Put His Allowance Into Bitcoin… Then Became a Millionaire 🤯",
    );
    expect(displayTitle("The quietest millionaire... #story")).toBe("The quietest millionaire...");
    expect(displayTitle("She made $40K trading… in ONE competition 😳💰 #Trading #PriceChat")).toBe("She made $40K trading… in ONE competition 😳💰");
    expect(displayTitle("The guy who stole and gambled millions.#story")).toBe("The guy who stole and gambled millions.");
    expect(displayTitle("the man who bet everything he owned #storytime")).toBe("The man who bet everything he owned");
  });
  it("keeps the word of a hashtag inside the sentence", () => {
    expect(displayTitle("Why #Bitcoin rose today")).toBe("Why Bitcoin rose today");
  });
  it("never returns an empty headline", () => {
    expect(displayTitle("#shorts #crypto")).toBe("Shorts crypto");
  });
  it("leaves clean titles alone", () => {
    expect(displayTitle("Is Bitcoin next?")).toBe("Is Bitcoin next?");
  });
});

describe("formatting", () => {
  it("formats real view counts and never invents one", () => {
    expect(formatViews(1326)).toBe("1.3K views");
    expect(formatViews(88)).toBe("88 views");
    expect(formatViews(1)).toBe("1 view");
    expect(formatViews(2_450_000)).toBe("2.4M views");
    expect(formatViews(null)).toBeNull();
    expect(formatViews(undefined)).toBeNull();
  });
  it("shows the year only for other years", () => {
    const now = new Date("2026-10-04T12:00:00Z");
    expect(formatDate("2026-10-03T05:50:02+00:00", now)).toBe("3 Oct");
    expect(formatDate("2025-12-31T23:00:00+00:00", now)).toBe("31 Dec 2025");
    expect(formatDate(null, now)).toBeNull();
    expect(formatDate("garbage", now)).toBeNull();
  });
});

describe("sectionStates", () => {
  it("hides every section without real data", () => {
    const s = sectionStates(home({ latest: [card()] }));
    expect(s.latest.visible).toBe(true);
    for (const k of ["trending", "whats_it_worth", "price_board", "originals", "community"] as const) {
      expect(s[k].visible, k).toBe(false);
      expect(s[k].reason, k).not.toBe("");
    }
    expect(s.price_board.reason).toBe("No market-data source connected");
  });
  it("respects the dashboard switch even when data exists", () => {
    const s = sectionStates(home({ latest: [card()], sections: { ...home().sections, latest: false } }));
    expect(s.latest).toEqual({ on: false, hasData: true, visible: false, reason: "Switched off" });
  });
  it("shows community once any public link exists", () => {
    expect(sectionStates(home({ links: { telegram: null, x: "https://x.com/pricechat", youtube: null } })).community.visible).toBe(true);
  });
});

describe("filterArchive", () => {
  const items = [card({ external_id: "s", type: "short", category: "crypto" }), card({ external_id: "v", type: "long_form" })];
  it("filters by format and category", () => {
    expect(filterArchive(items, "all", null).map((i) => i.external_id)).toEqual(["s", "v"]);
    expect(filterArchive(items, "short", null).map((i) => i.external_id)).toEqual(["s"]);
    expect(filterArchive(items, "long_form", null).map((i) => i.external_id)).toEqual(["v"]);
    expect(filterArchive(items, "all", "crypto").map((i) => i.external_id)).toEqual(["s"]);
  });
});

describe("handleFromUrl", () => {
  it("reads the public handle from profile links", () => {
    expect(handleFromUrl("https://t.me/thepricechat")).toBe("@thepricechat");
    expect(handleFromUrl("https://x.com/pricechat")).toBe("@pricechat");
    expect(handleFromUrl("https://www.youtube.com/@pricechat")).toBe("@pricechat");
  });
  it("shows no handle when the link has none", () => {
    expect(handleFromUrl("https://www.youtube.com/channel/UCskEczJNGMiaQ59GAJwoFIw")).toBeNull();
    expect(handleFromUrl("https://t.me/+AbCdEf123")).toBeNull();
    expect(handleFromUrl(null)).toBeNull();
    expect(handleFromUrl("not a url")).toBeNull();
  });
});

describe("community growth", () => {
  const g = (over: Partial<Growth> = {}): Growth => ({
    count: 1051, unit: "members", as_of: "2026-10-06T08:10:00Z", tracking_since: "2026-10-04", change_24h: 6, change_7d: null,
    change_30d: null, change_window: 11, change_window_pct: 1.1, window_start: "2026-10-04",
    history: [{ day: "2026-10-04", value: 1040 }, { day: "2026-10-05", value: 1045 }, { day: "2026-10-06", value: 1051 }], ...over,
  });
  it("shows 7 days once there is a week of history, otherwise the change since tracking began", () => {
    expect(growthChange(g())).toEqual({ abs: 11, pct: 1.1, label: "since 4 Oct" });
    expect(growthChange(g({ change_7d: 51 }))).toEqual({ abs: 51, pct: 5.1, label: "7d" });
    expect(growthChange(g({ history: [{ day: "2026-10-06", value: 1051 }] }))).toBeNull(); // one day: just "tracking since"
  });
  it("draws the real days: spaced by date, higher value higher up, flat when nothing changed", () => {
    const p = sparkPoints([{ day: "2026-10-01", value: 10 }, { day: "2026-10-02", value: 20 }, { day: "2026-10-05", value: 15 }], 100, 20);
    expect(p.map(([x]) => x)).toEqual([0, 25, 100]); // a missing day keeps its gap: nothing is filled in
    expect(p[1][1]).toBeLessThan(p[2][1]);
    expect(p[0][1]).toBeGreaterThan(p[2][1]);
    expect(sparkPoints([{ day: "2026-10-01", value: 5 }, { day: "2026-10-02", value: 5 }], 100, 20).map(([, y]) => y)).toEqual([10, 10]);
    expect(sparkPoints([{ day: "2026-10-01", value: 5 }], 100, 20)).toEqual([]);
  });
});
