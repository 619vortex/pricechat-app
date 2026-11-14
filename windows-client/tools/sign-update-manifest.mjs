// OWNER ONLY (or the future "Publish update" workflow in the kiwi-publish environment). Writes and signs a Kiwi VPN update
// manifest for a SIGNED installer the owner has already checked (SIGNING.md). It publishes nothing: the owner puts the two files
// under public/downloads/kiwi/windows/ and deploys them, by hand. docs/AUTO-UPDATE.md has the field rules; the service checks
// every one of them again.
//
//   node windows-client/tools/sign-update-manifest.mjs --key <k1.pem | env:KIWI_MANIFEST_KEY> --key-id k1
//        --installer KiwiVPN-2.2.1-<sha>-signed-Setup.exe --version 2.2.1 --seq 43 [--channel stable] [--rollout 10000]
//        [--notes "..."] [--security] [--min 2.2.0] [--paused] [--days 14] [--out stable.json]
//
// The installer is published as KiwiVPN-<version>-Setup.exe (versioned name, never overwritten); the manifest names that URL.
// The private key is read, used and dropped; it is never printed or written anywhere.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

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

const pemText = o.key.startsWith("env:") ? process.env[o.key.slice(4)] : fs.readFileSync(o.key, "utf8");
if (!pemText) die("no key");
const key = crypto.createPrivateKey(pemText);
if (key.asymmetricKeyType !== "ed25519") die("the key isn't an Ed25519 key");

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
  pin_add: [],
  revoke_keys: [],
};
const bytes = Buffer.from(JSON.stringify(payload));
const message = Buffer.concat([Buffer.from(PREFIX), bytes]);
const sig = crypto.sign(null, message, key);
if (!crypto.verify(null, message, crypto.createPublicKey(key), sig)) die("the signature didn't verify");
const out = o.out ?? `${payload.channel}.json`;
fs.writeFileSync(out, JSON.stringify({ key_id: o["key-id"], payload: bytes.toString("base64"), sig: sig.toString("base64") }));
const pub = crypto.createPublicKey(key).export({ format: "der", type: "spki" }).subarray(12).toString("hex");
console.log(`wrote ${path.resolve(out)}: ${payload.channel} ${o.version}, seq ${seq}, rollout ${rollout / 100}%, expires ${payload.expires_at}`);
console.log(`installer ${installer.length} bytes, SHA-256 ${sha256}; publish it as KiwiVPN-${o.version}-Setup.exe`);
console.log(`signed by ${o["key-id"]} (public ${pub}): it must match PRODUCTION_KEYS in service/src/update.rs`);
