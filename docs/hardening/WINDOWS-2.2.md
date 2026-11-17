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

## Owner actions

1. **Generate the update keys offline** on the main PC: `powershell -ExecutionPolicy Bypass -File windows-client\tools\new-update-key.ps1`.
   Paste the two printed public-key lines into `PRODUCTION_KEYS` (`windows-client/service/src/update.rs`) and commit. Back up
   k1.pem/k2.pem offline (password manager + encrypted USB), then delete them from the PC.
2. **Keep k1 offline (changed 2026-11-17, feat/kiwi-sec).** Don't create `kiwi-publish` / `KIWI_MANIFEST_KEY` yet: the "Publish
   update" workflow that would use it doesn't exist, required reviewers need GitHub Pro on a private repo, and this PC's deploy key
   can push to KIWIVPN `main`, so a workflow edit could read any environment secret. Sign each manifest on the main PC instead
   (Release steps 3). Revisit when the publish workflow is built and the environment can require the owner's approval.
3. **Record the certificate's SHA-256** from a signed build (Get-AuthenticodeSignature ... SignerCertificate, SHA-256 of RawData)
   and add it to the pin (`Pin { sha1, sha256: Some(..) }`) if wanted.
4. **Signed build**: merge to main, sync to KIWIVPN main (sync-ci-repo.ps1), run the workflow by hand with `sign` ticked, check
   the artifact on the main PC (ci-fetch.ps1, main-pc-check.ps1) as in SIGNING.md.
5. Decide whether to tick `run_e2e` for the release run (one real-account run; it uses the shared device rate limit).

## Release steps

1. Owner actions 1-4 (the keys must be in the build that ships, or 2.2 can never auto-update).
2. Publish the signed 2.2.0 installer by hand as today at `downloads/KiwiVPN-Setup.exe` (2.1 users install it once).
3. For later releases: `node windows-client/tools/sign-update-manifest.mjs --key k1.pem --key-id k1 --installer <signed exe>
   --version 2.2.1 --seq <next> --rollout 100` → upload `stable.json` and `KiwiVPN-2.2.1-Setup.exe` to
   `public/downloads/kiwi/windows/`, deploy; raise `--rollout` (new `seq`) over days. `--rollout` is basis points: 100 = 1 %,
   10000 = everyone. Re-sign at least every 14 days (expiry; a lapsed manifest only means nothing is offered). Since 2026-11-17 the
   tool refuses a key that isn't `PRODUCTION_KEYS`' entry for `--key-id`, or placeholder keys (tests: `node --test
   windows-client/tools/sign-update-manifest.test.mjs`).
   Certificate renewal (current one ends 2027-11-09): installs trust only the pinned thumbprint, so publish a manifest with the new
   certificate in `pin_add` (signed by k1) BEFORE shipping an installer signed with the new certificate.
4. Confirm Vercel serves `/downloads/kiwi/windows/*` with 200 and no redirect (the service refuses redirects).

## Rollback

- A bad release already offered: republish the manifest with `--paused` (new `seq`); installs that haven't started stop. No
  downgrade path by design: ship a fixed higher version.
- A bad 2.2 build before any auto-update: keep serving 2.1.0 at `downloads/KiwiVPN-Setup.exe`; installs of 2.2 can be replaced by
  hand (the NSIS installer refuses a silent downgrade; a manual one works).
- A PC stuck offline by the strict kill switch: Disconnect in the app, or as administrator
  `"C:\Program Files\KiwiVPN\kiwivpn-service.exe" unblock` (works with the service stopped), or uninstall.
- Code: revert the `feat/kiwi-windows-22` merge; nothing else depends on it.

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
