// Kiwi VPN infrastructure manager: the pure parts (no network, no files), so every decision is unit-tested.
import { createHash } from "node:crypto";

export const NODE_KEY = /^[a-z]{3}[0-9]{1,2}$/;            // ash1, sin1, fsn2 ...
export const BUNDLE = ["install.sh", "agent.py", "proxy.py", "install-proxy.sh"]; // copied to /opt/kiwi-src on the node
const HOST = /^[a-z0-9]+(\.[a-z0-9-]+)+$/;
const IPV4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(\/\d{1,2})?$/;

export function validateCatalog(cat) {
  const problems = [];
  if (!Array.isArray(cat.agentAllow) || !cat.agentAllow.length) problems.push("agentAllow: the backend's addresses are required (the agent port is never public on new nodes)");
  for (const ip of cat.agentAllow || []) if (!IPV4.test(ip)) problems.push(`agentAllow: ${ip} isn't an IPv4 address`);
  if (!/^([0-9a-f]{2}:){15}[0-9a-f]{2}$/.test(cat.sshKeyFingerprint || "")) problems.push("sshKeyFingerprint: the MD5 fingerprint of the Hetzner SSH key to install");
  for (const [key, n] of Object.entries(cat.nodes || {})) {
    if (!NODE_KEY.test(key)) problems.push(`${key}: node names look like ash1`);
    if (!HOST.test(n.host || "") || !n.host.endsWith(`.${cat.dnsZone}`)) problems.push(`${key}: host must be a name under ${cat.dnsZone}`);
    if (!/^[A-Z]{2}$/.test(n.country || "")) problems.push(`${key}: country is a 2-letter code`);
    if (n.managed && !(n.location && n.serverType)) problems.push(`${key}: a managed node needs location and serverType`);
  }
  return problems;
}

/** Hetzner's /pricing for one server type in one location, plus the primary IPv4 there. Amounts are net, monthly. */
export function priceFor(pricing, serverType, location) {
  const st = pricing.server_types.find((t) => t.name === serverType);
  const row = st?.prices.find((p) => p.location === location);
  if (!row) return null;
  const ip = pricing.primary_ips.find((p) => p.type === "ipv4")?.prices.find((p) => p.location === location);
  return {
    currency: pricing.currency,
    server: Number(row.price_monthly.net),
    ipv4: Number(ip?.price_monthly.net ?? 0),
    includedTB: Math.round(Number(row.included_traffic || 0) / 2 ** 40 * 10) / 10,
    perTB: Number(row.price_per_tb_traffic?.net ?? 0),
  };
}

/** Monthly cost for a given amount of outgoing traffic (Hetzner bills outgoing traffic over the included amount). */
export function monthlyCost(p, trafficTB = 0) {
  const over = Math.max(0, trafficTB - p.includedTB);
  return round2(p.server + p.ipv4 + over * p.perTB);
}
const round2 = (x) => Math.round(x * 100) / 100;

/** Can this type be ordered here now? (Hetzner marks deprecated or sold-out types per location.) */
export function orderable(serverTypes, serverType, location) {
  const t = serverTypes.find((x) => x.name === serverType);
  if (!t) return { ok: false, why: `no server type ${serverType}` };
  const loc = (t.locations || []).find((l) => l.name === location);
  if (!loc) return { ok: false, why: `${serverType} isn't offered in ${location}` };
  if (loc.available === false) return { ok: false, why: `${serverType} is sold out or retired in ${location}` };
  if (loc.deprecation && Date.parse(loc.deprecation.unavailable_after) < Date.now()) return { ok: false, why: `${serverType} is retired in ${location}` };
  return { ok: true };
}

/** The Hetzner Cloud firewall for a new node: only what the node serves. The agent port only from the backend. */
export function firewallRules(cat) {
  const any = ["0.0.0.0/0", "::/0"];
  return [
    { direction: "in", protocol: "tcp", port: "22", source_ips: any, description: "ssh (keys only)" },
    { direction: "in", protocol: "udp", port: "51820", source_ips: any, description: "wireguard" },
    { direction: "in", protocol: "tcp", port: "443", source_ips: any, description: "browser proxy" },
    { direction: "in", protocol: "tcp", port: "80", source_ips: any, description: "certificate (lets encrypt)" },
    { direction: "in", protocol: "tcp", port: "8443", source_ips: cat.agentAllow.map((ip) => (ip.includes("/") ? ip : `${ip}/32`)), description: "agent, backend only" },
    { direction: "in", protocol: "icmp", source_ips: any, description: "ping" },
  ];
}

/** cloud-init: hardening only. No secrets (Hetzner keeps user data, and it's readable on the server's metadata address). */
export function cloudInit() {
  return `#cloud-config
# Kiwi VPN node, first boot: SSH keys only; the manager installs the rest over SSH with verified files.
ssh_pwauth: false
disable_root: false
package_update: true
package_upgrade: true
packages: [unattended-upgrades]
write_files:
  - path: /etc/ssh/sshd_config.d/10-kiwi.conf
    permissions: "0644"
    content: |
      PasswordAuthentication no
      KbdInteractiveAuthentication no
      PermitRootLogin prohibit-password
      X11Forwarding no
      MaxAuthTries 3
runcmd:
  - [systemctl, reload, ssh]
  - [mkdir, -p, -m, "0700", /opt/kiwi-src]
  - [sh, -c, "echo booted > /opt/kiwi-src/status"]
`;
}

export function serverName(key) { return `kiwi-${key}`; }

export function createBody(key, node, cat, { sshKeyId, firewallId }) {
  return {
    name: serverName(key),
    server_type: node.serverType,
    location: node.location,
    image: cat.image,
    ssh_keys: [sshKeyId],
    firewalls: [{ firewall: firewallId }],
    labels: { kiwi: "node", node: key },
    public_net: { enable_ipv4: true, enable_ipv6: true },
    start_after_create: true,
    user_data: cloudInit(),
  };
}

/** Everything a create would do, and whether it may: from read-only API data. */
export function planFor(key, cat, live, { trafficTB = [0, 1, 2, 5] } = {}) {
  const node = cat.nodes[key];
  const problems = [...validateCatalog(cat)];
  if (!node) return { key, problems: [`no node ${key} in catalog.json`] };
  if (!node.managed) problems.push(`${key} is an existing server this tool doesn't build: ${node.note || ""}`);
  const o = orderable(live.serverTypes, node.serverType, node.location);
  if (!o.ok) problems.push(o.why);
  const price = priceFor(live.pricing, node.serverType, node.location);
  if (!price) problems.push(`no price for ${node.serverType} in ${node.location}`);
  const key_ = live.sshKeys.find((k) => k.fingerprint === cat.sshKeyFingerprint);
  if (!key_) problems.push(`the SSH key ${cat.sshKeyFingerprint} isn't in the Hetzner project`);
  if (live.servers.some((s) => s.name === serverName(key))) problems.push(`a server called ${serverName(key)} already exists`);
  const fwName = `kiwi-${key}`;
  const fw = live.firewalls.find((f) => f.name === fwName);
  const steps = [
    fw ? `use firewall ${fwName} (id ${fw.id}), rules unchanged` : `create firewall ${fwName} (free): ${firewallRules(cat).map((r) => `${r.protocol} ${r.port ?? ""} ${r.description}`).join("; ")}`,
    `create server ${serverName(key)}: ${node.serverType} in ${node.location}, ${cat.image}, IPv4 + IPv6, SSH key ${key_?.name ?? "?"}, delete/rebuild protection on`,
    `DNS: A ${node.host} -> the new IPv4 (Vercel DNS, ${cat.dnsZone})`,
    `install over SSH: install.sh (QUIET_SECRETS=1 WITH_PROXY=1 AGENT_ALLOW=${cat.agentAllow.join(",")}), then install-proxy.sh (PROXY_HOST=${node.host})`,
    `checks: kiwi-vpn-doctor, proxy answers 407, agent closed to everyone but the backend`,
    `register in Admin > VPN (token copied to your clipboard, never shown)`,
  ];
  const costs = price ? Object.fromEntries(trafficTB.map((t) => [`${t}TB`, monthlyCost(price, t)])) : {};
  const body = createBody(key, node, cat, { sshKeyId: key_?.id ?? 0, firewallId: fw?.id ?? 0 });
  const hash = createHash("sha256").update(JSON.stringify({ body, rules: firewallRules(cat), price })).digest("hex");
  return { key, node, problems, steps, price, costs, body, firewallName: fwName, firewallRules: firewallRules(cat), hash };
}

export function approvalPhrase(action, key, amount, hash) {
  return `${action} ${key} ${amount.toFixed(2)} ${hash.slice(0, 8)}`;
}

/** A person at a real terminal types the exact phrase. Anything else (no terminal, a script, an agent) is refused. */
export async function confirmApproval(expected, { isTTY, ask }) {
  if (!isTTY) throw new Error("this step spends money or changes a server: it needs the owner at a terminal (run it with ! in Claude Code)");
  const typed = (await ask(`Type "${expected}" to approve: `)).trim();
  if (typed !== expected) throw new Error("not approved: nothing was changed");
  return true;
}

/** Read-only account summary with the things worth fixing. */
export function audit({ servers, firewalls, primaryIps, pricing, volumes = [], snapshots = [] }) {
  const rows = [];
  const warnings = [];
  let total = 0;
  for (const s of servers) {
    const loc = s.location?.name ?? s.datacenter?.location?.name;
    const p = priceFor(pricing, s.server_type.name, loc);
    const outTB = (s.outgoing_traffic || 0) / 2 ** 40;
    const cost = p ? round2(p.server + (s.public_net?.ipv4 ? p.ipv4 : 0) + (s.backup_window ? p.server * 0.2 : 0)) : null;
    if (cost) total += cost;
    const fws = (s.public_net?.firewalls || []).map((f) => firewalls.find((x) => x.id === f.id)).filter(Boolean);
    rows.push({ name: s.name, id: s.id, type: s.server_type.name, location: loc, status: s.status, ipv4: s.public_net?.ipv4?.ip, ipv6: s.public_net?.ipv6?.ip,
      image: s.image?.name, created: s.created?.slice(0, 10), listMonthly: cost, trafficTB: round2(outTB), includedTB: p?.includedTB,
      backups: !!s.backup_window, deleteProtection: !!s.protection?.delete, firewalls: fws.map((f) => f.name) });
    if (!fws.length) warnings.push(`${s.name}: no Hetzner firewall`);
    for (const f of fws) for (const r of f.rules) {
      if (r.direction === "in" && r.port === "8443" && r.source_ips.some((ip) => ip === "0.0.0.0/0" || ip === "::/0")) warnings.push(`${s.name}: agent port 8443 open to the internet (firewall ${f.name})`);
    }
    if (!s.protection?.delete) warnings.push(`${s.name}: delete protection off`);
    if (p && outTB > p.includedTB * 0.8) warnings.push(`${s.name}: ${round2(outTB)} TB out of ${p.includedTB} TB included`);
  }
  for (const ip of primaryIps) if (!ip.assignee_id) warnings.push(`primary IP ${ip.ip} isn't attached to anything (still billed)`);
  for (const f of firewalls) if (!f.applied_to?.length) warnings.push(`firewall ${f.name} isn't applied to any server`);
  return { rows, warnings, monthlyList: round2(total), currency: pricing.currency, volumes: volumes.length, snapshots: snapshots.length };
}

export function sha256(buf) { return createHash("sha256").update(buf).digest("hex"); }
