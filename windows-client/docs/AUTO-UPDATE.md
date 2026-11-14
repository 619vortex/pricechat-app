# Kiwi VPN for Windows: signed auto-update (design)

Status: **built in 2.2** (phases 1-5 and the owner tools; see [KIWI-2.2.md](KIWI-2.2.md) for what differs from this design and
what isn't built yet: the "Publish update" workflow, weekly re-sign, update lock, telemetry). The production manifest keys are
still placeholders until the owner makes them (`tools/new-update-key.ps1`). Applies to 2.x onward. The public 0.1.1 download
(`https://pricechat.live/downloads/KiwiVPN-Setup.exe`) has no updater and stays untouched; 0.1.1 users move to 2.x by
installing it by hand, as the CI's "Upgrade from the public 0.1.1" job already tests.

## Goals and rules

1. Only an installer **Authenticode-signed by the owner's SSL.com OV certificate** (thumbprint pinned) is ever run.
2. Only an installer named by a **manifest signed with Kiwi's own Ed25519 key** is ever fetched.
3. **Never a lower version**, never an old manifest replayed, never a manifest past its expiry.
4. **The SYSTEM service verifies and installs.** The app (which runs as the person) only shows status and asks. It can't
   name a URL, a path or a file, so a program running as the person can't use the updater to run anything.
5. **Nobody is left unprotected without knowing it.** An update stops the service, which drops the tunnel and the kill
   switch's WFP filters (dynamic session). So it happens when no tunnel is wanted, or after the person agrees.
6. No change to the signing setup in SIGNING.md: builds are signed exactly as today; publishing is a separate step.

## Two signatures, and why both

| Check | Proves | Held where | Alone it fails when |
|---|---|---|---|
| Authenticode + pinned thumbprint on the installer | This exact file was built and signed by the owner's certificate | SSL.com eSigner, used only from GitHub environment `windows-signing` | Signing credentials stolen: attacker signs malware |
| Ed25519 signature on the manifest | The owner chose to release this version, with this hash, to this channel, now | GitHub environment `kiwi-publish` + offline backup | Manifest key stolen: attacker can point at any file |

An attacker needs **both** to run their own code through the updater. The manifest key alone only lets them offer
genuine signed Kiwi builds at version >= the installed one; the signing credentials alone get them nothing, because
no client fetches a file the manifest doesn't name and hash.

### Manifest signature: own Ed25519 vs Tauri's updater (minisign)

| | `tauri-plugin-updater` | Own Ed25519 manifest (chosen) |
|---|---|---|
| What is signed | each installer file (minisign `.sig`); `latest.json` itself is unsigned | the whole manifest (version, hash, size, URL, rollout, expiry) |
| Who checks and installs | the app, as the person; a perMachine NSIS installer then needs a UAC prompt | the SYSTEM service, silently |
| Authenticode pin | none | yes |
| Downgrade / freeze protection | only "newer than me" per run; old `latest.json` replays fine | stored highest version and manifest sequence; expiry |
| Staged rollout, pause | needs a dynamic server endpoint | fields in the signed manifest; static hosting works |
| Tooling on this PC | `minisign` / `tauri signer` binaries are unsigned: Smart App Control blocks them | OpenSSL from Git for Windows (signed) makes and uses Ed25519 keys |

The plugin puts the decision in the weak process (LOCAL-SECURITY.md: anything running as the person can drive the
app) and verifies the file, not the release. So: **Ed25519 (RFC 8032) over the manifest bytes**, verified in the
service with `ed25519-dalek` (`verify_strict`). No Tauri updater plugin, no updater code in the app.

## The manifest

Served at `https://pricechat.live/downloads/kiwi/windows/stable.json` (and `test.json` for the test channel). The file
is an envelope; the service checks the signature **before** parsing the payload.

```json
{ "key_id": "k1", "payload": "<base64 of the manifest bytes>", "sig": "<base64 Ed25519 signature>" }
```

The signature covers `"kiwi-update-v1\n" || payload bytes` (a fixed prefix so the key can't be tricked into signing
something meant for another purpose, such as the planned signed server list). Payload:

```json
{
  "schema": 1,
  "product": "kiwivpn-windows",
  "channel": "stable",
  "seq": 42,
  "signed_at": "2026-12-01T10:00:00Z",
  "expires_at": "2026-12-15T10:00:00Z",
  "release": {
    "version": "2.0.1",
    "min_version": "2.0.0",
    "security": false,
    "url": "https://pricechat.live/downloads/kiwi/windows/KiwiVPN-2.0.1-Setup.exe",
    "size": 9437184,
    "sha256": "…64 hex…",
    "notes": "Fixes reconnect after sleep.",
    "rollout_bp": 1000,
    "paused": false
  },
  "pin_add": [],
  "revoke_keys": []
}
```

| Field | Rule in the service |
|---|---|
| `product`, `channel` | Must equal the build's own (`kiwivpn-windows`, `stable`), so a test manifest can't be served to real installs |
| `seq` | Integer, increases with every signing. Rejected if lower than the stored `ManifestSeq`; equal is fine (same file re-fetched) |
| `signed_at`, `expires_at` | Rejected if `expires_at` has passed or `signed_at` is more than 1 day in the future (local clock). Expiry at most 30 days after signing; we use 14 |
| `version` | Plain `major.minor.patch`, numbers only, no pre-release tags. Installed only if higher than both the running version and `HighestCommitted` |
| `min_version` | Installs below it are told an update is required (shown prominently); combine with `security` for urgency |
| `security` | Allows installing without a click, under the rules in "Tunnel during an update" |
| `url` | `https://pricechat.live/` + path only, no query, no port, no userinfo. Host compared exactly |
| `size` | Must be 1 MB to 100 MB, and the download must be exactly this size |
| `sha256` | Of the installer; checked while streaming and again on the verified copy |
| `notes` | Plain text, at most 2,000 characters, shown as text (never HTML) |
| `rollout_bp` | Basis points 0-10000. Installs whose bucket is below it are offered the update |
| `paused` | `true` stops all new installs of this release (the kill switch for a bad release) |
| `pin_add`, `revoke_keys` | Key and certificate rotation, see below. Usually empty |

Unknown fields are ignored (forward compatible); a higher `schema` than the build knows is rejected.

## Pinned signer and certificate renewal

The service accepts an installer only if all of these hold, checked on the SYSTEM-only copy:

1. `WinVerifyTrust` with `WINTRUST_ACTION_GENERIC_VERIFY_V2`, whole-chain revocation check, no UI, file passed **by
   handle**. Revocation unknown (offline) = not accepted; try later.
2. The primary signer's certificate SHA-1 thumbprint is in the pin set. Starting set, compiled into
   `kiwivpn-service.exe`:
   `0C24831BCE28841DC6C31BCFB326C0624A283559` (owner's SSL.com OV certificate). Phase 0 also records that
   certificate's SHA-256 hash from a signed build, and the service pins both (SHA-1 is how the owner and SSL.com refer
   to it; SHA-256 removes any doubt about SHA-1).
3. The signature is timestamped (RFC 3161, as `sign.ps1` already demands), so it stays valid after the certificate
   expires.
4. The file's version resource matches the manifest's `version`, and its SHA-256 and size match the manifest.

Renewal (SSL.com issues a new certificate, which always has a new thumbprint):

| Step | When | What |
|---|---|---|
| 1 | 60+ days before the old certificate expires | Owner renews; run "Check certificate" in CI to read the new thumbprint |
| 2 | Next release | Build N, **still signed with the old certificate**, ships the pin set {old, new} |
| 3 | When most installs report N (or 30 days) | Switch `ES_CREDENTIAL_ID` to the new certificate; releases are signed with the new one |
| 4 | A later release | Drop the old thumbprint from the compiled set |

If step 2 was missed (the old certificate already expired or was revoked), the manifest's `pin_add` adds a thumbprint:
`[{"sha1": "...", "sha256": "...", "valid_until": "..."}]`. It is honoured only in a manifest signed with a currently
trusted manifest key, is stored in the protected state below, and can only **add**. Removing a pin needs a new build.
Installs older than the pin addition that never see such a manifest update by hand.

## Manifest key: creation, storage, rotation

- **Two keys from the start**: `k1` (signs every manifest) and `k2` (spare, never online). Both public keys are
  compiled into the service.
- **Created by the owner** on the main PC with OpenSSL from Git for Windows (signed, Smart App Control accepts it):
  `openssl genpkey -algorithm ed25519 -out k1.pem`, then `openssl pkey -in k1.pem -pubout`. Never by an agent, never
  in CI logs.
- **k1** goes into a new GitHub environment **`kiwi-publish`** (custom branch policy `main`, like `windows-signing`)
  as secret `KIWI_MANIFEST_KEY`. Separate from `windows-signing` on purpose: signing a build and releasing it to every
  user are different decisions, and a leak of one secret set shouldn't hand over both.
- **Offline backup**: k1 and k2 private keys in the owner's password manager, plus an encrypted USB stick (e.g. a
  7-Zip AES-256 archive with a passphrase kept apart). Then the `.pem` files are deleted from the PC.
- **Rotation (planned or after a leak)**: CI starts signing with k2; that manifest carries `revoke_keys: ["k1"]`. A
  key may only revoke a key **earlier** in the order (k2 revokes k1, never the reverse), so a stolen k1 can't lock out
  k2. Revocations are stored in the protected state. The next release ships {k2, k3} with a fresh offline k3.

**Weak spot, stated plainly:** the `619vortex` GitHub account reaches both `windows-signing` and `kiwi-publish`.
Protect it with a hardware security key, keep the deploy-key-only push rule on `main`, and add required reviewers
when the plan allows (SIGNING.md). Stronger option for later: the owner signs manifests on his own PC and CI only
re-signs freshness (see "Publishing").

## Protected state (rollback and freeze protection)

Stored in **`HKLM\SOFTWARE\KiwiVPN\Update`**, written **only by the service** (SYSTEM). Not under
`HKLM\SYSTEM\CurrentControlSet\Services\KiwiVPN`, because the installer's `kiwivpn-service uninstall` deletes the
service (and its key) on every upgrade. The service sets the key's DACL on first use: SYSTEM and Administrators full
control, Users read. Administrators can always change it; that's above every boundary here (LOCAL-SECURITY.md).

| Value | Meaning |
|---|---|
| `HighestCommitted` | Highest version that installed **and** passed the health check. Never lowered by the service |
| `ManifestSeq`, `ManifestSignedAt` | Highest `seq` accepted, and its `signed_at` |
| `Pending` | `{version, sha256, started_at, attempts}` while an install is in flight |
| `InstallId` | 128 random bits, made once; used only for the rollout bucket, never sent anywhere |
| `PinAdd`, `RevokedKeys` | From signed manifests |

The installer never writes these. A full uninstall removes the key (it's an administrator action anyway). A version
offered by a manifest must be higher than **both** the running exe's version and `HighestCommitted`; the repair copy
(below) is the only lower-or-equal version the service may run, and only one equal to `HighestCommitted`.

## Network rules

- HTTPS only, TLS 1.2+, normal Windows certificate validation (no custom CA, no pinning of the web certificate: the
  manifest signature already covers integrity).
- Host allowlist: exactly `pricechat.live`. **Redirects are not followed**; a 3xx is a failure. Phase 0 checks that
  Vercel serves these paths directly (no apex/www redirect).
- Limits: manifest at most 64 KB; installer exactly `size`, at most 100 MB, cut off at the first extra byte;
  30-second connect timeout; resumable download is not needed.
- The service checks at start, every 6 hours (with random jitter of up to an hour), and when the app asks (at most
  once per 10 minutes). This is a new network role for the service; ARCHITECTURE.md's "the service fetches nothing"
  becomes "fetches only the update manifest and installer from `pricechat.live`".

## Who does what

```
KiwiVPN.exe (person)                    Kiwi VPN service (SYSTEM)
  UpdateStatus  ───────────────────►    reads manifest, verifies, reports: none / available / required / downloading /
                                        ready / installing / failed (+ notes, version)
  UpdateInstall {consent} ─────────►    accepted only from KiwiVPN.exe in its own folder or an administrator (same rule
                                        as Connect); no URL, path or file in the request
```

Install sequence in the service:

1. Fetch and verify the manifest (signature, product, channel, seq, expiry, version, rollout, paused).
2. Download into `C:\Program Files\KiwiVPN\updates\download.part` (folder created by the service with an explicit
   DACL: SYSTEM and Administrators only, no inheritance; the service checks the DACL and owner before each use),
   hashing while streaming. Wrong size or hash: delete, fail.
3. Rename to `KiwiVPN-<version>-Setup.exe`, open it with **share mode read only** (no write, no delete) and keep that
   handle open until the installer has started. With the handle: hash again, `WinVerifyTrust` by handle, pinned
   thumbprint, version resource. This closes the time-of-check/time-of-use gap: the file that was checked is the file
   that runs, and nobody else can write to the folder anyway.
4. Write `Pending`; create a one-shot SYSTEM scheduled task `KiwiVPN Update Recovery` (in 10 minutes and at boot)
   that runs `C:\Program Files\KiwiVPN\kiwivpn-service.exe recover`.
5. Start the installer as SYSTEM: `KiwiVPN-<version>-Setup.exe /S`, with `CREATE_BREAKAWAY_FROM_JOB` and detached, so
   it survives the installer's own pre-install step stopping and deleting the service that started it.
6. The new service starts (NSIS post-install runs `kiwivpn-service install`), sees `Pending`, and runs the health
   check: its own version equals `Pending.version`, the pipe answers, and, if a tunnel was wanted, it reconnects
   within 2 minutes. Pass: set `HighestCommitted`, clear `Pending`, delete the recovery task, keep this installer as
   the **repair copy** and delete the older one.
7. The app was closed by the installer (Tauri's NSIS closes running copies). The service starts
   `C:\Program Files\KiwiVPN\KiwiVPN.exe` again in the signed-in person's session (`WTSQueryUserToken` +
   `CreateProcessAsUserW`, fixed path only). The installer itself must never start the app: it would run as SYSTEM.

### Installer changes this needs (`app/src-tauri/windows/hooks.nsh`)

- The post-install `MessageBox` gets `/SD IDOK`. Without it a silent install started by the service waits forever
  for a click nobody can see (session 0).
- Upgrades keep `tunnels\` (the wanted tunnel and its `.guard` file), so the new service brings the tunnel and kill
  switch back by itself. Today `kiwivpn-service uninstall` deletes it on every upgrade. Add
  `kiwivpn-service stop-for-update` (stops and deletes the services, keeps `tunnels\`) for the pre-install hook; only
  a real uninstall wipes it. Check in phase 3 whether Tauri's NSIS runs the old uninstaller during upgrades
  (post-uninstall also removes `tunnels\`) and skip that when it does.
- Post-uninstall removes `updates\` and `HKLM\SOFTWARE\KiwiVPN\Update` only on a real uninstall.

## Tunnel during an update

Stopping the service ends its WFP dynamic session: the kill switch filters vanish and the tunnel stops, typically
for 15-40 seconds. Rules:

| Situation | Behaviour |
|---|---|
| No tunnel wanted | Normal update: installs when ready (rollout permitting), small notice afterwards |
| Connected, normal update | Never installs on its own. The app shows "Update ready" with the notes; "Install now" says: "You'll be disconnected for about 30 seconds. Apps may use your normal connection meanwhile. Kiwi reconnects afterwards." Or "Later" / install when I disconnect |
| Connected with kill switch on | As above, but the text says the kill switch is off during the update too. Optional phase 7 "update lock" removes this gap |
| Security update (`security: true`) | Installs on its own when no tunnel is wanted. If connected: asks, and after 24 hours of "Later" installs at the next disconnect, or after a 5-minute visible countdown the person can postpone once more |
| `min_version` above the installed one | Same as a security update, and the window says the update is required |

Optional **update lock** (phase 7, owner decision): just before step 5 the service adds a small set of **persistent**
WFP block filters (own sublayer, fixed GUIDs, block everything except loopback and DHCP) so nothing leaks while no
service runs. Removed by the new service when its own filters hold, by `recover`, by `kiwivpn-service unblock`, and
at the latest by the recovery task after 10 minutes. Risk: a bug here leaves a PC offline, even across a restart,
which is why it's opt-in and comes last.

## Staged rollout and pausing

- Bucket = first 4 bytes of `SHA-256(InstallId || version)` as a number, mod 10,000. The update is offered when
  bucket < `rollout_bp`. Including the version gives a different early group each release.
- Decided on the PC, from the signed manifest: static hosting is enough and the server learns nothing.
- Typical rollout: 1% (`100`), 10%, 50%, 100%, a day or more apart. Security updates usually go straight to 100%.
- **Pause**: republish the manifest with `paused: true` (new `seq`). PCs that haven't started stop there; PCs that
  already updated stay (no downgrade) and get a fixed N+2 instead.

## When an update fails

Recovery always moves forward or reinstalls the committed version; it never installs a version lower than
`HighestCommitted`.

| Failure | Recovery |
|---|---|
| Download, hash, size or signature check fails | Nothing installed. Retry at the next check with backoff (1 h, 6 h, 24 h). After 3 failures of the same file, stop and show "Update failed, download it from pricechat.live" |
| Installer exits non-zero before touching the service | Old version still runs; the service records it and retries as above |
| Installer dies after removing the service, or the new service doesn't start | The recovery task runs `recover`: (1) re-run the verified new installer once (same version is allowed); (2) if that fails too, run the **repair copy**, the installer of `HighestCommitted` (the version that was working). Allowed because the new version was never committed. Then remove the update lock if any, and mark that version bad locally so it isn't retried until a manifest offers a higher one |
| New version starts but fails the health check | `Pending` stays; `recover` treats it like the row above |
| New version passes, but is bad in a way the check can't see | Pause the rollout; ship N+2. No downgrade path, by design |

The repair copy is one verified installer of the committed version kept in `updates\`. It is never downloaded again
or replaced from the network; only a passed health check replaces it.

## Telemetry (minimal)

Default: none beyond normal web server logs of the downloads. Optional, owner decision, phase 7: after each attempt the
service sends one anonymous `POST https://pricechat.live/api/kiwi/update-report`
`{"from": "2.0.0", "to": "2.0.1", "result": "ok" | "<stage>:<code>"}`. No `InstallId`, no account, no hardware data;
the backend stores counts per version and result, not IP addresses. That's enough to see a bad rollout and pause it.
It needs a backend change, a privacy page line (PRIVACY-REVIEW.md) and the owner's approval to deploy.

## Threat model

| Threat | What the attacker can do | What stops it |
|---|---|---|
| Compromised website / CDN (Vercel) | Replace manifest or installer | Manifest signature (key not on Vercel); installer hash in the signed manifest; Authenticode pin. Worst case: withhold updates, limited by expiry |
| Man in the middle | Change or replay responses | HTTPS + host allowlist + no redirects; then the same two signatures; `seq` and expiry stop replays |
| Stolen manifest key | Sign manifests pointing anywhere | Installer must still carry the pinned Authenticode signature and a higher version: only genuine Kiwi builds can run. Rotate to k2 with `revoke_keys` |
| Stolen code-signing credentials | Sign malware as the owner | No client fetches a file the manifest didn't name and hash. Owner revokes the certificate at SSL.com (WinVerifyTrust then rejects it) and rotates the pin via a new build or `pin_add` |
| Both stolen (e.g. GitHub owner account) | Push malware to everyone | Hardware-key 2FA, `main`-only environments, required reviewers when available; optionally offline manifest signing |
| Local program running as the person | Drive the app, fake pipe requests, plant files | Requests carry no URL, path or file; only the service downloads, into a SYSTEM-only folder; checks and runs the same open handle; worst case it starts a genuine, newer update early |
| Local program pre-creating folders | Plant a folder it controls | `updates\` lives under Program Files (people can't write there); the service checks owner and DACL before use |
| Downgrade to an old vulnerable signed build | Offer an old version or an old manifest | Version must exceed running and `HighestCommitted`; `seq` never goes back |
| Freeze (keep serving an old valid manifest) | Hide a security fix | `expires_at` at most 14 days; weekly re-signing; after expiry the app says it can't check for updates |
| Fake service catching requests | Pretend to be the service | Existing client check: pipe owned by SYSTEM in session 0 (LOCAL-SECURITY.md layer 2) |
| Malware running as administrator | Anything | Out of scope; above every boundary |

## Publishing (how a release goes out)

1. Build and sign as today (Run workflow > Code sign). Artifact: `KiwiVPN-<version>-<sha>-signed-Setup.exe` + `.sha256`.
2. Owner checks it on the main PC (`ci-fetch.ps1`, `main-pc-check.ps1`), as in SIGNING.md.
3. New workflow **"Publish update"** (owner, by hand, `main`, environment `kiwi-publish`): inputs version, channel,
   rollout, notes, security. It re-verifies the artifact (signature, pinned thumbprint, hash), writes the manifest
   with the next `seq`, signs it with `KIWI_MANIFEST_KEY`, and outputs both files.
4. Files go to the website under `public/downloads/kiwi/windows/` (versioned installer names, never overwritten;
   `downloads/KiwiVPN-Setup.exe` untouched), deployed to Vercel. Whether CI deploys directly (needs a Vercel token in
   `kiwi-publish`) or the owner commits the files is an owner decision.
5. **Freshness re-sign**: a weekly scheduled job re-signs the current manifest with a new `seq`, `signed_at` and
   `expires_at`, changing nothing else. It can't change version, URL or hash (it reads them from the published
   manifest and checks them against the last owner-approved one).
6. Raise `rollout_bp` or set `paused` with the same workflow.

## Implementation plan

| Phase | Work | Tests |
|---|---|---|
| 0. Owner decisions and prep | Hosting location; create k1/k2 and `kiwi-publish`; confirm Vercel serves without redirects; record the certificate's SHA-256 from a signed build | – |
| 1. Pure logic (`service/src/update/`) | Version parse/compare, manifest envelope and signature, field rules, expiry and `seq`, rollout bucket, URL/host rule, pin set matching, key revocation order | Unit tests in CI: version edge cases (2.0.10 > 2.0.9, leading zeros, tags refused); valid manifest; tampered payload byte; wrong `key_id`; signature by an unknown key; k1 revoking k2 refused; `seq` lower refused; expired; `signed_at` in the future; wrong product/channel; non-allowlisted host, `http://`, port, userinfo, query; rollout boundaries 0 and 10000; `paused` |
| 2. Windows checks | `WinVerifyTrust` by handle + chain + thumbprint + timestamp; version resource; protected state with DACL; `updates\` DACL check | CI with fixtures: the signed installer (signed runs) passes; unsigned build fails; **`wireguard.dll` (valid signature, wrong signer) fails**; a byte flipped fails; a self-signed test certificate trusted only on the runner fails the real pin and passes a test pin |
| 3. Service flow and installer | Download, pipe commands `UpdateStatus` / `UpdateInstall`, detached installer, recovery task and `recover`, health commit, app relaunch; hooks.nsh changes (`/SD IDOK`, keep `tunnels\` on upgrade, `stop-for-update`) | CI: silent install as SYSTEM via the service finishes with no window; tunnel config survives an upgrade; a non-app caller's `UpdateInstall` refused |
| 4. App | "Update available / required" banner, notes, consent dialog with the disconnect warning, progress, failure message | vitest for every state and the kill-switch wording, as for the 2.0 features |
| 5. CI end to end (test channel) | Build N and N+1 (version bumped, e.g. 2.0.900) in one run, with feature `update-test` (adds a test key made per run, channel `test`, host `kiwi-update.test` mapped to 127.0.0.1 with a local HTTPS server whose certificate the runner trusts). A release check fails if `update-test` is in a public build, like `--no-default-features` | Positive: install N, publish N+1, N updates to N+1, service running, `HighestCommitted` = N+1, tunnel rules restored. Negative: wrong signer, lower version, equal version, tampered hash, wrong size, expired manifest, old `seq` replayed, redirect to another host, oversized download, paused release, installer killed midway (recovery runs the repair copy) |
| 6. Publishing | "Publish update" workflow, weekly re-sign, pause | Dry run to the test channel; manifest verified by the service's own code in CI before upload |
| 7. Optional | Update lock (persistent WFP filters); anonymous telemetry endpoint | Lock removed in every path (new service, `recover`, `unblock`, timeout, restart); PC never left offline |

Add to RELEASE-CHECKLIST.md when built: auto-update e2e and negative tests pass; `update-test` absent from the public
build; pin set and manifest public keys checked in the release binary; recovery tested on a real PC.

## Needs the owner

- **Hosting**: manifest and installers on Vercel under `pricechat.live/downloads/kiwi/windows/` (recommended) or
  elsewhere; and whether CI may deploy them.
- **Create the manifest keys** (k1 online in `kiwi-publish`, k2 offline) and keep the backups.
- **Create the `kiwi-publish` environment** (main only) and decide on the weekly re-sign job.
- **Every publish and rollout change** is the owner's click; agents never publish.
- Whether to build the update lock and the telemetry endpoint (backend change and deploy need approval).
- Certificate renewal timing with SSL.com, about 60 days before expiry.
