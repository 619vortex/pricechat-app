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

RESULTS

## Owner actions

1. **Generate the update keys offline** on the main PC: `powershell -ExecutionPolicy Bypass -File windows-client\tools\new-update-key.ps1`.
   Paste the two printed public-key lines into `PRODUCTION_KEYS` (`windows-client/service/src/update.rs`) and commit. Back up
   k1.pem/k2.pem offline (password manager + encrypted USB), then delete them from the PC.
2. **Create the `kiwi-publish` environment** in 619vortex/KIWIVPN (deployment branches: main only); secret `KIWI_MANIFEST_KEY` =
   the text of k1.pem. k2 never goes online.
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
   `public/downloads/kiwi/windows/`, deploy; raise `--rollout` (new `seq`) over days. Re-sign at least every 14 days (expiry).
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
