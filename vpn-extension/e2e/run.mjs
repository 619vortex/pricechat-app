// The real extension in a real browser through the real proxy, with a fake account API. Nothing outside this PC is contacted.
//   node vpn-extension/e2e/run.mjs <python with `cryptography`> <browser exe> <folder for screenshots>
import { spawn } from "node:child_process";
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const [python, browser, shots] = process.argv.slice(2);
const HERE = dirname(fileURLToPath(import.meta.url));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failed = 0;
const check = (name, ok, extra = "") => { console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : `  ${extra}`}`); if (!ok) failed++; };

// ----- the servers -----
const servers = spawn(python, [join(HERE, "servers.py")], { stdio: ["ignore", "pipe", "inherit"] });
const ports = await new Promise((res) => servers.stdout.once("data", (d) => res(JSON.parse(String(d).trim()))));
const API = `http://127.0.0.1:${ports.api}`;
const seen = async () => (await fetch(`${API}/__seen`)).json();

// ----- a copy of the extension pointing at the fake API -----
const work = mkdtempSync(join(tmpdir(), "kiwi-ext-"));
const ext = join(work, "ext");
cpSync(join(HERE, ".."), ext, { recursive: true, filter: (p) => !p.includes("e2e") && !p.endsWith(".test.js") });
writeFileSync(join(ext, "lib.js"), readFileSync(join(ext, "lib.js"), "utf8").replace('"https://pricechat.live/api/members/vpn"', `"${API}/api/members/vpn"`));
writeFileSync(join(ext, "manifest.json"), readFileSync(join(ext, "manifest.json"), "utf8").replace('"https://pricechat.live/*"', '"http://127.0.0.1/*"'));  // the fake API stands in for pricechat.live

// ----- the browser and a tiny DevTools client -----
const port = 9400 + Math.floor(Math.random() * 400);
const proc = spawn(browser, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${join(work, "profile")}`, `--load-extension=${ext}`, `--disable-extensions-except=${ext}`,
  "--disable-features=DisableLoadExtensionCommandLineSwitch", "--ignore-certificate-errors", "--no-first-run", "--disable-gpu", "--window-size=900,700", "about:blank"], { stdio: "ignore" });

async function targets() {
  for (let i = 0; i < 60; i++) {
    try { return await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); } catch { await sleep(250); }
  }
  throw new Error("the browser never opened its debugging port");
}

class Tab {
  static async open(url) {
    const t = await (await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(url)}`, { method: "PUT" })).json();
    const tab = new Tab();
    tab.id = t.id;
    await tab.connect(t.webSocketDebuggerUrl);
    await tab.send("Runtime.enable");
    await tab.send("Page.enable");
    return tab;
  }
  connect(url) {
    this.n = 0; this.wait = new Map();
    this.ws = new WebSocket(url);
    this.ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && this.wait.has(m.id)) { this.wait.get(m.id)(m); this.wait.delete(m.id); } };
    return new Promise((res) => { this.ws.onopen = res; });
  }
  send(method, params = {}) {
    const id = ++this.n;
    return new Promise((res) => { this.wait.set(id, res); this.ws.send(JSON.stringify({ id, method, params })); });
  }
  async eval(expression) {
    const r = await this.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    return r.result?.result?.value;
  }
  async go(url) {
    await this.send("Page.navigate", { url });
    await sleep(1500);
  }
  async text() { return (await this.eval("document.body ? document.body.innerText : ''")) ?? ""; }
  async shot(name) {
    if (!shots) return;
    const r = await this.send("Page.captureScreenshot", { format: "png" });
    writeFileSync(join(shots, name), Buffer.from(r.result.data, "base64"));
  }
  close() { this.ws.close(); return fetch(`http://127.0.0.1:${port}/json/close/${this.id}`); }
}

try {
  await targets();
  let worker = null;
  for (let i = 0; i < 40 && !worker; i++) {
    worker = (await targets()).find((t) => t.type === "service_worker" && t.url.includes("background.js"));
    if (!worker) await sleep(250);
  }
  if (!worker) throw new Error("the extension didn't load: this browser may refuse --load-extension");
  const id = new URL(worker.url).host;
  const manifest = JSON.parse(readFileSync(join(HERE, "..", "manifest.json"), "utf8"));
  check("the extension loads as a Manifest V3 service worker", !!id);
  console.log(`      extension id ${id}`);

  const popup = await Tab.open(`chrome-extension://${id}/popup.html`);
  await popup.send("Emulation.setDeviceMetricsOverride", { width: 320, height: 560, deviceScaleFactor: 2, mobile: false });
  const web = await Tab.open("about:blank");
  const reopen = async () => { await popup.go(`chrome-extension://${id}/popup.html`); };
  const poll = async (fn, ms = 20000) => { const end = Date.now() + ms; let v; while (Date.now() < end) { v = await fn(); if (v) return v; await sleep(300); } return v; };

  // signed out
  await reopen();
  check("signed out: asks to sign in", /Sign in to connect/.test(await popup.text()), await popup.text());
  await popup.shot("1-signed-out.png");

  // signing in on the site gives the extension the session
  await web.go(`${API}/__sign_in`);
  await reopen();
  const off = await popup.text();
  check("signed in on the website: the extension sees the account (the SameSite=Lax session cookie is sent)", /Not connected/.test(off) && /Helsinki 1/.test(off), off);
  check("shows no price or payment in the popup", !/\$|USDT|pay/i.test(off), off);
  await popup.shot("2-off.png");

  // before connecting: the test sites don't exist on this machine's normal connection
  await web.go(`http://origin.test:${ports.http}/before`);
  check("before connecting, the test site can't be reached", !/reached/.test(await web.text()));

  // connect
  await popup.eval(`document.querySelector('.power').click()`);
  const on = await poll(async () => { const t = await popup.text(); return /Connected/.test(t) && !/Connecting/.test(t) ? t : null; });
  check("the switch connects and the proxy proves it with the exit IP", !!on && /203\.0\.113\.10/.test(on), on);
  await popup.shot("3-on.png");
  const api = (await seen()).api.find((a) => a.method === "POST" && a.path.endsWith("/browser"));
  check("the login request came from the extension's own origin", api?.origin === `chrome-extension://${id}`, JSON.stringify(api));
  check("that origin is the one the backend allows", readFileSync(join(HERE, "..", "..", "backend", "app", "config", "settings.py"), "utf8").includes(`chrome-extension://${id}`), id);
  check("the manifest's key gives a fixed id", typeof manifest.key === "string");

  // traffic goes through it
  await web.go(`http://origin.test:${ports.http}/plain-page`);
  check("a plain http site loads through the proxy", /reached \/plain-page/.test(await web.text()), await web.text());
  await web.go(`https://origin.test:${ports.https}/secure-page`);
  check("an https site loads through the proxy", /reached \/secure-page/.test(await web.text()), await web.text());
  const heads = (await seen()).origin.join("\n");
  check("the site never sees the proxy's login", !/proxy-authorization/i.test(heads) && !/e2e-secret/.test(heads) && !/e2euser/.test(heads));

  // a website asking for a password doesn't get ours
  await web.go(`http://origin.test:${ports.http}/needs-password`);
  await sleep(500);
  const asked = (await seen()).origin.filter((h) => h.includes("/needs-password"));
  check("a website's password request never gets the proxy login", asked.length >= 1 && !asked.some((h) => /authorization/i.test(h)), asked.join("|"));

  const state = await popup.eval(`chrome.storage.local.get().then(s => JSON.stringify(s))`);
  check("the extension keeps a login, for reconnecting", /e2euser/.test(state));
  const toPopup = await popup.eval(`chrome.runtime.sendMessage({type:'state'}).then(r => JSON.stringify(r))`);
  check("the popup's view of the state doesn't include the password", !/e2e-secret/.test(toPopup) && /"on":true/.test(toPopup), toPopup);

  const webrtc = await popup.eval(`new Promise(r => chrome.privacy.network.webRTCIPHandlingPolicy.get({}, d => r(d.value)))`);
  check("WebRTC is limited to the proxy while on", webrtc === "disable_non_proxied_udp", webrtc);

  // the login is revoked and no new one can be had: traffic stops instead of going around the VPN
  await fetch(`${API}/__refuse`);
  await fetch(`${API}/__revoke_all`);
  await sleep(2600); // the proxy's reload
  await web.go(`http://origin.test:${ports.http}/after-revoke`);
  await sleep(1000);
  const bad = await poll(async () => { await reopen(); const t = await popup.text(); return /Connection problem/.test(t) ? t : null; });
  check("revoked: the popup says so, with the reason", !!bad && /can't use Kiwi VPN/.test(bad), bad);
  await popup.shot("4-problem.png");
  await web.go(`http://origin.test:${ports.http}/after-revoke-2`);
  check("revoked: traffic is stopped, not sent around the VPN", !/reached/.test(await web.text()), await web.text());
  const cfgClosed = await popup.eval(`new Promise(r => chrome.proxy.settings.get({}, d => r(d.value.mode)))`);
  check("revoked: the proxy setting stays in place", cfgClosed === "fixed_servers", cfgClosed);

  // off always works, even now
  await popup.eval(`[...document.querySelectorAll('button')].find(b => b.getAttribute('aria-label') === 'Switch off').click()`);
  const clean = await poll(async () => { const t = await popup.text(); return /Not connected/.test(t) || /Not available/.test(t) ? t : null; });
  check("switching off always works", !!clean, clean);
  const mode = await popup.eval(`new Promise(r => chrome.proxy.settings.get({}, d => r(d.value.mode + '/' + d.levelOfControl)))`);
  check("off: the browser's proxy setting is back to normal", /^system|^direct/.test(mode ?? ""), mode);
  const webrtc2 = await popup.eval(`new Promise(r => chrome.privacy.network.webRTCIPHandlingPolicy.get({}, d => r(d.value)))`);
  check("off: WebRTC is back to normal", webrtc2 === "default", webrtc2);
  await web.go(`http://origin.test:${ports.http}/after-off`);
  check("off: traffic no longer goes through the proxy", !/reached/.test(await web.text()));
  await web.close(); await popup.close();
} catch (e) {
  check("the run finished", false, e.stack);
} finally {
  proc.kill(); servers.kill();
  await sleep(800);
  try { rmSync(work, { recursive: true, force: true, maxRetries: 3 }); } catch { /* the browser may still hold it */ }
}
console.log(failed ? `\n${failed} FAILED` : "\nall passed");
process.exit(failed ? 1 : 0);
