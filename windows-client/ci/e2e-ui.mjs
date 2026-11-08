// Kiwi VPN 2.0 window tests on a GitHub Actions Windows runner: the installed KiwiVPN.exe, driven through WebView2's
// remote debugging (Chrome DevTools Protocol), against the real service and the real servers.
// Smart Connect, automatic failover (the server is cut off with a firewall rule ON THE RUNNER), the Tools (speed test,
// leak check), disconnect and reconnect. Prints PASS / FAIL / WARN lines and writes a JSON report; exits 1 on a FAIL.
//   node ci/e2e-ui.mjs <report.json>
import { spawn, execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";

const APP = "C:\\Program Files\\KiwiVPN\\KiwiVPN.exe";
const PORT = 9222;
const RULE = "KiwiCI-failover-block";
const checks = [];
const add = (name, result, detail = "") => { console.log(`${result.padEnd(4)} ${name}: ${detail}`); checks.push({ name, result, detail }); };
const check = (name, ok, detail) => { add(name, ok ? "PASS" : "FAIL", detail); return ok; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ps = (cmd) => execFileSync("powershell.exe", ["-NoProfile", "-Command", cmd], { encoding: "utf8" }).trim();

async function publicIp() {
  try { const r = await fetch("https://api.ipify.org", { signal: AbortSignal.timeout(8000) }); return (await r.text()).trim(); } catch { return null; }
}

// ---- a minimal CDP client
let ws, nextId = 1;
const pending = new Map();
async function attach() {
  for (let i = 0; i < 60; i++) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
      const page = targets.find((t) => t.type === "page");
      if (page) {
        ws = new WebSocket(page.webSocketDebuggerUrl);
        await new Promise((ok, bad) => { ws.onopen = ok; ws.onerror = bad; });
        ws.onmessage = (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } };
        return page.url;
      }
    } catch { /* not up yet */ }
    await sleep(1000);
  }
  throw new Error("the window never offered a debugging connection");
}
function send(method, params = {}) {
  const id = nextId++;
  return new Promise((ok) => { pending.set(id, ok); ws.send(JSON.stringify({ id, method, params })); });
}
async function js(expr) {
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
  return r.result?.result?.value;
}
const headline = () => js(`document.querySelector("h1")?.textContent ?? ""`);
const bodyText = () => js(`document.body.innerText`);
const clickText = (text, sel = "button") => js(`(() => { const b = [...document.querySelectorAll(${JSON.stringify(sel)})].find(x => x.textContent.includes(${JSON.stringify(text)}) && !x.disabled); if (b) b.click(); return !!b; })()`);
async function waitFor(fn, ms, every = 1000) {
  const end = Date.now() + ms;
  while (Date.now() < end) { const v = await fn(); if (v) return v; await sleep(every); }
  return null;
}

async function main() {
  const home = await publicIp();
  const app = spawn(APP, [], { env: { ...process.env, WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: `--remote-debugging-port=${PORT}` }, stdio: "ignore", detached: true });
  app.unref();
  let url;
  try { url = await attach(); } catch (e) {
    // what's running, and with which switches: the WebView2 processes' command lines show whether the debugging switch arrived
    let seen = "";
    try {
      seen = ps(`Get-CimInstance Win32_Process -Filter "Name='KiwiVPN.exe' or Name='msedgewebview2.exe'" | ForEach-Object { "$($_.ProcessId) $($_.Name) session $($_.SessionId): " + ([string]$_.CommandLine).Substring(0, [Math]::Min(400, ([string]$_.CommandLine).Length)) } | Select-Object -First 6 | Out-String -Width 600`);
      seen += "\nlistening: " + ps(`Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -ge 9000 -and $_.LocalPort -lt 10000 } | ForEach-Object { "$($_.LocalAddress):$($_.LocalPort) pid $($_.OwningProcess)" } | Out-String`);
      seen += "\nthis session: " + ps("[System.Diagnostics.Process]::GetCurrentProcess().SessionId") + ", interactive: " + ps("[Environment]::UserInteractive");
    } catch (d) { seen = `diagnostics failed: ${d}`; }
    check("window opens with a debugging connection", false, `${e}\n${seen}`);
    return;
  }
  check("window opens", true, url);

  const ready = await waitFor(async () => (await js(`document.querySelectorAll('[role=radiogroup] [role=radio]').length`)) > 1, 60_000);
  if (!check("signed in, servers listed", !!ready, (await bodyText())?.slice(0, 200).replace(/\s+/g, " "))) return;

  // Smart Connect
  const smart = await clickText("Fastest server", "[role=radio]");
  if (!check("Smart Connect offered", smart, "the Fastest server choice")) return;
  await sleep(500);
  await clickText("Connect", "button.go");
  const on = await waitFor(async () => (await headline()) === "Connected", 90_000);
  const picked = await js(`[...document.querySelectorAll('[role=radio]')].find(x => x.getAttribute('aria-checked') === 'true')?.textContent ?? ''`);
  const timings = await js(`[...document.querySelectorAll('[role=radio] .ms')].map(x => x.parentElement.textContent.trim()).join(' | ')`);
  const first = await publicIp();
  if (!check("Smart Connect connects", !!on && first && first !== home, `${picked}; public IPv4 ${first} (direct ${home}); timings: ${timings}`)) return;

  // Tools: speed test and leak check, from the window
  await clickText("Tools", "button.link");
  await sleep(500);
  await clickText("Run speed test");
  const speed = await waitFor(() => js(`document.querySelector('.tools .result')?.textContent ?? ''`), 120_000);
  check("Tools: speed test", !!speed, speed ?? "no result in 2 minutes");
  await clickText("Check for leaks");
  const leaks = await waitFor(() => js(`(() => { const l = document.querySelector('.tools .leaks'); return l ? (l.classList.contains('is-ok') ? 'OK: ' : 'LEAK: ') + l.innerText.replace(/\\s+/g, ' ') : ''; })()`), 60_000);
  check("Tools: leak check says no leak", !!leaks && leaks.startsWith("OK"), leaks ?? "no result");
  await clickText("Back", "button.link");
  await sleep(500);

  // Failover: cut this server off on the runner, wait for the window to move
  try {
    ps(`Set-NetFirewallProfile -All -Enabled True -DefaultOutboundAction Allow -DefaultInboundAction Allow; New-NetFirewallRule -DisplayName '${RULE}' -Direction Outbound -Protocol UDP -RemoteAddress ${first} -Action Block | Out-Null`);
    const t0 = Date.now();
    const dropped = await waitFor(async () => (await headline()) !== "Connected", 300_000, 2000);
    check("failover: the cut-off is noticed", !!dropped, dropped ? `after ${Math.round((Date.now() - t0) / 1000)} s (the tunnel counts as down once its handshake is 180 s old)` : "still Connected after 5 minutes");
    const moved = await waitFor(async () => /moved you to/.test(await bodyText()) && (await headline()) === "Connected", 300_000, 2000);
    const now = await publicIp();
    const msg = await js(`[...document.querySelectorAll('p[role=status]')].map(p => p.textContent).find(t => t.includes('moved you')) ?? ''`);
    check("failover: moved to another server", !!moved && now && now !== first && now !== home, `${msg || "no message"}; public IPv4 ${first} -> ${now} after ${Math.round((Date.now() - t0) / 1000)} s`);
  } finally {
    try { ps(`Remove-NetFirewallRule -DisplayName '${RULE}' -ErrorAction SilentlyContinue`); } catch { /* gone */ }
  }

  // Disconnect, then reconnect from the window
  await clickText("Disconnect", "button.go");
  const off = await waitFor(async () => (await headline()) === "Not connected", 60_000);
  const back = await publicIp();
  check("window: disconnect", !!off && back === home, `public IPv4 ${back} (direct ${home})`);
  await clickText("Connect", "button.go");
  const again = await waitFor(async () => (await headline()) === "Connected", 90_000);
  const ip2 = await publicIp();
  check("window: reconnect", !!again && ip2 && ip2 !== home, `public IPv4 ${ip2}`);
  await clickText("Disconnect", "button.go");
  await waitFor(async () => (await headline()) === "Not connected", 60_000);
}

try { await main(); } catch (e) { add("window tests", "FAIL", String(e?.stack ?? e)); }
try { ps(`Remove-NetFirewallRule -DisplayName '${RULE}' -ErrorAction SilentlyContinue`); } catch { /* none */ }
try { ps("Get-Process KiwiVPN -ErrorAction SilentlyContinue | Stop-Process -Force"); } catch { /* closed */ }
writeFileSync(process.argv[2] ?? "e2e-ui-report.json", JSON.stringify(checks, null, 2));
const failed = checks.filter((c) => c.result === "FAIL").length;
console.log(`\n${checks.length} window checks, ${failed} failed`);
process.exit(failed ? 1 : 0);
