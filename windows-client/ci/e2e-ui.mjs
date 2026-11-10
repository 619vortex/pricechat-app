// Kiwi VPN 2.0 window tests on a GitHub Actions Windows runner: the installed KiwiVPN.exe, driven through WebView2's
// remote debugging (Chrome DevTools Protocol), against the real service and the real servers.
// Smart Connect, automatic failover (the server is cut off with a firewall rule ON THE RUNNER), the Tools (speed test,
// leak check), disconnect and reconnect. Prints PASS / FAIL / WARN lines and writes a JSON report; exits 1 on a FAIL.
//   node ci/e2e-ui.mjs <report.json>
import { spawn, execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { get as httpsGet } from "node:https";

const APP = "C:\\Program Files\\KiwiVPN\\KiwiVPN.exe";
const PORT = 9222;
const RULE = "KiwiCI-failover-block";
const GUARD_CHECK = "tools/guard_check.exe"; // built with the installer; cuts a server off with a WFP block (Windows Firewall rules don't apply on the runner)
const POLICY = "HKCU:\\Software\\Policies\\Microsoft\\Edge\\WebView2\\AdditionalBrowserArguments";
const checks = [];
const add = (name, result, detail = "") => { console.log(`${result.padEnd(4)} ${name}: ${detail}`); checks.push({ name, result, detail }); };
const check = (name, ok, detail) => { add(name, ok ? "PASS" : "FAIL", detail); return ok; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ps = (cmd) => execFileSync("powershell.exe", ["-NoProfile", "-Command", cmd], { encoding: "utf8" }).trim();

/** The public address once traffic flows through the tunnel (the first seconds after a handshake can still be settling). */
async function vpnIp(home) {
  return (await waitFor(async () => { const ip = await publicIp(); return ip && ip !== home ? ip : null; }, 20_000, 1000)) ?? (await publicIp());
}

// A NEW connection (and DNS lookup) every time: fetch() keeps connections alive, and one opened before the tunnel came up still
// leaves through the normal network afterwards, so it kept answering with the runner's own address after a reconnect.
function publicIp() {
  return new Promise((resolve) => {
    const req = httpsGet("https://api.ipify.org", { agent: false, headers: { Connection: "close" }, timeout: 8000 }, (res) => {
      let body = "";
      res.on("data", (c) => (body += c));
      res.on("end", () => resolve(res.statusCode === 200 ? body.trim() : null));
      res.on("error", () => resolve(null));
    });
    req.on("timeout", () => req.destroy());
    req.on("error", () => resolve(null));
  });
}

// Holds a WFP block on `ip` (both ways) until stopped. Resolves to the child once the block is in place.
function cutOff(ip) {
  return new Promise((resolve, reject) => {
    const child = spawn(GUARD_CHECK, ["cut", ip, "900"], { stdio: ["ignore", "pipe", "pipe"] });
    let out = "", err = "";
    child.stdout.on("data", (c) => { out += c; if (out.includes("held")) resolve(child); });
    child.stderr.on("data", (c) => (err += c));
    child.on("exit", (code) => reject(new Error(`guard_check cut ended (${code}): ${err.trim()}`)));
    setTimeout(() => reject(new Error("guard_check cut didn't confirm in 15 s")), 15_000);
  });
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
  // The app sets WebView2's switches itself, which overrides the environment variable, so the debugging switch also goes in through
  // WebView2's per-app policy key. This runner's own registry only; removed at the end.
  ps(`Get-Process KiwiVPN -ErrorAction SilentlyContinue | Stop-Process -Force; New-Item -Force '${POLICY}' | Out-Null; Set-ItemProperty -Path '${POLICY}' -Name 'KiwiVPN.exe' -Value '--remote-debugging-port=${PORT}'`);
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
  const first = await vpnIp(home);
  if (!check("Smart Connect connects", !!on && first && first !== home, `${picked}; public IPv4 ${first} (direct ${home}); timings: ${timings}; window: ${(await bodyText())?.slice(0, 300).replace(/\s+/g, " ")}`)) return;

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
  let cut = null;
  try {
    cut = await cutOff(first);
    const tCut = Date.now();
    // the cut itself must work, or a failure below says nothing about the app: the tunnel carries nothing once its server is unreachable
    await sleep(5000);
    const through = await publicIp();
    if (!check("failover: test cut-off takes effect", through !== first, `public IPv4 seen through the cut tunnel: ${through ?? "none (cut)"}`)) throw new Error("cut-off had no effect");
    const dropped = await waitFor(async () => (await headline()) !== "Connected", 300_000, 1000);
    const noticedS = Math.round((Date.now() - tCut) / 1000);
    check("failover: the cut-off is noticed", !!dropped, dropped ? `${noticedS} s after the cut (2.1: liveness probes through the tunnel; before, the handshake had to be 180 s old)` : "still Connected after 5 minutes");
    const moved = await waitFor(async () => /moved you to/.test(await bodyText()) && (await headline()) === "Connected", 300_000, 1000);
    const movedS = Math.round((Date.now() - tCut) / 1000);
    const now = await vpnIp(home);
    const msg = await js(`[...document.querySelectorAll('p[role=status]')].map(p => p.textContent).find(t => t.includes('moved you')) ?? ''`);
    check("failover: moved to another server", !!moved && now && now !== first && now !== home, `${msg || "no message"}; public IPv4 ${first} -> ${now}; Connected again ${movedS} s after the cut, traffic through the new server ${Math.round((Date.now() - tCut) / 1000)} s after`);
    // the 2.1 target: noticed and recovered in under a minute
    if (moved) check("failover: back within 60 s of the cut", movedS <= 60, `noticed after ${noticedS} s, Connected to the next server after ${movedS} s`);
  } catch (e) {
    add("failover", "FAIL", String(e?.message ?? e));
  } finally {
    try { cut?.kill(); } catch { /* gone */ }
  }

  // Disconnect, then reconnect from the window
  await clickText("Disconnect", "button.go");
  const off = await waitFor(async () => (await headline()) === "Not connected", 60_000);
  const back = await publicIp();
  check("window: disconnect", !!off && back === home, `public IPv4 ${back} (direct ${home})`);
  await clickText("Connect", "button.go");
  const again = await waitFor(async () => (await headline()) === "Connected", 90_000);
  const ip2 = await vpnIp(home);
  check("window: reconnect", !!again && ip2 && ip2 !== home, `public IPv4 ${ip2}`);
  await clickText("Disconnect", "button.go");
  await waitFor(async () => (await headline()) === "Not connected", 60_000);
}

try { await main(); } catch (e) { add("window tests", "FAIL", String(e?.stack ?? e)); }
try { ps(`Remove-NetFirewallRule -DisplayName '${RULE}' -ErrorAction SilentlyContinue`); } catch { /* none */ }
try { ps("Get-Process KiwiVPN -ErrorAction SilentlyContinue | Stop-Process -Force"); } catch { /* closed */ }
try { ps(`Remove-Item -Recurse -Force '${POLICY}' -ErrorAction SilentlyContinue`); } catch { /* gone */ }
writeFileSync(process.argv[2] ?? "e2e-ui-report.json", JSON.stringify(checks, null, 2));
const failed = checks.filter((c) => c.result === "FAIL").length;
console.log(`\n${checks.length} window checks, ${failed} failed`);
process.exit(failed ? 1 : 0);
