// OWNER ONLY (or the future "Publish update" workflow in the kiwi-publish environment). Writes and signs a Kiwi VPN update
// manifest for a SIGNED installer the owner has already checked (SIGNING.md). It publishes nothing: the owner puts the two files
// under public/downloads/kiwi/windows/ and deploys them, by hand. docs/AUTO-UPDATE.md has the field rules; the service checks
// every one of them again.
//
//   node windows-client/tools/sign-update-manifest.mjs --key <k1.pem | env:KIWI_MANIFEST_KEY> --key-id k1
//        --installer KiwiVPN-2.2.1-<sha>-signed-Setup.exe --version 2.2.1 --seq 43 [--channel stable] [--rollout 10000]
//        [--notes "..."] [--security] [--min 2.2.0] [--paused] [--days 14] [--out stable.json] [--keys-from <update.rs>]
//        [--pin-add <sha1>[:<sha256>]] [--revoke k1]
//   node windows-client/tools/sign-update-manifest.mjs --verify stable.json [--installer <exe>]   (no key needed)
//
// The installer is published as KiwiVPN-<version>-Setup.exe (versioned name, never overwritten); the manifest names that URL.
// The private key is read, used and dropped; it is never printed or written anywhere.
// Refuses to write a manifest that no install would accept: the key must be the public key PRODUCTION_KEYS holds under --key-id in
// service/src/update.rs (or --keys-from, for tests), and that entry must not be the all-zero placeholder.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PREFIX = "kiwi-update-v1\n";
const HOST = "https://pricechat.live/downloads/kiwi/windows/";
const FLAGS = ["security", "paused"];

function args(list) {
  const o = {};
  for (let i = 0; i < list.length; i++) {
    const name = list[i].replace(/^--/, "");
    if (FLAGS.includes(name)) o[name] = true;
    else o[name] = list[++i];
  }
  return o;
}

const die = (m) => { console.error(m); process.exit(1); };
const o = args(process.argv.slice(2));

// --verify <stable.json> [--installer <exe>] [--keys-from <update.rs>]: checks a manifest the way an install would (signature by
// PRODUCTION_KEYS, product, dates, URL) and, with --installer, that it names exactly that file. Needs no private key: run it on
// the file you are about to upload AND on the copy the website serves.
if (o.verify) {
  const keysFrom = o["keys-from"] ?? path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "service", "src", "update.rs");
  const block = /pub const PRODUCTION_KEYS: &\[Key\] = &\[([\s\S]*?)\];/.exec(fs.readFileSync(keysFrom, "utf8"))?.[1];
  if (block === undefined) die(`PRODUCTION_KEYS wasn't found in ${keysFrom}`);
  let env;
  try { env = JSON.parse(fs.readFileSync(o.verify, "utf8")); } catch { die(`FAIL: ${o.verify} isn't a manifest (JSON envelope)`); }
  if (!env || typeof env.key_id !== "string" || typeof env.payload !== "string" || typeof env.sig !== "string" || Object.keys(env).length !== 3) die("FAIL: not a { key_id, payload, sig } envelope");
  if (!/^k[0-9]{1,2}$/.test(env.key_id)) die(`FAIL: signed by "${env.key_id}", not a production key id`);
  const entry = new RegExp(`Key \\{ id: "${env.key_id}", public: (?:hex32\\("([0-9a-f]{64})"\\)|\\[0; 32\\]) \\}`).exec(block);
  if (!entry) die(`FAIL: signed by ${env.key_id}, which isn't in PRODUCTION_KEYS (${keysFrom})`);
  if (!entry[1]) die(`FAIL: PRODUCTION_KEYS' ${env.key_id} is the all-zero placeholder: no install accepts anything`);
  const spki = Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), Buffer.from(entry[1], "hex")]);
  const payloadBytes = Buffer.from(env.payload, "base64");
  const sigBytes = Buffer.from(env.sig, "base64");
  const good = sigBytes.length === 64 && crypto.verify(null, Buffer.concat([Buffer.from(PREFIX), payloadBytes]), crypto.createPublicKey({ key: spki, format: "der", type: "spki" }), sigBytes);
  if (!good) die(`FAIL: the signature doesn't verify with PRODUCTION_KEYS' ${env.key_id}`);
  const m = JSON.parse(payloadBytes);
  const r = m.release ?? {};
  const t = (s) => (/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(s ?? "") ? Date.parse(s) : NaN);
  const problems = [];
  if (m.product !== "kiwivpn-windows") problems.push(`product ${m.product}`);
  if (!(t(m.expires_at) > Date.now())) problems.push(`expired or bad expires_at (${m.expires_at}): installs offer nothing`);
  if (!(t(m.signed_at) <= Date.now() + 86400e3)) problems.push(`signed_at ${m.signed_at} is in the future`);
  if (!(t(m.expires_at) - t(m.signed_at) <= 30 * 86400e3)) problems.push("valid for more than 30 days (installs refuse it)");
  if (r.url !== `${HOST}KiwiVPN-${r.version}-Setup.exe`) problems.push(`url ${r.url} isn't ${HOST}KiwiVPN-${r.version}-Setup.exe`);
  if (o.installer) {
    const bytes = fs.readFileSync(o.installer);
    const sha = crypto.createHash("sha256").update(bytes).digest("hex").toUpperCase();
    if (bytes.length !== r.size) problems.push(`the installer is ${bytes.length} bytes, the manifest says ${r.size}`);
    if (sha !== String(r.sha256).toUpperCase()) problems.push(`the installer's SHA-256 is ${sha}, the manifest says ${r.sha256}`);
  }
  console.log(`signature OK (${env.key_id}); ${m.channel} ${r.version}, seq ${m.seq}, rollout ${r.rollout_bp / 100}%${r.paused ? ", PAUSED" : ""}, expires ${m.expires_at}`);
  console.log(`names ${r.url}, ${r.size} bytes, SHA-256 ${r.sha256}${o.installer ? `; compared with ${path.basename(o.installer)}` : "; no --installer given, not compared"}`);
  if (problems.length) die(`FAIL: ${problems.join("; ")}`);
  console.log("PASS");
  process.exit(0);
}

for (const need of ["key", "key-id", "installer", "version", "seq"]) if (!o[need]) die(`--${need} is required`);
if (!/^(0|[1-9]\d{0,4})\.(0|[1-9]\d{0,4})\.(0|[1-9]\d{0,4})$/.test(o.version)) die("--version must be plain major.minor.patch");
if (o.min && !/^(0|[1-9]\d{0,4})\.(0|[1-9]\d{0,4})\.(0|[1-9]\d{0,4})$/.test(o.min)) die("--min must be plain major.minor.patch");
const seq = Number(o.seq);
if (!Number.isInteger(seq) || seq < 1) die("--seq must be a whole number above the last published one");
const rollout = Number(o.rollout ?? 10000);
if (!Number.isInteger(rollout) || rollout < 0 || rollout > 10000) die("--rollout is basis points, 0 to 10000");
const days = Number(o.days ?? 14);
if (!(days > 0 && days <= 30)) die("--days must be 1 to 30");
const notes = o.notes ?? "";
if ([...notes].length > 2000) die("--notes is at most 2000 characters");
// --pin-add <sha1>[:<sha256>][,...]: a renewed code-signing certificate, trusted from now on (add-only). Publish it BEFORE the first
// installer signed with that certificate, or every install refuses that installer.
const pinAdd = (o["pin-add"] ?? "").split(",").filter(Boolean).map((p) => {
  const [sha1, sha256, extra] = p.split(":");
  if (extra !== undefined || !/^[0-9A-Fa-f]{40}$/.test(sha1) || (sha256 !== undefined && !/^[0-9A-Fa-f]{64}$/.test(sha256))) die(`--pin-add ${p}: <40 hex SHA-1>[:<64 hex SHA-256>]`);
  return { sha1: sha1.toUpperCase(), sha256: sha256 ? sha256.toUpperCase() : null };
});
// --revoke k1[,...]: key rotation. Installs honour only revocations of keys before the signing key (k2 revokes k1).
const revoke = (o.revoke ?? "").split(",").filter(Boolean);

const pemText = o.key.startsWith("env:") ? process.env[o.key.slice(4)] : fs.readFileSync(o.key, "utf8");
if (!pemText) die("no key");
const key = crypto.createPrivateKey(pemText);
if (key.asymmetricKeyType !== "ed25519") die("the key isn't an Ed25519 key");
if (!/^k[0-9]{1,2}$/.test(o["key-id"])) die("--key-id is k1, k2, ...");
const pub = crypto.createPublicKey(key).export({ format: "der", type: "spki" }).subarray(12).toString("hex");
const keysFrom = o["keys-from"] ?? path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "service", "src", "update.rs");
const block = /pub const PRODUCTION_KEYS: &\[Key\] = &\[([\s\S]*?)\];/.exec(fs.readFileSync(keysFrom, "utf8"))?.[1];
if (block === undefined) die(`PRODUCTION_KEYS wasn't found in ${keysFrom}`);
const entry = new RegExp(`Key \\{ id: "${o["key-id"]}", public: (?:hex32\\("([0-9a-f]{64})"\\)|\\[0; 32\\]) \\}`).exec(block);
if (!entry) die(`${o["key-id"]} isn't in PRODUCTION_KEYS (${keysFrom})`);
if (!entry[1]) die(`PRODUCTION_KEYS' ${o["key-id"]} is still the all-zero placeholder: builds from this source refuse every manifest. Paste the keys first (tools/new-update-key.ps1).`);
if (entry[1] !== pub) die(`this key isn't PRODUCTION_KEYS' ${o["key-id"]}: every install would refuse the manifest. Wrong .pem, or the wrong --key-id?`);
const order = [...block.matchAll(/id: "(k[0-9]{1,2})"/g)].map((m) => m[1]);
for (const id of revoke) {
  if (!order.includes(id) || order.indexOf(id) >= order.indexOf(o["key-id"])) die(`--revoke ${id}: only a key listed before ${o["key-id"]} in PRODUCTION_KEYS can be revoked by it`);
}

const installer = fs.readFileSync(o.installer);
if (installer.length < 1024 * 1024 || installer.length > 100 * 1024 * 1024) die("the installer must be 1 MB to 100 MB");
const sha256 = crypto.createHash("sha256").update(installer).digest("hex").toUpperCase();
const now = Date.now();
const iso = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");
const payload = {
  schema: 1,
  product: "kiwivpn-windows",
  channel: o.channel ?? "stable",
  seq,
  signed_at: iso(now),
  expires_at: iso(now + days * 86400e3),
  release: {
    version: o.version,
    min_version: o.min ?? null,
    security: !!o.security,
    url: `${HOST}KiwiVPN-${o.version}-Setup.exe`,
    size: installer.length,
    sha256,
    notes,
    rollout_bp: rollout,
    paused: !!o.paused,
  },
  pin_add: pinAdd,
  revoke_keys: revoke,
};
const bytes = Buffer.from(JSON.stringify(payload));
const message = Buffer.concat([Buffer.from(PREFIX), bytes]);
const sig = crypto.sign(null, message, key);
if (!crypto.verify(null, message, crypto.createPublicKey(key), sig)) die("the signature didn't verify");
const out = o.out ?? `${payload.channel}.json`;
fs.writeFileSync(out, JSON.stringify({ key_id: o["key-id"], payload: bytes.toString("base64"), sig: sig.toString("base64") }));
console.log(`wrote ${path.resolve(out)}: ${payload.channel} ${o.version}, seq ${seq}, rollout ${rollout / 100}%, expires ${payload.expires_at}`);
console.log(`installer ${installer.length} bytes, SHA-256 ${sha256}; publish it as KiwiVPN-${o.version}-Setup.exe`);
console.log(`signed by ${o["key-id"]} (public ${pub}), the same key as PRODUCTION_KEYS in ${path.relative(process.cwd(), keysFrom)}`);
