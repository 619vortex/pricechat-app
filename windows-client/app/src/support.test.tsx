// Tools › Support (2.1): a signed-in person sends a bug report or feedback; the page passes only kind, message and whether to include
// diagnostics (the Rust side builds and strips them); errors are shown as they came; signed out, it points to the website instead.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { Support } from "./support";
import type { AccountView, Answer, Settings } from "./vpn";

let calls: { cmd: string; a: unknown }[] = [];
let refuse: string | null = null;
let view: AccountView;
vi.mock("@tauri-apps/api/core", () => ({
  invoke: async (cmd: string, a?: unknown) => {
    calls.push({ cmd, a });
    if (cmd === "send_support") { if (refuse) throw refuse; return null; }
    if (cmd === "account_state") return view;
    if (cmd === "get_settings") return { launch_at_startup: false, auto_connect: false, minimize_to_tray: false, notifications: true } satisfies Settings;
    if (cmd === "vpn_status") return { service: true, message: null, status: { state: "disconnected", handshake_age_seconds: null, rx_bytes: 0, tx_bytes: 0, error: null, connected_since: null } } satisfies Answer;
    if (cmd === "has_test_config" || cmd === "should_auto_connect") return false;
    if (cmd === "my_ip") return "5.30.213.225";
    return null;
  },
}));

const flush = () => act(async () => { for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0)); });
const sent = () => calls.filter((c) => c.cmd === "send_support").map((c) => c.a);
const message = () => screen.getByLabelText("Message") as HTMLTextAreaElement;
const diagBox = () => screen.getByLabelText(/Include diagnostics/) as HTMLInputElement;

beforeEach(() => {
  calls = []; refuse = null;
  view = { signed_in: true, account: null, suggested: null, last_server: null, error: null };
});
afterEach(() => cleanup());

describe("Support", () => {
  it("sends a bug report with diagnostics by default and thanks the person", async () => {
    render(<Support signedIn />);
    expect(diagBox().checked).toBe(true);
    const send = screen.getByRole("button", { name: "Send" }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);  // nothing written yet
    fireEvent.change(message(), { target: { value: "  Failover never moved me  " } });
    fireEvent.click(send);
    await flush();
    expect(sent()).toEqual([{ kind: "bug", message: "Failover never moved me", includeDiagnostics: true }]);
    expect(screen.getByRole("status").textContent).toBe("Sent, thank you.");
    expect(message().value).toBe("");
  });

  it("feedback leaves diagnostics out unless ticked, and the box can be unticked for a bug", async () => {
    render(<Support signedIn />);
    fireEvent.click(screen.getByRole("radio", { name: "Feedback" }));
    expect(diagBox().checked).toBe(false);
    fireEvent.change(message(), { target: { value: "Love the kill switch" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await flush();
    fireEvent.click(screen.getByRole("radio", { name: "Bug" }));
    expect(diagBox().checked).toBe(true);
    fireEvent.click(diagBox());
    fireEvent.change(message(), { target: { value: "Crash" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await flush();
    expect(sent()).toEqual([{ kind: "feedback", message: "Love the kill switch", includeDiagnostics: false }, { kind: "bug", message: "Crash", includeDiagnostics: false }]);
  });

  it("shows the error and keeps the message when sending fails", async () => {
    refuse = "You've sent several reports already. Please try again later.";
    render(<Support signedIn />);
    fireEvent.change(message(), { target: { value: "It broke" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await flush();
    expect(screen.getByRole("alert").textContent).toBe("You've sent several reports already. Please try again later.");
    expect(message().value).toBe("It broke");
    expect(screen.queryByText("Sent, thank you.")).toBeNull();
  });

  it("blank or over-long messages can't be sent", () => {
    render(<Support signedIn />);
    fireEvent.change(message(), { target: { value: "    " } });
    expect((screen.getByRole("button", { name: "Send" }) as HTMLButtonElement).disabled).toBe(true);
    expect(message().maxLength).toBe(4000);
  });

  it("signed out: says to sign in and offers the website's contact page, with no form", async () => {
    render(<Support signedIn={false} />);
    expect(screen.getByText(/Sign in with Price Chat to send a bug report/)).toBeTruthy();
    expect(screen.queryByLabelText("Message")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Contact Price Chat" }));
    await flush();
    expect(calls).toContainEqual({ cmd: "open_site", a: { page: "contact" } });
    expect(sent()).toEqual([]);
  });

  it("is on the Tools page of the app, and knows whether the person is signed in", async () => {
    view = { signed_in: false, account: null, suggested: null, last_server: null, error: null };
    render(<App />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Tools" }));
    await flush();
    expect(screen.getByRole("region", { name: "Support" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Contact Price Chat" })).toBeTruthy();
  });
});
