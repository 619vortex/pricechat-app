// The popup only shows state and asks the service worker to act. It never sees the proxy password.
import { PRIVACY_URL, SIGN_IN_URL, SMART, VPN_URL, appOnlyServers, browserServers, bytes, flag, pickServer, planLabel, refusal } from "./lib.js";

const app = document.getElementById("app");
const POWER = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v9"/><path d="M6.3 7.3a8 8 0 1 0 11.4 0"/></svg>`;
let busy = false;
let flash = null;

async function ask(type, extra = {}) {
  const r = await chrome.runtime.sendMessage({ type, ...extra });
  if (!r?.ok) throw new Error(r?.error || "Something went wrong.");
  return r.value;
}

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "onclick" || k === "onchange") n[k] = v;
    else if (v === true) n.setAttribute(k, "");
    else if (v !== false && v != null) n.setAttribute(k, v);
  }
  n.append(...kids.filter((x) => x != null));
  return n;
}

const head = (s) => {
  const plan = planLabel(s?.account?.plan);
  return el("header", { class: "head" }, el("img", { src: "icons/48.png", alt: "" }), el("b", { class: "brand" }, "Kiwi VPN"),
    plan ? el("span", { class: "pill" }, plan) : null);
};

function gate(title, text, action) {
  return [el("section", { class: "gate" }, el("b", {}, title), text ? el("p", {}, text) : null, action ?? null)];
}

const offButton = () => el("button", { class: "button ghost", onclick: () => act("disconnect") }, "Switch off");

function view(s) {
  if (!s.signedIn) {
    if (s.on) return [el("p", { class: "error" }, "You're signed out, so the connection can't be renewed."), offButton(),
      el("a", { class: "button", href: SIGN_IN_URL, target: "_blank" }, "Sign in")];
    return gate("Sign in to connect", "Use your Kiwi Passport. It's free to join.", el("a", { class: "button", href: SIGN_IN_URL, target: "_blank", rel: "noopener" }, "Sign in"));
  }
  if (!s.account) {
    if (s.on) return [statusBlock(s), offButton()];
    return gate("Can't reach Price Chat", s.error, el("button", { class: "button ghost", onclick: () => render() }, "Try again"));
  }
  const no = refusal(s.account);
  if (no && !s.on) return gate("Not available", no, el("a", { class: "button", href: VPN_URL, target: "_blank", rel: "noopener" }, "Open Kiwi VPN page"));

  const servers = browserServers(s.account);
  const hand = s.choice !== SMART && servers.some((x) => x.id === s.choice && x.online) ? s.choice : null;
  const smartOn = s.on ? s.choice === SMART : hand == null;
  const chosen = s.on ? s.loginServer : hand ?? pickServer(servers, s.lastServer);
  const picker = el("select", { "aria-label": "Server", disabled: s.on || busy || undefined },
    el("option", { value: SMART, selected: smartOn || undefined }, "⚡ Smart Connect · the fastest server now"),
    ...servers.map((x) => el("option", { value: String(x.id), selected: (!smartOn && x.id === chosen) || undefined, disabled: !x.online || undefined },
      `${flag(x.country_code)} ${x.name} · ${x.region}${x.online ? "" : " (offline)"}`)));
  const smartNote = s.on && s.choice === SMART && s.smart?.serverId === s.loginServer
    ? el("p", { class: "note" }, `Smart Connect picked ${servers.find((x) => x.id === s.loginServer)?.name ?? "a server"}${s.smart.ms != null ? ` (${s.smart.ms} ms)` : ""}.`) : null;
  const used = s.account.usage;
  return [
    statusBlock(s, () => (picker.value === SMART ? SMART : Number(picker.value))),
    flash ? el("p", { class: "error", role: "alert" }, flash) : null,
    el("label", { class: "field" }, "Server", picker),
    smartNote,
    appOnly(s.account),
    el("div", { class: "stats" },
      el("span", {}, el("b", {}, bytes(used?.used_bytes ?? 0)), used?.cap_bytes ? ` of ${bytes(used.cap_bytes)} this month` : " this month"),
      s.exitIp && s.on && s.conflict?.kind !== "proxy" ? el("span", {}, "IP ", el("b", {}, s.exitIp)) : null),
    el("p", { class: "note" }, "Covers this browser only; the Kiwi VPN apps cover your whole device. What we keep: pricechat.live/vpn/privacy."),
    help(),
    el("button", { class: "link", onclick: () => { if (confirm("Switch off and remove this browser from your account?")) void act("forget"); } }, "Remove this browser"),
  ];
}

/** What it doesn't cover, said where people look, not only in the store listing. */
function help() {
  return el("details", { class: "help" }, el("summary", {}, "What isn't covered"),
    el("ul", {},
      el("li", {}, "pricechat.live always uses your normal connection, so the extension can still reach your account when the VPN is down. Price Chat sees your real IP address."),
      el("li", {}, "Incognito windows aren't covered unless you allow Kiwi VPN in incognito (chrome://extensions > Kiwi VPN > Details)."),
      el("li", {}, "Addresses on your own network (like your router) and other apps on this device aren't covered."),
      el("li", {}, "If another extension controls the proxy or WebRTC settings, Kiwi VPN says so and doesn't claim to protect you.")));
}

/** Servers the account has in the apps but not here yet (no browser proxy on them), so they don't look lost. */
function appOnly(account) {
  const names = appOnlyServers(account).map((x) => `${flag(x.country_code)} ${x.name}`);
  return names.length ? el("p", { class: "soon" }, el("b", {}, names.join(", ")), " in the Kiwi VPN apps; not in the browser yet.") : null;
}

function statusBlock(s, server) {
  // Another extension (or policy) holding the proxy or WebRTC setting: never "Connected", whatever else is true.
  const taken = s.on && s.conflict ? s.conflict : null;
  const bad = s.on && (!!s.problem || !!taken);
  const title = taken ? (taken.kind === "proxy" ? "Not protected" : "Not fully protected") : bad ? "Connection problem" : s.on ? "Connected" : "Not connected";
  const sub = taken ? taken.text : bad ? s.problem : s.on ? "Your browser's traffic is going through Kiwi VPN." : "Your browser is using its normal connection.";
  const click = s.on ? () => act("disconnect") : () => act("connect", { serverId: server?.() });
  return el("section", { class: `switch${bad ? " is-bad" : s.on ? " is-on" : ""}` },
    powerButton(s.on ? "Switch off" : "Connect", click),
    el("div", { class: "state" }, busy ? (s.on ? "Switching off…" : "Connecting…") : title),
    el("p", { class: "sub" }, sub),
    bad && !taken ? el("button", { class: "button ghost", onclick: () => act("retry") }, "Try again") : null,
    !s.on && s.conflict ? el("p", { class: "error", role: "alert" }, s.conflict.text) : null);
}

function powerButton(label, onclick) {
  const b = el("button", { class: "power", "aria-label": label, disabled: busy || undefined, onclick });
  b.innerHTML = POWER;
  return b;
}

async function act(type, extra) {
  busy = true; flash = null;
  await render();
  try { await ask(type, extra); }
  catch (e) { flash = e.message; }
  busy = false;
  await render();
}

async function render() {
  let s;
  try { s = await ask("state"); }
  catch (e) { app.replaceChildren(el("p", { class: "error" }, e.message)); return; }
  app.replaceChildren(head(s), ...view(s).filter(Boolean), el("a", { class: "link", href: PRIVACY_URL, target: "_blank", rel: "noopener" }, "Privacy policy"));
}

void render();
chrome.storage.onChanged.addListener((_, area) => { if (area === "local" && !busy) void render(); });
