# Kiwi VPN for Windows 2.2

2.2 adds **signed auto-update** and hardens the **kill switch** (security audit M3, L10-L14, I2 groundwork). Branch
`feat/kiwi-windows-22`; CI branch `kiwi-22` in 619vortex/KIWIVPN. Results and owner steps: `docs/hardening/WINDOWS-2.2.md`.

## 1. Signed auto-update

Built as designed in [AUTO-UPDATE.md](AUTO-UPDATE.md): the SYSTEM service fetches and verifies; the app only shows and asks. No
Tauri updater plugin.

| Piece | Where |
|---|---|
| Rules (pure, unit tested): envelope + Ed25519 `verify_strict` over `"kiwi-update-v1\n" || payload`, schema/product/channel, `seq` replay, expiry (max 30 days, 1 day clock slack), version (plain `x.y.z`, higher than running and `HighestCommitted`), URL (`https://pricechat.live/downloads/kiwi/windows/<name>.exe`, no port/userinfo/query), size 1-100 MB, SHA-256, notes, rollout bucket, pause, key revocation order, pin matching, download size/hash checks, commit/recover decision | `service/src/update.rs` |
| Windows half: WinHTTP (TLS 1.2+, Windows certificate checks, redirects refused, size cap), WinVerifyTrust **by handle** with whole-chain revocation, signer SHA-1/SHA-256 thumbprints, timestamp countersignature, file version resource, protected state in `HKLM\SOFTWARE\KiwiVPN\Update` (SYSTEM/Administrators write, Users read), `updates\` (SYSTEM/Administrators only, owner checked), recovery scheduled tasks, detached silent installer | `service/src/update_win.rs` |
| Pipe: `update_check`, `update_install` (no fields; app or administrator only); every answer carries `update` and the service `version` | `service/src/protocol.rs` |
| App: checks 15 s after opening and every 24 h; "New version available: Kiwi VPN x.y.z" + notes + **Install**; while connected, Install first explains the ~30 s disconnect (and the kill switch / strict wording) | `app/src/update.tsx`, `App.tsx` |
| Installer: silent-safe `MessageBox /SD IDOK`; upgrades keep `tunnels\`; uninstall runs `kiwivpn-service unblock` first and removes `updates\` and the recovery tasks | `app/src-tauri/windows/hooks.nsh` |
| Owner tools (never run by agents or CI) | `tools/new-update-key.ps1` (makes k1/k2 with OpenSSL, prints the Rust lines), `tools/sign-update-manifest.mjs` (writes and signs `stable.json` for a checked, signed installer) |

What it refuses (unit tests + CI `update-test`): wrong signature, signature by another key than claimed, unknown key, revoked
key, placeholder key, missing signature, extra envelope fields, oversized manifest, wrong product/channel/schema, expired,
signed in the future, too long a lifetime, older `seq` replayed, lower or equal version (downgrade), paused, outside the
rollout, other host, `http://`, port, userinfo, query, sub-folders, a redirect, size out of range, bad hash field, notes too
long, and at install time: hash mismatch, truncated download, longer download, unsigned installer (no thumbprint), trusted
but unpinned signer, no timestamp, wrong version resource.

**Production keys are placeholders** (`PRODUCTION_KEYS` all zero): a public 2.2.0 build refuses every manifest and the app
shows nothing until the owner generates k1/k2 offline and a build with them ships. Tests use the throwaway **TEST** keys t1/t2
(private halves in `service/src/update.rs` tests and `ci/update-test/manifest.mjs`), compiled into a service only with the
`update-test` feature; CI fails a build whose public service contains the test host or test key.

Decisions taken here (differences from the design doc, all safer or simpler):

- Normal updates wait for the person's **Install** click. Only `security: true` or `min_version`-required updates install by
  themselves, and only while no tunnel is wanted and no strict block holds.
- Reopening the app after an update uses the installer's own `/R` (Tauri's RunAsUser), best effort. No `CreateProcessAsUser`
  code in the service.
- The health check commits when the new service starts at the pending version (it doesn't wait for the tunnel). The tunnel and
  kill switch come back by themselves because upgrades keep `tunnels\`.
- A version installed by hand becomes `HighestCommitted` when it is higher.
- Optional "update lock" and telemetry (phase 7) are not built. With the **strict** kill switch on, its persistent filters
  already hold through an update, which gives the same protection.

## 2. Kill switch

| Audit | 2.2 rule | Test |
|---|---|---|
| L10 | ICMPv6 only neighbour discovery (types 133-137) and only to/from `fe80::/10` and `ff02::/16`. IPv6 ping and every other ICMPv6 outside the tunnel is blocked | `guard::tests::l10_*` |
| L11 | DHCPv4 out only from port 68 to port 67 at `255.255.255.255` and the private/link-local ranges; replies only from port 67 to 68. DHCPv6 only on the link | `l11_*` |
| L12 | KiwiVPN.exe: outbound TCP 443 only (nothing inbound). The tunnel's UDP: only to its server's address and port (no program condition, because WireGuard's driver sends from the kernel). The service has no other allowance; its update checks go through the tunnel | `l12_*`, CI kill-switch job (port 80 refused) |
| L13 | The config's `Address` must be /32 (IPv4) or /128 (IPv6) | `config::tests::l13_*` |
| L14 | Optional "Block DNS over TLS outside the VPN" (with the kill switch): TCP 853 blocked outside the tunnel, also to the LAN and split ranges | `l14_*`, CI kill-switch job |
| M3 | Optional **strict kill switch**, default off: the same rules also as persistent WFP filters (fixed keys, own sublayer) plus a boot-time set. Removed only by Disconnect, `kiwivpn-service unblock`, uninstalling, or a connect with strict off | `m3_*`, CI kill-switch job (holds after its process exits, after a give-up, with the service stopped; Disconnect and `unblock` lift it) |

A model of WFP's decision (`guard::verdict`: highest matching weight wins, block on a tie) lets the tests check behaviour packet
by packet, not just the rule list.

### When the normal kill switch lets traffic out

Shown under the kill switch in Settings and kept in `protocol::RELEASE_CASES`:

1. for about 3 seconds while Windows restarts the Kiwi VPN service after a crash;
2. while Windows starts, before the Kiwi VPN service is running;
3. after the tunnel fails to restart 6 times in a row;
4. 10 minutes after a restart if the server never answers;
5. 2 minutes after a server switch that never completes;
6. while Kiwi VPN is being updated.

The strict kill switch holds in all six. It never blocks after Disconnect. If it can't reconnect, the person stays offline
until they press **Disconnect and unblock** (the app shows it), or an administrator runs
`"C:\Program Files\KiwiVPN\kiwivpn-service.exe" unblock`, which works with the service stopped. A manual reinstall that runs
the old uninstaller also lifts it.

## 3. Signed server list (I2): verification built, enforcement designed

`update::accept_server_list` verifies a list signed with the same k1/k2 under the prefix `"kiwi-servers-v1\n"` (so an update
signature never verifies as a list and the reverse; tested), product `kiwivpn-servers`, `seq`, expiry; `server_listed` checks
an endpoint and server key. Not wired in yet. Design for 2.3:

1. Backend: a job signs `{schema, product, seq, signed_at, expires_at, servers: [{id, endpoint, public_key}]}` with k1 in the
   `kiwi-publish` environment whenever servers change, and at least weekly; published at
   `https://pricechat.live/downloads/kiwi/servers.json` (static).
2. Service: fetches it with the update manifest (same WinHTTP rules), stores the highest `seq`, keeps the last valid list.
3. On `connect`, after `config::parse`, the service refuses a config whose `Endpoint` and `PublicKey` pair isn't on the list
   ("This server isn't one of Kiwi's"), so same-user code driving KiwiVPN.exe can no longer point the tunnel anywhere.
4. Rollout: log-only for one release (records would-be refusals), then enforce. No list yet (first start offline) = allow,
   logged. The test-config option (`--no-default-features` removes it) bypasses it only in test builds.

## 4. Version

2.2.0 everywhere: `app/package.json` (+ lock), `app/src-tauri/tauri.conf.json`, `app/src-tauri/Cargo.toml`, and the service
crate (`service/Cargo.toml`, now 2.2.0 instead of 0.1.0, because the updater compares its own version).

## 5. Bootstrap limit

2.1.0 and older have no updater. Everyone on 2.1 installs 2.2 by hand once (the download on /vpn). From 2.2 on, and once a
build with the real k1/k2 keys ships, updates arrive through the service.
