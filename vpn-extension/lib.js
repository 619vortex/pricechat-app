// The parts of the extension with no browser APIs in them, so they can be tested on their own.

export const API = "https://pricechat.live/api/members/vpn";
export const SIGN_IN_URL = "https://pricechat.live/join";
export const PRIVACY_URL = "https://pricechat.live/vpn/privacy";
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

/** The server to preselect: the last one used if still there and online, else the recommended one, else any online one, else the first. */
export function pickServer(servers, lastId) {
  const online = servers.filter((s) => s.online);
  return (online.find((s) => s.id === lastId) ?? online.find((s) => s.recommended) ?? online[0] ?? servers[0])?.id ?? null;
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
  if (state.problem) return { text: "!", color: "#d92d20", title: `Kiwi VPN: ${state.problem}` };
  return { text: "ON", color: "#00a86b", title: "Kiwi VPN: connected" };
}
