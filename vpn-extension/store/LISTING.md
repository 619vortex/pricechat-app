# Kiwi VPN: store submission kit

Everything to paste or upload. Packages are in `dist/` (not in git; `python vpn-extension/build.py` recreates them), images in `assets/`.

The Chrome and Edge packages hold the same code and leave out the manifest's `key`, because each store assigns its own id.

Checked against the code: `manifest.json`, `background.js`, `lib.js`, `popup.js`, `vpn-node/proxy.py`, `vpn-node/agent.py`, and the backend's VPN service and origin rule. The live server's logs weren't inspected.

## Before submitting

- [ ] Reviewer test account works with no email code or other login step (see "Reviewer access").
- [ ] Helsinki 1 is added in Admin, shows the browser extension badge and is online.
- [ ] https://pricechat.live/vpn/privacy has passed legal review, and its Draft banner has been removed.
- [ ] Publisher name, support email and the owner's legal details are confirmed.
- [ ] The Chrome store id is in `VPN_EXTENSION_ORIGINS` on the Railway `api` service. Until then the store version gets 403 when it asks for a login.
- [ ] The owner has decided whether Kiwi VPN stays open to members during review.

## Chrome Web Store: Store listing tab

**Name and summary:** come from the uploaded package and can't be edited in the dashboard without a new upload.

Summary: Private browsing with Kiwi VPN, from Price Chat. One switch sends your browser's traffic through an encrypted connection.

**Description (paste as is):**

Kiwi VPN — Private Browsing, One Click Away

Kiwi VPN by Price Chat sends your browser's traffic through an encrypted connection to a Kiwi VPN server, so websites see the server's address instead of yours. It works as an encrypted proxy for this browser. It doesn't create a device-wide VPN tunnel.

Features:
- One-click connection and disconnection.
- Encrypted connection between this browser and the Kiwi VPN server.
- Clear connection status, including the address websites see.
- Works with your Kiwi Passport, your Price Chat account.
- Limits WebRTC while connected, so it can't reveal your real address outside the proxy.
- If the connection is lost, browser traffic stops until it's back or you switch off.
- On public Wi-Fi, the network can't read browser traffic sent through this encrypted connection.

How it works:
1. Install the Kiwi VPN extension.
2. Sign in to your Kiwi Passport on pricechat.live.
3. Click the Kiwi VPN icon and choose a server.
4. Press the switch to connect. Press it again to go back to your normal connection.

Kiwi VPN must be open to your account. Each account gets one free 30-day trial, started on the Kiwi VPN page. After that it's $9.99 for 30 days when paid plans are open, renewed by hand with no automatic billing, and free for members with Royal Kiwi rank or higher. Nothing can be bought inside the extension.

Important:
- Kiwi VPN covers this browser only, not other apps. For a whole device, use Kiwi VPN with the free WireGuard app.
- pricechat.live and addresses on your own network don't go through it. Private windows are covered only if you allow the extension in them.
- Your traffic passes through our server. Pages on plain http aren't encrypted beyond our server.
- It doesn't guarantee complete anonymity, and it doesn't protect you from malware or from websites you sign in to.

Privacy: the extension has no ads or analytics. Full details: https://pricechat.live/vpn/privacy

Learn more: https://pricechat.live/vpn

**Category:** Privacy & Security if the dropdown offers it, otherwise the closest privacy or security option.
**Language:** English
**Store icon:** already in the package (`icons/128.png`, 128x128). If the dashboard asks for one separately, upload that file.
**Screenshots (1280x800):** `assets/screenshot-1-connected-1280x800.png`, `-2-choose-`, `-3-problem-`, `-4-sign-in-`. They frame real popup captures from a test run with a stand-in account API; the IP shown, 203.0.113.10, is a documentation address.
**Small promo tile (440x280):** `assets/promo-small-440x280.png`
**Marquee (1400x560, optional):** `assets/promo-marquee-1400x560.png`
**Official URL:** choose https://pricechat.live once it's verified in Search Console under the same Google account (optional).
**Homepage URL:** https://pricechat.live/vpn
**Support URL:** the owner's support page or address; https://pricechat.live/website/contact until there's a dedicated one.
**Mature content:** off

## Chrome Web Store: Privacy tab

**Single purpose:**
Sends the browser's web traffic through an encrypted proxy on a Kiwi VPN server, switched on and off by the user.

### Permission justifications

| Permission | Why it's needed |
|---|---|
| `proxy` | Core function: points the browser at the HTTPS proxy on the server the user chose while switched on, and clears it when switched off. |
| `webRequest` | Listens for the proxy's password request and for request results, so the extension can sign in and detect a refused login. It doesn't read or change page content. |
| `webRequestAuthProvider` | Answers that password request with the login issued to this browser. Only the proxy's own challenge is answered, never a website's. |
| `privacy` | Sets WebRTC to use only the proxy while connected, so it can't reveal the user's real address, and restores it on disconnect. |
| `storage` | Keeps the on/off state, chosen server and this browser's proxy login on the device, so the connection survives a browser restart. |
| Host access `https://pricechat.live/*` | Reaches the Kiwi VPN account API with the user's Kiwi Passport sign-in, to read their access and get a proxy login. |
| Host access `http://check.kiwi-vpn.invalid/*` | This address exists only inside the Kiwi VPN proxy. The extension requests it to confirm traffic really goes through the proxy, and to show the exit IP. No content scripts run on any page. |

**Remote code:** No, I am not using remote code.

### Data usage

The owner certifies these, so read them against https://pricechat.live/vpn/privacy before ticking. Google's user data FAQ counts transmitting data as handling it, and doesn't address proxies specifically, so this errs towards disclosing.

| Dashboard category | Tick? | Why |
|---|---|---|
| Authentication information | Yes | Sends the Kiwi Passport session cookie to pricechat.live, and stores and uses a proxy login. |
| Web history | Yes | The address of each site passes through the proxy in transit. The proxy code doesn't record it. |
| Website content | Yes | Page traffic passes through the proxy in transit: encrypted for https, readable by the server for plain http. The proxy code doesn't store it. |
| Location | Yes, if its definition includes IP address | The server necessarily sees the user's IP address to carry traffic, and system or firewall logs can record it. |
| Personally identifiable, health, financial, personal communications, user activity | No | The extension doesn't collect these. The account API it reads returns no name or email for it to use. Payments never pass through the extension. |

Certify, if true for the owner's business: data isn't sold or transferred to third parties outside the approved uses, isn't used for anything unrelated to the single purpose, and isn't used for creditworthiness or lending.

**Privacy policy URL:** https://pricechat.live/vpn/privacy

### What was verified, and what wasn't

- Verified in code: the proxy's only log lines are startup, shutdown and errors, with no addresses. The agent suppresses request logging. Stored usage is bytes per login. The extension has no analytics, no content scripts and a single account API.
- Not claimed: "no logs" or "no data collected". The server's ordinary system and firewall logs, and hosting providers' request logs for pricechat.live, can record IP addresses, and the policy says so.
- Not verified: the live server's actual log files and retention.

## Reviewer access (notes for testers)

**Not ready.** Sign-in is by emailed code, Google, X, Telegram or wallet, so a reviewer can't use a plain username and password yet. Once a method is approved, fill this in:

1. Install the extension and click its icon. It asks you to sign in.
2. Open https://pricechat.live/join and sign in with: REVIEWER LOGIN GOES HERE
3. Click the icon again, leave Helsinki 1 selected and press the round switch. It shows Connected and the server's IP address.
4. Visit https://api.ipify.org to see that address. Press the switch again to return to the normal connection.

## Extension id for the backend

The backend allows only listed extension ids to ask for a proxy login. Replace `STORE_ID` with the id shown in the Chrome dashboard. Keep the testers' id. Run only after approval, because setting a variable restarts the `api` service:

```
npx @railway/cli variables --project c2a48c3b-60de-4eec-80a3-1e7cf5ff3e26 --environment 7c06bad6-768e-4625-9a69-7251e6225a89 --service api --set "VPN_EXTENSION_ORIGINS=[\"chrome-extension://gfajopjikiojbdlgmimbcmcnffbnjpfi\",\"chrome-extension://STORE_ID\"]"
```

## Microsoft Edge Add-ons

- **Package:** `dist/kiwi-vpn-edge-1.0.0.zip`
- **Description:** the one above works, as it doesn't reference another browser.
- **Category:** Productivity, or the closest privacy category offered
- **Logo:** `assets/logo-300x300.png`
- **Screenshots (up to 6, 1280x800):** same four files
- **Small / large promotional tiles:** `assets/promo-small-440x280.png`, `assets/promo-marquee-1400x560.png`
- **Search terms (up to 7):** vpn, privacy, proxy, secure browsing, kiwi vpn
- **Notes for certification:** the reviewer steps above
- Add the Edge id to `VPN_EXTENSION_ORIGINS` as well.

## After approval

1. Copy each listing's official address into `STORE_LINKS` in `frontend/src/website/vpnConfig.ts`.
2. Deploy the frontend. The buttons become real links, and the manual install disappears once both are in.
3. Install from each store on a clean browser profile, connect with a real account and check the sign-in, connect, disconnect and browser-restart paths.
