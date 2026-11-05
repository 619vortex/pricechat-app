# Signing in on iPhone

**Decision: `ASWebAuthenticationSession` with PKCE, using the `kiwivpn` callback scheme, with loopback as the route that works against today's live site.** Neither has been tried on a real iPhone.

## The problem
The live `/vpn/desktop` page can only send its code to `http://127.0.0.1:<port>`. That suits a PC. On an iPhone it's unproven: the app has to keep listening while Apple's sign-in sheet is open.

## What the app does
1. Makes a verifier and `state`, and starts a listener on `127.0.0.1` on a port iOS picks.
2. Opens `/vpn/desktop?challenge&port&state&client=ios` in `ASWebAuthenticationSession`, with `kiwivpn` as its callback scheme. A password never reaches the app. It isn't an embedded web view.
3. Takes the code from whichever arrives, each checked against `state`:
   - **Live page today:** the browser goes to `127.0.0.1`. The listener takes the code and replies with a redirect to `kiwivpn://signed-in`, which closes the sheet. The code isn't in that redirect.
   - **Page with the proposed change:** the browser goes straight to `kiwivpn://callback?code&state`, which closes the sheet.
4. Swaps code and verifier for a session, in native code, over TLS.

## Why this is safe
- **A code alone is useless:** it needs the verifier, which never leaves the app, and it dies after one try or 5 minutes.
- **A custom scheme is usually hijackable, since any app can claim one.** With `ASWebAuthenticationSession`, the callback goes back to the session that opened it. PKCE covers the rest, because a stolen code still can't be swapped. The scheme isn't registered in `Info.plist` at all, because it doesn't need to be.
- **`state` ties the answer to this attempt.** Anything else is refused.
- **The token never appears in a URL.**
- **The Allow page names the account,** and the person has to press Allow. Same remaining weakness as Windows: another app on the phone could start this flow, and if the person presses Allow, it gets a session limited to VPN routes.

## What needs testing: test plan 2 and 3
- Whether the listener hears the page while the sheet is open.
- Whether the browser follows a redirect from `http://127.0.0.1` to `kiwivpn://`. If this is what fails, the code has still arrived, but the sheet won't close by itself: the person would have to press Cancel, which the app reads as cancelled.
- Whether iOS asks for Local Network permission. Loopback shouldn't need it.
- Google sign-in in the sheet: the page's memory of the request across the trip to Google.

**If loopback fails on iPhone,** the website change is the fix, and it's small. It's written and tested on this branch, and it needs your approval to deploy. **I'd deploy it before relying on the iPhone app either way,** since it's the cleaner route and it fixes the page's wording.

The stricter option for later is a verified `https` callback (`ASWebAuthenticationSession.Callback.https`, iOS 17.4), which needs an `apple-app-site-association` file on the website and the Team ID.
