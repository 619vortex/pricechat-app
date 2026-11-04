# Privacy claims against what the system does

Reviewed 2026-11-04 from the code only. **I did not inspect the live servers' configuration or logs**, so anything about them is what the code and install scripts set up. Fixes to website wording need a frontend deploy, and nothing is deployed.

| # | Claim, and where | What the code does | Verdict | Fix |
|---|---|---|---|---|
| 1 | "Free with Kiwi Rank. No subscription. Rank up in the community and it's yours." Homepage VPN box, `Website.tsx` | One 30-day trial, then $9.99 per 30 days. Free only from Royal Kiwi up | **False, and live** | Replace with the real plan |
| 2 | "We never record what you do or where you go." Homepage | No logging of sites in the proxy or agent code. But usage bytes and last-active time are recorded, and "never" covers things the code can't promise, such as hosting and system logs | **Overstated** | "We don't log the sites you visit" with a link to the privacy page |
| 3 | "A private WireGuard VPN for Kiwi Passport members. No traffic logs." Search description, `seo.ts` | Anonymous keys exist too; "no traffic logs" is true of the proxy and agent code but leaves out usage counters | **Imprecise** | Reword |
| 4 | "The sites you visit aren't logged." `/vpn` hero | True of `proxy.py`, `agent.py` and the firewall rules, which have no LOG rules. Not checked on the live servers | **Supported by code, not verified live** | Keep, with the privacy link. Check the live servers once, read-only, with your approval |
| 5 | "We never keep your device's private key." `/vpn` hero | Generated **on our server**, put in the config returned once, never written to the database | **True, but incomplete** | Say it's created on our server and not stored. Better: have the Windows app generate its own key in milestone 4, so the server never holds it |
| 6 | "We don't log ... your DNS lookups." Privacy page | True of our servers. Devices use Cloudflare's 1.1.1.1, so Cloudflare receives the lookups under its own policy | **True, but easy to misread** | The page names Cloudflare under "Who else". Say plainly there that Cloudflare receives the lookups |
| 7 | "These companies provide infrastructure for us; we don't send them your data for their own use." Privacy page | Accurate for what we send; Cloudflare's and hosts' own logging is theirs | **Acceptable** | Keep |
| 8 | Hosting providers' logs "can record" the **extension's** requests. Privacy page | Also true of the website, the WireGuard flow and the coming desktop app. The backend's own access log is off (`--no-access-log`) | **Too narrow** | Widen to all account requests |
| 9 | Devices "switched off and removed after 30 days" | `KEEP_LAPSED = 30 days` | **Matches** | None |
| 10 | "Renewal is by hand, never automatic" | `CARD_SUBSCRIPTIONS = False` | **Matches** | None. Revisit if cards are ever switched on |
| 11 | Sites "see our server's address instead of yours" | IP change proven in the VM with the service. **DNS and IPv6 leak tests haven't been reported** | **Proven for IPv4 only** | Run the leak tests before release |
| 12 | Nothing about the desktop app | The test build asks ipinfo.io and ipapi.co for the public IP while connected | **Missing, not yet public** | Replace with our own endpoint, then add a section on what the app stores before it ships |

Not a false claim, but worth your decision: **customers share the exit IP with your personal `client1` on Helsinki**, so their traffic and yours look the same from outside. The privacy page doesn't mention it.

## Replacing the outside lookups
What changed already: the app asks only **while connected**, when the service sees the VPN server's address, never the customer's. Disconnected, it shows no IP. The earlier "IP didn't change" warning went with that, because it needed the customer's real address.

The plan, with no customer data going to anyone new:
1. **Public IP, milestone 3 deploy:** `GET /api/members/vpn/ip` returns only the caller's address, using the existing `client_ip` logic, with `Cache-Control: no-store`, a rate limit, and nothing stored or logged by our code. Vercel and Railway can see that address on any request anyway, which the privacy page already covers.
2. **Location, milestone 4:** the app knows which server it connected to, and `/members/vpn` gives its name, region and country. So location becomes a fact rather than a guess, and no lookup is needed.
3. **Bring the warning back:** with our own endpoint, the app can compare the address before and after connecting without telling anyone else.
4. **Remove** ipinfo.io and ipapi.co from the code and from the content security policy. A test already fails if the policy and the lookups disagree.
