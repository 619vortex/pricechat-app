// node --test vpn-node/infra/   (no network)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import * as R from "./regions-lib.mjs";
import { main, parseArgs } from "./regions.mjs";
import { evaluate, median, parseArgs as verifyArgs } from "./region-verify.mjs";

const CAT = JSON.parse(readFileSync(new URL("./regions.json", import.meta.url)));

test("regions.json is valid, covers the nine requested countries and holds no IP address", () => {
  assert.deepEqual(R.validateRegions(CAT), []);
  const countries = new Set(Object.values(CAT.regions).map((r) => r.country));
  for (const c of ["GB", "CY", "IL", "AE", "NL", "SG", "JP", "CA", "AU"]) assert.ok(countries.has(c), c);
  assert.doesNotMatch(JSON.stringify(CAT), /\b(\d{1,3}\.){3}\d{1,3}\b/);
});

test("validation catches missing prices, sources, bad keys and IPs", () => {
  const bad = { asOf: "2026-11-10", currency: "USD", regions: { London: { country: "GB", recommendation: "go", risk: "low", provider: "x", plan: "p", location: "l", monthly: 5, includedTB: 1, perTB: 10, ipv4: 0, wave: 1, sources: ["http://x"], note: "203.0.113.1" } } };
  const p = R.validateRegions(bad).join("\n");
  assert.match(p, /keys look like lon1/);
  assert.match(p, /https URL/);
  assert.match(p, /no IP addresses/);
  assert.match(R.validateRegions({ ...bad, currency: "EUR", regions: {} }).join(), /USD/);
});

test("monthly cost: plan + IPv4 + traffic over the allowance; avoid has no cost", () => {
  assert.equal(R.regionCost(CAT.regions.lon1, 1), 5);
  assert.equal(R.regionCost(CAT.regions.lon1, 3), 25);
  assert.equal(R.regionCost(CAT.regions.lca1, 3), 29.02);
  assert.equal(R.regionCost(CAT.regions.dxb1, 1), null);
  assert.equal(R.existingCost(CAT, 0), 40.77);
});

test("selection: default = wave 1 go; --wave N includes earlier waves; --all skips avoid; keys exactly", () => {
  const keys = (sel) => sel.map(([k]) => k).sort();
  assert.deepEqual(keys(R.selectRegions(CAT)), ["ams1", "lon1", "sgp1"]);
  assert.deepEqual(keys(R.selectRegions(CAT, { wave: 2 })), ["ams1", "lon1", "nrt1", "sgp1", "syd1", "tor1"]);
  assert.ok(!keys(R.selectRegions(CAT, { all: true })).includes("dxb1"));
  assert.deepEqual(keys(R.selectRegions(CAT, { keys: ["tlv1"] })), ["tlv1"]);
  assert.throws(() => R.selectRegions(CAT, { keys: ["xxx9"] }), /not in regions.json/);
});

test("plan totals for the first wave and every step names the tests", () => {
  const plan = R.planRegions(CAT, R.selectRegions(CAT), { trafficTB: [1, 3] });
  assert.deepEqual(plan.totals, { "1TB": 15, "3TB": 75 });
  for (const r of plan.rows) {
    assert.ok(r.steps.some((s) => s.includes("region-verify.mjs")));
    assert.ok(r.steps.some((s) => s.includes("Testers only")));
    assert.ok(r.steps[0].startsWith("order by hand")); // Vultr isn't automated
  }
  assert.match(plan.hash, /^[0-9a-f]{64}$/);
});

test("--apply never orders: avoid/wait/manual providers are refused, Hetzner goes to kiwi-infra's typed approval", () => {
  assert.equal(R.applyDecision("dxb1", CAT.regions.dxb1).ok, false);
  assert.match(R.applyDecision("tlv1", CAT.regions.tlv1).why, /wait/);
  const v = R.applyDecision("lon1", CAT.regions.lon1);
  assert.equal(v.ok, false); assert.equal(v.manual, true);
  const h = R.applyDecision("sin1", { ...CAT.regions.sgp1, provider: "hetzner" });
  assert.deepEqual(h.command, ["node", "vpn-node/infra/kiwi-infra.mjs", "create", "sin1"]);
});

test("CLI: dry run by default prints costs and runs nothing; --apply on a manual provider exits 2 without running anything", () => {
  const out = []; let ran = 0;
  const run = () => { ran++; return { status: 0 }; };
  assert.equal(main(["plan"], { cat: CAT, say: (s) => out.push(s), run }), 0);
  assert.match(out.join("\n"), /DRY RUN/);
  assert.match(out.join("\n"), /\$15\.00 at 1 TB each/);
  assert.equal(main(["plan", "lon1", "--apply"], { cat: CAT, say: (s) => out.push(s), run }), 2);
  assert.equal(ran, 0);
  const hz = { ...CAT, regions: { ...CAT.regions, sin1: { ...CAT.regions.sgp1, provider: "hetzner", host: "sin1.vpn.pricechat.live" } } };
  assert.equal(main(["plan", "sin1", "--apply"], { cat: hz, say: () => {}, run }), 0);
  assert.equal(ran, 1); // handed to kiwi-infra.mjs, which asks the owner for the phrase itself
  assert.throws(() => parseArgs(["plan", "--wave", "0"]), /--wave/);
  assert.throws(() => main(["plan", "lon1", "ams1", "--apply"], { cat: CAT, say: () => {}, run }), /exactly one/);
});

// ---------- region-verify ----------

const OK = { hostA: ["192.0.2.10"], hostAAAA: ["2001:db8:1:2::1"], exit4: ["192.0.2.10", "192.0.2.10"], geo: ["GB", "gb"],
  resolverIPs: ["192.0.2.10", "2001:db8:1:2::53"], v6: { ok: false, ms: 120 },
  sites: [{ url: "https://a.example/", ok: true, ms: 50 }, { url: "https://b.example/", ok: true, ms: 70 }] };
const level = (checks, id) => checks.find((c) => c.id === id).level;

test("verify: a good region passes every check", () => {
  const c = evaluate(OK, { country: "GB" });
  assert.deepEqual(c.map((x) => x.level), ["PASS", "PASS", "PASS", "PASS", "PASS"]);
});

test("verify: wrong exit, DNS leak and IPv6 leak fail; geo mismatch and slow v6 refusal only warn", () => {
  assert.equal(level(evaluate({ ...OK, exit4: ["198.51.100.7", "198.51.100.7"] }, { country: "GB" }), "exit-ip"), "FAIL");
  assert.equal(level(evaluate({ ...OK, exit4: ["192.0.2.10", "198.51.100.7"] }, { country: "GB" }), "exit-ip"), "FAIL");
  assert.equal(level(evaluate({ ...OK, resolverIPs: ["192.0.2.10", "198.51.100.53"] }, { country: "GB" }), "dns"), "FAIL");
  assert.equal(level(evaluate({ ...OK, v6: { ok: true, ip: "2001:db8:ffff::5", ms: 80 } }, { country: "GB" }), "ipv6"), "FAIL");
  assert.equal(level(evaluate({ ...OK, v6: { ok: true, ip: "2001:db8:1:2::77", ms: 80 } }, { country: "GB" }), "ipv6"), "PASS");
  assert.equal(level(evaluate({ ...OK, v6: { ok: false, ms: 9000 } }, { country: "GB" }), "ipv6"), "WARN");
  assert.equal(level(evaluate({ ...OK, geo: ["NL", "GB"] }, { country: "GB" }), "country"), "WARN");
  assert.equal(level(evaluate({ ...OK, sites: [{ url: "https://a.example/", ok: false, ms: 1 }] }, { country: "GB" }), "connect"), "FAIL");
  assert.equal(level(evaluate({ ...OK, hostA: [] }, { country: "GB" }), "exit-ip"), "FAIL");
});

test("verify: arguments only accept region names and country codes", () => {
  assert.deepEqual(verifyArgs(["--host", "lon1.vpn.pricechat.live", "--country", "gb"]), { host: "lon1.vpn.pricechat.live", country: "GB", json: false });
  assert.throws(() => verifyArgs(["--host", "example.com", "--country", "GB"]), /--host/);
  assert.equal(median([30, 10, 20]), 20);
});
