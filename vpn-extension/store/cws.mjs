// Chrome Web Store API v2 for Kiwi VPN: credentials, the store's view of the item, uploads and (owner-approved) submissions.
// No dependencies: Node 20+ (fetch, crypto). Used by release.mjs; tested by cws.test.mjs with a fake API.
import { createHash, createSign } from "node:crypto";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

export const SCOPE = "https://www.googleapis.com/auth/chromewebstore";
export const API = "https://chromewebstore.googleapis.com";
export const TOKEN_URL = "https://oauth2.googleapis.com/token";

/** Where the encrypted service-account key lives: outside the repo, in this Windows user's profile, readable only by this user (DPAPI). */
export const KEY_FILE = join(process.env.APPDATA || join(homedir(), "AppData", "Roaming"), "KiwiVPN", "cws-service-account.dpapi");

// ----- versions -----

/** "1.0.3" -> [1,0,3]; Chrome allows 1-4 dot-separated integers 0..65535. */
export function parseVersion(v) {
  if (typeof v !== "string" || !/^\d{1,5}(\.\d{1,5}){0,3}$/.test(v)) throw new Error(`not a Chrome extension version: ${v}`);
  const parts = v.split(".").map(Number);
  if (parts.some((n) => n > 65535)) throw new Error(`version part over 65535: ${v}`);
  return parts;
}

export function compareVersions(a, b) {
  const x = parseVersion(a), y = parseVersion(b);
  for (let i = 0; i < 4; i++) { const d = (x[i] ?? 0) - (y[i] ?? 0); if (d) return Math.sign(d); }
  return 0;
}

export function bump(v, part = "patch") {
  const [maj, min = 0, pat = 0] = parseVersion(v);
  if (part === "major") return `${maj + 1}.0.0`;
  if (part === "minor") return `${maj}.${min + 1}.0`;
  if (part === "patch") return `${maj}.${min}.${pat + 1}`;
  throw new Error(`bump major, minor or patch, not ${part}`);
}

/** The store versions a new upload has to beat: what's live and what's waiting in review. */
export function storeVersions(status) {
  const pick = (rev) => (rev?.distributionChannels ?? []).map((c) => c.crxVersion).filter(Boolean);
  return { live: pick(status?.publishedItemRevisionStatus), submitted: pick(status?.submittedItemRevisionStatus),
           liveState: status?.publishedItemRevisionStatus?.state ?? null, submittedState: status?.submittedItemRevisionStatus?.state ?? null };
}

/** Why `version` can't be uploaded over what the store has, or null. The store refuses a version that isn't higher. */
export function versionProblem(version, status) {
  const { live, submitted } = storeVersions(status);
  const top = [...live, ...submitted].sort(compareVersions).at(-1);
  if (top && compareVersions(version, top) <= 0) return `version ${version} isn't higher than the store's ${top}: bump it`;
  return null;
}

// ----- the package -----

export const sha256 = (buf) => createHash("sha256").update(buf).digest("hex");

/** What the owner types to approve one exact package: the version and the start of its SHA-256. */
export const approvalPhrase = (action, version, hash) => `${action} ${version} ${hash.slice(0, 8)}`;

// ----- credentials -----

function powershell(script, input) {
  const r = spawnSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], { input, encoding: "utf8", maxBuffer: 1 << 20 });
  if (r.status !== 0) throw new Error(`Windows DPAPI failed: ${(r.stderr || "").trim().split("\n")[0]}`);
  return r.stdout.trim();
}
const DPAPI = (fn) => `Add-Type -AssemblyName System.Security; $s=[Console]::In.ReadToEnd(); ${fn}`;
export const dpapi = {
  protect: (text) => powershell(DPAPI("$b=[Text.Encoding]::UTF8.GetBytes($s); [Convert]::ToBase64String([Security.Cryptography.ProtectedData]::Protect($b,$null,'CurrentUser'))"), text),
  unprotect: (b64) => powershell(DPAPI("$b=[Convert]::FromBase64String($s.Trim()); [Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect($b,$null,'CurrentUser'))"), b64),
};

/** A downloaded service-account key, checked enough that a wrong file is refused before it's stored. */
export function checkKey(json) {
  let k;
  try { k = JSON.parse(json); } catch { throw new Error("that file isn't JSON"); }
  if (k.type !== "service_account") throw new Error(`that's a "${k.type}" credential, not a service-account key`);
  for (const f of ["client_email", "private_key", "token_uri"]) if (!k[f]) throw new Error(`the key has no ${f}`);
  if (!/-----BEGIN PRIVATE KEY-----/.test(k.private_key)) throw new Error("the key's private_key isn't a PEM key");
  return k;
}

export function storeKey(json, file = KEY_FILE, protect = dpapi.protect) {
  const k = checkKey(json);
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, protect(json));
  return k.client_email;
}

export function loadKey(file = KEY_FILE, unprotect = dpapi.unprotect) {
  if (!existsSync(file)) throw new Error(`no store credentials yet: run "node vpn-extension/store/release.mjs setup-key <downloaded key.json>"`);
  return checkKey(unprotect(readFileSync(file, "utf8")));
}

const b64url = (x) => Buffer.from(x).toString("base64url");

/** A signed JWT for Google's token endpoint (the documented service-account flow), valid for 10 minutes. */
export function jwtFor(key, now = Math.floor(Date.now() / 1000)) {
  const head = b64url(JSON.stringify({ alg: "RS256", typ: "JWT" }));
  const body = b64url(JSON.stringify({ iss: key.client_email, scope: SCOPE, aud: key.token_uri || TOKEN_URL, iat: now, exp: now + 600 }));
  const sig = createSign("RSA-SHA256").update(`${head}.${body}`).sign(key.private_key, "base64url");
  return `${head}.${body}.${sig}`;
}

export async function accessToken(key, fetchImpl = fetch) {
  const r = await fetchImpl(key.token_uri || TOKEN_URL, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer", assertion: jwtFor(key) }).toString() });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.access_token) throw new Error(`Google refused the service account (${r.status} ${j.error || ""} ${j.error_description || ""}`.trim() + ")");
  return j.access_token;
}

// ----- the store -----

/** One item on the store, through API v2. Upload never submits; publish is a separate, explicit call. */
export class Store {
  constructor({ publisherId, itemId, token, fetchImpl = fetch }) {
    if (!/^[\w-]+$/.test(publisherId || "")) throw new Error("set publisherId in vpn-extension/store/cws.json (Developer Dashboard > Publisher > Settings)");
    if (!/^[a-p]{32}$/.test(itemId || "")) throw new Error("itemId must be the 32-letter extension ID");
    Object.assign(this, { name: `publishers/${publisherId}/items/${itemId}`, token, fetchImpl });
  }

  async call(method, url, body, headers = {}) {
    const r = await this.fetchImpl(url, { method, body, headers: { Authorization: `Bearer ${this.token}`, ...headers } });
    const text = await r.text();
    let j = {};
    try { j = text ? JSON.parse(text) : {}; } catch { j = { raw: text.slice(0, 300) }; }
    if (!r.ok) throw new Error(`store API ${r.status}: ${j.error?.message || j.raw || "no detail"}`);
    return j;
  }

  status() { return this.call("GET", `${API}/v2/${this.name}:fetchStatus`); }

  /** Replaces the item's draft with this ZIP. It does not submit anything for review. */
  upload(zip) { return this.call("POST", `${API}/upload/v2/${this.name}:upload`, zip, { "Content-Type": "application/zip" }); }

  /** Submits the uploaded draft for review. STAGED_PUBLISH (the default here) waits after approval for a separate go-live. */
  publish({ staged = true } = {}) {
    return this.call("POST", `${API}/v2/${this.name}:publish`, JSON.stringify({ publishType: staged ? "STAGED_PUBLISH" : "DEFAULT_PUBLISH", skipReview: false, blockOnWarnings: true }),
      { "Content-Type": "application/json" });
  }
}

/** Waits out UPLOAD_IN_PROGRESS by polling fetchStatus, as the API docs say. */
export async function waitForUpload(store, first, { tries = 30, every = 4000, sleep = (ms) => new Promise((r) => setTimeout(r, ms)) } = {}) {
  let state = first?.uploadState;
  for (let i = 0; state === "UPLOAD_IN_PROGRESS" && i < tries; i++) {
    await sleep(every);
    state = (await store.status()).lastAsyncUploadState ?? state;
  }
  return state;
}

// ----- the published package's public key, so tests can run the exact ZIP under the store's ID -----

/** The store's public key for an item: from its CRX's header (CRX3 protobuf), whose SHA-256 gives the extension ID. */
export function crxKeyFor(crx, itemId) {
  if (crx.subarray(0, 4).toString() !== "Cr24") throw new Error("not a CRX file");
  const header = crx.subarray(12, 12 + crx.readUInt32LE(8));
  const fields = function* (buf) {
    let i = 0;
    const varint = () => { let n = 0, s = 0, c; do { c = buf[i++]; n += (c & 0x7f) * 2 ** s; s += 7; } while (c & 0x80); return n; };
    while (i < buf.length) {
      const k = varint(), f = Math.floor(k / 8), t = k & 7;
      if (t === 2) { const n = varint(); yield [f, buf.subarray(i, i + n)]; i += n; } else if (t === 0) varint(); else throw new Error("unexpected CRX field");
    }
  };
  for (const [f, proof] of fields(header)) {
    if (f !== 2) continue;  // sha256_with_rsa proofs
    for (const [g, key] of fields(proof)) {
      if (g !== 1) continue;
      const id = [...sha256(key).slice(0, 32)].map((c) => String.fromCharCode(97 + parseInt(c, 16))).join("");
      if (id === itemId) return key.toString("base64");
    }
  }
  throw new Error(`no key in that CRX gives ${itemId}`);
}

// ----- the owner's approval -----

/** Only a person at a real terminal can approve: they must type the exact phrase for this package. Automation (no TTY) is refused. */
export async function confirmApproval(expected, { isTTY = !!process.stdin.isTTY, ask } = {}) {
  if (!isTTY) throw new Error(`refused: "${expected.split(" ")[0]}" needs the owner at a terminal (run it yourself, e.g. with "!" in Claude Code)`);
  const typed = (await ask(`Type exactly "${expected}" to approve, anything else cancels: `)).trim();
  if (typed !== expected) throw new Error("not approved: nothing was sent to the store");
  return true;
}

// ----- the store's public view, without credentials -----

/** The live version and the CRX download address, from the public update check every Chrome install uses. */
export async function publicUpdate(itemId, fetchImpl = fetch) {
  const url = `https://clients2.google.com/service/update2/crx?response=updatecheck&prodversion=140.0&acceptformat=crx3&x=${encodeURIComponent(`id=${itemId}&uc`)}`;
  const xml = await (await fetchImpl(url)).text();
  const m = /<updatecheck[^>]*codebase="([^"]+)"[^>]*version="([^"]+)"/.exec(xml) || /<updatecheck[^>]*version="([^"]+)"[^>]*codebase="([^"]+)"/.exec(xml);
  if (!m) return null;  // not published (or not public)
  const unxml = (x) => x.replace(/&amp;/g, "&").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">");
  const out = m[0].indexOf("codebase") < m[0].indexOf("version") ? { codebase: m[1], version: m[2] } : { version: m[1], codebase: m[2] };
  return { version: out.version, codebase: unxml(out.codebase) };
}
