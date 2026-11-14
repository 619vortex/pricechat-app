// Kiwi VPN's service worker: owns the proxy setting, answers the proxy's login request, and watches the connection.
// Fails closed: while switched on, a lost connection stops traffic and shows a red badge instead of quietly going around the VPN.
// Only "off" (always one click, needs no network) puts traffic back on the normal connection.
import { API, CHECK_URL, SMART, badge, browserServers, controlConflict, isOurChallenge, median, proxyConfig, refusal, smartPick } from "./lib.js";

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function api(path, init = {}) {
  let r;
  try {
    r = await fetch(API + path, { ...init, credentials: "include", cache: "no-store", headers: { "Content-Type": "application/json", ...(init.headers ?? {}) } });
  } catch {
    throw new ApiError(0, "Can't reach Price Chat. Check your connection.");
  }
  if (r.status === 204) return null;
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, typeof body.detail === "string" ? body.detail : `Something went wrong (${r.status}).`);
  return body;
}

const write = (v) => chrome.storage.local.set(v);

// The proxy password lives only in chrome.storage.session: in memory, gone when the browser closes, never on disk.
// chrome.storage.local keeps the rest of the login (name, server), so after a restart traffic is held at once and a fresh
// login is asked for with the Price Chat session (the old one is replaced, not left behind).
async function read() {
  const [s, { secret }] = await Promise.all([
    chrome.storage.local.get({ on: false, login: null, problem: null, lastServer: null, exitIp: null, choice: SMART, smart: null, conflict: null }),
    chrome.storage.session.get({ secret: null }),
  ]);
  if (s.login?.password) { // saved by 1.0.4 or earlier: move the password off the disk
    const { password, ...rest } = s.login;
    await chrome.storage.session.set({ secret: { username: rest.username, password } });
    await write({ login: rest });
    return { ...s, login: s.login };
  }
  if (s.login) s.login = { ...s.login, password: secret?.username === s.login.username ? secret.password : null };
  return s;
}

async function saveLogin(login, extra = {}) {
  if (!login) {
    await chrome.storage.session.remove("secret");
    return write({ login: null, ...extra });
  }
  const { password, ...rest } = login;
  await chrome.storage.session.set({ secret: { username: login.username, password } });
  return write({ login: rest, ...extra });
}

const setting = (s) => s.get({}).then((d) => d?.levelOfControl ?? null, () => null);

/** Whether another extension (or policy) holds the proxy or WebRTC setting, saved so the badge and popup show it. */
async function conflictNow(on) {
  const [proxy, webrtc] = await Promise.all([setting(chrome.proxy.settings), setting(chrome.privacy.network.webRTCIPHandlingPolicy)]);
  return controlConflict({ on, proxy, webrtc });
}

async function refreshConflict() {
  const { on, conflict: was } = await chrome.storage.local.get({ on: false, conflict: null });
  const conflict = await conflictNow(on);
  if (JSON.stringify(conflict) !== JSON.stringify(was)) await write({ conflict });
  return conflict;
}

async function paint() {
  const b = badge(await read());
  await chrome.action.setBadgeText({ text: b.text });
  await chrome.action.setBadgeBackgroundColor({ color: b.color });
  await chrome.action.setTitle({ title: b.title });
}

async function applyProxy(login) {
  await chrome.proxy.settings.set({ value: proxyConfig(login), scope: "regular" });
  // WebRTC can reveal the real address outside a proxy: while on, it may only use the proxy.
  await chrome.privacy.network.webRTCIPHandlingPolicy.set({ value: "disable_non_proxied_udp" }).catch(() => undefined);
}

async function clearProxy() {
  await chrome.proxy.settings.clear({ scope: "regular" });
  await chrome.privacy.network.webRTCIPHandlingPolicy.clear({}).catch(() => undefined);
}

/** Through the tunnel to the proxy itself. The exit IP on success, null when traffic isn't getting through. */
async function check() {
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 12000);
    const r = await fetch(CHECK_URL, { cache: "no-store", credentials: "omit", signal: ctl.signal });
    clearTimeout(timer);
    if (!r.ok) return null;
    const body = await r.json();
    return body.ok === true ? { ip: body.ip ?? null } : null;
  } catch {
    return null;
  }
}

async function newLogin(serverId, replace) {
  const login = await api("/browser", { method: "POST", body: JSON.stringify({ server_id: serverId, replace: replace ?? null }) });
  await saveLogin(login, { lastServer: serverId });
  return login;
}

/** Time to reach one server's proxy: 3 HTTPS requests (the proxy answers a direct request with a quick 404 and no data),
 *  median in ms, or null if none answered. Only run while off, so the current proxy doesn't skew it. */
async function timeServer(server) {
  const out = [];
  for (let i = 0; i < 3; i++) {
    const t0 = performance.now();
    try {
      await fetch(`https://${server.proxy_host}/kiwi-ping?${Date.now()}${i}`, { cache: "no-store", credentials: "omit", redirect: "error", signal: AbortSignal.timeout(2500) });
      out.push(performance.now() - t0);
    } catch { /* no answer: no sample */ }
  }
  return median(out);
}

async function smartChoice(account) {
  const servers = browserServers(account).filter((s) => s.online);
  const { lastServer } = await read();
  const measurable = servers.filter((s) => s.proxy_host);
  const timings = Object.fromEntries(await Promise.all(measurable.map(async (s) => [s.id, await timeServer(s)])));
  const id = smartPick(servers, timings, lastServer);
  await write({ smart: { serverId: id, ms: timings[id] == null ? null : Math.round(timings[id]), at: Date.now() } });
  return id;
}

async function connect(serverId) {
  const account = await api("");
  const no = refusal(account);
  if (no) throw new ApiError(403, no);
  const before = await conflictNow(false);
  if (before?.kind === "proxy") { await write({ conflict: before }); throw new ApiError(409, before.text); }
  await write({ choice: serverId === SMART ? SMART : serverId }); // remembered for next time: Smart, or the server picked by hand
  if (serverId === SMART) {
    serverId = await smartChoice(account);
    if (serverId == null) throw new ApiError(503, "No server is online right now. Try again in a minute.");
  }
  const { login: old } = await read();
  let login = old?.server_id === serverId && old.password ? old : await newLogin(serverId, old?.username);
  await applyProxy(login);
  await write({ problem: null, lastServer: serverId });
  let ok = await check();
  if (!ok && login === old) { // a saved login can have been replaced or revoked: one fresh try
    login = await newLogin(serverId, old.username);
    await applyProxy(login);
    ok = await check();
  }
  if (!ok) { // nothing was protected yet and the person is looking at the popup: back to off, and say so
    await clearProxy();
    await write({ on: false, exitIp: null });
    await paint();
    throw new ApiError(503, "Couldn't connect to that server. Your browser is using its normal connection.");
  }
  const conflict = await conflictNow(true);
  if (conflict?.kind === "proxy") { // someone else's proxy setting is the one in force: nothing here is ours to protect
    await clearProxy();
    await write({ on: false, exitIp: null, conflict });
    await paint();
    throw new ApiError(409, conflict.text);
  }
  await write({ on: true, problem: null, exitIp: ok.ip, conflict }); // a WebRTC conflict stays on, shown as "not fully protected"
  await paint();
}

async function disconnect() {
  await clearProxy();
  await write({ on: false, problem: null, exitIp: null, conflict: await conflictNow(false) });
  await paint();
}

/** Off, and this browser's login removed from the account. */
async function forget() {
  const { login } = await read();
  await disconnect();
  await saveLogin(null);
  if (login) await api(`/browser/${login.username}`, { method: "DELETE" }).catch(() => undefined);
}

/** While on: is traffic still getting through? If not, try for a new login once; if that fails, stay closed and show why. */
let rechecking = null;
let mintFailedAt = 0; // a refused new login isn't asked for again on every blocked request: at most every 30 s, or on "Try again"
function recheck(manual = false) {
  rechecking ??= (async () => {
    const state = await read();
    if (!state.on) return;
    await refreshConflict();
    let ok = state.login?.password ? await check() : null; // no password = the browser was restarted: straight to a new login
    let problem = "Connection lost. Traffic is stopped until it's back, or you switch off.";
    if (!ok && state.login && !manual && Date.now() - mintFailedAt < 30000) {
      problem = state.problem ?? problem; // keep the reason the last attempt gave
    } else if (!ok && state.login) {
      try {
        const login = await newLogin(state.login.server_id, state.login.username);
        await applyProxy(login);
        ok = await check();
      } catch (e) {
        mintFailedAt = Date.now();
        if (e.status === 401) problem = "You're signed out. Traffic is stopped until you sign in, or switch off.";
        else if (e.status === 403) problem = `${e.message} Traffic is stopped until you switch off.`;
        else if (e.status === 429) problem = `${e.message} Traffic is stopped until then, or until you switch off.`;
      }
    }
    await write(ok ? { problem: null, exitIp: ok.ip } : { problem });
    await paint();
  })().finally(() => { rechecking = null; });
  return rechecking;
}

// ----- browser events: registered at the top level so they wake the worker -----

const asked = new Set(); // requests we've already answered: asked again means the login was refused
chrome.webRequest.onAuthRequired.addListener((details, reply) => {
  void read().then(({ login }) => {
    if (!isOurChallenge(details, login)) return reply({});
    if (!login.password) { // after a browser restart, until the fresh login arrives: held, not sent around the VPN
      reply({ cancel: true });
      void recheck();
      return;
    }
    if (asked.has(details.requestId)) {
      asked.delete(details.requestId);
      reply({ cancel: true });
      void recheck();
      return;
    }
    asked.add(details.requestId);
    reply({ authCredentials: { username: login.username, password: login.password } });
  });
}, { urls: ["<all_urls>"] }, ["asyncBlocking"]);

const settled = ({ requestId }) => { asked.delete(requestId); };
chrome.webRequest.onCompleted.addListener(settled, { urls: ["<all_urls>"] });
// Chrome sometimes fails a request whose proxy login was refused without asking us a second time. A request we gave the
// login to that then fails, or a proxy/tunnel error, triggers the check, so the popup never says "Connected" while traffic is held.
chrome.webRequest.onErrorOccurred.addListener(({ requestId, error }) => {
  const answered = asked.delete(requestId);
  if (answered || /PROXY|TUNNEL/.test(error || "")) void recheck();
}, { urls: ["<all_urls>"] });

// One site failing also lands here, so this only triggers a check: the check decides whether the connection is really down.
chrome.proxy.onProxyError.addListener(() => { void recheck(); });

// Another extension taking (or giving back) the proxy or WebRTC setting: the badge and popup must say so straight away.
const onControlChange = () => { void refreshConflict().then(paint); };
chrome.proxy.settings.onChange.addListener(onControlChange);
chrome.privacy.network.webRTCIPHandlingPolicy.onChange.addListener(onControlChange);

async function resume() {
  const { on, login } = await read();
  if (on && login) {
    await applyProxy(login); // closed from the very first request after a restart
    void recheck();
  }
  await refreshConflict();
  await paint();
}
chrome.runtime.onStartup.addListener(() => { void resume(); });
chrome.runtime.onInstalled.addListener(() => { void resume(); });

const ACTIONS = {
  state: async () => {
    await refreshConflict(); // always the live answer, not the last one seen
    const state = await read();
    let account = null, signedIn = true, error = null;
    try {
      account = await api("");
    } catch (e) {
      if (e.status === 401) signedIn = false;
      else error = e.message;
    }
    const { login, ...visible } = state; // the popup never needs the password
    return { ...visible, loginServer: login?.server_id ?? null, account, signedIn, error };
  },
  connect: ({ serverId }) => connect(serverId),
  disconnect,
  forget,
  retry: () => recheck(true),
};

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  const run = sender.id === chrome.runtime.id ? ACTIONS[msg?.type] : null;
  if (!run) return false;
  run(msg).then((value) => reply({ ok: true, value: value ?? null }), (e) => reply({ ok: false, error: e.message, status: e.status ?? 0 }));
  return true; // answered asynchronously
});
