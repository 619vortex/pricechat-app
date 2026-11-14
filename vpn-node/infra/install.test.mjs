// node --test vpn-node/infra/*.test.mjs   (no network, no SSH: every outside effect is a fake that records what it was asked)
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { runInstall } from "./kiwi-infra.mjs";
import * as L from "./lib.mjs";

const CAT = JSON.parse(readFileSync(new URL("./catalog.json", import.meta.url)));
const FRESH = "os=ubuntu 24.04\nuid=0\nwg=\nstatus=none\nkiwi_nft=no\nfw_rules=0\ndocker=no\nport53=0\n";
const BUNDLE = { rev: "abc1234", files: L.BUNDLE.map((name, i) => ({ name, body: Buffer.from(name), sha: String(i).repeat(64) })) };
const NODE = { external: true, host: "ext1.vpn.example.com", country: "NL", name: "Example" };
const IP = "192.0.2.77";
const KEY = "SHA256:abcdefABCDEF0123456789+/xyz";

function fakes({ facts = FRESH, dns = [IP], keys = [KEY], approveOk = true } = {}) {
  const log = [];
  return {
    log,
    deps: {
      key: "ext1", node: NODE, ip: IP, bundle: BUNDLE, cat: { ...CAT, certEmail: "ops@example.com" }, say: () => {},
      ssh: (ip, cmd, o = {}) => { log.push(["ssh", ip, cmd, o.input ? String(o.input) : undefined]); return cmd === L.PREFLIGHT ? facts : ""; },
      upload: (ip) => log.push(["upload", ip]),
      approve: async (phrase) => { log.push(["approve", phrase]); if (!approveOk) throw new Error("not approved: nothing was changed"); },
      resolve4: async () => dns,
      probe: async () => ({ ok: true, tlsMs: 5, certDays: 80 }),
      hostKeys: () => keys,
    },
  };
}

test("an external install checks read-only first, asks for approval, then installs with the backend allowlist and the cert email", async () => {
  const { log, deps } = fakes();
  await runInstall({ ...deps, external: true });
  assert.deepEqual(log.map((x) => x[0]), ["ssh", "approve", "upload", "ssh", "ssh"]);
  assert.equal(log[0][2], L.PREFLIGHT);
  assert.match(log[1][1], /^install ext1 0\.00 [0-9a-f]{8}$/);
  assert.match(log[3][2], /sha256sum -c/);
  const cmd = log[4][2];
  assert.match(cmd, new RegExp(`AGENT_ALLOW=${CAT.agentAllow.join(",").replace(/\./g, "\\.")} bash install\\.sh`));
  assert.match(cmd, /QUIET_SECRETS=1 WITH_PROXY=1/);
  assert.match(cmd, /PROXY_HOST=ext1\.vpn\.example\.com CERT_EMAIL=ops@example\.com bash install-proxy\.sh/);
});

test("the approval phrase is bound to the server's address and host key", async () => {
  const phrase = async (over) => { const { log, deps } = fakes(over.f); await runInstall({ ...deps, ...over.d, external: true }); return log.find((x) => x[0] === "approve")[1]; };
  const a = await phrase({ f: {}, d: {} });
  const otherIp = await phrase({ f: { dns: ["192.0.2.78"] }, d: { ip: "192.0.2.78" } });
  const otherKey = await phrase({ f: { keys: ["SHA256:different"] }, d: {} });
  assert.notEqual(a, otherIp);
  assert.notEqual(a, otherKey);
  // a Hetzner-built node keeps the phrase it always had (files only), so earlier written approvals still mean the same
  const { log, deps } = fakes();
  await runInstall({ ...deps, external: false });
  assert.equal(log.find((x) => x[0] === "approve")[1], L.approvalPhrase("install", "ext1", 0, L.sha256(BUNDLE.files.map((f) => f.sha).join())));
});

test("nothing changes on a server that isn't a fresh Ubuntu 24.04 the installer may own", async () => {
  for (const [facts, why] of [
    [FRESH.replace("wg=", "wg=wg0.conf"), /already has WireGuard \(wg0\.conf\).*install-shared\.sh/],
    [FRESH.replace("docker=no", "docker=yes"), /Docker/],
    [FRESH.replace("os=ubuntu 24.04", "os=debian 12"), /debian 12, not Ubuntu 24\.04/],
    [FRESH.replace("uid=0", "uid=1000"), /isn't root/],
    [FRESH.replace("fw_rules=0", "fw_rules=3"), /own nftables rules/],
    [FRESH.replace("port53=0", "port53=1"), /port 53/],
  ]) {
    const { log, deps } = fakes({ facts });
    await assert.rejects(runInstall({ ...deps, external: true }), why);
    assert.deepEqual(log.map((x) => x[0]), ["ssh"], String(why)); // only the read-only check ran
  }
});

test("a node this tool installed before may be reinstalled, with the reinstall phrase", async () => {
  const facts = FRESH.replace("status=none", "status=installed abc1234").replace("wg=", "wg=wg0.conf").replace("kiwi_nft=no", "kiwi_nft=yes").replace("fw_rules=0", "fw_rules=6");
  const { log, deps } = fakes({ facts });
  await runInstall({ ...deps, external: true });
  assert.match(log.find((x) => x[0] === "approve")[1], /^reinstall ext1 /);
});

test("a host key that isn't the expected one stops it before the approval", async () => {
  const { log, deps } = fakes();
  await assert.rejects(runInstall({ ...deps, external: true, expectHostKey: "SHA256:theRightOne" }), /host key isn't SHA256:theRightOne/);
  assert.deepEqual(log.map((x) => x[0]), ["ssh"]);
});

test("DNS that doesn't point at the server yet stops it before the approval", async () => {
  const { log, deps } = fakes({ dns: ["192.0.2.99"] });
  await assert.rejects(runInstall({ ...deps, external: true }), /doesn't point at 192\.0\.2\.77/);
  assert.ok(!log.some((x) => x[0] === "approve" || x[0] === "upload"));
});

test("no approval, no upload", async () => {
  const { log, deps } = fakes({ approveOk: false });
  await assert.rejects(runInstall({ ...deps, external: true }), /not approved/);
  assert.ok(!log.some((x) => x[0] === "upload"));
});

test("the pre-check only reads", () => {
  const c = L.PREFLIGHT.replace(/2>\/dev\/null|>\/dev\/null 2>&1|>\/dev\/null/g, "");
  assert.ok(!/>/.test(c), "no redirection into files");
  for (const verb of ["rm ", "apt", "systemctl", "ufw ", "iptables", "nft add", "nft flush", "wg set", "tee", "mv ", "cp "]) assert.ok(!c.includes(verb), verb);
  assert.deepEqual(L.parseFacts(FRESH), { os: "ubuntu 24.04", uid: "0", wg: "", status: "none", kiwi_nft: "no", fw_rules: "0", docker: "no", port53: "0" });
  assert.deepEqual(L.preflightProblems(L.parseFacts(FRESH)), []);
});

test("only public IPv4 addresses are install targets", () => {
  for (const ip of ["192.0.2.77", "203.0.113.5", "8.8.8.8"]) assert.equal(L.isPublicIPv4(ip), true, ip);
  for (const ip of ["10.0.0.1", "172.16.5.5", "192.168.1.1", "127.0.0.1", "100.64.0.1", "169.254.169.254", "0.0.0.0", "224.0.0.1", "300.1.1.1", "1.2.3.4/32", "x", "", "2001:db8::1"])
    assert.equal(L.isPublicIPv4(ip), false, ip);
});

test("certificate expiry: ok, warn before 21 days, fail when expired or unreadable", () => {
  assert.equal(L.certVerdict(80).level, "ok");
  assert.equal(L.certVerdict(21).level, "ok");
  assert.equal(L.certVerdict(20).level, "warn");
  assert.equal(L.certVerdict(0).level, "warn");
  assert.equal(L.certVerdict(-1).level, "fail");
  assert.equal(L.certVerdict(null).level, "fail");
  assert.match(L.certVerdict(-3).text, /EXPIRED 3 days ago/);
});

test("catalog: external nodes aren't Hetzner-managed, and the cert email must be an address", () => {
  assert.deepEqual(L.validateCatalog(CAT), []);
  const bad = { ...CAT, nodes: { ...CAT.nodes, ext1: { ...NODE, host: `ext1.${CAT.dnsZone}`, managed: true, location: "x", serverType: "y" } } };
  assert.ok(L.validateCatalog(bad).some((p) => /external server/.test(p)));
  assert.ok(L.validateCatalog({ ...CAT, certEmail: "nope" }).some((p) => /certEmail/.test(p)));
  assert.deepEqual(L.validateCatalog({ ...CAT, certEmail: "ops@example.com" }), []);
});

test("register --delete-file needs its own approval and deletes only the registration file", () => {
  const src = readFileSync(new URL("./kiwi-infra.mjs", import.meta.url), "utf8");
  const block = src.slice(src.indexOf('if (process.argv.includes("--delete-file"))'), src.indexOf("async [\"agent-firewall\"]"));
  assert.match(block, /await approve\(L\.approvalPhrase\("forget", key, 0, /);
  assert.ok(block.indexOf("await approve") < block.indexOf("ssh(ip"));
  assert.match(block, /shred -u \/root\/kiwi-node\.txt 2>\/dev\/null \|\| rm -f \/root\/kiwi-node\.txt; test ! -e \/root\/kiwi-node\.txt/);
});
