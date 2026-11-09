# Code signing Kiwi VPN 2.0 (SSL.com eSigner)

Signing runs only on GitHub's Windows runners in the private repo 619vortex/KIWIVPN, only when the owner starts it by hand, and
only after the owner approves the run. The credentials live only in GitHub's encrypted environment secrets. They are never on
the main PC, never in the repo, and never printed (ci/sign.ps1 passes them straight to SSL.com's CodeSignTool).

## What gets signed
`kiwivpn-service.exe`, `tunnel.dll`, `KiwiVPN.exe`, Tauri's NSIS plugins, the uninstaller and the installer. Each one is
Authenticode SHA-256, timestamped by ts.ssl.com, and checked for a valid, timestamped signature before the build continues.
`wireguard.dll` keeps WireGuard LLC's own signature. A signed build is a private 14-day artifact; nothing is published.
Each signed build uses about 8-10 eSigner signings. Check your eSigner plan's monthly limit.

## One-time setup (owner)
1. **SSL.com, the certificate.** In your SSL.com account, open the code signing order and check:
   - it says **eSigner** (cloud signing) and is issued;
   - the key type: **RSA** is what we want (Smart App Control may not accept ECDSA signatures);
   - copy its **Credential ID** (the order's eSigner section; CodeSignTool `get_credential_ids` lists it too).
2. **SSL.com, the automation secret.** On the same order, under eSigner, generate the **QR code** for 2FA and copy the
   **secret code** shown with it (a long text string). That string is `ES_TOTP_SECRET`: it lets the build make its own
   one-time codes, so no one types an OTP during a build. Add the same QR code to your authenticator app too: generating a
   new one replaces the old one. Treat this string like a private key.
3. **GitHub, the protected environment.** In 619vortex/KIWIVPN: Settings > Environments > New environment `kiwi-signing`:
   - **Required reviewers:** yourself (every signing run waits for your click);
   - **Deployment branches:** selected branches, `main` only;
   - **Environment secrets:** `ES_USERNAME` (SSL.com username), `ES_PASSWORD` (SSL.com password), `ES_CREDENTIAL_ID`,
     `ES_TOTP_SECRET`.
4. Optional, after the first signed build: an environment **variable** `KIWI_SIGNER_THUMBPRINT` set to the thumbprint the
   build summary shows. Every later signature must then come from exactly that certificate.

## A signed build
Actions > Kiwi VPN Windows > Run workflow > branch `main` > tick **Code sign** > Run. Approve the `kiwi-signing` deployment when
GitHub asks. The summary lists every signed file with its certificate, then the upgrade, kill switch and real-server tests run
against the signed installer.

On the main PC: `windows-client\ci-fetch.ps1` downloads the newest artifact and checks its SHA-256, then
`windows-client\ci\main-pc-check.ps1` checks the signature, chain and timestamp, and Smart App Control's state.
