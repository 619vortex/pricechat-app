// The popup only shows state and asks the service worker to act. It never sees the proxy password.
import { SIGN_IN_URL, browserServers, bytes, flag, pickServer, refusal } from "./lib.js";

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
  const billing = s?.account?.billing;
  return el("header", { class: "head" }, el("img", { src: "icons/48.png", alt: "" }), el("b", {}, "Kiwi VPN"),
    s?.account?.enabled && !billing ? el("span", { class: "pill" }, "Free while we test") : null);
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
  if (no && !s.on) return gate("Not available", no);

  const servers = browserServers(s.account);
  const chosen = s.on ? s.loginServer : pickServer(servers, s.lastServer);
  const picker = el("select", { "aria-label": "Server", disabled: s.on || busy || undefined },
    ...servers.map((x) => el("option", { value: String(x.id), selected: x.id === chosen || undefined, disabled: !x.online || undefined },
      `${flag(x.country_code)} ${x.name} · ${x.region}${x.online ? "" : " (offline)"}`)));
  const used = s.account.usage;
  return [
    statusBlock(s, () => Number(picker.value)),
    flash ? el("p", { class: "error", role: "alert" }, flash) : null,
    el("label", { class: "field" }, "Server", picker),
    el("div", { class: "stats" },
      el("span", {}, el("b", {}, bytes(used?.used_bytes ?? 0)), used?.cap_bytes ? ` of ${bytes(used.cap_bytes)} this month` : " this month"),
      s.exitIp && s.on ? el("span", {}, "IP ", el("b", {}, s.exitIp)) : null),
    el("p", { class: "note" }, "Covers this browser only, not other apps. Use the WireGuard app for your whole device."),
    el("button", { class: "link", onclick: () => { if (confirm("Switch off and remove this browser from your account?")) void act("forget"); } }, "Remove this browser"),
  ];
}

function statusBlock(s, server) {
  const bad = s.on && !!s.problem;
  const title = bad ? "Connection problem" : s.on ? "Connected" : "Not connected";
  const sub = bad ? s.problem : s.on ? "Your browser's traffic is going through Kiwi VPN." : "Your browser is using its normal connection.";
  const click = s.on ? () => act("disconnect") : () => act("connect", { serverId: server?.() });
  return el("section", { class: `switch${bad ? " is-bad" : s.on ? " is-on" : ""}` },
    powerButton(s.on ? "Switch off" : "Connect", click),
    el("div", { class: "state" }, busy ? (s.on ? "Switching off…" : "Connecting…") : title),
    el("p", { class: "sub" }, sub),
    bad ? el("button", { class: "button ghost", onclick: () => act("retry") }, "Try again") : null);
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
  app.replaceChildren(head(s), ...view(s).filter(Boolean));
}

void render();
chrome.storage.onChanged.addListener((_, area) => { if (area === "local" && !busy) void render(); });
