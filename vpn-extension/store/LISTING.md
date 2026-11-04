# Kiwi VPN: store submission kit

Everything to paste or upload. Packages are in `dist/`, images in `assets/`. Rebuild with `python vpn-extension/build.py`.

The Chrome and Edge packages hold the same code and leave out the manifest's `key`, because each store assigns its own id.

## Before submitting

- [ ] Reviewer test account works with no email code or other login step (see "Reviewer access").
- [ ] Helsinki 1 is added in Admin, shows the browser extension badge and is online.
- [ ] https://pricechat.live/vpn/privacy has passed legal review, and its Draft banner has been removed.
- [ ] Publisher name, support email and the owner's legal details are confirmed.
- [ ] After the first upload of each store, its new id is in `VPN_EXTENSION_ORIGINS` on the Railway `api` service.

## Both stores

**Name:** Kiwi VPN (from the manifest)

**Short description (from the manifest):**
Private browsing with Kiwi VPN, from Price Chat. One switch sends your browser's traffic through an encrypted connection.

**Full description:**

Kiwi VPN puts one switch in your browser. Press it and this browser's traffic goes through an encrypted connection to a Kiwi VPN server, so websites see the server's address instead of yours.

What it does
- One switch to connect or disconnect, with a clear status and the address websites see.
- Choose from the servers available to the browser extension.
- If the connection is lost, traffic stops instead of quietly going around the VPN, until it's back or you switch off.
- Limits WebRTC while connected, so it can't reveal your real address outside the VPN.
- Shows your data use for the month.

What you need
- A free Kiwi Passport account on pricechat.live. Sign in there, and the extension uses that sign-in.
- Kiwi VPN must be open to your account. It's free while we test. If paid plans are switched on later, the Kiwi VPN page will show the price before you pay. Nothing can be bought inside the extension.

What it doesn't do
- It covers this browser only, not other apps on your device. For your whole device, use Kiwi VPN with the free WireGuard app.
- pricechat.live and addresses on your own network don't go through the VPN, so the extension can reach your account and explain any problem.
- It doesn't make you anonymous, and it doesn't protect you from malware or from websites you sign in to.
- Private windows are only covered if you allow the extension in them.

Privacy
We don't log the websites you visit, your DNS lookups or the content of your traffic. We keep your account link, a fingerprint of the extension's login, and how much data each login used. There are no ads or analytics. Full details: https://pricechat.live/vpn/privacy

**Website:** https://pricechat.live/vpn
**Privacy policy URL:** https://pricechat.live/vpn/privacy
**Support:** needs the owner's support email (https://pricechat.live/website/contact until then)
**Mature content:** No
**Remote code:** No, none. All code is in the package.

**Single purpose:**
Sends the browser's web traffic through an encrypted proxy on a Kiwi VPN server, switched on and off by the user.

### Permission justifications

| Permission | Why it's needed |
|---|---|
| `proxy` | Core function: points the browser at the HTTPS proxy on the server the user chose while switched on, and clears it when switched off. |
| `webRequest` | Listens for the proxy's password request and for proxy-related request errors, so the extension can sign in and detect a lost connection. It doesn't read or change page content. |
| `webRequestAuthProvider` | Answers that password request with the login issued to this browser. Only the proxy's own challenge is answered, never a website's. |
| `privacy` | Sets WebRTC to use only the proxy while connected, so it can't reveal the user's real address, and restores it on disconnect. |
| `storage` | Keeps the on/off state, chosen server and this browser's proxy login on the device, so the connection survives a browser restart. |
| Host access `<all_urls>` | The proxy can ask for its password on any page the user opens, and the browser only reports that to an extension with access to that address. Also used to reach the account API on pricechat.live and the connection check, which only the proxy answers. No content scripts run on any page. |

### Data disclosures

The owner certifies these, so read them against https://pricechat.live/vpn/privacy before ticking.

- **Tick: Authentication information.** The extension uses the Kiwi Passport session and holds a proxy login.
- **Tick: Web history / browsing activity.** Requests pass through our server in transit to provide the feature. They aren't stored.
- **Do not tick:** personally identifiable, health, financial, location, personal communications, user activity or website content, unless the owner's own review finds otherwise.
- **Certify:** data isn't sold to third parties, isn't used for anything unrelated to the single purpose, and isn't used to determine creditworthiness or for lending.

### Reviewer access (notes for testers)

**Not ready.** Sign-in is by emailed code, Google, X, Telegram or wallet, so a reviewer can't use a plain username and password yet. Edge requires a test account or a reasonable explanation, and both stores can reject what they can't test. Once a method is chosen, fill this in:

1. Install the extension and click its icon. It asks you to sign in.
2. Open https://pricechat.live/join and sign in with: REVIEWER LOGIN GOES HERE
3. Click the icon again, leave Helsinki 1 selected and press the round switch. It shows Connected and the server's IP address.
4. Visit https://api.ipify.org to see that address. Press the switch again to return to the normal connection.

## Chrome Web Store

- **Package:** `dist/kiwi-vpn-chrome-1.0.0.zip`
- **Category:** the closest privacy or security category offered; otherwise Tools
- **Language:** English
- **Screenshots (up to 5, 1280x800):** `assets/screenshot-1-…` to `assets/screenshot-4-…`
- **Small promo tile (440x280):** `assets/promo-small-440x280.png`
- **Marquee (1400x560):** `assets/promo-marquee-1400x560.png`
- **Visibility:** Public. Use "Unlisted" first if you want to check the live listing before it's searchable.

## Microsoft Edge Add-ons

- **Package:** `dist/kiwi-vpn-edge-1.0.0.zip`
- **Category:** Productivity, or the closest privacy category offered
- **Logo:** `assets/logo-300x300.png`
- **Screenshots (up to 6, 1280x800):** same four files
- **Small / large promotional tiles:** `assets/promo-small-440x280.png`, `assets/promo-marquee-1400x560.png`
- **Search terms (up to 7):** vpn, privacy, proxy, secure browsing, kiwi vpn
- **Notes for certification:** the reviewer steps above

Edge's rules say a listing must not reference other browsers. The description above doesn't.

## After approval

1. Copy each listing's official address into `STORE_LINKS` in `frontend/src/website/vpnConfig.ts`.
2. Deploy the frontend. The buttons become real links, and the manual install disappears once both are in.
3. Install from each store on a clean browser profile, connect with a real account and check the sign-in, connect, disconnect and browser-restart paths.
