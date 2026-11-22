# Owner steps: Windows 2.2 update keys, and the Chrome extension submission

Two jobs only you can do. Neither changes the live website, the servers or any user until the very last step of each, and
both last steps are yours. Nothing here asks you to show a key, password or token to anyone, including Claude.

---

## Part 1. Windows 2.2: make the update-signing keys (about 15 minutes, once)

**Why:** Windows 2.2 updates itself, and it only trusts updates signed with your key. Today the keys in the code are
placeholders, so a 2.2 built now could never update itself, and every user would need another manual install later.
The keys must exist **before** the signed 2.2.0 build.

You need: the main PC, Git for Windows (already installed), your password manager, and a USB stick.

1. **Open PowerShell in the repository** (the `feat/kiwi-windows-22` checkout, `D:\pc-wt\kiwi-22`) and run:
   ```
   powershell -ExecutionPolicy Bypass -File windows-client\tools\new-update-key.ps1
   ```
   It makes a private folder in your user profile (outside the repository) with `k1.pem` and `k2.pem`, and prints **two lines**
   that start with `Key { id: "k1"` and `Key { id: "k2"`. Those two lines are **public**: safe to paste anywhere.
2. **Give Claude the two printed lines** (or paste them yourself into `PRODUCTION_KEYS` in
   `windows-client/service/src/update.rs`). They replace the `[0; 32]` placeholders. Never paste the `.pem` files.
3. **Put k1 in GitHub** (it signs the update notices):
   GitHub > `619vortex/KIWIVPN` > Settings > Environments > **New environment** > name `kiwi-publish` >
   Deployment branches: **Selected branches** > add `main` > then **Add environment secret**: name `KIWI_MANIFEST_KEY`, value =
   the whole text of `k1.pem` (open it in Notepad, copy everything including the BEGIN/END lines).
   **k2 never goes online**: it is the spare that replaces k1 if k1 is ever lost.
4. **Back up both files twice**: as secure notes in your password manager, and in an encrypted archive on the USB stick
   (7-Zip > Add to archive > AES-256 with a passphrase you keep separately). Check you can open the archive.
5. **Delete the key folder from the PC** (the one the script printed, under your user profile) and empty the Recycle Bin.
6. **Tell Claude "keys done".** Claude then commits the public lines, runs CI, and prepares the signed 2.2.0 build for your
   approval (you tick `sign` on the workflow yourself, as with 2.0 and 2.1). The website keeps serving 2.1.0 until you approve.

If anything goes wrong in steps 1-5, delete the folder and start again: nothing has used the keys yet.

---

## Part 2. Chrome extension: submit **1.0.5** (not 1.0.3)

**Important:** 1.0.3 is out of date. **1.0.5** contains 1.0.3's fixes, plus the security audit fixes (another extension taking
over the proxy or WebRTC setting is detected and reported; the proxy password is kept in memory only), and the popup wording
that matches the privacy page. The store still serves 1.0.0; 1.0.3 and 1.0.4 were never submitted, so 1.0.5 goes straight
over 1.0.0.

The tested package: `D:\pc-wt\kiwi-release-rc\vpn-extension\store\dist\kiwi-vpn-chrome-1.0.5.zip`, SHA-256 starting
**`2119c6c1`** (30,579 bytes; 27 unit tests and 48/48 browser tests on it). Check it in PowerShell:
`Get-FileHash D:\pc-wt\kiwi-release-rc\vpn-extension\store\dist\kiwi-vpn-chrome-1.0.5.zip`.
(An older 1.0.5 build with a different hash, `DA6E0E83…`, is in `D:\pc-wt\kiwi-ext-105`: don't use it.)

The Chrome files named below (`vpn-extension/store/...`, `docs/hardening/CHROME-EXTENSION.md`) are on `main`
(C:\AI\price-chat or a fresh checkout), not on the Windows 2.2 branch. You can submit it either way.

### Option 1: by hand in the dashboard (simplest, about 20 minutes)

1. Open the **Chrome Web Store Developer Dashboard** (https://chrome.google.com/webstore/devconsole), signed in with the
   account that owns Kiwi VPN. Open the Kiwi VPN item.
2. **Package** > **Upload new package** > pick the 1.0.5 ZIP above. The dashboard shows version 1.0.5.
3. **Store listing**: paste the description from `vpn-extension/store/LISTING.md`; screenshots from
   `vpn-extension/store/assets/` (`screenshot-1..4-*-1280x800.png`, small tile `promo-small-440x280.png`).
4. **Privacy practices**: one justification per permission (`proxy`, `webRequest`, `webRequestAuthProvider`, `privacy`,
   `storage`, all-sites access), all written in LISTING.md; remote code **No**; data-use ticks as listed in
   `docs/hardening/CHROME-EXTENSION.md` section 7 D; privacy policy URL `https://pricechat.live/vpn/privacy`.
5. **Test instructions for the reviewer**: Google's reviewer can't sign in with Google/Telegram/email code. Either:
   - create a reviewer login first (recommended, so they can connect): ask Claude to walk you through
     `vpn-extension/store/reviewer_account.py`. It sets temporary reviewer variables on the backend, which is a production
     change you approve and run yourself; or
   - paste the "no account" text from `docs/hardening/CHROME-EXTENSION.md` section 7 (higher risk of a "not functional"
     rejection).
6. Choose **deferred / staged publishing** if offered, so approval doesn't publish by itself. Then **Submit for review**.
7. After Google approves (usually 1-3 days), press **Publish** when you're ready. That is the moment users get 1.0.5.

### Option 2: with the release tool (one-time Google setup, then automatic checks every release)

Setup steps A1-A7 in `docs/hardening/CHROME-EXTENSION.md` section 7: Google Cloud project, enable the Chrome Web Store API,
a service account with a JSON key (stored encrypted with `release.mjs setup-key`), add that account in the dashboard, and the
publisher ID in `vpn-extension/store/cws.json`. Then, in your own terminal (in Claude Code, prefix with `!`):

```
node vpn-extension/store/release.mjs upload 1.0.5     # Claude can run this one: it only makes a draft
node vpn-extension/store/release.mjs submit 1.0.5     # you type: submit 1.0.5 2119c6c1
node vpn-extension/store/release.mjs go-live 1.0.5    # after approval, you type: go-live 1.0.5 2119c6c1
```
The tool refuses any ZIP whose bytes differ from the tested one. Steps 3-5 of Option 1 (listing, privacy, test instructions)
are still done in the dashboard.

### Before either option

- Helsinki 1 is online with the browser badge in Admin (the reviewer needs a browser server). Today: Helsinki proxy OK,
  certificate 71 days.
- If something goes wrong after publishing: the dashboard can't roll back to 1.0.0; fix forward with 1.0.6. While still in
  review, the submission can be cancelled in the dashboard.
