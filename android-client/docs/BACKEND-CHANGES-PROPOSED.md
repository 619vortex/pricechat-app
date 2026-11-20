# Backend and website changes: proposals only

The Android app works against production as it is today. Nothing here has been deployed.

## 1. Wording on `/vpn/desktop` (written on this branch, not deployed)
The page says "Kiwi VPN for Windows" and "this PC". The app adds `&client=android`, which the live page ignores. With this change the page says "Kiwi VPN for Android" and "this phone". Website only, no backend, and the existing Windows wording stays the default.

## 2. Verified App Link for signing in (not written; needs a decision)
Today the code returns over `http://127.0.0.1`, the same as Windows. That's allowed by RFC 8252 for native apps, and it's protected by PKCE and `state`, but it has two weak points on a phone:
- The app has to stay alive in the background while the browser is open. If Android kills it, signing in fails and has to be repeated.
- Chrome's Local Network Access rules could change. A Chromium engineer's note says main-frame navigations aren't restricted today. Test it on real phones.

A verified App Link would remove both. It needs:
- `https://pricechat.live/.well-known/assetlinks.json` holding the release signing key's fingerprint, so **it can't be done until the release key exists**.
- A callback path on the site, and the page choosing that path only for `client=android`. A code in a URL handled by a verified link is safe only with PKCE, which is already there.
- No backend or database change.

**Recommendation:** ship the beta on loopback, measure how often signing in fails, and move to App Links before Google Play.

## 3. Session wording (optional)
`member_sessions.kind` is `desktop` for phones too, and the My Devices button says it signs out "every PC". The behaviour is right and the wording isn't. A new kind would need code changes in several places, so this is left until there's a reason.

## 4. Things that would be needed for Google Play
See `GOOGLE-PLAY.md`: a privacy policy section for Android and Auto-Call, and a decision on billing.
