// node --test vpn-node/infra/   (no network: Hetzner's public price list from 2026-11-07 and a fake API)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { HCloud, loadToken } from "./hcloud.mjs";
import * as L from "./lib.mjs";

const FIX = JSON.parse(readFileSync(new URL("./fixture-hetzner-2026-11-07.json", import.meta.url)));
const CAT = JSON.parse(readFileSync(new URL("./catalog.json", import.meta.url)));
const live = (over = {}) => ({ pricing: FIX.pricing, serverTypes: FIX.serverTypes, servers: [], firewalls: [],
  sshKeys: [{ id: 7, name: "owner", fingerprint: CAT.sshKeyFingerprint }], ...over });

test("the catalog is valid, and US East + Singapore are buildable nodes", () => {
  assert.deepEqual(L.validateCatalog(CAT), []);
  assert.equal(CAT.nodes.ash1.managed, true);
  assert.equal(CAT.nodes.sin1.managed, true);
  assert.equal(CAT.nodes.hel1.managed, false); // shared with the owner's own tunnel: never rebuilt by this tool
  assert.match(L.validateCatalog({ ...CAT, agentAllow: [] })[0], /agent port is never public/);
});

test("prices come from Hetzner's list: server + IPv4, traffic over the included amount", () => {
  const ash = L.priceFor(FIX.pricing, "cpx11", "ash");
  assert.deepEqual(ash, { currency: "USD", server: 20.49, ipv4: 0.6, includedTB: 1, perTB: 1.2 });
  assert.equal(L.monthlyCost(ash, 0), 21.09);
  assert.equal(L.monthlyCost(ash, 3), 23.49);
  const sin = L.priceFor(FIX.pricing, "cpx02", "sin");
  assert.equal(sin.includedTB, 0.5);
  assert.equal(L.monthlyCost(sin, 0), 9.59);
  assert.equal(L.monthlyCost(sin, 2), 22.04); // Singapore traffic is the expensive part
  assert.equal(L.priceFor(FIX.pricing, "cpx11", "nowhere"), null);
});

test("only types that can be ordered there now are planned", () => {
  assert.equal(L.orderable(FIX.serverTypes, "cpx11", "ash").ok, true);
  assert.match(L.orderable(FIX.serverTypes, "cpx11", "sin").why, /sold out or retired|retired/);
  assert.equal(L.orderable(FIX.serverTypes, "cpx02", "sin").ok, true);
  assert.match(L.orderable(FIX.serverTypes, "cpx99", "sin").why, /no server type/);
});

test("the plan for a new node: its own firewall, agent port only from the backend, protection, no secrets", () => {
  const p = L.planFor("ash1", CAT, live());
  assert.deepEqual(p.problems, []);
  assert.equal(p.body.server_type, "cpx11");
  assert.equal(p.body.location, "ash");
  assert.deepEqual(p.body.ssh_keys, [7]);
  assert.deepEqual(p.body.labels, { kiwi: "node", node: "ash1" });
  const agent = p.firewallRules.find((r) => r.port === "8443");
  assert.deepEqual(agent.source_ips, CAT.agentAllow.map((ip) => `${ip}/32`));
  for (const r of p.firewallRules) if (r.port !== "8443") assert.ok(["22", "51820", "443", "80", undefined].includes(r.port), r.port);
  // cloud-init: keys only, and nothing secret (Hetzner keeps user data; it's readable on the metadata address)
  assert.match(p.body.user_data, /PasswordAuthentication no/);
  assert.match(p.body.user_data, /ssh_pwauth: false/);
  assert.doesNotMatch(p.body.user_data, /token|password=|PRIVATE KEY/i);
  assert.ok(Buffer.byteLength(p.body.user_data) < 32 * 1024);
  assert.equal(p.costs["0TB"], 21.09);
});

test("a plan says why it can't go ahead", () => {
  assert.match(L.planFor("hel1", CAT, live()).problems.join(), /doesn't build/);
  assert.match(L.planFor("ash1", CAT, live({ sshKeys: [] })).problems.join(), /SSH key/);
  assert.match(L.planFor("ash1", CAT, live({ servers: [{ name: "kiwi-ash1" }] })).problems.join(), /already exists/);
  const sinOnCpx11 = { ...CAT, nodes: { ...CAT.nodes, sin1: { ...CAT.nodes.sin1, serverType: "cpx11" } } };
  assert.match(L.planFor("sin1", sinOnCpx11, live()).problems.join(), /sold out or retired|retired/);
  assert.match(L.planFor("xyz1", CAT, live()).problems.join(), /no node/);
});

test("the approval phrase names the node, the monthly cost and this exact plan", async () => {
  const p = L.planFor("sin1", CAT, live());
  const phrase = L.approvalPhrase("create", "sin1", p.costs["0TB"], p.hash);
  assert.match(phrase, /^create sin1 9\.59 [0-9a-f]{8}$/);
  const other = L.planFor("sin1", { ...CAT, agentAllow: ["203.0.113.9"] }, live());
  assert.notEqual(other.hash, p.hash); // a different firewall is a different approval
  await assert.rejects(L.confirmApproval(phrase, { isTTY: false, ask: async () => phrase }), /owner at a terminal/);
  await assert.rejects(L.confirmApproval(phrase, { isTTY: true, ask: async () => "yes" }), /not approved/);
  assert.equal(await L.confirmApproval(phrase, { isTTY: true, ask: async () => phrase }), true);
});

test("the audit flags an open agent port, missing protection and unused paid IPs", () => {
  const fw = { id: 1, name: "wg", applied_to: [{ server: { id: 5 } }], rules: [{ direction: "in", port: "8443", source_ips: ["0.0.0.0/0", "::/0"] }] };
  const s = { id: 5, name: "ubuntu-2gb-hel1-1", status: "running", server_type: { name: "cpx12" }, location: { name: "hel1" }, created: "2026-11-03T00:00:00Z",
    public_net: { ipv4: { ip: "2.29.149.29" }, ipv6: { ip: "2a01:4f9:c015:4b9c::/64" }, firewalls: [{ id: 1 }] }, protection: { delete: false }, outgoing_traffic: 0 };
  const a = L.audit({ servers: [s], firewalls: [fw], primaryIps: [{ ip: "1.2.3.4", assignee_id: null }], pricing: FIX.pricing });
  assert.equal(a.rows[0].listMonthly, 14.09);
  assert.ok(a.warnings.some((w) => /8443 open/.test(w)));
  assert.ok(a.warnings.some((w) => /delete protection off/.test(w)));
  assert.ok(a.warnings.some((w) => /1\.2\.3\.4.*still billed/.test(w)));
});

test("the Hetzner token is never in an error, and comes from the user's settings when this process is older", async () => {
  const token = "t".repeat(64);
  assert.equal(loadToken({ HCLOUD_TOKEN: "x" }, "win32", () => ` ${token}\r\n`), token);
  assert.throws(() => loadToken({ HCLOUD_TOKEN: "x" }, "linux"), /64-character/);
  const calls = [];
  const h = new HCloud(token, async (url, init) => { calls.push({ url, init }); return { ok: false, status: 403, json: async () => ({ error: { code: "forbidden", message: "nope" } }) }; });
  const err = await h.call("POST", "/servers", { name: "x" }).catch((e) => e);
  assert.match(err.message, /403 forbidden/);
  assert.ok(!err.message.includes(token));
  assert.equal(calls[0].url, "https://api.hetzner.cloud/v1/servers");
  assert.equal(calls[0].init.headers.Authorization, `Bearer ${token}`);
});

test("lists follow every page", async () => {
  let n = 0;
  const h = new HCloud("t".repeat(64), async () => {
    n++;
    return { ok: true, status: 200, json: async () => ({ servers: [{ id: n }], meta: { pagination: { next_page: n < 3 ? n + 1 : null } } }) };
  });
  assert.deepEqual((await h.list("/servers", "servers")).map((s) => s.id), [1, 2, 3]);
});
