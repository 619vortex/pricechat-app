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

Refund the unused part in NOWPayments, then Admin › Kiwi VPN › Payments › Record refund. Record refund takes back the payment's
**whole** time. That's right when it's the only or the latest payment. If later paid time is queued after it, credit the used part
back by hand (Credit by hand) so the later payment's time isn't cut.

## Deploy

Backend and frontend together (the backend refuses a payment without `start_now`; the old website doesn't send it, and payments are
in test mode, so the gap only affects the owner's own test). `railway up` from the worktree, then Vercel. No migration. SEO heads unchanged.
