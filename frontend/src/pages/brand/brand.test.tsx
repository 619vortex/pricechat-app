import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BrandManagerPage } from "./BrandManager";

/** TEST FIXTURES only: a fake Brand Manager API. No real TikTok, nothing is posted. */
const calls: { method: string; url: string; body?: string }[] = [];
const now = new Date().toISOString();
const automation = (over: Record<string, unknown> = {}) => ({ running: false, status: "stopped", reason: "", started_at: null, last_tick_at: now, emergency_stop: false,
  upload_consent: false, blocking: ["Confirm that Brand Manager may upload finished videos to your TikTok drafts (the checkbox next to START AUTO)."], warnings: [], ...over });
const tiktok = { status: "connected", username: "pricechat", display_name: "Price Chat", avatar_url: null, open_id: "o", profile: { follower_count: 1200 },
  scopes: ["video.upload", "video.publish", "video.list"], missing_scopes: [], can_upload_drafts: true, can_direct_post: true, can_read_stats: true,
  access_expires_at: now, refresh_expires_at: now, creator_info: { creator_nickname: "Price Chat", creator_username: "pricechat",
  privacy_level_options: ["PUBLIC_TO_EVERYONE", "SELF_ONLY"], comment_disabled: false, duet_disabled: true, stitch_disabled: false }, last_error: null,
  connected_at: now, app_configured: true, redirect_uri: "https://pricechat.live/api/brand-manager/tiktok/callback" };
const overview = (a = automation()) => ({ brand: { id: 1, name: "Price Chat" }, automation: a, tiktok,
  stats: { created_today: 2, published_today: 1, sent_to_drafts_today: 1, upcoming: 3, total_views: 5400, followers: 1200, followers_gained: 35, best_video: null, measured_videos: 1 },
  upcoming: [], in_progress: [], pipeline: [{ key: "discover", label: "Discover topic", where: "backend" }], workers: [], events: [{ at: now, level: "info", stage: null, message: "AUTO started.", video_id: null }] });

function serve(routes: Record<string, unknown | ((body: string | undefined) => unknown)>) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input).split("?")[0];
    calls.push({ method: init?.method ?? "GET", url, body: init?.body as string | undefined });
    const hit = routes[`${init?.method ?? "GET"} ${url}`] ?? routes[url];
    const body = typeof hit === "function" ? (hit as (b?: string) => unknown)(init?.body as string | undefined) : hit;
    return body === undefined ? new Response(JSON.stringify({ detail: "nope" }), { status: 404 }) : new Response(JSON.stringify(body), { status: 200 });
  }));
}
const flush = async () => { for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Brand Manager", () => {
  it("is one button: START AUTO needs the upload consent, then says RUNNING", async () => {
    let state = automation();
    serve({ "/api/brand-manager/overview": () => overview(state),
            "POST /api/brand-manager/auto/start": () => (state = automation({ running: true, status: "running", upload_consent: true, blocking: [] })) });
    render(<MemoryRouter initialEntries={["/brand-manager"]}><Routes><Route path="/brand-manager/*" element={<BrandManagerPage />} /></Routes></MemoryRouter>);
    await flush();
    expect(screen.getByRole("heading", { name: "YOUR AI BRAND MANAGER" })).toBeTruthy();
    expect(screen.getByText("Your social media runs itself.")).toBeTruthy();
    expect(screen.getByText(/AUTO can't run yet/)).toBeTruthy();
    const start = screen.getByRole("button", { name: "START AUTO" }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);  // consent first
    fireEvent.click(screen.getByRole("checkbox"));
    expect(start.disabled).toBe(false);
    await act(async () => { fireEvent.click(start); });
    await flush();
    expect(JSON.parse(calls.find((c) => c.url.endsWith("/auto/start"))?.body ?? "{}")).toEqual({ consent: true });
    expect(screen.getByRole("button", { name: "STOP AUTO" })).toBeTruthy();
    expect(screen.getAllByText(/AI RUNNING/).length).toBeGreaterThan(0);
    expect(screen.getByText("5,400")).toBeTruthy();
    expect(screen.getByText("+35")).toBeTruthy();
  });

  it("shows the TikTok account, its granted permissions and the setup checklist", async () => {
    serve({ "/api/brand-manager/tiktok": tiktok });
    render(<MemoryRouter initialEntries={["/brand-manager/tiktok"]}><Routes><Route path="/brand-manager/*" element={<BrandManagerPage />} /></Routes></MemoryRouter>);
    await flush();
    expect(screen.getByText("@pricechat")).toBeTruthy();
    expect(screen.getByText(/Upload videos to your TikTok drafts/).closest("li")?.className).toBe("is-on");
    expect(screen.getByText(/See follower and like counts/).closest("li")?.className).toBe("is-off");
    expect(screen.getByText("https://pricechat.live/api/brand-manager/tiktok/callback")).toBeTruthy();
  });

  it("direct posts use TikTok's posting screen: privacy has no default, branded content can't be private", async () => {
    const video = { id: 9, topic: "Bitcoin is up 3.2%", category: "crypto_market", category_label: "Market", template: "price_alert", template_label: "B · Crypto Price Alert",
      status: "needs_review", score: 1, slot_at: now, title: "Bitcoin up 3.2%", hashtags: ["#bitcoin"], duration: 28, has_video: true, has_thumb: true,
      publish_mode: "direct", publish_status: null, published_at: null, metrics: null, error: null, created_at: now, stages: {}, approved: false, private: false,
      generation: 1, script: { title: "Bitcoin up 3.2%", hashtags: ["#bitcoin"], description: "From CoinGecko." }, brief: { facts: [] }, qc: null, render_info: null,
      post_settings: null, sources: [], events: [] };
    serve({ "/api/brand-manager/videos/9": video, "/api/brand-manager/tiktok": tiktok, "/api/brand-manager/videos": { items: [], total: 0 },
            "POST /api/brand-manager/videos/9/approve": { ...video, status: "approved" }, "POST /api/brand-manager/tiktok/creator-info": tiktok,
            "POST /api/brand-manager/videos/9/publish-now": { ...video, status: "publishing", publish_status: "PROCESSING" } });
    render(<MemoryRouter initialEntries={["/brand-manager/studio/9"]}><Routes><Route path="/brand-manager/*" element={<BrandManagerPage />} /></Routes></MemoryRouter>);
    await flush();
    expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/tiktok/creator-info"))).toBe(true);  // fresh, every time the screen renders
    expect(screen.getByRole("radio", { name: "Post directly" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByText(/Posting to/).textContent).toContain("Price Chat");
    const approve = screen.getByRole("button", { name: "Approve & post at its slot" }) as HTMLButtonElement;
    const privacy = screen.getByLabelText("Who can see this video") as HTMLSelectElement;
    expect(privacy.value).toBe("");
    expect(approve.disabled).toBe(true);
    expect((screen.getByLabelText("Duet") as HTMLInputElement).disabled).toBe(true);  // off in the creator's settings
    fireEvent.change(privacy, { target: { value: "SELF_ONLY" } });
    expect(approve.disabled).toBe(false);
    fireEvent.click(screen.getByLabelText("Disclose video content"));
    expect(approve.disabled).toBe(true);  // disclosure on: pick an option
    fireEvent.click(screen.getByLabelText(/Branded content/));
    expect(privacy.value).toBe("");  // branded content can't be private
    expect(screen.getByText(/Paid partnership/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Branded Content Policy" })).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/Branded content/));
    fireEvent.click(screen.getByLabelText(/Your brand/));
    fireEvent.change(privacy, { target: { value: "SELF_ONLY" } });
    await act(async () => { fireEvent.click(approve); });
    expect(JSON.parse(calls.find((c) => c.url.endsWith("/approve"))?.body ?? "{}")).toMatchObject({ privacy_level: "SELF_ONLY", your_brand: true, branded_content: false,
      allow_comment: false, allow_duet: false, consent: true });
    expect(calls.some((c) => c.url.endsWith("/publish-now"))).toBe(false);  // approved for its slot only
  });

  it("Post to TikTok now confirms the choices, then submits; posting is off when TikTok returns no creator info", async () => {
    const video = { id: 9, topic: "Bitcoin", category: "crypto_market", category_label: "Market", template: "price_alert", template_label: "B",
      status: "ready", score: 1, slot_at: now, title: "Bitcoin", hashtags: [], duration: 700, has_video: true, has_thumb: false, publish_mode: "direct",
      publish_status: null, published_at: null, metrics: null, error: null, created_at: now, stages: {}, approved: false, private: false, generation: 1,
      script: { title: "Bitcoin" }, brief: { facts: [] }, qc: null, render_info: null, post_settings: null, sources: [], events: [] };
    serve({ "/api/brand-manager/videos/9": video, "/api/brand-manager/tiktok": tiktok, "/api/brand-manager/videos": { items: [], total: 0 },
            "POST /api/brand-manager/tiktok/creator-info": { ...tiktok, creator_info: { ...tiktok.creator_info, max_video_post_duration_sec: 600 } } });
    render(<MemoryRouter initialEntries={["/brand-manager/studio/9"]}><Routes><Route path="/brand-manager/*" element={<BrandManagerPage />} /></Routes></MemoryRouter>);
    await flush();
    expect(screen.getByText("Too long for this account")).toBeTruthy();  // 700 s > the account's 600 s
    fireEvent.change(screen.getByLabelText("Who can see this video"), { target: { value: "SELF_ONLY" } });
    expect((screen.getByRole("button", { name: "Post to TikTok now" }) as HTMLButtonElement).disabled).toBe(true);
    cleanup();

    serve({ "/api/brand-manager/videos/9": { ...video, duration: 30 }, "/api/brand-manager/tiktok": tiktok, "/api/brand-manager/videos": { items: [], total: 0 },
            "POST /api/brand-manager/tiktok/creator-info": tiktok, "POST /api/brand-manager/videos/9/approve": { ...video, status: "approved" },
            "POST /api/brand-manager/videos/9/publish-now": { ...video, status: "publishing", publish_status: "PROCESSING" } });
    render(<MemoryRouter initialEntries={["/brand-manager/studio/9"]}><Routes><Route path="/brand-manager/*" element={<BrandManagerPage />} /></Routes></MemoryRouter>);
    await flush();
    fireEvent.change(screen.getByLabelText("Who can see this video"), { target: { value: "SELF_ONLY" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Post to TikTok now" })); });
    await flush();
    expect(calls.filter((c) => c.method === "POST").map((c) => c.url.split("/").pop())).toEqual(expect.arrayContaining(["approve", "publish-now"]));
    expect(calls.findIndex((c) => c.url.endsWith("/approve"))).toBeLessThan(calls.findIndex((c) => c.url.endsWith("/publish-now")));
    cleanup();

    serve({ "/api/brand-manager/videos/9": { ...video, duration: 30 }, "/api/brand-manager/tiktok": tiktok, "/api/brand-manager/videos": { items: [], total: 0 } });
    render(<MemoryRouter initialEntries={["/brand-manager/studio/9"]}><Routes><Route path="/brand-manager/*" element={<BrandManagerPage />} /></Routes></MemoryRouter>);
    await flush();
    expect(screen.getByText(/posting is off/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Post to TikTok now" })).toBeNull();
  });

  it("Settings save every tab without the read-only catalogs (no `voices`), keeping the narrator", async () => {
    const strategy = { videos_per_day: 3, min_seconds: 20, max_seconds: 40, language: "English", voice: "auto", voice_name: "bm_george", voice_speed: 1.05,
      categories: ["crypto_news"], category_weights: {}, templates: {}, hashtags: { always: ["#pricechat"], per_category: {}, max: 5 }, timezone: "Asia/Dubai",
      post_times: ["10:00"], publish_mode: "drafts", approval_required: false, tiktok_audited: false, content_disclosure: "your_brand", ai_disclosure: true,
      watermark: false, outro_cta: true, llm: "auto", ollama_model: "gemma3:27b", quality: "standard", monthly_cost_limit_usd: 20, risk_level: "low",
      banned_topics: [], require_sources: true, duplicate_days: 14, music: "", creative: { engine: "motion", narrator: "documentary" } };
    const brand = { id: 1, name: "Price Chat", website: "https://pricechat.live", description: "", audience: "", industry: "", language: "English", personality: "",
      logo_url: "", cta: "Follow.", topics: [], avoid: [], colors: ["#00e58f"], competitors: [], examples: [], is_default: true, strategy };
    serve({ "/api/brand-manager/brand": { ...brand, categories: { crypto_news: { label: "Crypto news", template: "breaking_news" } }, templates: {},
                                          voices: { bm_george: "George · UK male", am_michael: "Michael · US male" }, defaults: strategy },
            "PATCH /api/brand-manager/brand": (b?: string) => ({ ...brand, ...JSON.parse(b ?? "{}") }),
            "/api/brand-manager/workers": { workers: [], queued: 0, running: 0 }, "/api/brand-manager/overview": overview() });
    render(<MemoryRouter initialEntries={["/brand-manager/settings"]}><Routes><Route path="/brand-manager/*" element={<BrandManagerPage />} /></Routes></MemoryRouter>);
    await flush();
    for (const tab of ["General", "Content", "Publishing", "AI", "Safety"]) {
      fireEvent.click(screen.getByRole("button", { name: tab }));
      await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Save settings" })); });
      await flush();
      expect(screen.queryByText(/Extra inputs/)).toBeNull();
    }
    const saves = calls.filter((c) => c.method === "PATCH" && c.url.endsWith("/brand")).map((c) => JSON.parse(c.body ?? "{}"));
    expect(saves).toHaveLength(5);
    for (const s of saves) {
      expect(Object.keys(s).sort()).toEqual(["audience", "avoid", "colors", "competitors", "cta", "description", "examples", "industry", "language", "logo_url",
        "name", "personality", "strategy", "topics", "website"]);
      expect(s.strategy.voice_name).toBe("bm_george");
      expect(s.strategy.creative.narrator).toBe("documentary");
    }
  });

  it("Content Studio v2: pick a narrator, edit a scene and regenerate it with the same script; the quality report is shown", async () => {
    const video = { id: 7, topic: "Mewtwo PSA 10 sold for $17,225", category: "pokemon_rare", category_label: "Rare sale", template: "pokemon_spotlight",
      template_label: "C · Pokémon Spotlight", status: "ready", score: 1, slot_at: null, title: "Mewtwo PSA 10", hashtags: [], duration: 30, has_video: true,
      has_thumb: true, publish_mode: "drafts", publish_status: null, published_at: null, metrics: null, error: null, created_at: now, stages: {}, approved: false,
      private: false, generation: 2, brief: { facts: [] }, qc: null, post_settings: null, sources: [], events: [], creative: {},
      script: { title: "Mewtwo PSA 10", segments: [{ scene: "price_reveal", visual: "price_reveal", beat: "hook", text: "Someone paid $17,225 for cardboard.", screen: "$17,225" },
                                                  { scene: "card_entrance", visual: "card_entrance", beat: "context", text: "It's Rocket's Mewtwo.", screen: "Rocket's Mewtwo" }] },
      render_info: { renderer: "remotion", seconds: 62, cost_usd: 0, narration_match: 0.97, timings: { render: 31 },
                     storyboard: [{ kind: "price_reveal", start: 0, end: 3.1, transition: "cut", screen: "$17,225" }],
                     checks: [{ id: "facts", label: "No unsupported numbers on screen", status: "pass", detail: "every number on screen is in the research" },
                              { id: "frame", label: "Graphics fit the frame", status: "warn", detail: "bright content at the frame edge in: 2 card_detail" }] } };
    const options = { defaults: { engine: "motion" }, options: {
      engine: { motion: "Motion graphics (v2)", classic: "Classic slides (v1)" }, style: { auto: "Auto", pokemon: "Pokémon card sale" },
      tone: { auto: "Auto", funny: "Funny" }, narrator: { auto: "Auto", documentary: "Documentary (calm, British)" }, intensity: { auto: "Auto" },
      music: { auto: "Auto" }, captions: { auto: "Auto", boxed: "Boxed word" }, preset: { "": "None" },
      visual: { price_reveal: "Price reveal", card_entrance: "Card entrance (3D)", punchline: "Kinetic text" } } };
    serve({ "/api/brand-manager/videos/7": video, "/api/brand-manager/tiktok": tiktok, "/api/brand-manager/videos": { items: [], total: 0 },
            "/api/brand-manager/creative/options": options, "POST /api/brand-manager/videos/7/rerender": { ...video, status: "queued", generation: 3 } });
    render(<MemoryRouter initialEntries={["/brand-manager/studio/7"]}><Routes><Route path="/brand-manager/*" element={<BrandManagerPage />} /></Routes></MemoryRouter>);
    await flush();
    fireEvent.change(screen.getByLabelText("Narrator"), { target: { value: "documentary" } });
    fireEvent.change(screen.getByLabelText("Scene 2 visual"), { target: { value: "punchline" } });
    fireEvent.change(screen.getByLabelText("Scene 2 on-screen words"), { target: { value: "Gym Challenge" } });
    await act(async () => { fireEvent.click(screen.getAllByRole("button", { name: "Regenerate scene" })[1]); });
    await flush();
    expect(JSON.parse(calls.find((c) => c.url.endsWith("/rerender"))?.body ?? "{}")).toMatchObject({ scope: "scene",
      creative: { narrator: "documentary", scenes: { 1: { visual: "punchline", screen: "Gym Challenge" } } } });
    expect(screen.getByText("No unsupported numbers on screen").closest("li")?.className).toBe("is-pass");
    expect(screen.getByText("Graphics fit the frame").closest("li")?.className).toBe("is-warn");
    expect(screen.getByText(/narration 97% matched to the script/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "New script" })).toBeTruthy();
  });
});
