import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VpnAdminPage, type VpnOverview } from "./VpnAdmin";

/** TEST FIXTURES only: a fake Kiwi VPN admin API. No real agents, tokens or certificates. */
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const flush = async () => { for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };

const SERVER = { id: 3, name: "Auckland 1", region: "New Zealand", country_code: "NZ", endpoint_host: "203.0.113.10", listen_port: 51820,
  agent_url: "https://203.0.113.10:8443", capacity: 250, enabled: true, online: true, last_seen: new Date().toISOString(), last_error: null,
  devices: 12, active_24h: 5 };
const overview = (over: Partial<VpnOverview> = {}): VpnOverview => ({
  settings: { enabled: false, min_rank: "Scout Kiwi", max_devices: 3 }, ranks: ["Kiwi Chick", "Kiwi", "Scout Kiwi", "Warrior Kiwi"],
  servers: [SERVER], totals: { devices: 12, members: 9, active_24h: 5 }, ...over,
});

let calls: string[] = [];
function serve(handler: (key: string, body: string | undefined) => [number, unknown]) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${input}`;
    calls.push(`${key} ${init?.body ?? ""}`.trim());
    const [status, body] = handler(key, init?.body as string | undefined);
    return new Response(status === 204 ? null : JSON.stringify(body), { status });
  }));
}

describe("VPN admin", () => {
  it("shows totals and the servers table", async () => {
    serve(() => [200, overview()]);
    const { container } = render(<VpnAdminPage />);
    await flush();
    expect(container.textContent).toContain("Members using it");
    const row = container.querySelector("tbody tr")!.textContent!;
    expect(row).toContain("Auckland 1");
    expect(row).toContain("online");
    expect(row).toContain("12 / 250");
    expect(container.textContent).toContain("Run vpn-node/install.sh on a fresh Ubuntu VPS and paste what it prints.");
  });

  it("saves the settings", async () => {
    let s = overview();
    serve((key, body) => {
      if (key === "PUT /api/vpn/admin/settings") { s = { ...s, settings: { ...s.settings, ...JSON.parse(body!) } }; return [200, s.settings]; }
      return [200, s];
    });
    render(<VpnAdminPage />);
    await flush();
    fireEvent.click(screen.getByLabelText("VPN enabled"));
    fireEvent.change(screen.getByLabelText("Minimum rank"), { target: { value: "Warrior Kiwi" } });
    fireEvent.change(screen.getByLabelText("Max devices"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await flush();
    expect(calls).toContain('PUT /api/vpn/admin/settings {"enabled":true,"min_rank":"Warrior Kiwi","max_devices":5}');
    expect(screen.getByText("Saved")).toBeTruthy();
  });

  it("adds a server and shows the agent error when it can't be verified", async () => {
    let servers = [SERVER];
    let fail = true;
    serve((key) => {
      if (key === "POST /api/vpn/admin/servers") {
        if (fail) return [400, { detail: "Couldn't verify the agent: certificate fingerprint mismatch" }];
        servers = [...servers, { ...SERVER, id: 4, name: "Frankfurt 1", country_code: "DE" }];
        return [201, servers[1]];
      }
      return [200, overview({ servers })];
    });
    const { container } = render(<VpnAdminPage />);
    await flush();
    const type = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
    type("Name", "Frankfurt 1");
    type("Region", "Germany");
    type("Country code", "de");
    type("Agent URL", "https://198.51.100.7:8443");
    type("Agent token", "test-token-not-real");
    type("Cert SHA-256", "AB:CD:EF");
    type("Capacity", "200");
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));
    await flush();
    expect(screen.getByRole("alert").textContent).toContain("certificate fingerprint mismatch");
    const post = calls.find((c) => c.startsWith("POST /api/vpn/admin/servers"))!;
    expect(JSON.parse(post.slice(post.indexOf("{")))).toEqual({
      name: "Frankfurt 1", region: "Germany", country_code: "DE", agent_url: "https://198.51.100.7:8443",
      agent_token: "test-token-not-real", cert_sha256: "abcdef", capacity: 200,
    });
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));
    await flush();
    expect(screen.getByText("Added Frankfurt 1.")).toBeTruthy();
    expect(container.querySelectorAll("tbody tr")).toHaveLength(2);
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("");
  });

  it("tests, disables and deletes (after confirming) a server", async () => {
    let servers = [SERVER];
    serve((key) => {
      if (key === "POST /api/vpn/admin/servers/3/test") return [200, { ok: false, error: "agent unreachable", peers: 0 }];
      if (key === "PATCH /api/vpn/admin/servers/3") { servers = [{ ...SERVER, enabled: false }]; return [200, servers[0]]; }
      if (key === "DELETE /api/vpn/admin/servers/3") { servers = []; return [204, null]; }
      return [200, overview({ servers })];
    });
    render(<VpnAdminPage />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Test" }));
    await flush();
    expect(screen.getByText("agent unreachable")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Enable Auckland 1"));
    await flush();
    expect(calls).toContain('PATCH /api/vpn/admin/servers/3 {"enabled":false}');
    vi.stubGlobal("confirm", vi.fn(() => false));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await flush();
    expect(calls.some((c) => c.startsWith("DELETE"))).toBe(false);
    vi.stubGlobal("confirm", vi.fn(() => true));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await flush();
    expect(calls).toContain("DELETE /api/vpn/admin/servers/3");
    expect(screen.getByText(/No servers yet/)).toBeTruthy();
  });
});
