// node --test vpn-extension/lib.test.js
import assert from "node:assert/strict";
import { test } from "node:test";
import { BYPASS, badge, browserServers, bytes, flag, isOurChallenge, pickServer, proxyConfig, refusal } from "./lib.js";

const LOGIN = { username: "u", password: "p", host: "hel1.vpn.example", port: 443, server_id: 1 };
const S = (over) => ({ id: 1, online: true, recommended: false, browser: true, ...over });

test("the proxy setting is HTTPS only, and only the account API and local addresses skip it", () => {
  const c = proxyConfig(LOGIN);
  assert.equal(c.mode, "fixed_servers");
  assert.deepEqual(c.rules.singleProxy, { scheme: "https", host: "hel1.vpn.example", port: 443 });
  assert.deepEqual(c.rules.bypassList, BYPASS);
  assert.deepEqual(BYPASS, ["<local>", "pricechat.live"]);
});

test("the login only ever goes to our own proxy, never a website asking for a password", () => {
  const ours = { isProxy: true, challenger: { host: "hel1.vpn.example", port: 443 } };
  assert.equal(isOurChallenge(ours, LOGIN), true);
  assert.equal(isOurChallenge({ ...ours, isProxy: false }, LOGIN), false);
  assert.equal(isOurChallenge({ isProxy: true, challenger: { host: "evil.example", port: 443 } }, LOGIN), false);
  assert.equal(isOurChallenge({ isProxy: true, challenger: { host: "hel1.vpn.example", port: 8080 } }, LOGIN), false);
  assert.equal(isOurChallenge(ours, null), false);
});

test("says why an account can't connect", () => {
  assert.match(refusal({ enabled: false }), /isn't open yet/);
  assert.equal(refusal({ enabled: true, eligible: false, reason: "Add time." }), "Add time.");
  assert.match(refusal({ enabled: true, eligible: true, servers: [S({ browser: false })] }), /No servers are ready/);
  assert.equal(refusal({ enabled: true, eligible: true, servers: [S()] }), null);
});

test("only servers with a proxy are offered", () => {
  assert.deepEqual(browserServers({ servers: [S({ id: 1, browser: false }), S({ id: 2 })] }).map((s) => s.id), [2]);
  assert.deepEqual(browserServers({}), []);
});

test("picks the last server, else the recommended one, and skips offline ones", () => {
  const list = [S({ id: 1 }), S({ id: 2, recommended: true }), S({ id: 3, online: false })];
  assert.equal(pickServer(list, 1), 1);
  assert.equal(pickServer(list, 3), 2);
  assert.equal(pickServer(list, null), 2);
  assert.equal(pickServer([S({ id: 9, online: false })], null), 9);
  assert.equal(pickServer([], null), null);
});

test("the badge never says connected during a problem", () => {
  assert.equal(badge({ on: false }).text, "");
  assert.equal(badge({ on: true }).text, "ON");
  const bad = badge({ on: true, problem: "Connection lost." });
  assert.equal(bad.text, "!");
  assert.match(bad.title, /Connection lost/);
});

test("sizes and flags", () => {
  assert.equal(bytes(0), "0 MB");
  assert.equal(bytes(1536 * 1024 * 1024), "1.5 GB");
  assert.equal(flag("fi"), "🇫🇮");
  assert.equal(flag("nope"), "🌐");
});

test("the manifest asks for what it needs and no more", async () => {
  const { readFileSync } = await import("node:fs");
  const m = JSON.parse(readFileSync(new URL("./manifest.json", import.meta.url)));
  assert.equal(m.manifest_version, 3);
  assert.deepEqual([...m.permissions].sort(), ["privacy", "proxy", "storage", "webRequest", "webRequestAuthProvider"]);
  for (const bad of ["cookies", "history", "tabs", "scripting", "webRequestBlocking"]) assert.ok(!m.permissions.includes(bad));
  assert.equal(m.content_scripts, undefined);
  // Chrome only tells an extension about the proxy's password request (and a refused login) for sites it has access to.
  // With narrower access Chrome answers it itself and the popup can keep saying "Connected" after a login is revoked.
  assert.deepEqual(m.host_permissions, ["<all_urls>"]);
});

test("the plan reads like the website's, and never says it's a free test", async () => {
  const { planLabel } = await import("./lib.js");
  assert.equal(planLabel({ kind: "trial", days_left: 23 }), "Free trial · 23 days left");
  assert.equal(planLabel({ kind: "trial", days_left: 1 }), "Free trial · 1 day left");
  assert.equal(planLabel({ kind: "paid", days_left: 30 }), "30 days left");
  assert.equal(planLabel({ kind: "tester", days_left: 2 }), "Tester · 2 days left");
  assert.equal(planLabel({ kind: "royal" }), "Royal Kiwi");
  assert.equal(planLabel({ kind: "owner" }), "Owner");
  assert.equal(planLabel({ kind: "free" }), "Free access");
  assert.equal(planLabel({ kind: "granted" }), "Included");
  for (const kind of ["expired", "not_activated", "blocked", "closed"]) assert.equal(planLabel({ kind }), null);
  assert.equal(planLabel(undefined), null);
  for (const kind of ["trial", "paid", "tester", "royal", "owner", "free", "granted"]) assert.doesNotMatch(planLabel({ kind, days_left: 3 }), /while we test/i);
});

test("servers without a browser proxy are listed apart, never offered for the browser", async () => {
  const { appOnlyServers } = await import("./lib.js");
  const account = { servers: [S({ id: 1 }), S({ id: 2, name: "Germany", browser: false })] };
  assert.deepEqual(appOnlyServers(account).map((s) => s.id), [2]);
  assert.deepEqual(browserServers(account).map((s) => s.id), [1]);
  assert.deepEqual(appOnlyServers({}), []);
});

test("Smart Connect: the fastest online server, with a penalty for how busy it is now", async () => {
  const { median, smartScore, smartPick, BUSY_PENALTY_MS } = await import("./lib.js");
  assert.equal(median([30, 10, 20]), 20);
  assert.equal(median([10, 20, 30, 40]), 25);
  assert.equal(median([]), null);
  assert.equal(median([NaN, 50]), 50);
  const hel = { id: 1, online: true, live_load: 0 }, ash = { id: 3, online: true, live_load: 0 }, fsn = { id: 2, online: false };
  assert.equal(smartPick([hel, ash, fsn], { 1: 40, 3: 120, 2: 5 }), 1); // fastest wins; an offline server never does, however fast
  assert.equal(smartPick([{ ...hel, live_load: 1 }, ash], { 1: 40, 3: 120 }), 3); // 40 + 300 (full) loses to 120
  assert.equal(smartScore({ online: true, live_load: 0.5 }, 100), 100 + BUSY_PENALTY_MS / 2);
  assert.equal(smartScore({ online: true, load: 2 }, 10), 10 + BUSY_PENALTY_MS); // load capped at 1; older lists only have load
  assert.equal(smartScore({ online: true }, null), Infinity); // couldn't be measured
  assert.equal(smartPick([hel, ash], { 1: 50, 3: 50 }), 1); // a tie goes to the lower id: stable
});

test("Smart Connect never does worse than before when nothing can be measured", async () => {
  const { smartPick, pickServer } = await import("./lib.js");
  const servers = [{ id: 1, online: true }, { id: 3, online: true, recommended: true }];
  assert.equal(smartPick(servers, {}, null), pickServer(servers, null)); // the recommended one
  assert.equal(smartPick(servers, { 1: null, 3: null }, 1), 1); // the last one used
  assert.equal(smartPick([], {}, null), null);
});

test("never claims protection while another extension or policy holds the proxy or WebRTC setting", async () => {
  const { controlConflict, HELD, FREE } = await import("./lib.js");
  const OTHER = "controlled_by_other_extensions", POLICY = "not_controllable";
  // the normal cases: off and free to take both, on and holding both
  assert.equal(controlConflict({ on: false, proxy: FREE, webrtc: FREE }), null);
  assert.equal(controlConflict({ on: true, proxy: HELD, webrtc: HELD }), null);
  assert.equal(controlConflict({ on: false, proxy: HELD, webrtc: HELD }), null);
  // the proxy: nothing is protected, and it's named first
  for (const on of [false, true]) {
    assert.equal(controlConflict({ on, proxy: OTHER, webrtc: HELD }).kind, "proxy");
    assert.match(controlConflict({ on, proxy: OTHER, webrtc: OTHER }).text, /Another extension controls this browser's proxy settings/);
    assert.match(controlConflict({ on, proxy: POLICY, webrtc: FREE }).text, /managed by your organization/);
  }
  assert.equal(controlConflict({ on: true, proxy: FREE, webrtc: HELD }).kind, "proxy"); // on, but our setting isn't the one in force
  assert.equal(controlConflict({ on: true, proxy: null, webrtc: HELD }).kind, "proxy"); // couldn't be read: don't assume
  // WebRTC: traffic is proxied, but the real address may leak
  const w = controlConflict({ on: true, proxy: HELD, webrtc: OTHER });
  assert.equal(w.kind, "webrtc");
  assert.match(w.text, /WebRTC/);
  assert.equal(controlConflict({ on: false, proxy: FREE, webrtc: OTHER }).kind, "webrtc");
  assert.match(controlConflict({ on: true, proxy: HELD, webrtc: POLICY }).text, /managed by your organization/);
  assert.equal(controlConflict({ on: true, proxy: HELD, webrtc: FREE }).kind, "webrtc");
});

test("the badge never says ON while another extension holds a setting", () => {
  const b = badge({ on: true, conflict: { kind: "webrtc", text: "Another extension controls WebRTC." } });
  assert.equal(b.text, "!");
  assert.match(b.title, /not protected/);
  assert.equal(badge({ on: false, conflict: { kind: "proxy", text: "x" } }).text, ""); // off: nothing to warn about in the toolbar
});

test("the popup never says Connected during a conflict, and says what isn't covered", async () => {
  const { readFileSync } = await import("node:fs");
  const popup = readFileSync(new URL("./popup.js", import.meta.url), "utf8");
  assert.match(popup, /taken \? \(taken\.kind === "proxy" \? "Not protected" : "Not fully protected"\)/);
  assert.match(popup, /pricechat\.live always uses your normal connection/);
  assert.match(popup, /Incognito windows aren't covered/);
});

test("the proxy password is only ever kept in session storage", async () => {
  const { readFileSync } = await import("node:fs");
  const bg = readFileSync(new URL("./background.js", import.meta.url), "utf8");
  assert.match(bg, /chrome\.storage\.session\.set\(\{ secret: \{ username: login\.username, password \} \}\)/);
  assert.match(bg, /return write\(\{ login: rest, \.\.\.extra \}\)/); // local gets the login without its password
  assert.doesNotMatch(bg, /write\(\{ login(,| \})/); // never the whole login object into local storage
});
