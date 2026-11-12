// node --test vpn-extension/store/cws.test.mjs   (a fake store API; nothing reaches Google)
import assert from "node:assert/strict";
import { createHash, createVerify, generateKeyPairSync } from "node:crypto";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import * as C from "./cws.mjs";

const ITEM = "jeadaadhacdikcbahliklcaaeaddeloa";
const { privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const PEM = privateKey.export({ type: "pkcs8", format: "pem" });
const KEY = { type: "service_account", client_email: "kiwi-release@example.iam.gserviceaccount.com", private_key: PEM, token_uri: "https://oauth2.googleapis.com/token" };

/** A fake fetch that records every call and answers from a table. */
function fakeFetch(answers = {}) {
  const calls = [];
  const f = async (url, init = {}) => {
    calls.push({ url, method: init.method || "GET", headers: init.headers || {}, body: init.body });
    const key = Object.keys(answers).find((k) => url.includes(k));
    const [status, body] = key ? answers[key] : [404, { error: { message: "no fake for " + url } }];
    return { ok: status < 300, status, json: async () => body, text: async () => JSON.stringify(body) };
  };
  f.calls = calls;
  return f;
}

test("versions compare like Chrome's and only ever go up", () => {
  assert.equal(C.compareVersions("1.0.3", "1.0.2"), 1);
  assert.equal(C.compareVersions("1.0", "1.0.0"), 0);
  assert.equal(C.compareVersions("1.0.10", "1.0.9"), 1);
  assert.equal(C.bump("1.0.3"), "1.0.4");
  assert.equal(C.bump("1.0.3", "minor"), "1.1.0");
  assert.equal(C.bump("1.0.3", "major"), "2.0.0");
  for (const bad of ["1.0.3-beta", "v1", "1.2.3.4.5", "70000.0", ""]) assert.throws(() => C.parseVersion(bad));
});

test("a version that isn't above both the live and the in-review one is refused", () => {
  const status = { publishedItemRevisionStatus: { state: "PUBLISHED", distributionChannels: [{ crxVersion: "1.0.0" }] },
                   submittedItemRevisionStatus: { state: "PENDING_REVIEW", distributionChannels: [{ crxVersion: "1.0.3" }] } };
  assert.match(C.versionProblem("1.0.3", status), /isn't higher than the store's 1.0.3/);
  assert.match(C.versionProblem("1.0.2", status), /bump/);
  assert.equal(C.versionProblem("1.0.4", status), null);
  assert.equal(C.versionProblem("1.0.0", {}), null);  // a brand-new item
});

test("only a real service-account key is accepted", () => {
  assert.throws(() => C.checkKey("not json"), /isn't JSON/);
  assert.throws(() => C.checkKey(JSON.stringify({ type: "authorized_user" })), /not a service-account key/);
  assert.throws(() => C.checkKey(JSON.stringify({ ...KEY, private_key: "abc" })), /PEM/);
  assert.equal(C.checkKey(JSON.stringify(KEY)).client_email, KEY.client_email);
});

test("the key is stored only in its protected form, and comes back intact", () => {
  const file = join(mkdtempSync(join(tmpdir(), "kiwi-key-")), "k.dpapi");
  const protect = (t) => "PROTECTED:" + Buffer.from(t).toString("base64");
  const unprotect = (t) => Buffer.from(t.replace("PROTECTED:", ""), "base64").toString();
  assert.equal(C.storeKey(JSON.stringify(KEY), file, protect), KEY.client_email);
  const onDisk = readFileSync(file, "utf8");
  assert.ok(!onDisk.includes("PRIVATE KEY") && !onDisk.includes(KEY.client_email));
  assert.equal(C.loadKey(file, unprotect).private_key, PEM);
  assert.throws(() => C.loadKey(join(tmpdir(), "missing-kiwi-key"), unprotect), /setup-key/);
});

test("the token request is a signed JWT for the Chrome Web Store scope only", async () => {
  const f = fakeFetch({ "oauth2.googleapis.com/token": [200, { access_token: "ya29.fake" }] });
  assert.equal(await C.accessToken(KEY, f), "ya29.fake");
  const jwt = new URLSearchParams(f.calls[0].body).get("assertion");
  const [h, b, sig] = jwt.split(".");
  const claims = JSON.parse(Buffer.from(b, "base64url"));
  assert.equal(claims.scope, "https://www.googleapis.com/auth/chromewebstore");
  assert.equal(claims.iss, KEY.client_email);
  assert.ok(claims.exp - claims.iat <= 600);
  assert.ok(createVerify("RSA-SHA256").update(`${h}.${b}`).verify(privateKey, sig, "base64url"));
  await assert.rejects(C.accessToken(KEY, fakeFetch({ "oauth2.googleapis.com/token": [400, { error: "invalid_grant" }] })), /refused the service account/);
});

test("upload sends the ZIP to the v2 upload address and never submits", async () => {
  const f = fakeFetch({ ":upload": [200, { uploadState: "SUCCEEDED", crxVersion: "1.0.3" }] });
  const s = new C.Store({ publisherId: "pub-123", itemId: ITEM, token: "t", fetchImpl: f });
  const zip = Buffer.from("PK fake zip");
  await s.upload(zip);
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].url, `https://chromewebstore.googleapis.com/upload/v2/publishers/pub-123/items/${ITEM}:upload`);
  assert.equal(f.calls[0].method, "POST");
  assert.equal(f.calls[0].headers.Authorization, "Bearer t");
  assert.equal(f.calls[0].body, zip);
  assert.ok(!f.calls.some((c) => c.url.includes(":publish")));
});

test("publishing is staged by default, never skips review, and stops on warnings", async () => {
  const f = fakeFetch({ ":publish": [200, { state: "PENDING_REVIEW" }] });
  const s = new C.Store({ publisherId: "pub-123", itemId: ITEM, token: "t", fetchImpl: f });
  await s.publish();
  assert.deepEqual(JSON.parse(f.calls[0].body), { publishType: "STAGED_PUBLISH", skipReview: false, blockOnWarnings: true });
  await s.publish({ staged: false });
  assert.equal(JSON.parse(f.calls[1].body).publishType, "DEFAULT_PUBLISH");
});

test("store errors are reported, not swallowed", async () => {
  const s = new C.Store({ publisherId: "pub-123", itemId: ITEM, token: "t", fetchImpl: fakeFetch({ ":fetchStatus": [403, { error: { message: "The caller does not have permission" } }] }) });
  await assert.rejects(s.status(), /403: The caller does not have permission/);
  assert.throws(() => new C.Store({ publisherId: "", itemId: ITEM, token: "t" }), /publisherId/);
  assert.throws(() => new C.Store({ publisherId: "p", itemId: "not-an-id", token: "t" }), /32-letter/);
});

test("an upload still in progress is followed until it finishes", async () => {
  let n = 0;
  const s = { status: async () => ({ lastAsyncUploadState: ++n < 3 ? "UPLOAD_IN_PROGRESS" : "SUCCEEDED" }) };
  assert.equal(await C.waitForUpload(s, { uploadState: "UPLOAD_IN_PROGRESS" }, { sleep: async () => {} }), "SUCCEEDED");
  assert.equal(await C.waitForUpload(s, { uploadState: "FAILED" }, { sleep: async () => {} }), "FAILED");
});

test("approval needs a person at a terminal typing the exact phrase for this package", async () => {
  const phrase = C.approvalPhrase("submit", "1.0.3", "baa0e083aa5448f4");
  assert.equal(phrase, "submit 1.0.3 baa0e083");
  await assert.rejects(C.confirmApproval(phrase, { isTTY: false, ask: async () => phrase }), /needs the owner at a terminal/);
  await assert.rejects(C.confirmApproval(phrase, { isTTY: true, ask: async () => "yes" }), /not approved/);
  await assert.rejects(C.confirmApproval(phrase, { isTTY: true, ask: async () => "submit 1.0.3 baa0e084" }), /not approved/);
  assert.equal(await C.confirmApproval(phrase, { isTTY: true, ask: async () => ` ${phrase} ` }), true);
});

test("the store's public key is found in a CRX header by the extension ID it produces", () => {
  const key = Buffer.from("a stand-in public key for the test");
  const id = [...createHash("sha256").update(key).digest("hex").slice(0, 32)].map((c) => String.fromCharCode(97 + parseInt(c, 16))).join("");
  const field = (n, bytes) => Buffer.concat([Buffer.from([(n << 3) | 2, bytes.length]), bytes]);
  const other = field(2, field(1, Buffer.from("someone else's key")));
  const header = Buffer.concat([other, field(2, Buffer.concat([field(1, key), field(2, Buffer.from("sig"))]))]);
  const len = Buffer.alloc(4); len.writeUInt32LE(header.length);
  const crx = Buffer.concat([Buffer.from("Cr24"), Buffer.from([3, 0, 0, 0]), len, header, Buffer.from("PK...")]);
  assert.equal(C.crxKeyFor(crx, id), key.toString("base64"));
  assert.throws(() => C.crxKeyFor(crx, ITEM), /no key/);
  assert.throws(() => C.crxKeyFor(Buffer.from("PK.."), id), /not a CRX/);
});
