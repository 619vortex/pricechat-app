// Kiwi VPN regions planner: the pure parts (no network, no files), so every number and decision is unit-tested.
// regions.json holds every candidate region with its provider, plan and list price; regions.mjs prints plans from it.
import { createHash } from "node:crypto";

export const REGION_KEY = /^[a-z]{3}[0-9]{1,2}$/; // lon1, ams1, tlv1 ... (same shape as catalog.json nodes)
export const RECOMMENDATIONS = ["go", "wait", "avoid"];
export const RISKS = ["low", "medium", "high"];
export const AUTOMATED = new Set(["hetzner"]); // providers kiwi-infra.mjs can create servers on (with the owner's typed approval)

export function validateRegions(cat) {
  const problems = [];
  if (!/^\d{4}-\d{2}-\d{2}$/.test(cat.asOf || "")) problems.push("asOf: the date the prices were read (YYYY-MM-DD)");
  if (cat.currency !== "USD") problems.push("currency: prices are kept in USD (Hetzner bills this account in USD)");
  for (const [key, r] of Object.entries(cat.regions || {})) {
    const where = `regions.${key}`;
    if (!REGION_KEY.test(key)) problems.push(`${where}: keys look like lon1`);
    if (!/^[A-Z]{2}$/.test(r.country || "")) problems.push(`${where}: country is a 2-letter code`);
    if (!RECOMMENDATIONS.includes(r.recommendation)) problems.push(`${where}: recommendation is one of ${RECOMMENDATIONS.join("/")}`);
    if (!RISKS.includes(r.risk)) problems.push(`${where}: risk is one of ${RISKS.join("/")}`);
    if (r.recommendation !== "avoid") {
      for (const f of ["provider", "plan", "location"]) if (!r[f]) problems.push(`${where}: ${f} is required`);
      for (const f of ["monthly", "includedTB", "perTB"]) if (!(typeof r[f] === "number" && r[f] >= 0)) problems.push(`${where}: ${f} must be a number >= 0`);
      if (!(typeof r.ipv4 === "number" && r.ipv4 >= 0)) problems.push(`${where}: ipv4 (monthly, 0 if included) must be a number`);
      if (!Array.isArray(r.sources) || !r.sources.length || r.sources.some((s) => !/^https:\/\//.test(s))) problems.push(`${where}: sources: at least one https URL for the price`);
      if (!Number.isInteger(r.wave) || r.wave < 1) problems.push(`${where}: wave is 1, 2, 3 ...`);
    }
    if (r.host && !r.host.endsWith(".vpn.pricechat.live")) problems.push(`${where}: host must be under vpn.pricechat.live`);
    if (/(\d{1,3}\.){3}\d{1,3}/.test(JSON.stringify(r))) problems.push(`${where}: no IP addresses in the catalog (servers are named, never numbered)`);
  }
  return problems;
}

const round2 = (x) => Math.round(x * 100) / 100;

/** Monthly cost of one region's server at a given outgoing traffic (TB): plan + IPv4 + overage past the allowance. */
export function regionCost(r, trafficTB = 0) {
  if (r.recommendation === "avoid" || typeof r.monthly !== "number") return null;
  const over = Math.max(0, trafficTB - (r.includedTB ?? 0));
  return round2(r.monthly + (r.ipv4 ?? 0) + over * (r.perTB ?? 0));
}

/** Which regions a plan covers: explicit keys, a wave (that wave and every earlier one), or everything that isn't "avoid". */
export function selectRegions(cat, { keys = [], wave = null, all = false } = {}) {
  const entries = Object.entries(cat.regions || {});
  if (keys.length) {
    const missing = keys.filter((k) => !cat.regions?.[k]);
    if (missing.length) throw new Error(`not in regions.json: ${missing.join(", ")}`);
    return keys.map((k) => [k, cat.regions[k]]);
  }
  if (wave != null) return entries.filter(([, r]) => r.recommendation === "go" && r.wave <= wave);
  if (all) return entries.filter(([, r]) => r.recommendation !== "avoid");
  return entries.filter(([, r]) => r.recommendation === "go" && r.wave === 1);
}

/** What adding a region takes, step by step. Nothing here runs: regions.mjs only prints it. */
export function stepsFor(key, r) {
  const host = r.host || `${key}.vpn.pricechat.live`;
  const order = AUTOMATED.has(r.provider)
    ? `create the server: add ${key} to catalog.json (location ${r.location}, type ${r.plan}), then the owner runs \`node vpn-node/infra/kiwi-infra.mjs plan ${key}\` and \`create ${key}\` (typed approval phrase)`
    : `order by hand in the ${r.providerName || r.provider} console: ${r.plan} in ${r.location}, Ubuntu 24.04, IPv4 + IPv6, the owner's SSH key only, hourly billing, no backups (nodes are rebuilt from the repo)`;
  return [
    order,
    `provider firewall (or the node's nftables only, if the provider has none): 22/tcp, 51820/udp, 80+443/tcp, ICMP; 8443/tcp only from Railway's static IPs`,
    `DNS: A (and AAAA) ${host} -> the new server (Vercel DNS, owner approval)`,
    `install: install.sh (QUIET_SECRETS=1 WITH_PROXY=1 AGENT_ALLOW=<Railway IPs>) then install-proxy.sh (PROXY_HOST=${host}), from committed files with checked hashes`,
    `node self-test on the server: vpn-node/infra/node-selftest.sh (exit, DNS, IPv6 refused, isolation, port 25)`,
    `register in Admin > VPN as Testers only, traffic allowance ${Math.round((r.includedTB ?? 0) * 1000)} GB, capacity ${r.capacity ?? 150}`,
    `client test through the tunnel: node vpn-node/infra/region-verify.mjs --host ${host} --country ${r.country} (exit IP, country, DNS, IPv6, connectivity); CI e2e on the Windows runner`,
    `owner switches it to everyone once both tests pass`,
  ];
}

/** The whole plan for some regions: one row each and totals at each traffic level. */
export function planRegions(cat, selected, { trafficTB = [1, 3] } = {}) {
  const rows = selected.map(([key, r]) => ({
    key, country: r.country, city: r.city, provider: r.provider, plan: r.plan, recommendation: r.recommendation, risk: r.risk, wave: r.wave,
    automated: AUTOMATED.has(r.provider),
    costs: Object.fromEntries(trafficTB.map((t) => [`${t}TB`, regionCost(r, t)])),
    steps: r.recommendation === "avoid" ? [] : stepsFor(key, r),
    note: r.note || "",
  }));
  const totals = Object.fromEntries(trafficTB.map((t) => [`${t}TB`, round2(rows.reduce((s, x) => s + (x.costs[`${t}TB`] ?? 0), 0))]));
  const hash = createHash("sha256").update(JSON.stringify({ asOf: cat.asOf, rows: rows.map(({ key, provider, plan, costs }) => ({ key, provider, plan, costs })) })).digest("hex");
  return { asOf: cat.asOf, currency: cat.currency, rows, totals, hash };
}

/** Today's fleet cost (existing servers in regions.json `existing`), for "before and after" totals. */
export function existingCost(cat, trafficTB = 0) {
  return round2(Object.values(cat.existing || {}).reduce((s, e) => s + e.monthly + (e.ipv4 ?? 0) + Math.max(0, trafficTB - (e.includedTB ?? 0)) * (e.perTB ?? 0), 0));
}

/**
 * Whether --apply may go ahead for one region. It never orders anything itself: Hetzner regions are handed to kiwi-infra.mjs
 * (which asks the owner for a typed phrase at a terminal), other providers are ordered by hand from the printed steps.
 */
export function applyDecision(key, r) {
  if (!r) return { ok: false, why: `no region ${key}` };
  if (r.recommendation === "avoid") return { ok: false, why: `${key} is marked avoid: ${r.note || "see REGIONS.md"}` };
  if (r.recommendation === "wait") return { ok: false, why: `${key} is marked wait: the owner changes it to go in regions.json first` };
  if (!AUTOMATED.has(r.provider)) return { ok: false, manual: true, why: `${r.provider} isn't automated: order it by hand from the steps above, then run the install and tests` };
  return { ok: true, command: ["node", "vpn-node/infra/kiwi-infra.mjs", "create", key] };
}
