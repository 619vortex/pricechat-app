import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PicturePanel, type Picture } from "./PicturePanel";

/** TEST FIXTURES (made up): a library photo, an article preview and a held post, as /terminal/.../picture returns them. */
const BASE: Picture = { kind: "photo", held: false, image: "data:image/png;base64,iVBORw0KGgo=", preview_image: "", source: "Editor's photo library (fed), from Wikimedia Commons",
  page: "https://commons.wikimedia.org/wiki/File:Eccles.jpg", title: "File:Eccles.jpg", author: "Federalreserve", licence: "public domain", licence_url: "",
  credit: "Photo: Federalreserve / Wikimedia Commons (public domain)", site: "", size: [3000, 2041], why: "the editor's approved photo", depiction: "strong", previews: [] };

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function stub(picture: Picture, post?: (url: string, body: unknown) => unknown) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const body = init?.method === "POST" ? post?.(url, JSON.parse(String(init.body))) : picture;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("story panel › picture", () => {
  it("shows a library photo with its source, page, author, licence and credit", async () => {
    stub(BASE);
    render(<PicturePanel target={{ kind: "topic", id: 3 }} />);
    expect(await screen.findByAltText("File:Eccles.jpg")).toBeTruthy();
    expect(screen.getByText("Editor's photo library (fed), from Wikimedia Commons")).toBeTruthy();
    expect(screen.getByText("Federalreserve")).toBeTruthy();
    expect(screen.getByText("public domain")).toBeTruthy();
    expect(screen.getByText(/Photo: Federalreserve/)).toBeTruthy();
    expect(screen.getByText("3000 × 2041 px")).toBeTruthy();
    expect((screen.getByRole("link", { name: "File:Eccles.jpg" }) as HTMLAnchorElement).href).toContain("commons.wikimedia.org");
  });

  it("shows an article preview as the publisher's own, loaded without a referrer", async () => {
    stub({ ...BASE, kind: "link_preview", image: "", preview_image: "https://i.guim.co.uk/a.jpg", site: "the Guardian", title: "Nasdaq erases all 2026 gains",
      source: "Article preview from the Guardian (shown by Telegram, not re-uploaded)", author: "", licence: "Publisher's own link preview" });
    render(<PicturePanel target={{ kind: "topic", id: 4 }} />);
    const img = (await screen.findByAltText("Nasdaq erases all 2026 gains")) as HTMLImageElement;
    expect(img.src).toBe("https://i.guim.co.uk/a.jpg");
    expect(img.getAttribute("referrerpolicy")).toBe("no-referrer");
    expect(screen.getByText("Article preview")).toBeTruthy();
    expect(screen.getByText(/not re-uploaded/)).toBeTruthy();
  });

  it("shows a held post with its reasons and sends the owner's decision", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetch = stub({ ...BASE, kind: "held", held: true, image: "", source: "No picture: held for your review", page: "", title: "", author: "", licence: "", credit: "",
      size: [0, 0], why: "no photograph shows this news story's subject", previews: [{ site: "Financial Times", ok: false, why: "the article offers no preview picture" }] },
      (url, body) => ({ ok: true, url, body }));
    render(<PicturePanel target={{ kind: "event", id: 9, held: true }} />);
    expect(await screen.findByText("No picture: held for your review", { selector: "strong" })).toBeTruthy();
    expect(screen.getByText(/Financial Times: the article offers no preview picture/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Send without picture" }));
    await waitFor(() => expect(screen.getByText(/goes out as words alone/)).toBeTruthy());
    const call = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(String(call?.[0])).toContain("/terminal/events/9/held");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ action: "text" });
  });

  it("asks before releasing and does nothing when the owner cancels", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const fetch = stub({ ...BASE, kind: "held", held: true, image: "" });
    render(<PicturePanel target={{ kind: "event", id: 9, held: true }} />);
    fireEvent.click(await screen.findByRole("button", { name: "Skip" }));
    expect(fetch.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });
});
