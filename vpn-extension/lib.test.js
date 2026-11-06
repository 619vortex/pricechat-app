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
  // only the account API and the check the proxy answers: the proxy's password request reaches the extension without access to every site
  assert.deepEqual(m.host_permissions, ["https://pricechat.live/*", "http://check.kiwi-vpn.invalid/*"]);
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
