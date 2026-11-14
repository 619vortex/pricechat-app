# Kiwi VPN: honest privacy text and draft acceptable-use policy

Branch `feat/kiwi-hardening-claims` (from main a7d2081). Fixes the claims flagged by the security audit (M6, M7, L5, I1,
L17): "We don't log the sites you visit" was not accurate while Helsinki WireGuard customers' DNS goes to Cloudflare and the
browser proxy's DNS goes to Hetzner's resolvers in clear text.

## What's true today (what the new text is based on)

- WireGuard DNS: each server's config uses the resolver its agent reports, or Cloudflare 1.1.1.1 if it reports none
  (`backend/app/vpn/service.py` `_dns`). Helsinki (shared host) uses 1.1.1.1; dedicated nodes run unbound with logging off.
- Browser extension (proxy) DNS: systemd-resolved to the hosting provider's (Hetzner's) resolvers, unencrypted.
- A no-log local resolver for the nodes is being built (not deployed). The new text says "depending on the server" and
  tells people how to check (the DNS line in their config), so it stays true when servers switch over.
- Railway and Vercel edge logs keep client IP + path under the platforms' retention.
- WireGuard keeps each peer's last endpoint IP in memory; conntrack holds open flows.
- The DB keeps the account, device names, public keys, private addresses, traffic counts and last handshake/active time.

## Old and new wording

| Where | Old | New |
|---|---|---|
| VPN page hero trust line (`vpn.tsx` TRUST) | We don't log the sites you visit | We don't keep a history of the sites you visit |
| Home page VPN perk (`Website.tsx` VPN_POINTS) | We don't log the sites you visit / Our privacy page lists exactly what we do keep. | We don't keep a history of the sites you visit / Our privacy page names who resolves your DNS and lists what we do keep. |
| VPN FAQ "Do you keep logs?" (`vpn.tsx`) | We don't log the websites you visit, your DNS lookups or the content of your traffic. We do keep your account, your devices' names and keys, and how much data each one used this month and when it was last active. Our privacy page lists all of it, including ordinary server logs. | We don't keep a history of the websites you visit or the content of your traffic. Your DNS lookups are answered by the server's own resolver or passed to a public one, and that one sees the names you look up: Cloudflare (1.1.1.1) for WireGuard devices on some servers, and the hosting provider's resolvers for the browser extension. We do keep your account, your devices' names and public keys, how much data each one used this month and when it last connected. Our privacy page lists all of it, including ordinary server and hosting logs. |
| SEO description `/vpn` (`seo.ts`, `seoHeads.golden.json`, `backend/app/seo/heads.py`) | A WireGuard VPN from Price Chat. We don't log the sites you visit. Start with a 30-day free trial. | A WireGuard VPN from Price Chat. We don't keep a history of the sites you visit, and we name who resolves your DNS. Start with a 30-day free trial. |
| Privacy page, short version | ... We don't record the sites you visit, and we don't sell your data ... | ... We don't keep a history of the sites you visit, and we don't sell your data or use it for advertising. Some things do pass through other companies, such as your DNS lookups: this page names them. |
| Privacy page, usage | ... and when it was last active. | ... and when it was last active (for a WireGuard device, the time of its last handshake with the server). |
| Privacy page, what we don't keep (1) | We don't log the websites or addresses you visit, your DNS lookups or the content of your traffic, and we don't keep a per-connection history of your use. | We don't keep a history of the websites or addresses you visit or of the content of your traffic, and we don't keep a per-connection history of your use. |
| (2) traffic | ... it has to read the destination to carry it there. | ... it has to read the destination to carry it there: while a connection is open, the server's memory holds where it's going. |
| (new) WireGuard endpoint (I1) | (none) | While a WireGuard device is set up on a server, the server's memory holds the IP address it last connected from, because WireGuard needs it to send your traffic back. We don't copy it to our database, and it's gone when the device is removed or the server restarts. |
| (4) hosting logs (L5) | ... our hosting providers' request logs can record them, including your IP address. | ... our hosting providers' (Railway's and Vercel's) request logs record them, including your IP address and the page or API address asked for, and keep them for as long as those providers keep their logs. |
| (new section) Your DNS lookups (M6, M7) | (none) | Every site you open starts with a DNS lookup ... whoever answers those lookups can see the names of the sites you visit. / Depending on the server, they're answered by the server's own resolver, which keeps no log of them, or passed to a public resolver: Cloudflare (1.1.1.1) for WireGuard devices, or the hosting provider's (Hetzner's) resolvers for the browser extension. The extension's lookups to Hetzner aren't encrypted. ... / A WireGuard device's config shows which one it uses on its DNS line ... |
| Extension (L17) | It doesn't cover other apps. | It doesn't cover other apps or private (incognito) windows. |
| Who else is involved | WireGuard devices look up addresses through Cloudflare's public DNS unless the server blocks ads, so Cloudflare receives those lookups under its own privacy policy. | DNS lookups that a server doesn't answer itself go to Cloudflare or Hetzner, as described above. |
| VPN terms, using the service | (ended at "can't buy more time.") | + The acceptable use policy at /vpn/acceptable-use lists the rules in full. |
| General terms, Kiwi VPN | ... and refund policy (/vpn/refunds). | ... and refund policy (/vpn/refunds), and the full rules by the acceptable use policy (/vpn/acceptable-use). |

**When the no-log resolver ships**: no wording change is required. Optionally then tighten the FAQ ("on some servers") and the
DNS section to name the servers that still use a public resolver, or drop the Cloudflare/Hetzner sentences if none do.

## New page: /vpn/acceptable-use (draft)

`LegalPage kind="vpnaup"` in `frontend/src/website/community.tsx`, route in `main.tsx`, SEO head in `seo.ts` / golden /
`heads.py` (so link previews and the sitemap include it). Linked from the VPN page's help box ("Acceptable use"), the VPN
terms and the general terms. Sections: short version, not allowed (illegal use, spam, scanning/attacks, fraud/malware/
harassment, getting around limits), P2P not supported for now, what we can and can't see, abuse reports and suspension,
reporting abuse, changes.

The legal pages already carry a draft banner; this page gets a stronger one through a new optional `note` field:
"Draft for review: this policy isn't in force yet and may change before it is."

**Owner must, before deploy**: approve the text; replace the placeholder `[abuse contact address to be confirmed]` with a
real mailbox (for example abuse@pricechat.live, which then needs to exist and be monitored); decide whether P2P should be
"not supported" or blocked outright. The privacy page wording should get the same review.

## Tests

- `frontend/src/website/community.test.tsx`: new tests for the privacy page (DNS section names Cloudflare and Hetzner, says
  the extension's lookups aren't encrypted, endpoint IP in memory, last handshake, Railway/Vercel logs, incognito; and no
  "We don't log" / "no logs" wording) and the acceptable-use page (draft note, title, all sections, placeholder, links from
  the VPN terms; standard banner unchanged on other pages).
- `frontend/src/website/vpn.test.tsx`: new test for the FAQ answer, hero line and the acceptable-use link; the existing
  "no claim the service can't back up" and "no over-claim" tests still pass.
- `frontend/src/website/Website.test.tsx`: home perk text updated.
- SEO golden: `seo.test.ts` and `backend/tests/test_seo_heads.py` compare both builders with the golden file.
- Results: frontend `npx vitest run` 58 files, 507 tests passed; `npx tsc -b` clean. Backend `test_seo_heads.py`,
  `test_seo_edge_backup.py`, `test_api_hardening.py`, `test_audit_web.py`: 35 passed. The Android and Windows disclosure tests
  are in the full frontend run and pass.

## Deploy steps (after owner approval of the wording)

`backend/app/seo/heads.py` changed (link-preview heads and the sitemap), so this is **frontend + backend**:
1. Merge `feat/kiwi-hardening-claims` into main.
2. Backend: `npx @railway/cli up <worktree>/backend --service api --ci` (path as an argument, never `cd ...;`). No migrations.
3. Frontend: `npx vercel --prod --yes` from `<worktree>/frontend`.
4. Verify: `/vpn`, `/vpn/privacy`, `/vpn/acceptable-use` render; a link preview of `/vpn` (`/api/website/seo/og/vpn`) shows
   the new description; `/sitemap.xml` lists `vpn/acceptable-use`.
If the owner wants the text live before a backend deploy, the frontend can go alone: only link previews and the sitemap lag.
