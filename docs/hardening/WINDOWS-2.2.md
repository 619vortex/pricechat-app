# Kiwi VPN for Windows 2.2: hardening report

Branch `feat/kiwi-windows-22` (worktree D:/pc-wt/kiwi-22, from main a7d2081). CI: 619vortex/KIWIVPN branch `kiwi-22`.
Details of each change: [windows-client/docs/KIWI-2.2.md](../../windows-client/docs/KIWI-2.2.md). Nothing was deployed,
published or signed; KIWIVPN main, its secrets and environments are untouched.

## What changed

- **Signed auto-update** (AUTO-UPDATE.md design): the SYSTEM service fetches `https://pricechat.live/downloads/kiwi/windows/stable.json`,
  checks the Ed25519 signature (prefix `kiwi-update-v1\n`) before reading it, then every field rule (product, channel, `seq`,
  expiry, version above running and `HighestCommitted`, host/URL, size, hash, rollout, pause). It downloads only the named file
  into a SYSTEM-only folder, checks size and SHA-256 while streaming and again through a read-only-shared handle, then
  WinVerifyTrust by handle (whole-chain revocation), the pinned thumbprint `0C24831BCE28841DC6C31BCFB326C0624A283559`, the
  timestamp and the version resource, and only then runs it silently. Protected state in `HKLM\SOFTWARE\KiwiVPN\Update`;
  recovery tasks; commit on restart. App: daily check, "New version available" + Install (with the disconnect warning when
  connected). Production keys are **placeholders**: the public build offers nothing until the owner's keys are in.
- **Kill switch**: L10 (ICMPv6 = ND 133-137 on fe80::/10, ff02::/16 only), L11 (DHCP only to broadcast/LAN), L12 (window TCP 443
  out only; tunnel UDP only to its server), L13 (Address /32 or /128), L14 (optional DNS-over-TLS block), M3 (optional strict
  kill switch, default off: persistent + boot-time WFP filters, removed only by Disconnect, `kiwivpn-service unblock`,
  uninstall, or connecting with strict off). The six release cases of the normal kill switch are listed in Settings and docs.
- **I2**: signed server list verification (prefix `kiwi-servers-v1\n`, same keys) built and tested; enforcement designed (KIWI-2.2.md §3).
- **CI**: `update-test` job (no account), release check (public service holds no test host/key), `run_e2e` input (manual runs
  skip the real-account e2e unless ticked), kill-switch job extended (L12, L14, strict).
- **Version** 2.2.0 (app, Tauri, service crate).

## CI runs

All runs are `workflow_dispatch` on KIWIVPN branch `kiwi-22`, with `sign` off and no `remove_devices`. Rust was compiled and
tested only on GitHub Actions. On this PC only `npm ci`, `npx vitest run` (99 passed) and `npx tsc -b` (clean) were run.

| Run | KIWIVPN commit (source) | build | upgrade | kill-switch | update-test | e2e |
|---|---|---|---|---|---|---|
| 43571026082 | 63b0bb5 (8fb92d3) | success | success | 40/41 (strict `unblock` race; GitHub shows "cancelled") | 0/1, broken setup check in run.ps1 | skipped |
| 43573165764 | 53e5cd9 (3c62a9b) | success | success | **success, 19 + 22 = 41/41** | 34/35 (after commit, the "no keys" message was overwritten) | skipped |
| **43574419119** (final) | **f871b4b (4afbec5)** | **success** | **success** | **41/41 checks pass, every step succeeds**; GitHub still marks the job "cancelled" during runner cleanup | **success, 35/35** | **success, 37 VPN + 12 window checks** (the one real-account run) |
| 43683280763 | f051fd9 (12bb5e9) | success | success | 41/41, every step succeeds, "cancelled" | success | skipped |
| 43684595279 | e57950c (31f83af) | success | success | 41/41, every step succeeds, "cancelled" | success | skipped |
| 43685983005 | 4fa06b6 | success | success | 41/41, every step succeeds, "cancelled" | success | skipped |
| **44069835693** (2026-11-17) | **573d9ad (e227431)** | **success** (Rust service 53 incl. the 2 new update.rs tests, client 58, vitest 99) | **success** | **success, 19 + 22 = 41/41** | **success, 35/35** (row 7: placeholder keys branch) | skipped |

**The kill-switch job's "cancelled" (investigated 2026-11-15): a GitHub runner artefact, not a product or test failure.** Every step,
including "Complete job", succeeds; GitHub records "cancelled" one second after "Cleaning up orphan processes". Ruled out, one run each:
the uninstaller still running (fixed anyway: NSIS `uninstall.exe /S` copies itself to TEMP and returns, so `-Wait` didn't wait; CI now runs a
copy with `_?=` and checks the service and processes are gone, in kill-switch and update-test), Kiwi processes or services left behind
(none: only Windows' NetSetupSvc/DsmSvc started during the adapter tests), and the network settling (api.github.com answered 6 times in a
row before the job ended). The same code was green once (43573165764: the only source difference to the next run is a log message in
update_win.rs, which this job never runs). Read the job's step results and its "41/41" report, not its badge. Not a release blocker.

Exact test counts (final run):

- Window (vitest): 99 tests in 7 files: 86 from earlier versions plus 13 new in App22.test.tsx.
- Service `cargo test`: 52 unit tests (update 21, guard 11, protocol 11, config 6, windows 3) plus 3 integration tests (fake_service 1, pipe 2).
- Client `cargo test`: 58, including the new settings test.
- App `cargo test --no-default-features`: 0 tests, compile only.
- Release check: the public service contains neither the test host nor the test key.
- kill-switch job: 19 service checks (6 new strict ones) and 22 raw filter checks (new: L12 port 80, L14 DoT, strict persistence/removal). The boot-time set was added (62 or 66 filters).
- update-test job: 35 checks: 4 setup; 14 manifest refusals or "nothing offered" (bad signature, unknown key, expired, future, channel, product, other host, plain http, lower version, same version, paused, 0% rollout, replayed seq, redirect); 5 installer refusals (hash mismatch, truncated, longer, unsigned, unpinned signer); 3 folder and offer checks; 7 checks after the update (service 2.2.0, committed, no pending, recovery tasks gone, repair copy kept, tunnels\ kept, one Apps entry); 2 kill switch checks after the update; 1 check that the public build has no keys.

## Owner release procedure (2.2.0 signed), in this order

Only the owner does these steps, on the main PC, by hand. An agent never generates, sees or handles k1/k2 or the eSigner secrets,
and never pushes to KIWIVPN `main`. Keep Smart App Control on throughout.

**A. Make the update keys (offline, once)**

1. Unplug the network if you like (nothing below needs it). In PowerShell, from the repository root:
   `powershell -ExecutionPolicy Bypass -File windows-client\tools\new-update-key.ps1`
   It makes `%USERPROFILE%\kiwi-update-keys-<date>\k1.pem` and `k2.pem` (only your user can read the folder) and prints a
   `pub const PRODUCTION_KEYS: &[Key] = &[ ... ];` block with two `Key { id: "k1"/"k2", public: hex32("<64 hex>") }` lines.
   These are **public** keys. Never paste the .pem files or their contents anywhere (chat, GitHub, the repository).
2. **Back up the private keys before anything else**: copy `k1.pem` and `k2.pem` into the password manager (as two secure notes)
   AND onto an encrypted USB stick (e.g. a 7-Zip AES-256 archive, passphrase kept apart from the stick). Check both copies open.
   k2 is the spare that only ever replaces k1; if both are lost, every 2.2 install must be updated by hand once more.
3. Delete the `kiwi-update-keys-<date>` folder from the PC (Shift+Delete) and empty any copy the backup tool left behind.

**B. Put the public keys in the source**

4. In `windows-client/service/src/update.rs`, replace the one line
   `pub const PRODUCTION_KEYS: &[Key] = &[Key { id: "k1", public: [0; 32] }, Key { id: "k2", public: [0; 32] }];`
   with the printed block (k1 first, then k2). Change nothing else.
5. Check the paste: `node windows-client/ci/release-keys.mjs --source windows-client/service/src/update.rs --require-real`
   must end `real k1/k2 (public keys), signed build: OK`.
6. Commit on the branch being released ("Kiwi 2.2: production update keys k1/k2 (public)") and merge it to `main` as usual.

**C. Signed build**

7. With `main` checked out (clean) in the worktree: `powershell -ExecutionPolicy Bypass -File windows-client\sync-ci-repo.ps1`.
   It pushes the exported `windows-client/` and the workflow to KIWIVPN `main` (it refuses uncommitted files and any `.pem`).
8. At once: GitHub > 619vortex/KIWIVPN > Actions > **Kiwi VPN Windows** > Run workflow > branch `main` > tick **sign**
   (tick `run_e2e` only if you want one real-account run; it uses the shared device rate limit) > Run. Starting it right away
   lets the concurrency group cancel the push run. Only `619vortex` on `main` gets the `windows-signing` secrets.
   The build fails before signing anything if `PRODUCTION_KEYS` are placeholders or test keys, or if the compiled service doesn't
   carry the k1/k2 bytes ("Update keys" and "Release check" steps).
9. Wait for green: build (incl. the update keys checks), upgrade, kill-switch (41/41; a "cancelled" badge is the known runner
   artefact, read the step results). `update-test` is skipped on signed runs; it ran on the unsigned run of the same source.

**D. Check the signed installer on the main PC (nothing is installed by these checks)**

10. `powershell -ExecutionPolicy Bypass -File windows-client\ci-fetch.ps1` downloads `KiwiVPN-2.2.0-<sha>-signed-Setup.exe` and
    checks its SHA-256 against the run's `.sha256`.
11. Signature, by hand:
    ```powershell
    $f = "C:\AI\pc-wt\kiwi-release\ci\<sha>\KiwiVPN-2.2.0-<sha>-signed-Setup.exe"
    $s = Get-AuthenticodeSignature $f
    $s.Status                                   # Valid
    $s.SignerCertificate.Thumbprint             # 0C24831BCE28841DC6C31BCFB326C0624A283559
    $s.SignerCertificate.Subject                # CN=Avigad Saadia, ...
    [bool]$s.TimeStamperCertificate             # True
    (Get-Item $f).VersionInfo.ProductVersion    # 2.2.0
    (Get-FileHash $f -Algorithm SHA256).Hash    # = the .sha256 file and the run summary
    ```
    then `powershell -ExecutionPolicy Bypass -File windows-client\ci\main-pc-check.ps1 -Installer $f` (all PASS).
12. Optional: record the certificate's SHA-256 for a stronger pin later:
    `[BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($s.SignerCertificate.RawData)) -replace '-'`.

**E. Publish 2.2.0 (manual download; 2.1 has no updater)**

13. Copy the checked file to `frontend/public/downloads/KiwiVPN-Setup.exe`, set `WINDOWS_BETA` in
    `frontend/src/website/vpnConfig.ts` to version `2.2.0` and its SHA-256 (a test ties the two), commit, deploy the website.
14. `node windows-client/check-download-live.mjs <SHA-256>` must pass, and `Get-AuthenticodeSignature` on a fresh download from
    https://pricechat.live/downloads/KiwiVPN-Setup.exe shows the same Valid / thumbprint as step 11.

**F. Later releases (2.2.1+): the signed update manifest**

15. Build and check the signed installer as in C-D. Plug in the USB stick and, on the main PC:
    `node windows-client/tools/sign-update-manifest.mjs --key <USB>\k1.pem --key-id k1 --installer <signed exe> --version 2.2.1 --seq <last + 1> --rollout 100 --out stable.json`
    (`--rollout` is basis points: 100 = 1 %, 10000 = everyone). It refuses a key that isn't `PRODUCTION_KEYS`' k1, or placeholders.
16. Verify before upload (no key needed):
    `node windows-client/tools/sign-update-manifest.mjs --verify stable.json --installer <signed exe>` must end `PASS`.
    Unplug the USB stick.
17. Put `stable.json` and the installer, renamed `KiwiVPN-2.2.1-Setup.exe`, in `frontend/public/downloads/kiwi/windows/`, commit,
    deploy. Then download both from `https://pricechat.live/downloads/kiwi/windows/` and run step 16 on the downloaded copies;
    both URLs must answer 200 with no redirect (the service refuses redirects).
18. Widen the rollout over days by re-signing with a new `--seq` and higher `--rollout`; re-sign at least every 14 days (expired
    manifests only mean nothing is offered). Certificate renewal (current one ends 2027-11-09): first publish a manifest with
    `--pin-add <new SHA-1>`, only then ship installers signed with the new certificate. If k1 leaks: sign with k2 and `--revoke k1`.

**G. Rollback**

- Before or after publishing 2.2.0, website: Vercel > promote the previous production deployment (serves 2.1.0, SHA-256
  `660C2D5B…387668C3`), or revert the download commit and redeploy. People who installed 2.2.0 keep it; a manual 2.1.0 install
  over it works (the NSIS installer refuses only a silent downgrade).
- A bad update already offered by a manifest: re-sign it with `--paused` and a new `--seq`, verify (step 16), deploy; installs that
  haven't started stop. There is no downgrade path by design: ship a fixed higher version.
- Wrong keys pasted (not yet released): revert the key commit, regenerate (A), repeat B-D. Once a build with keys is public, those
  keys are the only ones its installs trust: never lose the backups.
- A PC stuck offline by the strict kill switch: Disconnect in the app, or as administrator
  `"C:\Program Files\KiwiVPN\kiwivpn-service.exe" unblock` (works with the service stopped), or uninstall.
- Code: revert the `feat/kiwi-windows-22` merge; nothing else depends on it.

Not yet: don't create a `kiwi-publish` environment or a `KIWI_MANIFEST_KEY` secret. The "Publish update" workflow doesn't exist,
required reviewers need GitHub Pro on a private repo, and this PC's deploy key can push to KIWIVPN `main`, so a workflow edit could
read any environment secret. k1 stays offline; k2 goes nowhere online, ever.

## Bootstrap limit

2.1.0 (and 0.1.1) have no updater: **every existing user must install 2.2 by hand once**. Only builds that carry the owner's
real k1/k2 can be updated automatically; a 2.2.0 shipped with the placeholder keys would itself need a manual update. So:
generate the keys before the 2.2.0 signed build.

## Not done / limits

- "Publish update" workflow and the weekly freshness re-sign: designed (AUTO-UPDATE.md), not built; `sign-update-manifest.mjs` is the manual tool.
- Recovery when an installer dies midway (`kiwivpn-service recover`, scheduled tasks) is implemented but not exercised in CI.
- Boot-time WFP filters are best effort (the result is logged); a real reboot can't be tested on a CI runner.
- Signed server list is verified but not enforced on connect.

## Re-verified 2026-11-17 (feat/kiwi-sec)

- Live `https://pricechat.live/downloads/KiwiVPN-Setup.exe`: 3,771,632 bytes, SHA-256 `660c2d5b…387668c3` (= `WINDOWS_BETA`),
  ProductVersion 2.1.0, Authenticode Valid, signer `0C24831B…3559` (Avigad Saadia, until 2027-11-09), timestamped.
  `/downloads/kiwi/windows/stable.json` 404 (no manifest published, as expected).
- KIWIVPN (read-only API): `main` a1d5997 (2.1.0, run 42802154197 success); `kiwi-22` 900aca4 = source 0f706ad, the same
  windows-client/ as this branch, **never run** (last run 43685983005 on 4fa06b6: build, upgrade, update-test success, kill-switch
  "cancelled" = the runner artefact above, e2e skipped). Environments: `kiwi-build`, `windows-signing` (branch policy only);
  no `kiwi-publish`.
- Fixed here (Rust and run.ps1 not compiled/run locally: CI only): pasting the real keys would have turned CI red twice:
  `update.rs` test `placeholder_production_keys_refuse_everything` asserted PRODUCTION_KEYS are zero, and update-test row 7
  required "aren't set up". Both now follow whichever keys the source holds; new test
  `production_keys_are_both_placeholders_or_both_real` guards the paste. `sign-update-manifest.mjs` refuses a key that isn't
  PRODUCTION_KEYS' `--key-id` (or placeholders) and gained `--pin-add` / `--revoke`, which the design needs for certificate
  renewal and key rotation but the tool couldn't write (8 node tests).

## Release preparation 2026-11-17 (feat/kiwi-release-rc)

- New CI gate `windows-client/ci/release-keys.mjs` (+ 7 node tests, also run in CI): the build job fails BEFORE any signing step
  when `sign` is ticked and `PRODUCTION_KEYS` are the placeholders, a test key, half-pasted, duplicated or filler; after the release
  build it also checks the compiled `kiwivpn-service.exe`: no TEST key bytes (every build), both k1/k2 bytes present (signed builds).
  Before this, a signed 2.2.0 with placeholder keys would have built green (the Rust test accepts "both placeholders").
- `sign-update-manifest.mjs --verify <stable.json> [--installer <exe>]` checks a manifest as an install would, no key needed
  (2 new tests; 10 in that file).
- Log hygiene on the sign path: the eSigner secrets reach the Installer (Tauri) step only in signed runs; `sign.ps1` and
  `check-certificate.ps1` mask the trimmed secret values (`::add-mask::`); `sign.ps1` redacts them from CodeSignTool output and
  turns tracing off. Audit: no `set -x` / `Set-PSDebug -Trace`, no secret echoed, artifact names are version + commit, and the
  uploaded folders (out/, tools/, uptest/, reports) hold no secret. Residual: in a signed run the Tauri build (npm/cargo build
  scripts) inherits the secrets in its environment, because Tauri runs `sign.ps1` as a child process.
- Rust and the workflow were not compiled or run here (Smart App Control; owner's rule): CI only.
