// Design preview only (not in the app build): the real interface with a stand-in for the app's Rust side.
// ?screen=home|connected|settings|tools
const screen = new URLSearchParams(location.search).get("screen") ?? "home";
const connected = screen === "connected" || screen === "tools";
const servers = [
  { id: 1, name: "Helsinki 1", region: "Finland", country_code: "FI", online: true, recommended: true, live_load: 0.1, proxy_host: "hel1.vpn.pricechat.live" },
  { id: 2, name: "Germany", region: "Falkenstein, Germany", country_code: "DE", online: true, recommended: false, live_load: 0.05, proxy_host: "fsn1.vpn.pricechat.live" },
  { id: 3, name: "United States (Ashburn)", region: "US East", country_code: "US", online: true, recommended: false, live_load: 0.9, proxy_host: "ash1.vpn.pricechat.live" },
];
let prefs = { launch_at_startup: true, auto_connect: true, minimize_to_tray: true, notifications: true, smart_connect: true, failover: true, kill_switch: true, allow_lan: true, block_malware: false, split_exclude: ["192.168.1.0/24"] };
const t0 = Date.now() / 1000 - 3725;
const answers: Record<string, unknown> = {
  account_state: { signed_in: true, account: { enabled: true, eligible: true, reason: null, max_devices: 3, servers, devices: [], plan: { kind: "trial", days_left: 23 }, trial: null }, suggested: 1, last_server: connected ? 2 : null, error: null },
  vpn_status: { service: true, message: null, status: connected ? { state: "connected", handshake_age_seconds: 12, rx_bytes: 734_003_200, tx_bytes: 52_428_800, error: null, connected_since: t0 } : { state: "disconnected", handshake_age_seconds: null, rx_bytes: 0, tx_bytes: 0, error: null, connected_since: null } },
  has_test_config: false, should_auto_connect: false, my_ip: connected ? "2.31.133.78" : "5.30.213.225",
  probe_servers: { 1: 62, 2: 48, 3: 131 },
  diagnostics: { version: "2.0.0-dev", service: "Running", tunnel: connected ? "Connected" : "Off", handshake_age_seconds: connected ? 12 : null, settings_file: "settings.json", features: ["kill_switch"] },
  speed_test: { ping_ms: 47, down_bytes: 25_000_000, down_ms: 2300, up_bytes: 5_000_000, up_ms: 2600, via: "speed.cloudflare.com" },
  leak_check: { ipv4: "2.31.133.78", ipv6: null, dns: ["2.31.133.78"], tunnel_ip: "2.31.133.78", expected_dns: ["2.31.133.78"] },
};
(window as unknown as { __TAURI_INTERNALS__: unknown }).__TAURI_INTERNALS__ = {
  invoke: async (cmd: string, args: { new?: typeof prefs }) => {
    if (cmd === "get_settings") return prefs;
    if (cmd === "set_settings") { prefs = args.new!; return prefs; }
    return answers[cmd] ?? null;
  },
  transformCallback: () => 0,
};
const { createRoot } = await import("react-dom/client");
const { App } = await import("./App");
await import("@fontsource-variable/archivo");
await import("./app.css");
const root = document.getElementById("root")!;
Object.assign(root.style, { width: "440px", height: "720px" }); // the app's real window size
createRoot(root).render(<App />);
const click = (label: string) => setTimeout(() => [...document.querySelectorAll("button")].find((b) => b.textContent === label)?.click(), 400);
if (screen === "settings") click("Settings");
if (screen === "tools") { click("Tools"); setTimeout(() => [...document.querySelectorAll("button")].find((b) => b.textContent === "Run speed test")?.click(), 800); setTimeout(() => [...document.querySelectorAll("button")].find((b) => b.textContent === "Check for leaks")?.click(), 1100); }
