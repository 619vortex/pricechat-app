// MAIN PC. Gives the GitHub Actions VPN tests their own Kiwi VPN app session, the same way the app signs in: your browser
// opens Price Chat's "Allow Kiwi VPN on this PC" page, you press Allow, and the session comes back to this script on
// 127.0.0.1. It's stored straight into the KIWIVPN repo as the encrypted Actions secret KIWI_CI_SESSION (sealed with
// the repo's public key, as GitHub requires). The token is never printed, logged or written to disk.
//
// The session is a desktop one: it can reach only the VPN API (backend/app/members/desktop_scope.py), lasts 90 days,
// and is signed out with every other app session by "Sign out Kiwi VPN on all PCs" on pricechat.live.
// Runs no Kiwi binary: Node only. Needs the user env var KIWI_GH_TOKEN with Secrets: read and write on KIWIVPN.
//
//   node windows-client\ci\session-tool\ci-session.mjs            (sign in and store the secret)
//   node windows-client\ci\session-tool\ci-session.mjs --probe    (only check the token can store secrets)
import { createHash, randomBytes } from "node:crypto";
import { createServer } from "node:http";
import { execFileSync, execSync, spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// libsodium-wrappers (GitHub's required secret sealing) is this tool's only dependency, pinned with integrity hashes in
// package-lock.json next to this file. A clean checkout doesn't have it yet, so install exactly that, here, once
// (npm ci: lockfile only, no install scripts).
const HERE = dirname(fileURLToPath(import.meta.url));
if (!existsSync(join(HERE, "node_modules", "libsodium-wrappers", "package.json"))) {
  console.log("First run: installing this tool's one dependency (libsodium-wrappers, pinned in package-lock.json)...");
  execSync("npm ci --no-audit --no-fund --ignore-scripts", { cwd: HERE, stdio: "inherit" }); // fixed command, no user input
}
const { default: sodium } = await import("libsodium-wrappers");

const SITE = "https://pricechat.live";
const REPO = "619vortex/KIWIVPN";
const NAME = "KIWI_CI_SESSION";

function ghToken() {
  let t = process.env.KIWI_GH_TOKEN;
  if (!t && process.platform === "win32") {
    t = execFileSync("powershell.exe", ["-NoProfile", "-Command", "[Environment]::GetEnvironmentVariable('KIWI_GH_TOKEN','User')"], { encoding: "utf8" }).trim();
  }
  if (!t) throw new Error("KIWI_GH_TOKEN isn't set.");
  return t;
}

async function gh(token, method, path, body) {
  const r = await fetch(`https://api.github.com/repos/${REPO}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "kiwi-ci-session" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`GitHub ${method} ${path}: HTTP ${r.status}`);
  return r.status === 204 ? null : r.json().catch(() => null);
}

async function putSecret(token, name, value) {
  await sodium.ready;
  const key = await gh(token, "GET", "/actions/secrets/public-key");
  const sealed = sodium.crypto_box_seal(sodium.from_string(value), sodium.from_base64(key.key, sodium.base64_variants.ORIGINAL));
  await gh(token, "PUT", `/actions/secrets/${name}`, { encrypted_value: sodium.to_base64(sealed, sodium.base64_variants.ORIGINAL), key_id: key.key_id });
}

const b64url = (buf) => buf.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

async function signIn() {
  const verifier = b64url(randomBytes(32));
  const challenge = b64url(createHash("sha256").update(verifier).digest());
  const state = b64url(randomBytes(24));
  const code = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { server.close(); reject(new Error("No answer from the browser in 5 minutes.")); }, 300_000);
    const server = createServer((req, res) => {
      const url = new URL(req.url, "http://127.0.0.1");
      const ok = req.method === "GET" && url.pathname === "/callback" && url.searchParams.get("state") === state && (url.searchParams.get("code") || "").length >= 20;
      res.writeHead(ok ? 200 : 400, { "Content-Type": "text/html; charset=utf-8" });
      res.end(ok ? "<p style='font:18px system-ui'>Done. The GitHub VPN tests are signed in. You can close this tab.</p>" : "<p>Not this one.</p>");
      if (ok) { clearTimeout(timer); server.close(); resolve(url.searchParams.get("code")); }
    });
    server.listen(0, "127.0.0.1", () => {
      const page = `${SITE}/vpn/desktop?challenge=${challenge}&port=${server.address().port}&state=${state}`;
      console.log("Opening your browser. On the Price Chat page, press Allow.");
      console.log(`If it doesn't open, paste this into the browser: ${page}`);
      spawn("cmd.exe", ["/c", "start", "", page.replace(/&/g, "^&")], { stdio: "ignore", detached: true }).unref();
    });
  });
  const r = await fetch(`${SITE}/api/members/desktop/token`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ code, verifier }) });
  if (!r.ok) throw new Error(`Price Chat refused the sign-in (HTTP ${r.status}). Run this again.`);
  const { token } = await r.json();
  if (!token || !token.startsWith("kd.")) throw new Error("Price Chat didn't return an app session.");
  return token;
}

if (process.argv.includes("--check")) {
  // offline self-test: the dependency loads and seals/opens like GitHub's secret encryption. No network, no token, no secret.
  await sodium.ready;
  const kp = sodium.crypto_box_keypair();
  const sealed = sodium.crypto_box_seal(sodium.from_string("check"), kp.publicKey);
  const opened = sodium.to_string(sodium.crypto_box_seal_open(sealed, kp.publicKey, kp.privateKey));
  if (opened !== "check") throw new Error("libsodium sealing self-test failed");
  console.log("OK: session tool ready (libsodium-wrappers loaded, sealing works). Nothing was sent or stored.");
  process.exit(0);
}

const token = ghToken();
if (process.argv.includes("--probe")) {
  await putSecret(token, "KIWI_CI_PROBE", "probe");
  await gh(token, "DELETE", "/actions/secrets/KIWI_CI_PROBE");
  console.log("OK: this GitHub token can store Actions secrets in KIWIVPN (test secret written and deleted).");
} else {
  const session = await signIn();
  await putSecret(token, NAME, session);
  console.log(`OK: stored as the Actions secret ${NAME} in ${REPO}. It was never shown or saved here.`);
}
