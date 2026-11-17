# Kiwi VPN: release legal pages, checkout consent, onboarding (2026-11-17)

Branch `feat/kiwi-legal` (from `release/kiwi-hardening` c8c9e0c). Nothing deployed. Payments unchanged: `payments_enabled`,
`VPN_PAYMENTS_PUBLIC` and `CARD_SUBSCRIPTIONS` keep their defaults. No migration. Not legal advice: have a lawyer in your main
market read the pages before payments go public (docs/KIWI-LAUNCH-REVIEW.md §1).

## What changed

| Area | Change |
|---|---|
| `/vpn/terms` | Who provides it and contact; age 18+ (or majority); legality where you are; price, limits, pro-rata, what it buys; manual renewal; EU/UK 14-day right to cancel; ending; liability; governing law and disputes |
| `/vpn/refunds` | New "Right to cancel" section (`#right-to-cancel`), quoting the checkout box word for word; how to cancel; other refunds unchanged |
| `/vpn/privacy` | Controller; legal bases; trial fingerprints and checks; support reports; processors (Hetzner, Railway, Vercel, NOWPayments, Resend, Telegram; DNS via Cloudflare/Hetzner as before); retention from the code; GDPR rights; children. DNS and logging wording unchanged |
| `/vpn/acceptable-use` | In force: the draft note is gone; it's part of the terms |
| Checkout | "Pay with USDT" needs a box ticked (text below). The API refuses a payment without `start_now: true` (400, `X-Kiwi-Reason: start_now`) and records the consent with the payment in `member_audit_logs` (`vpn_payment_consent`, payment id, receipt, wording version). Admin › Kiwi VPN › Payments shows "no start-now consent" for payments without it |
| Onboarding | Sign in from /vpn or /vpn/devices comes back there (`/join?next=/vpn`); the FAQ no longer says the browser extension is unavailable |

Checkout box: "Start my Kiwi VPN time as soon as my payment is confirmed. I understand that if I cancel within 14 days, I get back
only the part of my payment for time I haven't used, and that I can't cancel once all the time I paid for has been used."

## Owner: facts to fill in before deploying

All in one place: `OWNER` at the top of the legal section of `frontend/src/website/community.tsx`. Replace a value with the real
text (or with an empty clause where it says "remove"), run the frontend tests, and update the list in `community.test.tsx`
("lists every owner fact still missing"). A page with any left shows "Being finalised" and highlighted `[OWNER: …]` marks.

| Marker | Pages |
|---|---|
| `[OWNER: legal entity or trader name]` | privacy, terms |
| `[OWNER: postal address]` | privacy, terms |
| `[OWNER: business email for Kiwi VPN]` | privacy, terms, refunds (the address must exist and be read) |
| `[OWNER: company registration number, or remove if there is none]` | terms |
| `[OWNER: VAT number, or remove if not VAT-registered]` | terms |
| `[OWNER: governing-law country]` | terms |
| `[OWNER: courts with jurisdiction]` | terms |
| `[OWNER: whether the price includes VAT or other sales tax]` | terms |
| `[OWNER: how long tax law requires payment records to be kept]` | privacy |
| `[OWNER: safeguard relied on for transfers outside the EU/UK, e.g. Standard Contractual Clauses]` | privacy |

## Owner: choices written into the text (change them if you disagree)

- Right to cancel follows the rule for services: cancelling within 14 days refunds the unused part, and the right ends once all
  the paid time is used. (The launch review's "lose the right once it starts" is the digital-content rule; a VPN is a service.)
- Cancellation refunds: we pay the network fee (the law doesn't let the trader charge for the refund). Other refunds stay "less fees".
- Liability cap: what the customer paid for Kiwi VPN in the 12 months before the claim (the review suggested 30 days; 12 months is
  the more defensible consumer term).
- A change that makes Kiwi VPN materially worse lets the customer end the purchase with a refund of unused time.
- Age: 18+ to use or pay (trial included).

## Handling a cancellation (Admin)

Since 2026-11-17 the Admin action does what `/vpn/refunds` says (`billing.refund_quote` / `refund_by_hand`, tests in
`backend/tests/test_vpn_refunds.py`):

1. Admin › Kiwi VPN › Payments › **Record refund** first shows the quote (nothing changes yet): days paid, days unused, their value
   at that payment's own rate, whether the start-now box was ticked, and the 14-day deadline.
2. Choose **cancellation** (within 14 days: the unused part, we pay the network fee; the **whole payment** if there's no start-now
   consent on record) or **other** (a fault on our side or closing the service: the unused part, less fees). After 14 days only
   "other" is possible.
3. Send the money yourself (NOWPayments or the wallet). Then enter the USD amount actually sent: it can't be more than the policy
   allows. Only that payment's unused days end; used days and later payments' days are untouched.
4. It's recorded once: a second click, a second admin, or NOWPayments' own "refunded" message afterwards changes nothing. Each
   refund leaves an audit row (`vpn_payment_refund`). No money moves from the app.

## Deploy

Backend and frontend together (the backend refuses a payment without `start_now`; the old website doesn't send it, and payments are
in test mode, so the gap only affects the owner's own test). `railway up` from the worktree, then Vercel. No migration. SEO heads unchanged.

## Owner checklist (2026-11-17: no placeholders, no stand-in identity)

No "[OWNER: …]" placeholder is published, nothing is invented, and "the Price Chat team" is never offered as the trader's legal
identity. Each Kiwi VPN page that still lacks facts opens with a "Not yet published" notice naming every missing item, and the text
says plainly what isn't published yet (`withoutOwnerGaps` and `MissingOwnerFacts` in `frontend/src/website/community.tsx`).
Fill each in the `OWNER` map (same file), run `npx vitest run src/website`, deploy: the notice disappears by itself once all are
filled. Public payments (`VPN_PAYMENTS_PUBLIC`) stay off until all are done and a lawyer has read the pages.

1. Legal entity or trader name
2. Postal address
3. Business email for Kiwi VPN (a monitored mailbox)
4. Company registration number (or "none")
5. VAT number (or "not VAT-registered")
6. Governing-law country
7. Courts with jurisdiction
8. Whether $9.99 includes VAT/sales tax
9. How long tax law requires payment records to be kept
10. Safeguard for transfers outside the EU/UK (e.g. Standard Contractual Clauses)
