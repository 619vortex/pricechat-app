// CI gate for the update keys (docs/hardening/WINDOWS-2.2.md). Reads only PUBLIC keys; there is no private key anywhere near it.
//
//   node ci/release-keys.mjs --source service/src/update.rs [--binary <kiwivpn-service.exe>] [--require-real]
//
// Always (exit 1 on failure):
//   - PRODUCTION_KEYS in update.rs is exactly k1 then k2, each the all-zero placeholder or 64 lower-case hex characters;
//     both placeholders or both real; real keys differ from each other, from TEST_KEYS, and aren't a filler such as "1111...";
//   - with --binary: the TEST_KEYS bytes are not in the compiled service (a public build never trusts test keys).
// With --require-real (every SIGNED build: the workflow passes it when "sign" is ticked):
//   - placeholders are a hard failure: a signed 2.2 with no real keys could never be updated automatically, and every user would
//     have to reinstall by hand once more (WINDOWS-2.2.md, "Bootstrap limit");
//   - with --binary: both real keys' bytes are in the compiled service, so the installer really carries the keys the source names.
// Without --require-real, placeholders only print a GitHub warning (unsigned test builds keep working until the owner pastes keys).
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SPKI_ED25519 = Buffer.from("302a300506032b6570032100", "hex");

export function parseKeys(text, name) {
  const block = new RegExp(`pub const ${name}: &\\[Key\\] = &\\[([\\s\\S]*?)\\];`).exec(text)?.[1];
  if (block === undefined) throw new Error(`${name} wasn't found`);
  const entries = [...block.matchAll(/Key\s*\{\s*id:\s*"([^"]*)",\s*public:\s*(hex32\("([^"]*)"\)|\[0;\s*32\]|[^}]*?)\s*\}/g)];
  const leftover = block.replace(/Key\s*\{[^}]*\}/g, "").replace(/[\s,]/g, "");
  if (leftover) throw new Error(`${name} has something that isn't a plain Key { id, public } entry: ${leftover.slice(0, 60)}`);
  return entries.map((m) => {
    if (m[3] !== undefined) {
      if (!/^[0-9a-f]{64}$/.test(m[3])) throw new Error(`${name} ${m[1]}: hex32 needs 64 lower-case hex characters`);
      return { id: m[1], hex: m[3] };
    }
    if (/^\[0;\s*32\]$/.test(m[2])) return { id: m[1], hex: null };
    throw new Error(`${name} ${m[1]}: public must be hex32("<64 hex>") or the [0; 32] placeholder, not ${m[2].trim().slice(0, 40)}`);
  });
}

export function check({ source, binary = null, requireReal = false }) {
  const errors = [];
  const notes = [];
  let prod;
  let test = [];
  try {
    prod = parseKeys(source, "PRODUCTION_KEYS");
  } catch (e) {
    return { ok: false, errors: [e.message], notes, real: false };
  }
  try {
    test = parseKeys(source, "TEST_KEYS");
  } catch (e) {
    errors.push(e.message);
  }
  if (prod.map((k) => k.id).join(",") !== "k1,k2") errors.push(`PRODUCTION_KEYS must be k1 then k2, found ${prod.map((k) => k.id).join(", ") || "nothing"}`);
  const set = prod.filter((k) => k.hex).length;
  const real = set > 0 && set === prod.length;
  if (set > 0 && set !== prod.length) errors.push("only one of k1/k2 is set: paste both lines new-update-key.ps1 printed");
  for (const k of prod.filter((k) => k.hex)) {
    if (test.some((t) => t.hex === k.hex)) errors.push(`${k.id} is a TEST key (its private half is in this repository)`);
    if (new Set(Buffer.from(k.hex, "hex")).size < 8) errors.push(`${k.id} looks like filler, not a generated key`);
    try {
      crypto.createPublicKey({ key: Buffer.concat([SPKI_ED25519, Buffer.from(k.hex, "hex")]), format: "der", type: "spki" });
    } catch {
      errors.push(`${k.id} isn't an Ed25519 public key`);
    }
  }
  if (real && prod[0].hex === prod[1].hex) errors.push("k1 and k2 are the same key");
  if (!real && set === 0) {
    if (requireReal) errors.push("PRODUCTION_KEYS are still the all-zero placeholders. A signed build without the owner's k1/k2 can never be updated automatically. Generate the keys offline (tools/new-update-key.ps1), paste the two public lines into service/src/update.rs, commit, sync, then sign.");
    else notes.push("PRODUCTION_KEYS are placeholders: this build offers no updates (fine for an unsigned test build; a signed build refuses to start)");
  }
  if (binary) {
    for (const t of test) if (t.hex && binary.includes(Buffer.from(t.hex, "hex"))) errors.push(`the compiled service contains TEST key ${t.id}: it was built with --features update-test`);
    if (requireReal && real) {
      for (const k of prod) if (!binary.includes(Buffer.from(k.hex, "hex"))) errors.push(`the compiled service doesn't contain ${k.id}: it wasn't built from this update.rs`);
    }
  }
  return { ok: errors.length === 0, errors, notes, real };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const argv = process.argv.slice(2);
  const opt = (n) => { const i = argv.indexOf(n); return i >= 0 ? argv[i + 1] : undefined; };
  const sourcePath = opt("--source");
  if (!sourcePath) { console.error("--source <update.rs> is required"); process.exit(2); }
  const binPath = opt("--binary");
  const requireReal = argv.includes("--require-real");
  const r = check({ source: fs.readFileSync(sourcePath, "utf8"), binary: binPath ? fs.readFileSync(binPath) : null, requireReal });
  for (const n of r.notes) console.log(`::warning::${n}`);
  for (const e of r.errors) console.log(`::error::${e}`);
  const what = r.real ? "real k1/k2 (public keys)" : "placeholders";
  console.log(`update keys: ${what}${binPath ? ", compiled service checked" : ""}${requireReal ? ", signed build" : ""}: ${r.ok ? "OK" : "FAILED"}`);
  process.exit(r.ok ? 0 : 1);
}
