// node --test windows-client/tools/sign-update-manifest.test.mjs
// Throwaway keys made here, in a temp folder; nothing real is read or written.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const TOOL = path.join(path.dirname(fileURLToPath(import.meta.url)), "sign-update-manifest.mjs");
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kiwi-manifest-test-"));
process.on("exit", () => fs.rmSync(dir, { recursive: true, force: true }));

const pubHex = (k) => crypto.createPublicKey(k).export({ format: "der", type: "spki" }).subarray(12).toString("hex");
function pem(name) {
  const { privateKey } = crypto.generateKeyPairSync("ed25519");
  const file = path.join(dir, `${name}.pem`);
  fs.writeFileSync(file, privateKey.export({ format: "pem", type: "pkcs8" }));
  return { file, pub: pubHex(privateKey) };
}
function updateRs(name, k1, k2) {
  const entry = (id, hex) => (hex ? `    Key { id: "${id}", public: hex32("${hex}") },` : `    Key { id: "${id}", public: [0; 32] },`);
  const file = path.join(dir, `${name}.rs`);
  fs.writeFileSync(file, `pub const PRODUCTION_KEYS: &[Key] = &[\n${entry("k1", k1)}\n${entry("k2", k2)}\n];\n`);
  return file;
}
const installer = path.join(dir, "KiwiVPN-2.2.1-test-Setup.exe");
fs.writeFileSync(installer, crypto.randomBytes(1024 * 1024 + 7));

function sign(key, keysFrom, keyId = "k1", more = []) {
  const out = path.join(dir, `stable-${crypto.randomUUID()}.json`);
  const r = spawnSync(process.execPath, [TOOL, "--key", key, "--key-id", keyId, "--installer", installer, "--version", "2.2.1", "--seq", "1",
    "--keys-from", keysFrom, "--out", out, ...more], { encoding: "utf8" });
  return { ...r, out };
}
const payloadOf = (out) => JSON.parse(Buffer.from(JSON.parse(fs.readFileSync(out, "utf8")).payload, "base64"));

const k1 = pem("k1");
const k2 = pem("k2");
const other = pem("other");

test("a manifest signed by PRODUCTION_KEYS' k1 is written and verifies with the kiwi-update-v1 prefix", () => {
  const r = sign(k1.file, updateRs("real", k1.pub, k2.pub));
  assert.equal(r.status, 0, r.stderr);
  const env = JSON.parse(fs.readFileSync(r.out, "utf8"));
  assert.equal(env.key_id, "k1");
  const payload = Buffer.from(env.payload, "base64");
  const spki = Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), Buffer.from(k1.pub, "hex")]);
  const ok = crypto.verify(null, Buffer.concat([Buffer.from("kiwi-update-v1\n"), payload]), crypto.createPublicKey({ key: spki, format: "der", type: "spki" }), Buffer.from(env.sig, "base64"));
  assert.ok(ok);
  const m = JSON.parse(payload);
  assert.equal(m.release.version, "2.2.1");
  assert.equal(m.release.sha256, crypto.createHash("sha256").update(fs.readFileSync(installer)).digest("hex").toUpperCase());
  assert.doesNotMatch(r.stdout + r.stderr, /PRIVATE KEY/);
});

test("k2 signs only as k2", () => {
  const rs = updateRs("real2", k1.pub, k2.pub);
  assert.equal(sign(k2.file, rs, "k2").status, 0);
  const wrongId = sign(k2.file, rs, "k1");
  assert.equal(wrongId.status, 1);
  assert.match(wrongId.stderr, /isn't PRODUCTION_KEYS' k1/);
  assert.equal(fs.existsSync(wrongId.out), false);
});

test("a key the builds don't trust is refused and nothing is written", () => {
  const r = sign(other.file, updateRs("real3", k1.pub, k2.pub));
  assert.equal(r.status, 1);
  assert.match(r.stderr, /every install would refuse/);
  assert.equal(fs.existsSync(r.out), false);
});

test("placeholder PRODUCTION_KEYS are refused (builds from that source trust nothing)", () => {
  const r = sign(k1.file, updateRs("zero", null, null));
  assert.equal(r.status, 1);
  assert.match(r.stderr, /all-zero placeholder/);
  assert.equal(fs.existsSync(r.out), false);
});

test("an unknown or odd key id is refused", () => {
  const rs = updateRs("real4", k1.pub, k2.pub);
  assert.match(sign(k1.file, rs, "k3").stderr, /k3 isn't in PRODUCTION_KEYS/);
  assert.match(sign(k1.file, rs, "k1.*").stderr, /--key-id is k1, k2/);
});

test("a renewed certificate can be pinned ahead of its first installer, in the service's format", () => {
  const rs = updateRs("pins", k1.pub, k2.pub);
  const plain = sign(k1.file, rs);
  assert.deepEqual(payloadOf(plain.out).pin_add, []);
  const r = sign(k1.file, rs, "k1", ["--pin-add", `${"ab".repeat(20)}:${"CD".repeat(32)},${"0".repeat(40)}`]);
  assert.equal(r.status, 0, r.stderr);
  assert.deepEqual(payloadOf(r.out).pin_add, [{ sha1: "AB".repeat(20), sha256: "CD".repeat(32) }, { sha1: "0".repeat(40), sha256: null }]);
  for (const bad of ["xyz", "ab".repeat(19), `${"ab".repeat(20)}:short`, `${"ab".repeat(20)}:${"cd".repeat(32)}:x`]) {
    const b = sign(k1.file, rs, "k1", ["--pin-add", bad]);
    assert.equal(b.status, 1, bad);
    assert.equal(fs.existsSync(b.out), false);
  }
});

test("k2 can revoke k1, never the reverse", () => {
  const rs = updateRs("revoke", k1.pub, k2.pub);
  const r = sign(k2.file, rs, "k2", ["--revoke", "k1"]);
  assert.equal(r.status, 0, r.stderr);
  assert.deepEqual(payloadOf(r.out).revoke_keys, ["k1"]);
  assert.match(sign(k1.file, rs, "k1", ["--revoke", "k2"]).stderr, /only a key listed before k1/);
  assert.match(sign(k2.file, rs, "k2", ["--revoke", "k2"]).stderr, /only a key listed before k2/);
  assert.match(sign(k2.file, rs, "k2", ["--revoke", "k9"]).stderr, /only a key listed before k2/);
});

test("the repository's own update.rs is read by default", () => {
  const r = spawnSync(process.execPath, [TOOL, "--key", k1.file, "--key-id", "k1", "--installer", installer, "--version", "2.2.1", "--seq", "1",
    "--out", path.join(dir, "default.json")], { encoding: "utf8" });
  assert.equal(r.status, 1);  // a throwaway key is never the real k1, and today's source still holds placeholders
  assert.match(r.stderr, /placeholder|isn't PRODUCTION_KEYS' k1/);
});
