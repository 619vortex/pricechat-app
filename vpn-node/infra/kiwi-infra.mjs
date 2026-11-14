#!/usr/bin/env node
// Kiwi VPN infrastructure manager (Hetzner Cloud). Read-only unless a step says otherwise; every step that spends money or
// changes a server needs the owner at a terminal typing an approval phrase for that exact change.
//
//   node vpn-node/infra/kiwi-infra.mjs audit                 servers, firewalls, IPs, list prices, traffic, warnings
//   node vpn-node/infra/kiwi-infra.mjs plan <node>           what create would do and cost (ash1, sin1 ...), nothing changes
//   node vpn-node/infra/kiwi-infra.mjs create <node>         owner: firewall + server (paid from this moment)
//   node vpn-node/infra/kiwi-infra.mjs dns <node>            owner: A record on Vercel DNS
//   node vpn-node/infra/kiwi-infra.mjs install <node>        owner: copies verified files over SSH, installs WireGuard + proxy
//   node vpn-node/infra/kiwi-infra.mjs install <node> --host <ip> [--host-key SHA256:...]
//                                                            owner: the same on an Ubuntu 24.04 server elsewhere (an
//                                                            "external" node in catalog.json); read-only checks first
//   node vpn-node/infra/kiwi-infra.mjs health [node]         outside checks of every node: DNS, TLS, proxy 407, cert expiry
//   node vpn-node/infra/kiwi-infra.mjs register <node> [--host <ip>] [--delete-file]
//                                                            prints Admin > VPN fields; the token goes to the clipboard only;
//                                                            --delete-file then removes /root/kiwi-node.txt (owner approves)
//   node vpn-node/infra/kiwi-infra.mjs reboot <node>         owner: Hetzner reboot of a managed node that's stuck
//   node vpn-node/infra/kiwi-infra.mjs agent-firewall <node> [--apply | --rollback <file>]
//                                                            narrow the agent port to the backend's IPs in the Hetzner firewall
//
// It never deletes anything (register --delete-file removes only the node's copy of its own token file). Secrets: the Hetzner token stays in memory; the agent token is copied to the clipboard, not shown.
import { execFileSync, spawnSync } from "node:child_process";
import { promises as dns } from "node:dns";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { createInterface } from "node:readline/promises";
import tls from "node:tls";
import { fileURLToPath } from "node:url";
import { HCloud, loadToken } from "./hcloud.mjs";
import * as L from "./lib.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const NODE_DIR = dirname(HERE);
const REPO = dirname(NODE_DIR);
const CAT = JSON.parse(readFileSync(join(HERE, "catalog.json"), "utf8"));
const STATE_DIR = join(process.env.APPDATA || join(homedir(), ".config"), "KiwiVPN");
const KNOWN_HOSTS = join(STATE_DIR, "kiwi-nodes-known_hosts");
const SSH_KEY = process.env.KIWI_SSH_KEY || join(homedir(), ".ssh", "id_ed25519");

const say = (...a) => console.log(...a);
const money = (x, cur) => `${cur === "USD" ? "$" : "€"}${x.toFixed(2)}`;
const api = () => new HCloud(loadToken());

// --approved "<phrase>": a written approval given in advance; it counts only if it is exactly the phrase for this change now
const PRE = process.argv.indexOf("--approved") > 0 ? process.argv[process.argv.indexOf("--approved") + 1] ?? "" : undefined;
const opt = (name) => (process.argv.indexOf(name) > 0 ? process.argv[process.argv.indexOf(name) + 1] ?? "" : undefined);
async function approve(phrase) {
  if (PRE !== undefined) {
    if (PRE.trim() !== phrase) throw new Error(`the approval given (${PRE}) doesn't match this change now (${phrase}): nothing was changed`);
    return say(`approved in advance: ${phrase}`);
  }
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  try { return await L.confirmApproval(phrase, { isTTY: !!process.stdin.isTTY && !!process.stdout.isTTY, ask: (q) => rl.question(q) }); }
  finally { rl.close(); }
}

function managedNode(key) {
  const n = CAT.nodes[key];
  if (!n) throw new Error(`no node ${key} in catalog.json`);
  return n;
}

async function findServer(h, key) {
  const s = (await h.list(`/servers?label_selector=${encodeURIComponent(`kiwi=node,node=${key}`)}`, "servers"))[0];
  if (!s) throw new Error(`no server for ${key} yet (create it first)`);
  return s;
}

// ---------- ssh (the owner's key that Hetzner put on the server; host keys pinned on first use in a separate file) ----------
function ssh(ip, command, { input, inherit = false } = {}) {
  mkdirSync(STATE_DIR, { recursive: true });
  const r = spawnSync("ssh", ["-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", `UserKnownHostsFile=${KNOWN_HOSTS}`,
    "-o", "ConnectTimeout=15", `root@${ip}`, command], { input, encoding: input && typeof input !== "string" ? undefined : "utf8", stdio: inherit ? ["pipe", "inherit", "inherit"] : "pipe" });
  if (r.status !== 0) throw new Error(`ssh ${ip}: exit ${r.status} ${inherit ? "" : String(r.stderr || "").trim().split("\n").pop()}`);
  return r.stdout;
}

/** SHA256 fingerprints of the host keys pinned for this address (after the first connection). */
function hostKeys(ip) {
  const r = spawnSync("ssh-keygen", ["-F", ip, "-f", KNOWN_HOSTS, "-l"], { encoding: "utf8" });
  return [...String(r.stdout || "").matchAll(/SHA256:[A-Za-z0-9+/]+/g)].map((m) => m[0]);
}

function upload(ip, bundle) {
  const tmp = mkdtempSync(join(tmpdir(), "kiwi-node-"));
  try {
    for (const f of bundle.files) writeFileSync(join(tmp, f.name), f.body);
    const tar = spawnSync("tar", ["-C", tmp, "-cf", "-", ...L.BUNDLE], { maxBuffer: 1 << 26 });
    if (tar.status !== 0) throw new Error("couldn't pack the files");
    ssh(ip, "rm -rf /opt/kiwi-src/new && mkdir -p -m 0700 /opt/kiwi-src/new && tar -C /opt/kiwi-src/new -xf -", { input: tar.stdout });
  } finally { rmSync(tmp, { recursive: true, force: true }); }
}

/**
 * One install over SSH. Every outside effect is passed in, so the order and the refusals are tested with fakes.
 * external: a server this tool didn't build (install --host): read-only checks first, and the approval phrase covers its
 * address and host key too, so an approval can't be reused for another server.
 */
export async function runInstall({ key, node, ip, external = false, bundle, ssh, upload, approve, resolve4, probe, hostKeys, expectHostKey, cat, say }) {
  const facts = L.parseFacts(ssh(ip, L.PREFLIGHT)); // read-only; the first connection pins the host key
  const keys = hostKeys(ip);
  if (external) {
    say(`${ip} host key ${keys.join(", ") || "(not pinned?)"}: compare it with the provider's console before approving`);
    if (expectHostKey && !keys.includes(expectHostKey)) throw new Error(`the server's host key isn't ${expectHostKey}: nothing was changed`);
    const problems = L.preflightProblems(facts);
    if (problems.length) throw new Error(`not installing on ${ip}:\n  - ${problems.join("\n  - ")}`);
  }
  const status = facts.status || "none";
  say(`${node.server ?? key} ${ip}: status ${status}; files from commit ${bundle.rev}:\n${bundle.files.map((f) => `  ${f.sha.slice(0, 16)}  ${f.name}`).join("\n")}`);
  if (!(await resolve4(node.host)).includes(ip)) throw new Error(`${node.host} doesn't point at ${ip} yet: run dns first (the proxy's certificate needs it)`);
  const binds = bundle.files.map((f) => f.sha).join() + (external ? ` ${ip} ${keys.join(",")}` : "");
  await approve(L.approvalPhrase(status.startsWith("installed") ? "reinstall" : "install", key, 0, L.sha256(binds)));
  upload(ip, bundle);
  ssh(ip, "cd /opt/kiwi-src/new && sha256sum -c --quiet -", { input: bundle.files.map((f) => `${f.sha}  ${f.name}`).join("\n") + "\n" });
  say("files verified on the server");
  const env = `QUIET_SECRETS=1 WITH_PROXY=1 AGENT_ALLOW=${cat.agentAllow.join(",")}`;
  const proxyEnv = `PROXY_HOST=${node.host}${cat.certEmail ? ` CERT_EMAIL=${cat.certEmail}` : ""}`;
  ssh(ip, `set -eo pipefail; cd /opt/kiwi-src/new; echo installing > ../status; ${env} bash install.sh 2>&1 | tee -a /var/log/kiwi-install.log; ` +
          `${proxyEnv} bash install-proxy.sh 2>&1 | tee -a /var/log/kiwi-install.log; kiwi-vpn-doctor; echo "installed ${bundle.rev}" > ../status`, { inherit: true });
  const pr = await probe(node.host);
  say(pr.ok ? `proxy answers 407 (TLS ${pr.tlsMs} ms, ${L.certVerdict(pr.certDays).text})` : `proxy check failed: ${pr.error ?? pr.status}`);
  say(`Next: node vpn-node/infra/kiwi-infra.mjs register ${key}${external ? ` --host ${ip}` : ""}`);
  return { status, facts };
}

/** The node files exactly as committed (LF endings), with their hashes. Refuses uncommitted changes. */
function committedBundle() {
  const dirty = execFileSync("git", ["status", "--porcelain", "--", ...L.BUNDLE.map((f) => `vpn-node/${f}`)], { cwd: REPO, encoding: "utf8" }).trim();
  if (dirty) throw new Error(`commit the node files first:\n${dirty}`);
  const rev = execFileSync("git", ["rev-parse", "--short", "HEAD"], { cwd: REPO, encoding: "utf8" }).trim();
  const files = L.BUNDLE.map((f) => {
    const body = execFileSync("git", ["show", `HEAD:vpn-node/${f}`], { cwd: REPO, maxBuffer: 1 << 24 });
    return { name: f, body, sha: L.sha256(body) };
  });
  return { rev, files };
}

// ---------- outside health check: TLS to the proxy, then an unauthenticated request that must get 407 ----------
export function probeProxy(host, { port = 443, timeout = 8000 } = {}) {
  return new Promise((resolve) => {
    const t0 = Date.now();
    const sock = tls.connect({ host, port, servername: host, timeout }, () => {
      const tlsMs = Date.now() - t0;
      const cert = sock.getPeerCertificate();
      const days = cert?.valid_to ? Math.floor((Date.parse(cert.valid_to) - Date.now()) / 864e5) : null;
      sock.write("GET http://check.kiwi-vpn.invalid/ HTTP/1.1\r\nHost: check.kiwi-vpn.invalid\r\nConnection: close\r\n\r\n");
      let buf = "";
      sock.on("data", (d) => { buf += d; if (buf.includes("\r\n")) { sock.destroy(); resolve({ ok: /^HTTP\/1\.[01] 407/.test(buf), status: buf.split(" ")[1], tlsMs, ms: Date.now() - t0, certDays: days }); } });
    });
    sock.on("timeout", () => { sock.destroy(); resolve({ ok: false, error: "timeout" }); });
    sock.on("error", (e) => resolve({ ok: false, error: e.code || e.message }));
  });
}

async function resolve4(host) {
  const r = new dns.Resolver(); r.setServers(["1.1.1.1", "8.8.8.8"]);
  return r.resolve4(host).catch(() => []);
}

// ---------- commands ----------
const COMMANDS = {
  async audit() {
    const snap = await api().snapshot();
    const a = L.audit(snap);
    say(`Hetzner Cloud project: ${a.rows.length} servers, ${snap.firewalls.length} firewalls, ${snap.primaryIps.length} primary IPs, ${a.volumes} volumes, ${a.snapshots} snapshots`);
    for (const r of a.rows) {
      say(`\n${r.name} (${r.id})  ${r.status}  ${r.type} in ${r.location}  ${r.image}  created ${r.created}`);
      say(`  ${r.ipv4}  ${r.ipv6}  firewalls: ${r.firewalls.join(", ") || "none"}  backups: ${r.backups ? "on" : "off"}  delete protection: ${r.deleteProtection ? "on" : "off"}`);
      say(`  list price ${r.listMonthly == null ? "?" : money(r.listMonthly, a.currency)}/month incl. IPv4 · traffic out this month ${r.trafficTB} TB of ${r.includedTB} TB`);
    }
    say(`\nTotal at today's list prices: ${money(a.monthlyList, a.currency)}/month, net (servers made before a price change may be billed less)`);
    say(a.warnings.length ? `\nWorth fixing:\n${a.warnings.map((w) => `  - ${w}`).join("\n")}` : "\nNo warnings.");
  },

  async plan(key) {
    const p = L.planFor(key, CAT, await api().snapshot());
    say(`Plan for ${key}: ${p.node?.name ?? ""}`);
    p.steps?.forEach((s, i) => say(`  ${i + 1}. ${s}`));
    if (p.price) {
      say(`\nMonthly, net: server ${money(p.price.server, p.price.currency)} + IPv4 ${money(p.price.ipv4, p.price.currency)}; ${p.price.includedTB} TB traffic included, then ${money(p.price.perTB, p.price.currency)}/TB`);
      say(`  by outgoing traffic: ${Object.entries(p.costs).map(([t, c]) => `${t} ${money(c, p.price.currency)}`).join(" · ")}`);
      say(`  billed hourly up to the monthly cap; nothing is billed until create`);
    }
    say(p.problems.length ? `\nCan't create yet:\n${p.problems.map((x) => `  - ${x}`).join("\n")}` : `\nTo create it (owner, at a terminal): ! node vpn-node/infra/kiwi-infra.mjs create ${key}`);
  },

  async create(key) {
    const h = api();
    const p = L.planFor(key, CAT, await h.snapshot());
    if (p.problems.length) throw new Error(`can't create ${key}:\n  - ${p.problems.join("\n  - ")}`);
    await COMMANDS.plan(key);
    await approve(L.approvalPhrase("create", key, p.costs["0TB"], p.hash));
    let fwId = (await h.snapshot()).firewalls.find((f) => f.name === p.firewallName)?.id;
    if (!fwId) {
      fwId = (await h.call("POST", "/firewalls", { name: p.firewallName, labels: { kiwi: "node", node: key }, rules: p.firewallRules })).firewall.id;
      say(`firewall ${p.firewallName} created (${fwId})`);
    }
    const body = { ...p.body, firewalls: [{ firewall: fwId }] };
    const made = await h.call("POST", "/servers", body);
    say(`server ${made.server.name} (${made.server.id}) is being created: ${made.server.public_net.ipv4?.ip}`);
    await h.waitAction(made.action);
    await h.waitAction((await h.call("POST", `/servers/${made.server.id}/actions/change_protection`, { delete: true, rebuild: true })).action);
    say(`running, with delete/rebuild protection. Next: ! node vpn-node/infra/kiwi-infra.mjs dns ${key}`);
  },

  async dns(key) {
    const n = managedNode(key);
    const s = await findServer(api(), key);
    const ip = s.public_net.ipv4.ip;
    const now = await resolve4(n.host);
    if (now.length === 1 && now[0] === ip) return say(`${n.host} already points at ${ip}`);
    if (now.length) throw new Error(`${n.host} points at ${now.join(", ")}, not ${ip}: change it by hand after checking why`);
    const sub = n.host.slice(0, -(CAT.dnsZone.length + 1));
    say(`Adds A ${n.host} -> ${ip} on Vercel DNS (${CAT.dnsZone}).`);
    await approve(L.approvalPhrase("dns", key, 0, L.sha256(`${n.host} ${ip}`)));
    execFileSync(process.platform === "win32" ? "npx.cmd" : "npx", ["vercel", "dns", "add", CAT.dnsZone, sub, "A", ip], { stdio: "inherit", shell: process.platform === "win32" });
    say(`Added. Next: ! node vpn-node/infra/kiwi-infra.mjs install ${key}`);
  },

  async install(key) {
    const n = managedNode(key);
    const host = opt("--host");
    let ip, external = false;
    if (host !== undefined) {
      if (!n.external) throw new Error(`${key} isn't an external node in catalog.json ("external": true): --host is only for servers this tool didn't build`);
      if (!L.isPublicIPv4(host)) throw new Error(`--host ${host} isn't a public IPv4 address`);
      ip = host; external = true;
    } else {
      if (!n.managed) throw new Error(`${key} isn't built by this tool: ${n.note ?? (n.external ? "use --host <ip>" : "")}`);
      ip = (await findServer(api(), key)).public_net.ipv4.ip;
    }
    await runInstall({ key, node: n, ip, external, bundle: committedBundle(), ssh, upload, approve, resolve4, probe: probeProxy, hostKeys,
      expectHostKey: opt("--host-key"), cat: CAT, say });
  },

  async health(only) {
    let servers = [];
    try { servers = (await api().snapshot()).servers; } catch (e) { say(`(Hetzner status skipped: ${e.message})`); }
    let bad = 0;
    for (const [key, n] of Object.entries(CAT.nodes)) {
      if (only && key !== only) continue;
      const ips = await resolve4(n.host);
      const s = servers.find((x) => x.labels?.node === key || x.name === n.server);
      if (!ips.length) { say(`${key.padEnd(5)} ${n.host}: no DNS record${s ? "" : " (not built)"}`); continue; }
      const pr = await probeProxy(n.host);
      const traffic = s ? ` · ${s.status}, ${(s.outgoing_traffic / 2 ** 40).toFixed(2)} TB out` : "";
      const cert = pr.ok ? L.certVerdict(pr.certDays) : null;
      if (!pr.ok || cert.level !== "ok") bad++;
      const certText = cert ? ` · ${cert.level === "ok" ? "" : `${cert.level.toUpperCase()}: `}${cert.text}` : "";
      say(`${key.padEnd(5)} ${n.host} ${ips.join(",")}: ${pr.ok ? `proxy OK ${pr.ms} ms (TLS ${pr.tlsMs})` : `proxy FAIL ${pr.error ?? pr.status}`}${certText}${traffic}`);
    }
    process.exitCode = bad ? 1 : 0;
  },

  async register(key) {
    const n = managedNode(key);
    const host = opt("--host");
    if (host !== undefined && !(n.external && L.isPublicIPv4(host))) throw new Error("--host is for external nodes, with their public IPv4");
    const ip = host ?? (await findServer(api(), key)).public_net.ipv4.ip;
    const kv = Object.fromEntries(ssh(ip, "cat /root/kiwi-node.txt").trim().split("\n").map((l) => l.split(/=(.*)/s).slice(0, 2)));
    if (!kv.agent_token || !kv.cert_sha256) throw new Error("the node has no registration file: run install first");
    const clip = spawnSync(process.platform === "win32" ? "clip.exe" : "pbcopy", [], { input: kv.agent_token });
    if (clip.status !== 0) throw new Error("couldn't copy the token to the clipboard; read it on the server: cat /root/kiwi-node.txt");
    say(`Admin > VPN > Add server:\n  name:         ${n.name}\n  region:       ${n.region ?? ""}\n  country_code: ${n.country}\n  agent_url:    ${kv.agent_url}`);
    say(`  cert_sha256:  ${kv.cert_sha256}\n  capacity:     ${n.capacity ?? 250}\n  agent_token:  (in your clipboard: paste it, then copy something else)`);
    say("The backend checks the agent before saving. The server shows in every app and the extension once it's online and has a proxy.");
    if (process.argv.includes("--delete-file")) {
      // L20: the token shouldn't stay on the server in a second file once it's saved in Admin (the agent keeps its own copy)
      say("\nOnce Admin > VPN has saved the server (it tested the agent), approve to delete /root/kiwi-node.txt on the node.");
      await approve(L.approvalPhrase("forget", key, 0, L.sha256(kv.cert_sha256)));
      ssh(ip, "shred -u /root/kiwi-node.txt 2>/dev/null || rm -f /root/kiwi-node.txt; test ! -e /root/kiwi-node.txt");
      say("deleted /root/kiwi-node.txt (the agent token is still in /etc/kiwi-vpn/agent.env, root and the agent only)");
    }
  },

  async ["agent-firewall"](key, mode, file) {
    const h = api();
    const n = managedNode(key);
    const snap = await h.snapshot();
    const s = snap.servers.find((x) => x.labels?.node === key || x.name === n.server);
    if (!s) throw new Error(`no server for ${key}`);
    const fwRefs = s.public_net.firewalls.map((f) => snap.firewalls.find((x) => x.id === f.id)).filter(Boolean);
    if (fwRefs.length !== 1) throw new Error(`${s.name} has ${fwRefs.length} firewalls; expected one`);
    const fw = fwRefs[0];
    const backupDir = join(STATE_DIR, "firewall-backups");
    if (mode === "--rollback") {
      const saved = JSON.parse(readFileSync(file, "utf8"));
      if (saved.firewall.id !== fw.id) throw new Error(`${file} is for firewall ${saved.firewall.id}, not ${fw.id}`);
      say(`Restores firewall ${fw.name} (${fw.id}) to the rules saved ${saved.savedAt}:`);
      for (const r of saved.firewall.rules) say(`  ${r.direction} ${r.protocol} ${r.port ?? ""} ${((r.direction === "in" ? r.source_ips : r.destination_ips) || []).join(",")}`);
      await approve(L.approvalPhrase("restore", key, 0, L.sha256(JSON.stringify(saved.firewall.rules))));
      await h.call("POST", `/firewalls/${fw.id}/actions/set_rules`, { rules: L.cleanRules(saved.firewall.rules) });
      return say("restored");
    }
    const next = L.restrictAgentRules(fw.rules, CAT.agentAllow);
    say(`Firewall ${fw.name} (${fw.id}) on ${s.name}: only the 8443 rule changes; every other rule is sent back unchanged.`);
    for (const [a, b] of fw.rules.map((r, i) => [r, next[i]])) {
      const ips = (r) => ((r.direction === "in" ? r.source_ips : r.destination_ips) || []).join(",");
      const was = ips(a), now = ips(b);
      say(`  ${a.direction} ${a.protocol} ${(a.port ?? "").padEnd(5)} ${was}${was === now ? "" : `  ->  ${now}`}`);
    }
    // the backend must already be reaching the agent from these addresses: Railway's static IPs (checked by the caller)
    if (mode !== "--apply") return say("\nNothing changed. Run again with --apply (a backup of today's rules is saved first).");
    mkdirSync(backupDir, { recursive: true });
    const backup = join(backupDir, `${fw.name}-${fw.id}-${new Date().toISOString().replace(/[:.]/g, "")}.json`);
    writeFileSync(backup, JSON.stringify({ savedAt: new Date().toISOString(), server: s.name, firewall: { id: fw.id, name: fw.name, rules: fw.rules } }, null, 1));
    say(`backup: ${backup}`);
    await approve(L.approvalPhrase("agent-firewall", key, 0, L.sha256(JSON.stringify(next))));
    const res = await h.call("POST", `/firewalls/${fw.id}/actions/set_rules`, { rules: L.cleanRules(next) });
    for (const a of res.actions || []) await h.waitAction(a);
    say(`applied. Rollback: node vpn-node/infra/kiwi-infra.mjs agent-firewall ${key} --rollback "${backup}"`);
  },

  async reboot(key) {
    const h = api();
    const s = await findServer(h, key);
    say(`Reboots ${s.name} (${s.public_net.ipv4.ip}): its users lose the connection for about a minute.`);
    await approve(L.approvalPhrase("reboot", key, 0, String(s.id).padStart(8, "0")));
    await h.waitAction((await h.call("POST", `/servers/${s.id}/actions/reboot`)).action);
    say("rebooted");
  },
};

const isMain = process.argv[1] && fileURLToPath(import.meta.url) === (await import("node:path")).resolve(process.argv[1]);
if (isMain) {
  const [cmd, arg, ...rest] = process.argv.slice(2);
  if (!COMMANDS[cmd]) { say(readFileSync(fileURLToPath(import.meta.url), "utf8").split("\n").slice(1, 20).map((l) => l.replace(/^\/\/ ?/, "")).join("\n")); process.exit(2); }
  if (["plan", "create", "dns", "install", "register", "reboot", "agent-firewall"].includes(cmd) && !L.NODE_KEY.test(arg || "")) { console.error("say which node, e.g. ash1"); process.exit(2); }
  COMMANDS[cmd](arg, ...rest).catch((e) => { console.error(`error: ${e.message}`); process.exit(1); });
}
