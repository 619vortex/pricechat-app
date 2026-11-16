# Kiwi VPN / Price Chat: credential exposure audit and rotation plan

Date: 2026-11-14. Branch `feat/kiwi-hardening-claims` (from main a7d2081). **Read-only audit**: no credential was rotated,
tested against an API or read. Existence checks only (file present, variable length). No secret values appear here; where a
prefix helps identify a credential, at most 4 characters are shown.

Sources: gitleaks 8.30.1 (`--redact`), the security audit (`feat/kiwi-production:docs/kiwi-production/SECURITY-AUDIT.md`,
items O3, O4, O5, M5, M6, M7, L2-L5, L15, L17, L18, I1, L20) and the project notes.

## 1. gitleaks results

| Scan | Scope | Raw hits | Real secrets |
|---|---|---|---|
| Full history, `C:/AI/price-chat` (HEAD, 450 commits, and again with `--log-opts=--all`, 451 non-empty commits) | every commit on every branch | 25 | **1**: `worker/worker.env` line 3, `PRICECHAT_WORKER_TOKEN` (commit e8645ea) |
| Untracked + ignored files in 82 git worktrees under `C:/AI/pc-wt` and `D:/pc-wt` (`git ls-files --others`, node_modules / .venv / target / dist / build caches and binaries excluded), dir mode | 82 files | 0 | 0 |
| Non-git folders in `C:/AI/pc-wt` (`kiwi-app-screens`, `kiwi-release`, `kiwi-vpn-dashboard-preview`, `kiwi-vpn-get-screenshots`, `toolchain`), dir mode | whole folders, same excludes | 459 | 0 (all in `toolchain`: vendored Go/Rust crates, Go crypto test vectors, WireGuard man pages and test configs) |

Raw reports stay outside the repo in the session scratchpad (`gl/*.json`); they're redacted but still not committed.

**Triage of the 24 false positives in history**: WireGuard/X25519 test keys (`windows-client` tests and examples,
`ios-client` and `android-client` unit-test fixtures), the Chrome extension's public `key` in `vpn-extension/manifest.json`,
Hyperliquid token IDs from a recorded public API response (`fundarb/tests/fixtures`), Tether's published USDT contract
addresses (`backend/app/market/payments.py`, `docs/MARKETPLACE.md`), and dummy values in backend/frontend tests (eth-account
docs keys, a fake Telegram API hash, a fake OAuth signature). They're allowlisted by path in the new `.gitleaks.toml`;
with it, the full-history scan reports exactly one finding, the worker token.

**New finding: `worker/worker.env` is still tracked on main.** Commit 184a521 ("Stop tracking worker/worker.env") only
added it to `.gitignore`; it never ran `git rm --cached`, so the file (unchanged since e8645ea) is still in main a7d2081 and
in every one of the 87 worktree checkouts that contain it. Removing it from the index is a one-line change, but on merge it
deletes the file from the main checkout, which the running worker reads, so it's in the owner steps (R1) instead of this
branch.

Other files that hold secrets by design, present and ignored by git (not scanned, not read): `C:/AI/price-chat/.env`,
`C:/AI/price-chat/frontend/.env.local`, `C:/AI/price-chat/worker/worker.env`.

## 2. Inventory

"Still valid?": *known* = confirmed in use or not rotated since exposure; *unknown* = not checked (checking would need an API
call, which this audit doesn't make).

| ID | Credential (type) | Where it lives | How it was exposed | Blast radius | Still valid? | Owner action |
|---|---|---|---|---|---|---|
| C1 | `PRICECHAT_WORKER_TOKEN` (Brand Manager worker token, prefix `pcw_`) | `worker/worker.env` on the PC; **tracked in git** on main and in ~87 worktree checkouts | committed in e8645ea, never untracked | claim and complete Brand Manager / News Desk / Short Market worker jobs: upload images and media that go into **public posts** (X, Telegram, website), read job inputs | known (file unchanged since the commit and in use) | R1 |
| C2 | `APP_ENCRYPTION_KEY` (Fernet master key for DB secrets) | Railway `api` service variables | Railway platform breach 2026-10-21 ("revoke all env vars") | with a DB copy or backup: decrypt every stored secret (C4 tokens, members' X tokens, Hyperliquid API wallets, agent tokens) | known (no `APP_ENCRYPTION_KEY_PREVIOUS` on Railway as of 11-03, so never rekeyed) | R2 |
| C3 | `PORTFOLIO_S3_KEY_ID` / `PORTFOLIO_S3_SECRET` (Railway bucket `portfolio-photos`) | Railway `api` variables | Railway breach | read/write nightly DB backups (accounts, devices, payments, encrypted secrets) and portfolio photos; with C2 = readable secrets | known: on 11-12 the variable was re-synced from the *existing* bucket credentials to fix backups; the bucket key itself was not rotated | R2 |
| C4 | Breach list, at source: Google + X member OAuth client secrets, TikTok secrets, `PSA_API_KEY`, `ADMIN_PASSWORD_HASH`, Telegram bot token, Telethon session, X / YouTube / Resend / Anthropic / Combot keys, members' Hyperliquid API wallets | Railway variables and the DB (encrypted with C2) | Railway breach | post as Price Chat on Telegram/X/YouTube, read the Telethon account, send email as the domain, spend Anthropic credit, trade members' Hyperliquid accounts (API wallets can trade, not withdraw), offline brute force of the admin hash | unknown (rotation never confirmed) | R3 |
| C5 | Old Hetzner root password (personal VPS, Helsinki) | was pasted in chat | chat transcript | root on the host that carries the owner's own wg0 and the customers' wg1 + proxy | unknown (audit O3: still valid when last checked) | R4 |
| C6 | Old Hetzner Cloud API token | was pasted in chat | chat transcript | create/delete servers, firewalls, floating IPs, rebuild with a new root password | unknown (audit O3: still valid when last checked) | R4 |
| C7 | Germany server password SSH | sshd on the Germany proxy server | reported still enabled | password guessing against root | unknown | R4 |
| C8 | `HCLOUD_TOKEN` (Hetzner, read/write, 64 chars) | Windows user environment | used for read-only audits too (O4); readable by any process running as the user | same as C6 for the current project | known (set) | R5 |
| C9 | Node agent tokens (one per node, `TOKEN` in `/etc/kiwi-vpn/agent.env`) | each node; **plain copy in `/root/kiwi-node.txt`** (L20); encrypted in the DB (C2) | left on disk after registration; DB covered by the breach | add/remove WireGuard peers on that node (free VPN access, cutting customers off); port 8443 is limited to Railway's IPs by the Hetzner firewall | known | R6 |
| C10 | Android release signing key (`kiwi-release.keystore`, DPAPI-wrapped password) | `%USERPROFILE%\.kiwi-android-signing` | not exposed; **single copy, no backup** | loss = the app can never be updated under the same package; theft = signed malicious updates | n/a (present) | R7 |
| C11 | `KIWI_GH_TOKEN` (GitHub token, 93 chars, fine-grained PAT format) | Windows user environment | readable by any process running as the user | the private repo 619vortex/KIWIVPN (source, Actions, secrets write if granted) | known (set) | R8 |
| C12 | GitHub Actions secrets in 619vortex/KIWIVPN: `KIWI_CI_SESSION` (90-day VPN-only desktop session), eSigner `ES_*` (code signing), stray `ES_USERNAME_NAME` | GitHub repo secrets | stored in CI; any workflow change can read them | `KIWI_CI_SESSION`: one account's VPN scope only. `ES_*`: sign any binary as Kiwi VPN (OV certificate) | known | R8 |
| C13 | `CODEMAGIC_API_TOKEN` (43 chars) | Windows user environment | readable by any process running as the user | start iOS builds, read build artifacts and Codemagic-stored signing material | known (set) | R9 |
| C14 | iOS keys: `certificate_private_key`, deploy `push_key` / `read_key` (SSH pairs) | `%USERPROFILE%\.kiwi-ios-cloud` | not exposed; plaintext files | push/read the iOS cloud-build repo; sign iOS builds with the certificate key | known (present) | R9 |
| C15 | Chrome Web Store service account key (DPAPI) | expected at `%APPDATA%\KiwiVPN\cws-service-account.dpapi`: **not present** (the folder holds only firewall backups and known_hosts), so the owner setup hasn't been done or it lives elsewhere | not exposed | upload drafts; submit/publish the extension (the release tool refuses without a TTY) | unknown | R10 |
| C16 | Hetzner SSH keys (`id_ed25519`, `kiwi_vpn`) | `%USERPROFILE%\.ssh` | not exposed | root on the VPN nodes | known (present) | R10 |
| C17 | `PROXY_SHARED_SECRET` (64 chars) | Vercel (sensitive) + Railway `api` | set fresh 2026-11-12 after the breach; never printed | forge the "came through our website" header to Railway (client-IP trust) | known, fresh | none now; rotate yearly or if Vercel/Railway report an incident |

Related privacy items handled in Part B (`docs/hardening/PRIVACY-TEXT.md`): M6, M7, L5, I1, L17; L2-L4, L15, L18 stay code
or ops follow-ups (section 4).

## 3. Rotation plan, in order

Who: **Owner** = needs the owner's logins or approval; **Claude can prepare** = scripts, commands or code ready for the
owner to run; Claude never runs rotations or touches servers in this task.

### R1. Worker token (C1): untrack the file, then rotate. Urgent.
1. Back up `C:/AI/price-chat/worker/worker.env` outside the repo.
2. On main: `git rm --cached worker/worker.env` and commit (the file stays on disk; `.gitignore` already lists it). Restore
   from the backup if any checkout loses it.
3. Admin > Brand Manager > Settings > AI > Workers: add a worker for the PC (new `pcw_` token, shown once).
4. Put it in `worker/worker.env` (`PRICECHAT_WORKER_TOKEN=`), restart the worker scheduled task(s) (worker, News Desk images,
   World Monitor).
5. Disable, then delete, the old worker entry. If Railway has `BM_WORKER_BOOTSTRAP_SHA256` set to the old token's hash,
   remove it (deleting the bootstrap worker is final, so it can't re-register).
- Update together: the PC's worker.env + the Admin worker list. Downtime: worker jobs pause for ~1 minute; posts aren't lost.
- Verify: Admin shows the new worker online, the old one gone; the next News Desk slot gets its image; `gitleaks git . -c .gitleaks.toml --log-opts=-1` is clean.
- History still contains the old token: that's fine once it's disabled. Rewriting history isn't needed (no remote).
- Claude can prepare: the untrack commit. Owner: steps 3-5.
- **Prepared 2026-11-16: `ops/r1/rotate-worker-token.ps1`** does steps 1, 2 and 4 in one go once the owner has created the new worker
  (step 3): backup to `%USERPROFILE%\.pricechat-secrets` (owner-only ACL), `git rm --cached` on main (the file stays on disk: use this
  instead of merging `chore/untrack-worker-env`, which deletes it), the new token as hidden input (never printed), only the
  `PRICECHAT_WORKER_TOKEN` line replaced, the three workers restarted (News Desk GPU, World Monitor, AI worker) and checked for a 401.
  Run without `-Apply` first (preview, changes nothing). Then step 5 in Admin.

### R2. Railway breach, data at rest (C3 then C2). Urgent.
Order matters: bucket keys first, then the master key, then purge backups made under the old key.
1. **C3**: create new bucket credentials for `portfolio-photos` in Railway (or rotate the bucket's key), set
   `PORTFOLIO_S3_KEY_ID` / `PORTFOLIO_S3_SECRET` on `api` (same deploy). Verify: next `system.backup` succeeds, a portfolio
   photo upload works, the old key is gone from the bucket's credential list.
2. **C2**: generate a new Fernet key; set `APP_ENCRYPTION_KEY_PREVIOUS` = current value and `APP_ENCRYPTION_KEY` = new; one
   Railway deploy (~1-2 min restart). Startup rekey re-seals stored secrets; run `POST /api/system/rekey` (owner login) and
   confirm `failed = 0`. Then remove `APP_ENCRYPTION_KEY_PREVIOUS` and redeploy. Verify: connected X accounts, Telegram bot,
   VPN agent calls and Hyperliquid status still work.
3. **Purge old backups**: after a fresh backup under the new key is restore-verified, delete bucket backups dated up to and
   including the rekey day (they hold secrets readable with the old key). Keep the restore-tested new one.
- Downtime: two short Railway restarts. Claude can prepare: key generation command, the rekey check, a backup-listing script
  for the purge. Owner: Railway variables, deploys, deletion.

### R3. Railway breach, credentials at source (C4). High.
Revoke and reissue at each provider, then set the new value (Railway variable or Admin setting) in the same sitting:
Telegram bot token (BotFather `/revoke`, update the bot setting, webhook re-registers on deploy; bot offline ~1 min);
Telethon session (Telegram > Devices: end the session, log in again from Admin with the QR flow); Google and X member OAuth
client secrets (new secret in each console, update Railway; sign-in fails only between the two steps); X / YouTube API
keys; Resend key (test email after); Anthropic key (check usage for misuse first); Combot key; TikTok secrets; `PSA_API_KEY`;
new admin password (`ADMIN_PASSWORD_HASH`); members' Hyperliquid API wallets (pause live trading, announce, members
re-create API wallets; check for unexpected orders).
- Verify each with the feature it powers (bot answers, sign-in with Google/X works, test email arrives, Admin login).
- Owner only (provider consoles). Claude can prepare a checklist page and post-rotation smoke tests.

### R4. Hetzner leftovers (C5, C6, C7). High.
1. Hetzner console > Security > API tokens: delete every token except the current one (C8), and after R5 the new read-only one.
2. Helsinki: change the root password (or lock it: `passwd -l root` with key-only SSH confirmed working first).
3. All nodes, starting with Germany: `PasswordAuthentication no`, `KbdInteractiveAuthentication no`,
   `PermitRootLogin prohibit-password` in `/etc/ssh/sshd_config.d/`, `sshd -t`, reload; keep a second SSH session open while
   testing. Hetzner rescue console is the fallback.
- No customer downtime. Verify: `ssh -o PubkeyAuthentication=no root@<node>` is refused; token list shows only expected tokens.
- Owner (console + server access approval). Claude can prepare the sshd drop-in and a check script.

### R5. Read-only Hetzner token for audits (C8). Medium (O4).
Create a **Read** token in the project; store it as `HCLOUD_TOKEN_RO` (user env or DPAPI file); point `kiwi-infra.mjs`
audit/health and monitoring at it. Move the read/write token out of the always-on user environment (DPAPI file, loaded only
for owner-run `create`/firewall changes), then rotate it once since it has been readable by every user process.
- No downtime. Verify: an audit run works with RO; a write call with RO is refused (owner checks in the console, not by test).
- Claude can prepare the `kiwi-infra.mjs` change to prefer `HCLOUD_TOKEN_RO`. Owner creates the tokens.

### R6. Node agent tokens (C9). Medium.
Per node: delete `/root/kiwi-node.txt`; generate a new token (`openssl rand -hex 32`); write it to
`/etc/kiwi-vpn/agent.env` `TOKEN=`, restart the agent, then immediately paste it into Admin > VPN > server > agent token.
- Update together: node agent.env + Admin (the DB copy). Do it after R2 so the DB copy is sealed with the new key.
- Downtime: device add/remove and usage sync for that node fail for the minute between restart and Admin save; existing
  tunnels and the proxy keep working.
- Verify: Admin health shows the server online; add and delete a test device. Owner (server access). Claude can prepare the
  per-node script and the installer change (L20) to stop writing the file.

### R7. Android signing key backup (C10). Medium, but losing it is irreversible.
Back up `kiwi-release.keystore` **and** its password to two offline places (password manager + encrypted USB). The password
file is DPAPI-bound to this Windows account, so a copy of it alone is useless on another PC: the owner must export the
plain password into the password manager. Consider enrolling in Play App Signing (Google holds the app key; the local key
becomes an upload key that can be reset). Verify: restore to a scratch folder and run `keytool -list` with the backed-up password. Owner only.

### R8. GitHub (C11, C12). Medium.
Delete the stray `ES_USERNAME_NAME` secret. Re-create `KIWI_GH_TOKEN` as a fine-grained PAT limited to 619vortex/KIWIVPN with
only the permissions `sync-ci-repo.ps1` / `ci-fetch.ps1` need (contents, actions read), with an expiry; keep it out of the
always-on user env (DPAPI file). Rotate `KIWI_CI_SESSION` before its 90 days end (sign out that session in the app's
"sign out everywhere" first). eSigner: require approval on the signing workflow (environment protection) so a workflow edit
can't sign arbitrary binaries. Verify: the next CI run fetches and signs. Owner (GitHub settings). Claude can prepare the
workflow environment change.

### R9. Codemagic and iOS keys (C13, C14). Low.
Rotate `CODEMAGIC_API_TOKEN` when the iOS work resumes and store it DPAPI-wrapped; the deploy keys (`push_key`, `read_key`)
should be per-repo deploy keys with read-only where possible; the certificate private key should move into Codemagic's
encrypted store or a password-protected .p12. Owner.

### R10. Chrome Web Store and SSH keys (C15, C16). Low.
CWS: when the owner does setup, give the service account only the publisher role on this item; keep the DPAPI file; never the
plain JSON. SSH: put a passphrase on `kiwi_vpn` / `id_ed25519` (ssh-agent for sessions); list authorised keys on each node
and remove unknown ones. Owner.

## 4. One-off hardening

- **gitleaks config + pre-commit hook** (added on this branch): `.gitleaks.toml` (default rules + the triaged allowlist) and
  `.pre-commit-config.yaml` (gitleaks v8.30.1 on staged changes). Not installed anywhere: the owner runs
  `pre-commit install` per clone if wanted. Manual check: `gitleaks git . --redact -c .gitleaks.toml`.
- **Read-only Hetzner token**: R5.
- **Purge old backups** made under the old encryption key: R2 step 3.
- **Android key backup**: R7.
- Follow-ups from the audit still open (code or ops, not in this branch): L20 stop writing `/root/kiwi-node.txt`; L18 7-day
  journald + `ufw logging off` on the shared and proxy hosts; L2/L3 scrub members' payment addresses and refs; L4 coarsen
  `last_handshake` / `last_active`; L15 move the extension's proxy login to `storage.session`; O5 replace real server IPs in
  old tests with 192.0.2.x.
