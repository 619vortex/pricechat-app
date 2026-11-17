# Kiwi VPN: before charging customers (2026-11-12)

Prepared at the owner's request ("Prioritize KiwiVPN production readiness"). Nothing here charges anyone or changes a live
server by itself. I'm not a lawyer: the policy points below are a careful read against common consumer and privacy rules
(EU/UK consumer law, GDPR, the Chrome Web Store and Google Play policies), not legal advice. Have a lawyer in your main
market read the final text before payments go public.

## 1. Policy review: /vpn/terms, /vpn/privacy, /vpn/refunds

What's already good: plain language; no auto-renewal and it says so; trial before paying; what's logged and not logged is
specific and matches the code (no site or DNS logs; byte counts and last-active only); payment data minimised for anonymous
keys (7-day unlink); the extension's permissions explained with the Limited Use statement the Chrome store requires; refunds
are honest about blockchain payments; "nothing takes away your legal rights".

Must fix before charging (each is missing today):

1. **Who you are.** No page names the business or person that sells the service, its country, or a contact email. EU/UK
   consumer law requires the trader's identity and a postal/email contact for a paid service, and payment disputes need a
   channel other than a public Telegram or X message. Proposed (Terms, new first section "Who provides Kiwi VPN"):
   "Kiwi VPN is provided by <legal name>, <city, country>. Contact: <email>. Payment questions: include your receipt number (KV-…)."
   (The Windows app's code-signing certificate is issued to "Avigad Saadia, Dubai, AE"; use whatever name you want public.)
2. **Withdrawal right (EU/UK customers).** A paid digital service carries a 14-day right to cancel unless the customer asks
   for it to start at once AND acknowledges losing that right. Today the refund page only says "nothing takes away your
   rights". Add at checkout a required tick: "Start my Kiwi VPN time now. I understand I lose my 14-day right to cancel once
   it starts." and the same sentence in Refunds. (Code change: a checkbox on the pay form, stored with the payment.)
3. **Governing law, liability, age.** Add to Terms: which country's law applies; that you're not liable beyond the amount
   paid in the last 30 days (except where the law doesn't allow limiting it); that customers must be 18+ (or the age of
   majority where they live) to pay; and that customers are responsible for whether VPN use is legal where they are.
4. **Privacy basics GDPR asks for.** Name the controller (as in 1); the legal basis (to provide the service you asked for;
   payment records to meet tax law); how long payment records are kept (say "as long as tax law requires, usually up to 7
   years" instead of the open-ended "stay for our accounts"); and the user's rights (access, correction, deletion, complaint
   to a regulator) with the contact email.
5. **Shared exit address.** Today customers on Helsinki leave the internet from the same address as the owner's personal
   tunnel. Fix that technically (section 2) rather than disclose it; until then don't take payments.

Should fix (not blocking):

6. Refunds offer money back only for faults on our side within 14 days. Consider a goodwill window (for example "unused
   time in the first 7 days on request") to reduce chargeback-style complaints; your call, it costs money.
7. Terms say "$5 buys about 15 days": correct with today's pro-rata code; keep it in sync if the price changes.
8. Privacy says the Windows app has "an optional kill switch": true for 2.0/2.1. If a future build drops it, update the page.

## 2. Separate the owner's Helsinki exit address from customers

Facts: Helsinki 1 (2.29.149.29) carries the owner's personal WireGuard peer client1 on wg0 and customers on wg1
(10.66.0.0/16, UDP 51821) plus the browser proxy, all leaving from 2.29.149.29.

Recommended: a Hetzner **floating IPv4** on the same server, **$3.50/month** (hel1 price from Hetzner's API today).
Customers' traffic leaves from the new address; wg0 and client1 keep 2.29.149.29. Nobody has to reconnect: the customers'
endpoint address doesn't change (they still connect to 2.29.149.29:51821), only the address websites see.

Steps (each needs the owner's approval; wg0.conf is never touched, checksum 33f1f9f16c0b917c checked before and after):
1. Create the floating IP in hel1 and assign it to ubuntu-2gb-hel1-1 (Hetzner API).
2. Add it to the server's interface persistently (a netplan file `60-kiwi-floating.yaml`, `netplan apply`).
3. NAT: change only wg1's rule from MASQUERADE to `SNAT --to-source <floating IP>` for 10.66.0.0/16 (wg0's rules unchanged).
4. Browser proxy: set its outgoing IPv4 to the floating IP (IPv6 already uses a separate address of the /64).
5. Checks: wg0.conf checksum unchanged; client1 still exits from 2.29.149.29; a test device on wg1 and the proxy show the
   new address; a live customer peer keeps its handshake.
6. Update DNS name hel1.vpn.pricechat.live? No: it's the address customers connect TO, which stays 2.29.149.29.

Alternative: a separate customer server in Finland (from about $5-9/month) and Helsinki 1 set to testers-only. Cleaner
isolation, but existing customers on Helsinki would have to move servers, and you asked to keep connections stable.

## 3. Real payment test (owner's wallet, ~$11 on Tron or ~$5 on Solana)

> 2026-11-17: superseded by docs/hardening/FIRST-PAYMENT.md. Before feat/kiwi-release-prep, "test mode" below only existed while
> Kiwi VPN was closed; with the VPN open to the public (as now) the payments switch offered payment to everyone. That branch adds
> a real payments test mode (`VPN_PAYMENTS_PUBLIC`, off by default) and the webhook checks step 3 relies on.

NOWPayments is configured on Railway (API key and IPN secret set; Stripe off). Steps:
1. Admin › System › VPN: payments ON in **test mode** (only the owner and testers are offered payment).
2. On /vpn signed in as the owner: Add time → USDT on Solana (lowest minimum) → pay the exact amount shown.
3. I then verify from the logs and the API: the webhook arrived with a valid signature (`_np_signature_ok`), the payment
   went pending → finished, the account's paid time grew by the pro-rata seconds, and the receipt shows in Payment history.
4. Expiry: unpaid invoices are marked expired after 14 days (`vpn.reconcile`); paid time ending switches devices off within
   minutes and keeps them 30 days. Both are covered by tests; a live check would mean waiting days, so it's not part of this.
5. Refund: NOWPayments refunds are made in its dashboard by hand; then Admin › VPN › payment › "Record refund" takes the time
   back (`refund_by_hand`), and a provider "refunded" webhook does the same. Do one on the test payment.
6. Payments stay test-mode until the owner says otherwise. Public payments need the owner's approval.

## 4. Ashburn back to testers-only

Admin › System › VPN › Servers › United States (Ashburn) › Testers only. Existing devices on it keep working (sync never
removes a device because its server became testers-only: `service.sync_server`); new customers stop seeing it.
