# Kiwi VPN browser extension

Chrome and Edge (Manifest V3). One switch sends the browser's traffic through a server's encrypted proxy (`vpn-node/proxy.py`).
Browsers can't run WireGuard, so this covers the browser only; the WireGuard app covers a whole device.

- **Sign-in:** the Kiwi Passport session on pricechat.live. The extension gets a login for one server from `POST /api/members/vpn/browser`; only its hash is stored on our side.
- **Fails closed:** while on, a lost connection stops traffic and shows a red badge. Only switching off goes back to the normal connection.
- **Goes direct:** `pricechat.live` and local addresses, so the extension can still reach the account API and explain a problem.
- **Doesn't cover:** other apps, private windows unless allowed in the extension's settings, and anything that runs outside the browser.

## Files

It has site access only to pricechat.live and the proxy's check address: tested in Edge, the proxy's password request reaches it on any page without access to every site. `background.js` owns the proxy setting and answers the proxy's password request. `popup.*` only displays state and never sees the password. `lib.js` holds the logic with no browser APIs.

## Tests

```
node --test vpn-extension/lib.test.js
node vpn-extension/e2e/run.mjs <python with cryptography> <path to msedge.exe> <folder for screenshots>
```

The second runs the real extension in a real browser through the real proxy, with a fake account API. Nothing outside the PC is contacted.

## Building the download

`python vpn-extension/build.py` writes the testers' `frontend/public/downloads/kiwi-vpn-extension.zip` and the store packages in `vpn-extension/store/dist/`. Run it after changing the extension, and bump `version` in `manifest.json`.

## Publishing

Everything for the listings is in `store/`: `LISTING.md` has the text and form answers, `assets/` the images, `dist/` the packages. Each store gives its own extension id at the first upload: add every `chrome-extension://<id>` to `VPN_EXTENSION_ORIGINS` on the backend (keep the testers' one), or sign-in from that install is refused. Put the approved listing addresses in `STORE_LINKS` in `frontend/src/website/vpnConfig.ts`; the buttons on /vpn follow it.
