// The parts of the extension with no browser APIs in them, so they can be tested on their own.

export const API = "https://pricechat.live/api/members/vpn";
export const SIGN_IN_URL = "https://pricechat.live/join";
export const PRIVACY_URL = "https://pricechat.live/vpn/privacy";
export const VPN_URL = "https://pricechat.live/vpn";
export const CHECK_URL = "http://check.kiwi-vpn.invalid/"; // answered by the proxy itself: proves traffic really goes through it
// The account API stays reachable when the proxy is down, so the extension can say what's wrong and get a new login.
export const BYPASS = ["<local>", "pricechat.live"];

export function proxyConfig(login) {
  return {
    mode: "fixed_servers",
    rules: { singleProxy: { scheme: "https", host: login.host, port: login.port }, bypassList: BYPASS },
  };
}

/** Only ever answer our own proxy's password request, never a website's. */
export function isOurChallenge(details, login) {
  return !!login && details.isProxy === true && details.challenger?.host === login.host && details.challenger?.port === login.port;
}

/** Why this account can't connect, from its /members/vpn answer. null = it can. */
export function refusal(account) {
  if (!account.enabled) return "Kiwi VPN isn't open yet.";
  if (!account.eligible) return account.reason || "Your account can't use Kiwi VPN.";
  if (!browserServers(account).length) return "No servers are ready for the browser yet. Try again soon.";
  return null;
}

export function browserServers(account) {
  return (account.servers ?? []).filter((s) => s.browser);
}

/** Servers that exist for the apps but have no browser proxy yet: shown, so nobody wonders where they went, but never selectable. */
export function appOnlyServers(account) {
  return (account.servers ?? []).filter((s) => !s.browser);
}

const left = (n) => (n === 1 ? "1 day left" : `${n} days left`);

/** The plan in a few words, from the account's own `plan` (the same kinds the website shows). null = say nothing. */
export function planLabel(plan) {
  if (!plan) return null;
  const days = Number.isFinite(plan.days_left) ? plan.days_left : null;
  switch (plan.kind) {
    case "trial": return days != null ? `Free trial · ${left(days)}` : "Free trial";
    case "paid": return days != null ? left(days) : "Active";
    case "tester": return days != null ? `Tester · ${left(days)}` : "Tester";
    case "royal": return "Royal Kiwi";
    case "owner": return "Owner";
    case "free": return "Free access";
    case "granted": return "Included";
    default: return null;  // expired, not activated, blocked, closed: the gate explains those
  }
}

/** The server to preselect: the last one used if still there and online, else the recommended one, else any online one, else the first. */
export function pickServer(servers, lastId) {
  const online = servers.filter((s) => s.online);
  return (online.find((s) => s.id === lastId) ?? online.find((s) => s.recommended) ?? online[0] ?? servers[0])?.id ?? null;
}

export const SMART = "smart";
export const BUSY_PENALTY_MS = 300; // a server in full use counts as this much slower

export function median(xs) {
  const v = xs.filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}

/** Lower is better: measured time to the server plus a penalty for how busy it is now. Unmeasured = never chosen. */
export function smartScore(server, ms) {
  if (ms == null || !server.online) return Infinity;
  const busy = Math.min(1, Math.max(0, server.live_load ?? server.load ?? 0));
  return ms + busy * BUSY_PENALTY_MS;
}

/** Smart Connect's choice: the best score among measured online servers; when nothing could be measured (an older
 *  server list without proxy names, or every check failed) the usual preselection, so connecting never gets worse. */
export function smartPick(servers, timings, lastId) {
  let best = null, bestScore = Infinity;
  for (const s of servers) {
    const score = smartScore(s, timings[s.id]);
    if (score < bestScore || (score === bestScore && best && s.id < best.id)) { best = s; bestScore = score; }
  }
  return best ? best.id : pickServer(servers, lastId);
}

export function bytes(n) {
  if (!n || n < 0) return "0 MB";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  const v = n / 1024 ** i;
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${u[i]}`;
}

/** "US" -> flag emoji; anything else -> a globe. */
export function flag(cc) {
  const c = (cc || "").trim().toUpperCase();
  return /^[A-Z]{2}$/.test(c) ? String.fromCodePoint(...[...c].map((ch) => 0x1f1e6 + ch.charCodeAt(0) - 65)) : "🌐";
}

/** What the toolbar badge shows for a state: off (nothing), connected, or a problem while traffic is held back. */
export function badge(state) {
  if (!state.on) return { text: "", color: "#000000", title: "Kiwi VPN: off" };
  if (state.conflict) return { text: "!", color: "#d92d20", title: `Kiwi VPN: not protected. ${state.conflict.text}` };
  if (state.problem) return { text: "!", color: "#d92d20", title: `Kiwi VPN: ${state.problem}` };
  return { text: "ON", color: "#00a86b", title: "Kiwi VPN: connected" };
}

// ----- who really holds the browser's proxy and WebRTC settings -----
// Chrome gives a setting to one extension at a time (the most recently installed one that set it), or to the
// organization's policy. If someone else holds either, Kiwi VPN must not say the browser is protected.
export const HELD = "controlled_by_this_extension";
export const FREE = "controllable_by_this_extension";

const PROXY_TAKEN = "Another extension controls this browser's proxy settings, so Kiwi VPN can't protect it. Turn that extension off in chrome://extensions.";
const PROXY_POLICY = "This browser's proxy settings are managed by your organization, so Kiwi VPN can't protect it.";
const WEBRTC_TAKEN = "Another extension controls this browser's WebRTC setting, so websites may see your real IP address through WebRTC. Turn that extension off in chrome://extensions.";
const WEBRTC_POLICY = "This browser's WebRTC setting is managed by your organization, so websites may see your real IP address through WebRTC.";

/** One setting's levelOfControl -> is it a problem? While off we only need to be able to take it; while on we must hold it. */
function lost(level, on) {
  if (level === HELD) return false;
  if (level === FREE) return on; // on, but our value isn't the one in force
  return true; // controlled_by_other_extensions, not_controllable, or unreadable
}

/** What stops Kiwi VPN from protecting this browser, from the two settings' levelOfControl. null = nothing.
 *  kind "proxy": nothing is protected (connecting is refused). kind "webrtc": traffic is proxied, but WebRTC may leak. */
export function controlConflict({ on, proxy, webrtc }) {
  if (lost(proxy, on)) return { kind: "proxy", text: proxy === "not_controllable" ? PROXY_POLICY : PROXY_TAKEN };
  if (lost(webrtc, on)) return { kind: "webrtc", text: webrtc === "not_controllable" ? WEBRTC_POLICY : WEBRTC_TAKEN };
  return null;
}
