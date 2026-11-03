// Kiwi VPN's service worker: owns the proxy setting, answers the proxy's login request, and watches the connection.
// Fails closed: while switched on, a lost connection stops traffic and shows a red badge instead of quietly going around the VPN.
// Only "off" (always one click, needs no network) puts traffic back on the normal connection.
import { API, CHECK_URL, badge, isOurChallenge, proxyConfig, refusal } from "./lib.js";

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

const read = () => chrome.storage.local.get({ on: false, login: null, problem: null, lastServer: null, exitIp: null });
const write = (v) => chrome.storage.local.set(v);

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
  await write({ login, lastServer: serverId });
  return login;
}

async function connect(serverId) {
  const account = await api("");
  const no = refusal(account);
  if (no) throw new ApiError(403, no);
  const { login: old } = await read();
  let login = old?.server_id === serverId ? old : await newLogin(serverId, old?.username);
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
  await write({ on: true, problem: null, exitIp: ok.ip });
  await paint();
}

async function disconnect() {
  await clearProxy();
  await write({ on: false, problem: null, exitIp: null });
  await paint();
}

/** Off, and this browser's login removed from the account. */
async function forget() {
  const { login } = await read();
  await disconnect();
  await write({ login: null });
  if (login) await api(`/browser/${login.username}`, { method: "DELETE" }).catch(() => undefined);
}

/** While on: is traffic still getting through? If not, try for a new login once; if that fails, stay closed and show why. */
let rechecking = null;
function recheck() {
  rechecking ??= (async () => {
    const state = await read();
    if (!state.on) return;
    let ok = await check();
    let problem = "Connection lost. Traffic is stopped until it's back, or you switch off.";
    if (!ok && state.login) {
      try {
        const login = await newLogin(state.login.server_id, state.login.username);
        await applyProxy(login);
        ok = await check();
      } catch (e) {
        if (e.status === 401) problem = "You're signed out. Traffic is stopped until you sign in, or switch off.";
        else if (e.status === 403) problem = `${e.message} Traffic is stopped until you switch off.`;
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
chrome.webRequest.onErrorOccurred.addListener(settled, { urls: ["<all_urls>"] });

// One site failing also lands here, so this only triggers a check: the check decides whether the connection is really down.
chrome.proxy.onProxyError.addListener(() => { void recheck(); });

async function resume() {
  const { on, login } = await read();
  if (on && login) {
    await applyProxy(login); // closed from the very first request after a restart
    void recheck();
  }
  await paint();
}
chrome.runtime.onStartup.addListener(() => { void resume(); });
chrome.runtime.onInstalled.addListener(() => { void resume(); });

const ACTIONS = {
  state: async () => {
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
  retry: () => recheck(),
};

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  const run = sender.id === chrome.runtime.id ? ACTIONS[msg?.type] : null;
  if (!run) return false;
  run(msg).then((value) => reply({ ok: true, value: value ?? null }), (e) => reply({ ok: false, error: e.message, status: e.status ?? 0 }));
  return true; // answered asynchronously
});
