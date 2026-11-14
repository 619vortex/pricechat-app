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
const t0 = Date.now();
const check = (name, ok, extra = "") => { console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : `  ${extra}`}  [${((Date.now() - t0) / 1000).toFixed(0)} s]`); if (!ok) failed++; };

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
// Started by launch(), and again for the restart check with the same profile.
let port, proc, pipeBuf, pipeWait, pipeId;
const pipe = (method, params) => new Promise((r) => { const id = ++pipeId; pipeWait.set(id, r); proc.stdio[3].write(JSON.stringify({ id, method, params }) + "\0"); });
async function launch() {
  port = 9400 + Math.floor(Math.random() * 400);
  proc = spawn(browser, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${join(work, "profile")}`, `--load-extension=${ext}`, `--disable-extensions-except=${ext},${join(HERE, "conflict-ext")}`,
    "--disable-features=DisableLoadExtensionCommandLineSwitch", "--enable-unsafe-extension-debugging", "--remote-debugging-pipe", "--ignore-certificate-errors", "--no-first-run", "--disable-gpu", "--window-size=900,700", "about:blank"], { stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"] });
  // Chrome 137+ ignores --load-extension: load it over the DevTools pipe instead (Extensions.loadUnpacked)
  pipeBuf = ""; pipeWait = new Map(); pipeId = 0;
  proc.stdio[4].on("data", (d) => { pipeBuf += d; let i; while ((i = pipeBuf.indexOf("\0")) >= 0) { const m = JSON.parse(pipeBuf.slice(0, i)); pipeBuf = pipeBuf.slice(i + 1); if (m.id && pipeWait.has(m.id)) { pipeWait.get(m.id)(m); pipeWait.delete(m.id); } } });
  const r = await pipe("Extensions.loadUnpacked", { path: ext });
  console.log("      loadUnpacked:", JSON.stringify(r.result ?? r.error));
  return r;
}
async function quit() {
  const gone = new Promise((r) => { if (proc.exitCode != null) r(); else proc.once("exit", r); });
  await Promise.race([pipe("Browser.close", {}), sleep(5000)]);
  await Promise.race([gone, sleep(8000)]);
  if (proc.exitCode == null) { proc.kill(); await sleep(1000); }
}
let loaded = await launch();

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
    // a DevTools call that never answers fails that check instead of hanging the whole run
    return new Promise((res) => {
      const timer = setTimeout(() => { this.wait.delete(id); console.log(`      (no answer in 30 s: ${method} ${JSON.stringify(params).slice(0, 160)})`); res({ timeout: true }); }, 30000);
      this.wait.set(id, (m) => { clearTimeout(timer); res(m); });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
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
  async html() { return (await this.eval("document.body ? document.body.innerHTML : ''")) ?? ""; }
  async shot(name) {
    if (!shots) return;
    await this.send("Page.bringToFront"); // a background tab may never paint a frame to capture
    const r = await this.send("Page.captureScreenshot", { format: "png" });
    if (r.result?.data) writeFileSync(join(shots, name), Buffer.from(r.result.data, "base64"));
    else console.log(`      (no screenshot for ${name})`);
  }
  close() { this.ws.close(); return fetch(`http://127.0.0.1:${port}/json/close/${this.id}`); }
}

try {
  const findWorker = async () => {
    await targets();
    for (let i = 0; i < 40; i++) {
      const w = (await targets()).find((t) => t.type === "service_worker" && t.url.includes("background.js") && (!loaded.result || t.url.includes(loaded.result.id)));
      if (w) return w;
      await sleep(250);
    }
    throw new Error("the extension didn't load: this browser may refuse --load-extension");
  };
  const worker = await findWorker();
  const id = new URL(worker.url).host;
  const manifest = JSON.parse(readFileSync(join(HERE, "..", "manifest.json"), "utf8"));
  check("the extension loads as a Manifest V3 service worker", !!id);
  console.log(`      extension id ${id}`);

  const openPopup = async () => {
    const t = await Tab.open(`chrome-extension://${id}/popup.html`);
    await t.send("Emulation.setDeviceMetricsOverride", { width: 320, height: 560, deviceScaleFactor: 2, mobile: false });
    return t;
  };
  let popup = await openPopup();
  let web = await Tab.open("about:blank");
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
  check("the plan reads as the website's (Free trial, days left), never 'Free while we test'", /Free trial · 23 days left/.test(off) && !/while we test/i.test(off), off);
  const opts = JSON.parse(await popup.eval(`JSON.stringify([...document.querySelectorAll('select option')].map(o => o.textContent))`));
  check("only servers with a browser proxy can be chosen", opts.length === 2 && /Helsinki 1/.test(opts[1]), JSON.stringify(opts));
  const sel = await popup.eval(`document.querySelector('select').value`);
  check("Smart Connect is offered first and chosen by default", /Smart Connect/.test(opts[0]) && sel === "smart", JSON.stringify({ opts, sel }));
  check("a server that's only in the apps (Germany) is named, not silently missing", /Germany in the Kiwi VPN apps; not in the browser yet/.test(off), off);
  check("the toolbar and header use the Kiwi icon", (await popup.eval(`document.querySelector('.head img').getAttribute('src')`)) === "icons/48.png" && manifest.icons["128"] === "icons/128.png");
  await popup.shot("2-off.png");

  // before connecting: the test sites don't exist on this machine's normal connection
  await web.go(`http://origin.test:${ports.http}/before`);
  check("before connecting, the test site can't be reached", !/reached \//.test(await web.text()), (await web.text()).slice(0,160));

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
  const resolved = (await seen()).resolved;
  check("DNS goes through the VPN: the browser sent the name, and only the VPN server could resolve it", resolved.includes("origin.test"), JSON.stringify(resolved));
  await web.go(`http://origin6.test:${ports.http}/ipv6-page`);
  check("an IPv6-only site loads, and only through the VPN server", /reached \/ipv6-page/.test(await web.text()) && (await seen()).origin6 > 0 && (await seen()).resolved.includes("origin6.test"), (await web.text()).slice(0, 160));
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
  const session = () => popup.eval(`chrome.storage.session.get().then(s => JSON.stringify(s))`);
  check("the proxy password is never written to disk: only to session storage (memory)", !/e2e-secret/.test(state) && /e2e-secret/.test(await session()), state);

  // ----- a browser restart: the password is gone with it, so a fresh login is minted and traffic never goes direct -----
  const mints = async () => (await seen()).api.filter((a) => a.method === "POST" && a.path.endsWith("/browser")).length;
  const mintedBefore = await mints();
  await quit();
  loaded = await launch();
  const worker2 = await findWorker();
  check("after a restart: the same extension id", new URL(worker2.url).host === id, worker2.url);
  popup = await openPopup();
  web = await Tab.open("about:blank");
  const back = await poll(async () => {
    await web.go(`http://origin.test:${ports.http}/after-restart`);
    return /reached \/after-restart/.test(await web.text()) ? await web.text() : null;
  }, 30000);
  check("after a restart: still on, and sites load through the proxy", !!back, (await web.text()).slice(0, 160));
  check("after a restart: a fresh login was minted (the old password went with the browser)", (await mints()) > mintedBefore, String(await mints()));
  const onAgain = await poll(async () => { await reopen(); const t = await popup.text(); return /Connected/.test(t) ? t : null; });
  check("after a restart: the popup says Connected", !!onAgain, await popup.text());
  const after = await popup.eval(`chrome.storage.local.get().then(s => JSON.stringify(s))`);
  check("after a restart: still no password on disk", !/e2e-secret/.test(after) && /e2e-secret/.test(await session()), after);

  const webrtc = await popup.eval(`new Promise(r => chrome.privacy.network.webRTCIPHandlingPolicy.get({}, d => r(d.value)))`);
  check("WebRTC is limited to the proxy while on", webrtc === "disable_non_proxied_udp", webrtc);
  await web.go(`http://origin.test:${ports.http}/webrtc-page`);
  const candidates = () => web.eval(`new Promise(async (done) => {
    const pc = new RTCPeerConnection({ iceServers: [] }); const found = [];
    pc.onicecandidate = (e) => { if (e.candidate && e.candidate.candidate) found.push(e.candidate.candidate); else if (!e.candidate) { pc.close(); done(JSON.stringify(found)); } };
    pc.createDataChannel("leak-test"); await pc.setLocalDescription(await pc.createOffer()); setTimeout(() => { pc.close(); done(JSON.stringify(found)); }, 4000); })`).then(JSON.parse);
  const onCands = await candidates();
  check("WebRTC leak test: a web page gathers no address candidates while on", onCands.length === 0, JSON.stringify(onCands));

  // the login is revoked and no new one can be had: traffic stops instead of going around the VPN
  await fetch(`${API}/__refuse`);
  await fetch(`${API}/__revoke_all`);
  await sleep(2600); // the proxy's reload
  await web.go(`http://origin.test:${ports.http}/after-revoke`);
  await sleep(1000);
  // one page load after the revoke, then only the popup: the refused login must be noticed from that load alone
  const bad = await poll(async () => { await reopen(); const t = await popup.text(); return /Connection problem/.test(t) ? t : null; });
  check("revoked: the popup says so, with the reason", !!bad && /can't use Kiwi VPN/.test(bad), bad ?? JSON.stringify({ popup: (await popup.text()).slice(0, 200), page: (await web.text()).slice(0, 120),
    state: await popup.eval(`chrome.runtime.sendMessage({type:'state'}).then(r => JSON.stringify(r))`), api: (await seen()).api.slice(-4) }));
  await popup.shot("4-problem.png");
  await web.go(`http://origin.test:${ports.http}/after-revoke-2`);
  check("revoked: traffic is stopped, not sent around the VPN", !/reached \//.test(await web.text()), (await web.text()).slice(0,160));
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
  await web.go("about:blank");
  const offCands = await candidates();
  check("off: the same WebRTC test finds candidates again (so the 'on' result means something)", offCands.length > 0, JSON.stringify(offCands));
  await web.go(`http://origin.test:${ports.http}/after-off`);
  check("off: traffic no longer goes through the proxy", !/reached \//.test(await web.text()), (await web.text()).slice(0,160));

  // ----- another extension takes the WebRTC or proxy setting: Kiwi VPN must never claim to protect the browser -----
  await fetch(`${API}/__allow`); // logins can be had again
  const other = await pipe("Extensions.loadUnpacked", { path: join(HERE, "conflict-ext") });
  check("a second extension (stand-in for another proxy/VPN extension) loads", !!other.result?.id, JSON.stringify(other.error ?? other.result));
  const rival = await Tab.open(`chrome-extension://${other.result?.id}/page.html`);
  await sleep(500);
  console.log("      rival page:", JSON.stringify(await rival.eval(`JSON.stringify({ href: location.href, privacy: typeof chrome.privacy, proxy: typeof chrome.proxy })`)));
  const takeWebrtc = await rival.eval(`chrome.privacy.network.webRTCIPHandlingPolicy.set({ value: "default" }).then(() => chrome.privacy.network.webRTCIPHandlingPolicy.get({})).then(d => d.levelOfControl)`);
  const badgeText = () => popup.eval(`chrome.action.getBadgeText({})`);
  const level = (what) => popup.eval(`${what}.get({}).then(d => d.levelOfControl)`);
  check("the other extension now holds WebRTC", takeWebrtc === "controlled_by_this_extension" && (await level("chrome.privacy.network.webRTCIPHandlingPolicy")) === "controlled_by_other_extensions", takeWebrtc);
  await reopen();
  const warnOff = await popup.text();
  check("WebRTC taken, while off: the popup warns before connecting", /controls this browser's WebRTC setting/.test(warnOff), warnOff + " | " + (await popup.html()).slice(0, 600));
  await popup.eval(`document.querySelector('.power').click()`);
  const partly = await poll(async () => { const t = await popup.text(); return /Not fully protected/.test(t) && !/Connecting/.test(t) ? t : null; });
  check("WebRTC taken, connected: 'Not fully protected' with the reason, never 'Connected'", !!partly && !/Connected/.test(partly) && /WebRTC/.test(partly), partly ?? await popup.text());
  check("WebRTC taken: the toolbar badge shows a problem, not ON", (await badgeText()) === "!", await badgeText());
  await popup.shot("5-webrtc-taken.png");

  const takeProxy = await rival.eval(`chrome.proxy.settings.set({ value: { mode: "direct" }, scope: "regular" }).then(() => chrome.proxy.settings.get({})).then(d => d.levelOfControl)`);
  check("the other extension now holds the proxy", takeProxy === "controlled_by_this_extension" && (await level("chrome.proxy.settings")) === "controlled_by_other_extensions", takeProxy);
  const unprot = await poll(async () => { await reopen(); const t = await popup.text(); return /Not protected/.test(t) ? t : null; });
  check("proxy taken while on: 'Not protected' with the reason, never 'Connected'", !!unprot && !/Connected/.test(unprot) && /controls this browser's proxy settings/.test(unprot), unprot ?? await popup.text());
  check("proxy taken while on: the badge shows a problem", (await badgeText()) === "!", await badgeText());
  await popup.shot("6-proxy-taken.png");

  await rival.eval(`Promise.all([chrome.proxy.settings.clear({ scope: "regular" }), chrome.privacy.network.webRTCIPHandlingPolicy.clear({})]).then(() => true)`);
  const regained = await poll(async () => { await reopen(); const t = await popup.text(); return /Connected/.test(t) && !/Connection problem/.test(t) ? t : null; });
  check("when the other extension lets go, Kiwi VPN's settings are in force again and it says Connected", !!regained && (await level("chrome.proxy.settings")) === "controlled_by_this_extension" && (await badgeText()) === "ON", regained ?? await popup.text());

  await popup.eval(`[...document.querySelectorAll('button')].find(b => b.getAttribute('aria-label') === 'Switch off').click()`);
  await poll(async () => /Not connected/.test(await popup.text()));
  await rival.eval(`chrome.proxy.settings.set({ value: { mode: "direct" }, scope: "regular" }).then(() => true)`);
  await reopen();
  await popup.eval(`document.querySelector('.power').click()`);
  const refused = await poll(async () => { const t = await popup.text(); return !/Connecting/.test(t) && /controls this browser's proxy settings/.test(t) && /Not connected/.test(t) ? t : null; });
  const stillOff = await popup.eval(`chrome.storage.local.get('on').then(s => s.on)`);
  check("proxy taken while off: connecting is refused with the reason, and it stays off", !!refused && stillOff === false && !/Connected/.test(refused), refused ?? await popup.text());
  await rival.eval(`chrome.proxy.settings.clear({ scope: "regular" }).then(() => true)`);
  await rival.close();
  await web.close(); await popup.close();
} catch (e) {
  check("the run finished", false, e.stack);
} finally {
  proc?.kill(); servers.kill();
  await sleep(800);
  try { rmSync(work, { recursive: true, force: true, maxRetries: 3 }); } catch { /* the browser may still hold it */ }
}
console.log(failed ? `\n${failed} FAILED` : "\nall passed");
process.exit(failed ? 1 : 0);
