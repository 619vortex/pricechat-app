// Kiwi VPN Chrome Web Store releases through Google's API v2. Builds, versions, tests and uploads on its own;
// submitting for review and going live each need the owner, typing an approval phrase at a real terminal.
//
//   node vpn-extension/store/release.mjs setup-key <downloaded-key.json> [--keep]   store the service-account key, encrypted for this Windows user
//   node vpn-extension/store/release.mjs status                                      what the store has (live / in review)
//   node vpn-extension/store/release.mjs prepare [--bump patch|minor|major]          check the version, run every test, build the ZIP, write a release record
//   node vpn-extension/store/release.mjs upload <version>                            upload that exact ZIP as the store draft (does NOT submit)
//   node vpn-extension/store/release.mjs submit <version>   (owner, at a terminal)   submit the draft for review, staged: it won't go live by itself
//   node vpn-extension/store/release.mjs go-live <version>  (owner, at a terminal)   publish a reviewed, staged version
//
// Env: KIWI_PYTHON (python with `cryptography`), KIWI_BROWSER (Chromium/Chrome for the browser tests).
import { spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { createInterface } from "node:readline/promises";
import { fileURLToPath } from "node:url";
import * as C from "./cws.mjs";

const STORE = dirname(fileURLToPath(import.meta.url));
const EXT = join(STORE, "..");
const REPO = join(EXT, "..");
const CONFIG = join(STORE, "cws.json");
const RECORDS = join(STORE, "releases");
const say = (s) => console.log(s);

/** Public Google downloads, tried three times: a long test run sometimes outlives a network blip. */
async function fetchRetry(url, init) {
  for (let i = 1; ; i++) {
    try { const r = await fetch(url, init); if (r.ok || i === 3) return r; } catch (e) { if (i === 3) throw new Error(`couldn't reach ${new URL(url).host}: ${e.cause?.code || e.message}`); }
    await new Promise((r) => setTimeout(r, 2000 * i));
  }
}

const config = () => JSON.parse(readFileSync(CONFIG, "utf8"));
const manifestVersion = () => JSON.parse(readFileSync(join(EXT, "manifest.json"), "utf8")).version;
const zipPath = (v) => join(STORE, "dist", `kiwi-vpn-chrome-${v}.zip`);
const recordPath = (v) => join(RECORDS, `${v}.json`);
const readRecord = (v) => { if (!existsSync(recordPath(v))) throw new Error(`no release record for ${v}: run prepare first`); return JSON.parse(readFileSync(recordPath(v), "utf8")); };
const writeRecord = (r) => { mkdirSync(RECORDS, { recursive: true }); writeFileSync(recordPath(r.version), JSON.stringify(r, null, 2) + "\n"); };

async function store() {
  const { publisherId, itemId } = config();
  return new C.Store({ publisherId, itemId, token: await C.accessToken(C.loadKey()) });
}
const hasCredentials = () => existsSync(C.KEY_FILE) && !!config().publisherId;

/** The package on disk must be byte-for-byte the one that was tested. */
function exactPackage(version) {
  const rec = readRecord(version);
  const zip = readFileSync(zipPath(version));
  if (C.sha256(zip) !== rec.sha256) throw new Error(`the ZIP for ${version} changed since it was tested: run prepare again`);
  if (!rec.tests?.passed) throw new Error(`${version} didn't pass its tests: nothing is sent`);
  return { rec, zip };
}

function run(label, cmd, args, opts = {}) {
  say(`  · ${label}`);
  const r = spawnSync(cmd, args, { encoding: "utf8", maxBuffer: 1 << 24, ...opts });
  const out = `${r.stdout || ""}${r.stderr || ""}`;
  return { ok: r.status === 0, out };
}

function tools() {
  const python = process.env.KIWI_PYTHON || ["C:/AI/price-chat/backend/.venv/Scripts/python.exe", "python3", "python"].find((p) => !p.includes("/") || existsSync(p));
  const browser = process.env.KIWI_BROWSER || [join(process.env.LOCALAPPDATA || "", "ms-playwright", "chromium-1243", "chrome-win64", "chrome.exe"),
    "C:/Program Files/Google/Chrome/Application/chrome.exe"].find(existsSync);
  if (!browser) throw new Error("set KIWI_BROWSER to a Chromium or Chrome executable for the browser tests");
  return { python, browser };
}

/** The browser suite, retried once: one check (the revoked popup) is timing-sensitive. Both results are kept. */
function browserSuite(label, cwd, extra) {
  const { python, browser } = tools();
  const tries = [];
  for (let i = 0; i < 2; i++) {
    const shots = mkdtempSync(join(tmpdir(), "kiwi-shots-"));
    const r = run(`${label}${i ? " (retry)" : ""}`, process.execPath, [join(cwd, "vpn-extension", "e2e", "run.mjs"), python, browser, shots], { cwd, timeout: 300000, ...extra });
    const pass = (r.out.match(/^PASS/gm) || []).length, fail = (r.out.match(/^FAIL.*$/gm) || []);
    tries.push({ ok: r.ok, pass, fail });
    if (!r.ok) say(`    ${pass} passed; ${fail.length ? fail.join(" | ").slice(0, 400) : r.out.trim().split(/\r?\n/).slice(-3).join(" | ").slice(0, 400)}`);
    if (r.ok) break;
  }
  return { ok: tries.at(-1).ok, tries };
}

/** Runs the exact store ZIP under the store's own extension ID (its public key from the published CRX). */
async function storeIdSuite(version, itemId) {
  const up = await C.publicUpdate(itemId, fetchRetry);
  if (!up) return { ok: false, tries: [], note: "the item isn't public, so its key can't be read" };
  const key = C.crxKeyFor(Buffer.from(await (await fetchRetry(up.codebase)).arrayBuffer()), itemId);
  const work = mkdtempSync(join(tmpdir(), "kiwi-zipcheck-"));  // the harness skips paths containing "e2e"
  try {
    mkdirSync(join(work, "vpn-extension"), { recursive: true });
    const unzip = run("unpack the store ZIP", "powershell.exe", ["-NoProfile", "-Command", `Expand-Archive -LiteralPath '${zipPath(version)}' -DestinationPath '${join(work, "vpn-extension")}'`]);
    if (!unzip.ok) return { ok: false, tries: [], note: unzip.out.slice(0, 300) };
    const m = JSON.parse(readFileSync(join(work, "vpn-extension", "manifest.json"), "utf8"));
    writeFileSync(join(work, "vpn-extension", "manifest.json"), JSON.stringify({ ...m, key }, null, 2));
    cpSync(join(EXT, "e2e"), join(work, "vpn-extension", "e2e"), { recursive: true });
    mkdirSync(join(work, "vpn-node"), { recursive: true });
    cpSync(join(REPO, "vpn-node", "proxy.py"), join(work, "vpn-node", "proxy.py"));
    mkdirSync(join(work, "backend", "app", "config"), { recursive: true });
    // what Railway's VPN_EXTENSION_ORIGINS allows (the dev ID and the store ID), for the harness's origin check
    writeFileSync(join(work, "backend", "app", "config", "settings.py"), `vpn_extension_origins = ["chrome-extension://gfajopjikiojbdlgmimbcmcnffbnjpfi", "chrome-extension://${itemId}"]\n`);
    return browserSuite(`browser tests on the store ZIP, as ${itemId}`, work);
  } finally { rmSync(work, { recursive: true, force: true }); }
}

const commands = {
  async "setup-key"(file, flag) {
    if (!file) throw new Error("give the path of the downloaded service-account key (.json)");
    const email = C.storeKey(readFileSync(file, "utf8"));
    C.loadKey();  // proves it decrypts
    if (flag !== "--keep") { unlinkSync(file); say(`Deleted the plaintext key ${file}.`); }
    say(`Stored, encrypted for this Windows user: ${C.KEY_FILE}`);
    say(`Add this address in the Chrome Web Store Developer Dashboard > Account > Service accounts:\n  ${email}`);
  },

  async status() {
    const { itemId } = config();
    const pub = await C.publicUpdate(itemId, fetchRetry);
    say(`Public: ${pub ? `version ${pub.version} is live` : "not public"}`);
    if (!hasCredentials()) { say("No store credentials yet, so review status isn't visible (see setup-key)."); return; }
    const s = await (await store()).status();
    const v = C.storeVersions(s);
    say(`Store API: live ${v.live.join(", ") || "none"} (${v.liveState ?? "-"}), submitted ${v.submitted.join(", ") || "none"} (${v.submittedState ?? "-"})${s.takenDown ? ", TAKEN DOWN" : ""}${s.warned ? ", warned" : ""}`);
  },

  async prepare(flag, part) {
    const { itemId } = config();
    if (flag === "--bump") {
      const m = JSON.parse(readFileSync(join(EXT, "manifest.json"), "utf8"));
      m.version = C.bump(m.version, part || "patch");
      writeFileSync(join(EXT, "manifest.json"), JSON.stringify(m, null, 2) + "\n");
      say(`Version bumped to ${m.version}`);
    }
    const version = manifestVersion();
    say(`Preparing ${version}`);
    const status = hasCredentials() ? await (await store()).status() : null;
    const pub = await C.publicUpdate(itemId, fetchRetry);
    const problem = C.versionProblem(version, status ?? { publishedItemRevisionStatus: pub ? { distributionChannels: [{ crxVersion: pub.version }] } : null });
    if (problem) throw new Error(problem);

    const results = {};
    results.unit = run("unit tests", process.execPath, ["--test", join(EXT, "lib.test.js"), join(STORE, "cws.test.mjs")]);
    results.node = run("proxy and agent tests", tools().python, ["-m", "pytest", "-q", "-p", "no:cacheprovider", join(REPO, "vpn-node", "test_proxy.py"), join(REPO, "vpn-node", "test_agent.py")]);
    results.dev = browserSuite("browser tests on the source", REPO);
    results.build = run("build the store ZIP", tools().python, [join(EXT, "build.py")]);
    const built = results.build.ok && existsSync(zipPath(version));
    results.zip = built ? await storeIdSuite(version, itemId) : { ok: false, note: "no ZIP" };
    const passed = Object.values(results).every((r) => r.ok);
    const zip = built ? readFileSync(zipPath(version)) : null;
    const rec = { version, zip: zipPath(version).slice(REPO.length + 1).replaceAll("\\", "/"), sha256: zip ? C.sha256(zip) : null, bytes: zip?.length ?? 0,
      prepared_at: new Date().toISOString(), store_before: status ? C.storeVersions(status) : { live: pub ? [pub.version] : [] },
      tests: { passed, unit: results.unit.ok, node: results.node.ok, browser_source: results.dev.tries, browser_store_zip: results.zip.tries ?? [], note: results.zip.note },
      uploaded: null, submitted: null, live: null };
    writeRecord(rec);
    for (const [k, r] of Object.entries(results)) if (!r.ok) say(`  FAILED: ${k}\n${(r.out || JSON.stringify(r.tries || r.note)).slice(-1500)}`);
    say(passed ? `\nReady: ${rec.zip}\n  sha256 ${rec.sha256}\n  next: upload ${version}` : `\nNot ready: ${version} failed tests (record kept in ${recordPath(version)})`);
    if (!passed) process.exitCode = 1;
  },

  async upload(version) {
    if (!version) throw new Error("upload which version?");
    if (version !== manifestVersion()) throw new Error(`manifest.json says ${manifestVersion()}, not ${version}`);
    const { rec, zip } = exactPackage(version);
    const s = await store();
    const problem = C.versionProblem(version, await s.status());
    if (problem) throw new Error(problem);
    say(`Uploading ${version} (${rec.sha256.slice(0, 12)}…) as the store draft. This does not submit it.`);
    const res = await s.upload(zip);
    const state = await C.waitForUpload(s, res);
    if (state !== "SUCCEEDED") throw new Error(`upload ended as ${state}`);
    rec.uploaded = { at: new Date().toISOString(), crxVersion: res.crxVersion || version, state };
    writeRecord(rec);
    say(`Uploaded. To submit for review (you, at a terminal):  node vpn-extension/store/release.mjs submit ${version}`);
  },

  async submit(version) {
    const { rec } = exactPackage(version);
    if (!rec.uploaded) throw new Error(`${version} hasn't been uploaded`);
    say(`Submitting ${version} for review, STAGED: after Google approves it, it waits for go-live.`);
    await C.confirmApproval(C.approvalPhrase("submit", version, rec.sha256), { ask: prompt });
    const res = await (await store()).publish({ staged: true });
    rec.submitted = { at: new Date().toISOString(), state: res.state ?? null, warnings: res.warningInfo?.warnings ?? [] };
    writeRecord(rec);
    say(`Submitted (${res.state ?? "no state given"}). Check with: status`);
  },

  async "go-live"(version) {
    const { rec } = exactPackage(version);
    const s = await store();
    const v = C.storeVersions(await s.status());
    if (!v.submitted.includes(version)) throw new Error(`the store's submitted version is ${v.submitted.join(", ") || "none"}, not ${version}`);
    say(`${version} is ${v.submittedState}. Going live publishes it to every user.`);
    await C.confirmApproval(C.approvalPhrase("go-live", version, rec.sha256), { ask: prompt });
    const res = await s.publish({ staged: false });
    rec.live = { at: new Date().toISOString(), state: res.state ?? null };
    writeRecord(rec);
    say(`Done (${res.state ?? "no state given"}).`);
  },
};

async function prompt(q) { const rl = createInterface({ input: process.stdin, output: process.stdout }); try { return await rl.question(q); } finally { rl.close(); } }

const [cmd, ...rest] = process.argv.slice(2);
if (!commands[cmd]) { say(readFileSync(fileURLToPath(import.meta.url), "utf8").split("\n").slice(0, 12).join("\n")); process.exit(cmd ? 1 : 0); }
commands[cmd](...rest).catch((e) => { console.error(`error: ${e.message}`); process.exit(1); });
