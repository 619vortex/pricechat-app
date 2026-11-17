# Kiwi VPN: the first real payment (owner runbook)

Branch `feat/kiwi-release-prep` (2026-11-17). Payments stay **off** until the owner switches them on, and with this branch deployed,
switching them on is a **test that only the owner and named testers can use**. Nobody else is offered a way to pay until
`VPN_PAYMENTS_PUBLIC=true` is set on Railway, which happens only after the steps below pass.

## Why this matters on today's production

Production (release/kiwi-hardening) has Kiwi VPN **open to the public** (`/api/members/vpn/public`: `enabled: true`). There, the
Admin "Payments" switch has no test mode: switching it on offers payment to every visitor at once, before anyone has seen a real
NOWPayments webhook arrive. **Don't switch payments on until this branch's backend is deployed.**

What this branch changes (backend `app/vpn/service.py`, `app/api/routes/vpn.py`, `app/vpn/billing.py`; no migration):

| Change | Effect |
|---|---|
| `VPN_PAYMENTS_PUBLIC` (default off) | Payments on in Admin = test mode: only the owner's browser signed in to Admin, and members named as testers, see prices, can start a USDT payment or make an anonymous key. Everyone else sees exactly what they see with payments off (`payments: false`, no `billing`, same trial and "plan isn't available yet" text, same reminders). |
| Webhook and reconcile still credit | Money that arrives is credited whatever the switch says: webhooks, `vpn.reconcile` and Check now work in test mode and after payments are switched off again. |
| Credited exactly once | The credited amount moves in one conditional UPDATE: the webhook, `vpn.reconcile` and Check now reporting the same payment at the same moment can't add the time twice. A late "expired" report can't mark a credited payment expired. |
| What the webhook saw | `GET /api/vpn/admin/overview` → `billing.ipn`: signed and refused webhook counts, times, the signature form NOWPayments uses (`last_form`), and how many payments were credited by webhook vs by asking the provider. Railway logs: `vpn: NOWPayments webhook (signed form …) for payment N: provider says …, now …, credited +… s`, or `vpn: NOWPayments webhook refused: bad or missing signature`. No secret, signature, address or account is logged. |
| Admin and /vpn labels | Admin › VPN › Payments says "On · test: only you and testers"; /vpn shows "Payments test … Payments here are real" to the owner and testers. |

Verified on production 2026-11-17 (read-only): an unsigned POST to `/api/members/vpn/webhooks/nowpayments` returns
`401 Bad signature` on both pricechat.live and api.pricechat.live, so the IPN secret is set and the callback reaches the backend.
Whether it is the **right** secret is only proven by step 5.

## Before you start

1. Deploy this branch's backend and website (your usual procedure; `railway up` from the worktree, never Railway "Redeploy").
   No migration runs. Check `/api/health/public` is ok and `/api/members/vpn/public` still shows `"payments": false`.
2. In the NOWPayments dashboard: account verified, payout wallet set, **IPN secret** shown there is the one in Railway's
   `NOWPAYMENTS_IPN_SECRET` (don't paste it anywhere; if unsure, generate a new one in NOWPayments and set it on Railway).
3. Have about $12 of USDT on Solana (lowest minimum) or Tron (minimum has been ~$10.95) in your own wallet.

## The test (about 15 minutes)

1. **Admin › System › VPN › Payments: switch on.** The confirmation must say "test mode … Only you … and named testers". The card then
   says "On · test: only you and testers".
2. **Public still sees nothing.** In a private window (not signed in to Admin), or:
   `curl -s https://api.pricechat.live/api/members/vpn/public` → `"payments": false, "billing": null`. If it says `true`, switch
   payments off at once: the deployed backend isn't this branch.
3. **Pay.** In the browser signed in to Admin, open https://pricechat.live/vpn signed in with your Passport. You see "Payments test".
   Add time → USDT on Solana → an amount at or just above the minimum shown → pay the **exact** amount shown, to the address shown,
   on that network. Note the receipt number in Payment history (KV-…).
4. **Wait for confirmation** (Solana: about a minute; Tron: a few minutes). Payment history should go from waiting to credited
   with ~N days (amount ÷ 9.99 × 30) and your plan shows paid time ending on the expected date.
5. **Check how it arrived** (this is the point of the test). Open https://pricechat.live/api/vpn/admin/overview in the
   Admin browser and find `billing.ipn`:
   - `signed` ≥ 1, `credited_by_webhook` = 1, `bad_signature` = 0, `last_form` set (for example `raw`): **the webhook works**. Done.
   - `credited_by_check` = 1 and `bad_signature` ≥ 1: the money was credited by `vpn.reconcile` asking NOWPayments (it checks
     every 10 minutes), but the webhook's signature was refused: **the IPN secret on Railway is wrong**. Fix it (step 2 of
     "Before you start") and repeat with a second small payment before going public.
   - Nothing credited after 20 minutes and both counters 0: the webhook didn't arrive and reconcile hasn't run. Press Check now
     in Payment history; if that credits it, the callback URL is the problem (Railway `MEMBERS_PUBLIC_URL` must be the public address that serves `/api`,
     such as `https://pricechat.live`), otherwise send Claude the receipt number.
   The counters live in the running process and reset on every deploy; the Railway log lines above are the lasting record:
   `railway logs` from `C:\AI\price-chat\backend`, search for `vpn: NOWPayments webhook`.
6. **Compare with NOWPayments.** The dashboard shows the payment `finished` with the same amount; Admin › VPN › Payments shows it
   credited with the same USD amount and days.
7. **Optional refund drill.** Refund it in the NOWPayments dashboard, then Admin › VPN › payment › Record refund. All of that
   payment's days are taken off the account (`refund_by_hand` takes back the whole payment, not part of it).
8. **Switch payments off again** (or leave them in test mode). A payment already started still gets credited.

## Going public (later, owner's decision)

Only after step 5 shows `credited_by_webhook` and the policy items in docs/KIWI-LAUNCH-REVIEW.md §1 (who provides the service and a
contact email, EU withdrawal wording, governing law/age, privacy retention and rights) are done: set `VPN_PAYMENTS_PUBLIC=true`
on Railway `api` (this restarts the service), confirm `/api/members/vpn/public` shows `"payments": true` and the Admin card says
"On · $9.99 for 30 days". To stop public sales again, remove the variable or switch payments off in Admin.

## What Claude can check read-only afterwards

- `curl -s https://api.pricechat.live/api/members/vpn/public` (payments flag as the public sees it)
- `curl -s https://api.pricechat.live/api/health/public` (deploy id, jobs)
- Railway logs from `C:\AI\price-chat\backend` for the two log lines above (payment ids only; no addresses or accounts).
