# The backend contract the Android app uses

Read from the code on 2026-11-05 (`backend/app/api/routes/desktop.py`, `vpn.py`, `backend/app/vpn/service.py`, `members/desktop_scope.py`, `security/rate_limit.py`), not assumed from the Windows client. All paths are under `https://pricechat.live/api`. No backend change is needed for any of these: they're all live.

## Session
The app's session goes as the cookie `pc_session=kd.<token>`. A `kd.` token can reach only `/members/vpn`, `/members/vpn/*`, `/members/me`, `/members/logout` and `/members/desktop/refresh`. Anything else answers 403 (`desktop_scope`). It lasts 90 days.

## Sign-in
1. The app makes a verifier and opens `https://pricechat.live/vpn/desktop?challenge=<S256, 43 chars>&port=<4-5 digits, 1024+>&state=<16-64 chars>`.
2. The signed-in person presses Allow. The page calls `POST /members/desktop/code {challenge}` and sends the browser to `http://127.0.0.1:<port>/callback?code=&state=`. That's the only destination it can use.
3. `POST /members/desktop/token {code, verifier}` gives `{token, expires_in_days}`. 400 means expired, used or wrong. The first attempt spends the code, right or wrong. Codes last 5 minutes. Limit: `signin`, 10 then 1 per 6 s per visitor.
4. `POST /members/desktop/refresh` gives a new `{token, expires_in_days}`, and the old token stops working.
5. `POST /members/logout` ends the session.

## Account
`GET /members/vpn` answers:
`enabled, test_mode, payments, eligible, reason, max_devices, servers[{id, name, region, country_code, online, load, adblock, recommended, browser}], devices[{id, name, server_id, ip, created_at, last_handshake, rx_bytes, tx_bytes, paused_until}], usage, paid_until, anonymous, has_card, billing, plan{kind, until, days_left}, royal, trial{available, days}`.

`plan.kind`: `trial, paid, tester, owner, royal, free, granted, expired, not_activated, blocked, closed`. Expired trial and expired subscription both read `expired`: `reason` holds the difference.

`POST /members/vpn/trial` takes no input and answers the same shape. 403: unavailable. 409: already used, or already has access. One per account, set by one conditional write. It also counts against `vpn_device`. **The app calls it only when the person presses the button.** The Windows app starts it automatically, so this differs on purpose: your brief said signing in must not create a trial.

`GET /members/me` answers `{member: {username, display_name, xp, ...}, rank, badges, ...}`. The app reads only the name.

`GET /members/vpn/ip` answers `{ip}`, with no session needed. It isn't stored. Limit: `earn`, 30 then 1 per second per visitor.

## Devices
- `POST /members/vpn/devices {name: 1-32 chars, server_id, public_key: 44 chars}` answers 201 `{device, peer}`.
  - 400 bad name or key; 403 not eligible; 404 no server; 409 device limit or key in use; 429 too many new devices; 503 offline or full.
- `GET /members/vpn/devices/{id}/peer` answers the `peer`. 403 not eligible; 404 not yours; 503 offline.
- `DELETE /members/vpn/devices/{id}` answers 204.
- `peer`: `{server_public_key, endpoint_host, listen_port, addresses: ["<ip>/32", optional "<ip6>/128"], dns: [one], allowed_ips: ["0.0.0.0/0", "::/0"], keepalive: 25}`.

## Limits that shaped the app
- **`vpn_device`: 5, then 1 every 10 minutes, per member.** Creating a device or starting a trial spends it, and `DELETE` doesn't refund it. So the app keeps a device for each country and reuses it.
- **A device is one key on one server, and an account has 3 by default.** A PC and a phone each using both countries would need 4. When the account is full, the phone gives up its own device on another country, and nothing else.
- **`dns` always has an entry:** the server's resolver, or `1.1.1.1` if it reports none.
- **IPv6:** `allowed_ips` always includes `::/0`, but `addresses` only has an IPv6 address if the server has a pool. On an IPv4-only server, IPv6 is routed into the tunnel and goes nowhere.

## Known Windows-only wording
`/vpn/desktop` says "Kiwi VPN for Windows" and "this PC". It works on a phone, but the wording is wrong there. A small fix is on this branch and not deployed: see `BACKEND-CHANGES-PROPOSED.md`.
