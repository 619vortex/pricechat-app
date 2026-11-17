// node --test windows-client/ci/release-keys.test.mjs
// Public keys only: throwaway key pairs are made in memory and only their public halves are used.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { check, parseKeys } from "./release-keys.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const TOOL = path.join(HERE, "release-keys.mjs");
const REAL_UPDATE_RS = path.join(HERE, "..", "service", "src", "update.rs");
const pub = () => crypto.generateKeyPairSync("ed25519").publicKey.export({ format: "der", type: "spki" }).subarray(12).toString("hex");
const T1 = "ca934a560e296684883f80e107452d396a3bcfb0c2b16842b49ce1f7bd9911f8";
const T2 = "aab74fc72a965f8cda1f24780edb30e3ee4c3ee7659d25a2164db558c5c0db18";
const TEST = `pub const TEST_KEYS: &[Key] = &[
    Key { id: "t1", public: hex32("${T1}") },
    Key { id: "t2", public: hex32("${T2}") },
];`;
const PLACEHOLDER = `pub const PRODUCTION_KEYS: &[Key] = &[Key { id: "k1", public: [0; 32] }, Key { id: "k2", public: [0; 32] }];\n${TEST}`;
// exactly what tools/new-update-key.ps1 prints
const pasted = (a, b) => `pub const PRODUCTION_KEYS: &[Key] = &[
    Key { id: "k1", public: hex32("${a}") },
    Key { id: "k2", public: hex32("${b}") },
];
${TEST}`;
const bin = (...hexes) => Buffer.concat([crypto.randomBytes(4096), ...hexes.flatMap((h) => [Buffer.from(h, "hex"), crypto.randomBytes(64)])]);

test("the repository's update.rs parses, and its TEST_KEYS are the ones the CI release check knows", () => {
  const src = fs.readFileSync(REAL_UPDATE_RS, "utf8");
  assert.deepEqual(parseKeys(src, "TEST_KEYS").map((k) => k.hex), [T1, T2]);
  assert.deepEqual(parseKeys(src, "PRODUCTION_KEYS").map((k) => k.id), ["k1", "k2"]);
  assert.equal(check({ source: src }).ok, true);
});

test("placeholders: an unsigned build passes with a warning, a SIGNED build fails hard", () => {
  const unsigned = check({ source: PLACEHOLDER });
  assert.equal(unsigned.ok, true);
  assert.match(unsigned.notes.join(), /placeholders/);
  const signed = check({ source: PLACEHOLDER, requireReal: true });
  assert.equal(signed.ok, false);
  assert.match(signed.errors.join(), /all-zero placeholders/);
});

test("real pasted keys pass a signed build when the compiled service carries them", () => {
  const [a, b] = [pub(), pub()];
  const r = check({ source: pasted(a, b), binary: bin(a, b), requireReal: true });
  assert.deepEqual(r.errors, []);
  assert.equal(r.real, true);
});

test("a service not built from this update.rs (keys missing from the binary) fails a signed build", () => {
  const [a, b] = [pub(), pub()];
  const r = check({ source: pasted(a, b), binary: bin(a), requireReal: true });
  assert.equal(r.ok, false);
  assert.match(r.errors.join(), /doesn't contain k2/);
});

test("TEST keys are refused in PRODUCTION_KEYS and in a compiled public service", () => {
  assert.match(check({ source: pasted(T1, pub()), requireReal: true }).errors.join(), /k1 is a TEST key/);
  const r = check({ source: PLACEHOLDER, binary: bin(T2) });
  assert.equal(r.ok, false);
  assert.match(r.errors.join(), /contains TEST key t2/);
});

test("half a paste, the same key twice, filler and malformed entries are refused", () => {
  const a = pub();
  const half = `pub const PRODUCTION_KEYS: &[Key] = &[Key { id: "k1", public: hex32("${a}") }, Key { id: "k2", public: [0; 32] }];\n${TEST}`;
  assert.match(check({ source: half }).errors.join(), /only one of k1\/k2/);
  assert.match(check({ source: pasted(a, a), requireReal: true }).errors.join(), /same key/);
  assert.match(check({ source: pasted("11".repeat(32), pub()), requireReal: true }).errors.join(), /filler/);
  assert.equal(check({ source: pasted(a.toUpperCase(), pub()) }).ok, false, "upper-case hex doesn't compile (hex32 is lower-case only)");
  assert.equal(check({ source: pasted(a.slice(2), pub()) }).ok, false);
  const swapped = `pub const PRODUCTION_KEYS: &[Key] = &[Key { id: "k2", public: hex32("${a}") }, Key { id: "k1", public: hex32("${pub()}") }];\n${TEST}`;
  assert.match(check({ source: swapped }).errors.join(), /k1 then k2/);
  assert.equal(check({ source: "nothing here" }).ok, false);
});

test("command line: exit 1 and a GitHub ::error:: line for a signed build with placeholders; no key material printed", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kiwi-release-keys-"));
  try {
    const rs = path.join(dir, "update.rs");
    fs.writeFileSync(rs, PLACEHOLDER);
    const bad = spawnSync(process.execPath, [TOOL, "--source", rs, "--require-real"], { encoding: "utf8" });
    assert.equal(bad.status, 1);
    assert.match(bad.stdout, /::error::PRODUCTION_KEYS are still the all-zero placeholders/);
    const warn = spawnSync(process.execPath, [TOOL, "--source", rs], { encoding: "utf8" });
    assert.equal(warn.status, 0);
    assert.match(warn.stdout, /::warning::/);
    const [a, b] = [pub(), pub()];
    fs.writeFileSync(rs, pasted(a, b));
    const exe = path.join(dir, "svc.exe");
    fs.writeFileSync(exe, bin(a, b));
    const good = spawnSync(process.execPath, [TOOL, "--source", rs, "--binary", exe, "--require-real"], { encoding: "utf8" });
    assert.equal(good.status, 0, good.stdout);
    assert.doesNotMatch(good.stdout + good.stderr, new RegExp(`${a}|${b}|PRIVATE`));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
