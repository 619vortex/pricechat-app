// TEST ONLY. Signs Kiwi update manifests (and server lists) with the throwaway TEST keys t1/t2, for the CI update test and the
// service's own unit tests. The private halves below are public on purpose: no public build trusts them (service/src/update.rs
// TEST_KEYS is compiled in only with the `update-test` feature). The owner's real key is never here: see tools/new-update-key.ps1.
//   node manifest.mjs sign <payload.json> <out.json> [t1|t2] [update|servers] [key id to claim, for the wrong-signature test]
//   node manifest.mjs payload <out.json> --version 2.2.1 --url ... --size N --sha256 HEX [--seq N] [--min 2.2.0] [--security] [--paused]
//                           [--rollout 10000] [--expired] [--future] [--channel test] [--product kiwivpn-windows]
import crypto from "node:crypto";
import fs from "node:fs";

export const TEST_SEEDS = {
  t1: "2e7d75964c7535f11a1cd41508a931b652a32efecc0193a3fc683420d49e3984",
  t2: "560cc2924a4d5cb6b0c785c74657b0238d3fd02b4837b3bb6db52d6a9994fc08",
};
export const PREFIX = { update: "kiwi-update-v1\n", servers: "kiwi-servers-v1\n" };

// PKCS#8 wrapping of a raw Ed25519 seed (RFC 8410)
function keyFromSeed(hex) {
  const der = Buffer.concat([Buffer.from("302e020100300506032b657004220420", "hex"), Buffer.from(hex, "hex")]);
  return crypto.createPrivateKey({ key: der, format: "der", type: "pkcs8" });
}

export function publicHex(keyId) {
  return crypto.createPublicKey(keyFromSeed(TEST_SEEDS[keyId])).export({ format: "der", type: "spki" }).subarray(12).toString("hex");
}

export function envelope(payloadBytes, keyId = "t1", kind = "update", claim = keyId) {
  const message = Buffer.concat([Buffer.from(PREFIX[kind]), payloadBytes]);
  const sig = crypto.sign(null, message, keyFromSeed(TEST_SEEDS[keyId]));
  return JSON.stringify({ key_id: claim, payload: payloadBytes.toString("base64"), sig: sig.toString("base64") });
}

const iso = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");

export function payload(o) {
  const now = Date.now();
  const signed = o.future ? now + 3 * 86400e3 : o.expired ? now - 20 * 86400e3 : now - 60e3;
  return {
    schema: 1,
    product: o.product ?? "kiwivpn-windows",
    channel: o.channel ?? "test",
    seq: Number(o.seq ?? 1),
    signed_at: iso(signed),
    expires_at: iso(signed + 14 * 86400e3),
    release: {
      version: o.version,
      min_version: o.min ?? null,
      security: !!o.security,
      url: o.url,
      size: Number(o.size),
      sha256: o.sha256,
      notes: o.notes ?? "CI update test.",
      rollout_bp: Number(o.rollout ?? 10000),
      paused: !!o.paused,
    },
    pin_add: [],
    revoke_keys: [],
  };
}

function args(list) {
  const o = {};
  for (let i = 0; i < list.length; i++) {
    const a = list[i];
    if (!a.startsWith("--")) continue;
    const name = a.slice(2);
    if (["security", "paused", "expired", "future"].includes(name)) o[name] = true;
    else o[name] = list[++i];
  }
  return o;
}

if (process.argv[1] && process.argv[1].endsWith("manifest.mjs")) {
  const [cmd, ...rest] = process.argv.slice(2);
  if (cmd === "sign") {
    const [inFile, outFile, keyId = "t1", kind = "update", claim = keyId] = rest;
    fs.writeFileSync(outFile, envelope(fs.readFileSync(inFile), keyId, kind, claim));
  } else if (cmd === "payload") {
    fs.writeFileSync(rest[0], JSON.stringify(payload(args(rest.slice(1)))));
  } else if (cmd === "check") {
    // self-test: the seeds match the public keys compiled into the service as TEST_KEYS
    const want = { t1: "ca934a560e296684883f80e107452d396a3bcfb0c2b16842b49ce1f7bd9911f8", t2: "aab74fc72a965f8cda1f24780edb30e3ee4c3ee7659d25a2164db558c5c0db18" };
    for (const k of ["t1", "t2"]) if (publicHex(k) !== want[k]) { console.error(`${k} doesn't match`); process.exit(1); }
    console.log("test keys ok");
  } else {
    console.error("usage: node manifest.mjs sign|payload|check ...");
    process.exit(2);
  }
}
