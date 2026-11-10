#!/usr/bin/env node
// Kiwi VPN regions planner. DRY RUN by default: prints what each new region needs and what it costs a month. Changes nothing.
//
//   node vpn-node/infra/regions.mjs plan                  the recommended first wave
//   node vpn-node/infra/regions.mjs plan --wave 2         waves 1 and 2 (regions marked "go")
//   node vpn-node/infra/regions.mjs plan --all            every region that isn't "avoid", including "wait"
//   node vpn-node/infra/regions.mjs plan lon1 ams1        just these
//   node vpn-node/infra/regions.mjs plan --traffic 1,3,5  costs at these TB of outgoing traffic per server per month
//   node vpn-node/infra/regions.mjs plan --json           the same, as JSON
//   node vpn-node/infra/regions.mjs plan sin1 --apply     the owner only: see below
//
// --apply never orders anything by itself. For a Hetzner region it hands over to `kiwi-infra.mjs create <key>`, which shows the
// exact request and price and needs the owner to type an approval phrase at a real terminal. For other providers it refuses
// and prints the manual order steps. Prices are list prices read on the date in regions.json; the provider's order page shows
// the final price. No tokens are read by this tool.
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import * as R from "./regions-lib.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = dirname(dirname(HERE));

export function parseArgs(argv) {
  const out = { cmd: argv[0] || "plan", keys: [], wave: null, all: false, json: false, apply: false, traffic: [1, 3] };
  for (let i = 1; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--wave") out.wave = Number(argv[++i]);
    else if (a === "--all") out.all = true;
    else if (a === "--json") out.json = true;
    else if (a === "--apply") out.apply = true;
    else if (a === "--traffic") out.traffic = String(argv[++i] || "").split(",").filter(Boolean).map(Number).filter((x) => Number.isFinite(x) && x >= 0);
    else if (a.startsWith("--")) throw new Error(`unknown option ${a}`);
    else out.keys.push(a);
  }
  if (out.wave != null && !(Number.isInteger(out.wave) && out.wave >= 1)) throw new Error("--wave takes 1, 2, 3 ...");
  if (!out.traffic.length) throw new Error("--traffic takes TB amounts like 1,3");
  return out;
}

const usd = (x) => (x == null ? "n/a" : `$${x.toFixed(2)}`);

export function render(cat, plan, traffic) {
  const lines = [];
  lines.push(`Kiwi VPN regions plan (DRY RUN: nothing is ordered or changed). List prices read ${plan.asOf}, ${plan.currency}, net of VAT.`);
  for (const r of plan.rows) {
    lines.push("");
    lines.push(`${r.key}  ${r.city || ""} (${r.country})  ${r.provider} ${r.plan || ""}  wave ${r.wave ?? "-"}  ${r.recommendation.toUpperCase()}  risk ${r.risk}`);
    lines.push(`  monthly: ${traffic.map((t) => `${usd(r.costs[`${t}TB`])} at ${t} TB`).join(" · ")}`);
    if (r.note) lines.push(`  note: ${r.note}`);
    r.steps.forEach((s, i) => lines.push(`  ${i + 1}. ${s}`));
  }
  lines.push("");
  lines.push(`New servers, total per month: ${traffic.map((t) => `${usd(plan.totals[`${t}TB`])} at ${t} TB each`).join(" · ")}`);
  const now = R.existingCost(cat, 0);
  lines.push(`Existing fleet today: ${usd(now)} (list, light traffic). Fleet after: ${traffic.map((t) => `${usd(now + plan.totals[`${t}TB`])} at ${t} TB each`).join(" · ")}`);
  lines.push(`Plan hash ${plan.hash.slice(0, 8)} (changes whenever a price or plan changes).`);
  return lines.join("\n");
}

export function main(argv, { cat = JSON.parse(readFileSync(join(HERE, "regions.json"), "utf8")), say = console.log, run = spawnSync } = {}) {
  const opts = parseArgs(argv);
  if (opts.cmd !== "plan") throw new Error(`unknown command ${opts.cmd} (only: plan)`);
  const problems = R.validateRegions(cat);
  if (problems.length) throw new Error(`regions.json has problems:\n  ${problems.join("\n  ")}`);
  const selected = R.selectRegions(cat, opts);
  const plan = R.planRegions(cat, selected, { trafficTB: opts.traffic });
  say(opts.json ? JSON.stringify(plan, null, 2) : render(cat, plan, opts.traffic));
  if (!opts.apply) return 0;
  if (opts.keys.length !== 1) throw new Error("--apply takes exactly one region key, e.g. plan sin1 --apply");
  const key = opts.keys[0];
  const d = R.applyDecision(key, cat.regions[key]);
  if (!d.ok) {
    say(`\n--apply refused: ${d.why}`);
    return 2;
  }
  say(`\nHanding over to: ${d.command.join(" ")} (it shows the exact request and asks the owner for a typed phrase)`);
  const r = run(d.command[0], d.command.slice(1), { cwd: REPO, stdio: "inherit" });
  return r.status ?? 1;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  try { process.exitCode = main(process.argv.slice(2)); } catch (e) { console.error(String(e.message || e)); process.exitCode = 1; }
}
