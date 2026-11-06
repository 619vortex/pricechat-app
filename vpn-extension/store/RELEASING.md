# Releasing Kiwi VPN to the Chrome Web Store

`release.mjs` uses Google's Chrome Web Store API v2 to build, version, test and upload. It never submits or publishes by
itself: `submit` and `go-live` only run for a person at a real terminal who types the approval phrase for that exact package.

## One-time setup (needs your Google login: about 10 minutes)

1. **Google Cloud project**: open https://console.cloud.google.com/projectcreate, name it `kiwi-vpn-release`, create it.
2. **Enable the API**: https://console.cloud.google.com/apis/library/chromewebstore.googleapis.com (pick the project) > Enable.
3. **Service account**: https://console.cloud.google.com/iam-admin/serviceaccounts > Create service account, name `kiwi-cws-release`.
   Grant it no roles (the store decides what it may do).
4. **Key**: open the account > Keys > Add key > Create new key > JSON. A `.json` file downloads.
5. **Store it encrypted** (deletes the plaintext download):
   `node vpn-extension/store/release.mjs setup-key "%USERPROFILE%\Downloads\<the file>.json"`
   It prints the service account's email.
6. **Link it to the store**: Chrome Web Store Developer Dashboard > **Account** > Service accounts > add that email.
   Only one service account can be linked per publisher.
7. **Publisher ID**: Developer Dashboard > Publisher > Settings. Put it in `vpn-extension/store/cws.json` as `publisherId`.
8. Check: `node vpn-extension/store/release.mjs status` shows the live version and anything in review.

Where the secret lives: `%APPDATA%\KiwiVPN\cws-service-account.dpapi`, encrypted with Windows DPAPI for your Windows user only.
It is never in the repo, never printed, and only decrypted in memory to sign a 10-minute token request. To revoke: delete the
key in Google Cloud (step 4 page) and remove the email from the dashboard.

## Each release

| Step | Who | What it does |
|---|---|---|
| `prepare --bump patch` | automatic | bumps the version, checks it's above the store's live and in-review versions, runs the unit, proxy/agent and browser tests, builds the ZIP, runs the browser tests again on the exact ZIP under the store's extension ID, writes `store/releases/<version>.json` with its SHA-256 |
| `upload <version>` | automatic | uploads that exact ZIP (refused if it changed since testing) as the store draft. Nothing is submitted |
| `submit <version>` | **you**, at a terminal | type `submit <version> <first 8 of sha256>`. Submits for review as **staged**: after Google approves it, it waits |
| `go-live <version>` | **you**, at a terminal | type `go-live <version> <first 8 of sha256>`. Publishes the approved, staged version to users |

In Claude Code, run the two owner steps with `!` so they get your terminal, e.g. `! node vpn-extension/store/release.mjs submit 1.0.3`.
Without a terminal (automation, scripts, an agent) they refuse.

Store listing text and images (`LISTING.md`, `assets/`) aren't part of the API: update them in the dashboard.
