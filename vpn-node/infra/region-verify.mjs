#!/usr/bin/env node
// Kiwi VPN new-region check, run on a CLIENT while it is connected through the region's tunnel (a CI runner after the e2e
// connects, a tester's PC, or a Linux netns). Read-only: it only makes outgoing HTTPS and DNS requests.
//
//   node vpn-node/infra/region-verify.mjs --host lon1.vpn.pricechat.live --country GB [--json]
//
// Checks (each PASS / WARN / FAIL):
//   exit-ip     the public IPv4 seen by two echo services equals the region host's A record
//   country     two geolocation services place the exit IP in the expected country (WARN only: new IPs lag in geo databases)
//   dns         the resolver the internet sees (whoami.akamai.net, bash.ws leak test) is the node itself, not the client's ISP
//   ipv6        no IPv6 leaves the client, or if it does, it is the node's own IPv6 (AAAA of the host)
//   connect     HTTPS to several sites works through the tunnel, with timings
// Exit code = number of FAILs. Prints no secrets and never the client's own pre-tunnel address (it never learns it).
import { promises as dns } from "node:dns";
import https from "node:https";
import { fileURLToPath } from "node:url";

const ECHO4 = ["https://api.ipify.org", "https://ipv4.icanhazip.com"];
const ECHO6 = "https://api6.ipify.org";
const GEO = [
  { url: (ip) => `https://ipinfo.io/${ip}/json`, pick: (j) => j.country },
  { url: (ip) => `https://ipapi.co/${ip}/json/`, pick: (j) => j.country_code || j.country },
];
const SITES = ["https://www.cloudflare.com/cdn-cgi/trace", "https://www.google.com/generate_204", "https://en.wikipedia.org/wiki/Main_Page", "https://pricechat.live/"];

// ---------- pure evaluation (unit-tested) ----------

const same6 = (a, b) => !!a && !!b && a.toLowerCase().split(":").slice(0, 4).join(":") === b.toLowerCase().split(":").slice(0, 4).join(":");

/**
 * obs: { hostA: [ipv4], hostAAAA: [ipv6], exit4: [ip per echo service], geo: [country per service], resolverIPs: [ips seen by
 * whoami/bash.ws], v6: { ok: bool, ip?: string, ms: number }, sites: [{ url, ok, ms }] }
 * expect: { country: "GB" }
 */
export function evaluate(obs, expect) {
  const out = [];
  const add = (id, level, msg) => out.push({ id, level, msg });
  const exits = [...new Set((obs.exit4 || []).filter(Boolean))];
  if (!obs.hostA?.length) add("exit-ip", "FAIL", "the region host has no A record");
  else if (!exits.length) add("exit-ip", "FAIL", "no echo service answered: no IPv4 connectivity through the tunnel");
  else if (exits.length > 1) add("exit-ip", "FAIL", "echo services disagree on the exit IP (traffic is split between paths)");
  else if (!obs.hostA.includes(exits[0])) add("exit-ip", "FAIL", "the exit IP is not the region's server: traffic is not going through it");
  else add("exit-ip", "PASS", "IPv4 exits through the region's server");

  const geos = (obs.geo || []).filter(Boolean).map((c) => String(c).toUpperCase());
  if (!geos.length) add("country", "WARN", "no geolocation service answered");
  else if (geos.every((c) => c === expect.country)) add("country", "PASS", `geolocated in ${expect.country} by ${geos.length} service(s)`);
  else add("country", "WARN", `geolocation says ${geos.join("/")} instead of ${expect.country}: ask the provider to update geofeeds (RFC 8805) before selling it as ${expect.country}`);

  const res = [...new Set((obs.resolverIPs || []).filter(Boolean))];
  const nodeAddrs = [...(obs.hostA || []), ...(obs.hostAAAA || [])];
  if (!res.length) add("dns", "WARN", "no resolver identity answer (whoami.akamai.net / bash.ws unreachable)");
  else {
    const foreign = res.filter((ip) => !nodeAddrs.includes(ip) && !(ip.includes(":") && (obs.hostAAAA || []).some((a) => same6(a, ip))));
    if (foreign.length) add("dns", "FAIL", `${foreign.length} resolver(s) outside the node answered: a DNS leak (or the node forwards to a public resolver)`);
    else add("dns", "PASS", "the only resolver the internet sees is the node's own");
  }

  const v6 = obs.v6 || { ok: false, ms: 0 };
  if (!v6.ok) add("ipv6", v6.ms > 5000 ? "WARN" : "PASS", v6.ms > 5000 ? `IPv6 blocked but slow to fail (${v6.ms} ms): apps may stall before falling back` : "no IPv6 leaves the client");
  else if ((obs.hostAAAA || []).some((a) => same6(a, v6.ip))) add("ipv6", "PASS", "IPv6 exits through the region's server");
  else add("ipv6", "FAIL", "IPv6 leaves outside the tunnel: the client's real IPv6 is exposed");

  const sites = obs.sites || [];
  const okSites = sites.filter((s) => s.ok);
  if (!sites.length || okSites.length === 0) add("connect", "FAIL", "no site reachable through the tunnel");
  else if (okSites.length < sites.length) add("connect", "WARN", `${sites.length - okSites.length} of ${sites.length} sites failed: ${sites.filter((s) => !s.ok).map((s) => new URL(s.url).host).join(", ")}`);
  else add("connect", "PASS", `${sites.length} sites, median ${median(okSites.map((s) => s.ms))} ms`);
  return out;
}

export function median(xs) {
  const s = [...xs].sort((a, b) => a - b);
  return s.length ? s[Math.floor((s.length - 1) / 2)] : null;
}

export function parseArgs(argv) {
  const o = { host: "", country: "", json: false };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--host") o.host = argv[++i] || "";
    else if (argv[i] === "--country") o.country = (argv[++i] || "").toUpperCase();
    else if (argv[i] === "--json") o.json = true;
    else throw new Error(`unknown argument ${argv[i]}`);
  }
  if (!/^[a-z0-9]+\.vpn\.pricechat\.live$/.test(o.host)) throw new Error("--host must be a region name like lon1.vpn.pricechat.live");
  if (!/^[A-Z]{2}$/.test(o.country)) throw new Error("--country is a 2-letter code like GB");
  return o;
}

// ---------- network (thin) ----------

function get(url, { family, timeout = 8000 } = {}) {
  return new Promise((resolve) => {
    const t0 = Date.now();
    const req = https.get(url, { family, timeout, agent: false, headers: { "User-Agent": "kiwi-region-verify/1" } }, (res) => {
      let body = "";
      res.on("data", (d) => { if (body.length < 65536) body += d; });
      res.on("end", () => resolve({ ok: res.statusCode >= 200 && res.statusCode < 400, status: res.statusCode, body, ms: Date.now() - t0 }));
    });
    req.on("timeout", () => req.destroy(new Error("timeout")));
    req.on("error", () => resolve({ ok: false, ms: Date.now() - t0 }));
  });
}

async function resolverIdentity() {
  const ips = [];
  try { ips.push(...(await dns.resolve4("whoami.akamai.net"))); } catch { /* unreachable */ }
  try { // bash.ws: resolve random names under a test id, then ask which resolvers fetched them
    const id = (await get("https://bash.ws/id")).body?.trim();
    if (/^\d+$/.test(id || "")) {
      for (let i = 0; i < 6; i++) await dns.lookup(`${i}.${id}.bash.ws`).catch(() => {});
      const r = await get(`https://bash.ws/dnsleak/test/${id}?json`);
      for (const row of JSON.parse(r.body || "[]")) if (row.type === "dns" && row.ip) ips.push(row.ip);
    }
  } catch { /* bash.ws unavailable */ }
  return ips;
}

export async function gather(host) {
  const hostA = await dns.resolve4(host).catch(() => []);
  const hostAAAA = await dns.resolve6(host).catch(() => []);
  const exit4 = [];
  for (const u of ECHO4) { const r = await get(u, { family: 4 }); exit4.push(r.ok ? r.body.trim() : null); }
  const ip = exit4.find(Boolean);
  const geo = [];
  if (ip) for (const g of GEO) { const r = await get(g.url(ip)); try { geo.push(r.ok ? g.pick(JSON.parse(r.body)) : null); } catch { geo.push(null); } }
  const v6r = await get(ECHO6, { family: 6, timeout: 10000 });
  const v6 = { ok: v6r.ok, ip: v6r.ok ? v6r.body.trim() : undefined, ms: v6r.ms };
  const sites = [];
  for (const u of SITES) { const r = await get(u); sites.push({ url: u, ok: r.ok, ms: r.ms }); }
  return { hostA, hostAAAA, exit4, geo, resolverIPs: await resolverIdentity(), v6, sites };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  try {
    const o = parseArgs(process.argv.slice(2));
    const checks = evaluate(await gather(o.host), { country: o.country });
    if (o.json) console.log(JSON.stringify({ host: o.host, country: o.country, checks }, null, 2));
    else for (const c of checks) console.log(`${c.level.padEnd(4)} ${c.id.padEnd(8)} ${c.msg}`);
    process.exitCode = checks.filter((c) => c.level === "FAIL").length;
  } catch (e) { console.error(String(e.message || e)); process.exitCode = 1; }
}
