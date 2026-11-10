# Code signing Kiwi VPN 2.0 (SSL.com eSigner)

Signing runs only on GitHub's Windows runners in the private repo 619vortex/KIWIVPN. The eSigner credentials live only in the
GitHub environment `windows-signing` as encrypted secrets. They are never on the main PC, never in the repo, and never printed:
ci/sign.ps1 and ci/check-certificate.ps1 hand them straight to SSL.com's CodeSignTool (v1.3.2, SHA-256 pinned).

## Already set up in GitHub (2026-11-09)
- Environment **`windows-signing`**: deployments from **`main` only** (custom branch policy `main`). No secrets in it yet.
- Workflow `Kiwi VPN Windows` (`.github/workflows/kiwi-windows.yml`, source `windows-client/ci/kiwi-windows.yml`):
  - push builds: unsigned, environment `kiwi-build` (no secrets), all tests;
  - **Run workflow > "Code sign"**: enters `windows-signing` only if started by **619vortex**, on **main**, by hand. Signs
    `kiwivpn-service.exe`, `tunnel.dll`, `KiwiVPN.exe`, Tauri's NSIS plugins, the uninstaller and the installer
    (Authenticode SHA-256, RFC 3161 timestamp from ts.ssl.com). Every file is checked valid + timestamped right after
    signing; the artifact is `KiwiVPN-2.0.0-<sha>-signed-Setup.exe` with `.sha256` and BUILD-INFO.txt (14 days, private).
    Then the same jobs as always run against the signed installer: every installed file's signature, upgrade from the
    public 0.1.1, kill switch, real servers.
  - **Run workflow > "Check certificate"**: no build, nothing signed, no signing used. Lists the account's credential IDs
    and checks the certificate behind `ES_CREDENTIAL_ID`: code signing purpose (Authenticode), RSA key, valid for 30+ days,
    chain to a root Windows trusts and not revoked, and the thumbprint if `KIWI_SIGNER_THUMBPRINT` is set.
- `wireguard.dll` keeps WireGuard LLC's own signature (checked, never re-signed).

**Not possible on the current GitHub plan:** a required reviewer on `windows-signing`. For a private repo that needs GitHub Pro
(or Team). Until then the guard is: only the owner account can start a manual run, signing only on `main`, and only the owner
and this PC's deploy key can push to `main`. With Pro: Settings > Environments > windows-signing > Required reviewers >
619vortex, and every signing run also waits for your click.

## What the owner does

### On SSL.com
1. Open the code signing order (Dashboard > Orders > the IV+OV code signing certificate) and check:
   - status **issued**, delivery **eSigner** (cloud signing). If it offers "eSigner" enrollment, finish that first;
   - **key type RSA** (Smart App Control is known to accept RSA; if it says ECDSA, ask SSL.com to reissue as RSA 3072/4096).
2. On the same order, find the **eSigner Credential ID** (a GUID-like string). That's `ES_CREDENTIAL_ID`.
3. Same order, eSigner section: set or confirm your **eSigner PIN**, then generate the **2FA QR code** and copy the
   **secret code** shown with it. That's `ES_TOTP_SECRET`: it lets the build make its own one-time codes. Scan the same QR into
   your authenticator app too (a new QR replaces the old one). Treat it like a private key.
4. Check your eSigner plan's **monthly signing limit**: each signed build uses about 8-10 signatures; "Check certificate"
   uses none.

### On GitHub (619vortex/KIWIVPN > Settings > Environments > windows-signing > Environment secrets > Add)
| Secret | Value |
|---|---|
| `ES_USERNAME` | your SSL.com account username |
| `ES_PASSWORD` | your SSL.com account password |
| `ES_CREDENTIAL_ID` | the eSigner Credential ID from step 2 |
| `ES_TOTP_SECRET` | the secret code from step 3 |

Put them in the **environment** (not repository secrets), so only the signing job can read them.

### Then
1. Actions > Kiwi VPN Windows > Run workflow > `main` > tick **Check certificate** only > Run. Every line should say PASS.
   Optionally copy the thumbprint it prints into a `windows-signing` **variable** `KIWI_SIGNER_THUMBPRINT`.
2. Run workflow > tick **Code sign** > Run. The summary lists every signed file with its certificate.
3. On the main PC: `windows-client\ci-fetch.ps1` (downloads the newest artifact, checks SHA-256), then
   `windows-client\ci\main-pc-check.ps1 -Installer <file>` before installing and `-Installed` after (read-only: signature,
   chain, timestamp, Smart App Control state and any blocks). Smart App Control stays on.
