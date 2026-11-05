# What the iPhone app uses

Read from the code, not assumed from another client. The full contract, with error codes, is in `android-client/docs/API-CONTRACT.md`, and it applies unchanged. `backend/tests/test_ios_contract.py` holds this app's fixtures against the real backend.

| Purpose | Route | Live now? | Notes |
|---|---|---|---|
| Allow page | `/vpn/desktop?challenge&port&state&client=ios` | Yes | The live page ignores `client` |
| Make code | `POST /api/members/desktop/code` | Yes | Called by the page |
| Swap code | `POST /api/members/desktop/token {code, verifier}` | Yes | Code lasts 5 minutes, one try |
| Refresh | `POST /api/members/desktop/refresh` | Yes | About weekly. The old token dies |
| Sign out | `POST /api/members/logout` | Yes | |
| Account, plan, servers, devices | `GET /api/members/vpn` | Yes | |
| Trial | `POST /api/members/vpn/trial` | Yes | Only on the person's press. One per account, enforced by the server |
| New device | `POST /api/members/vpn/devices {name, server_id, public_key}` | Yes | Limited to 5, then 1 per 10 minutes |
| Peer | `GET /api/members/vpn/devices/{id}/peer` | Yes | |
| Remove device | `DELETE /api/members/vpn/devices/{id}` | Yes | |
| Exit address | `GET /api/members/vpn/ip` | Yes | Not stored |
| Name | `GET /api/members/me` | Yes | |
| **Passport, capabilities** | **`GET /api/members/vpn/v1/state?platform=ios&version=`** | **No: 404 until deployed** | The app shows a link to the website until then |

**Session:** `Cookie: pc_session=kd.<token>`, limited by `members/desktop_scope.py` to VPN routes, so a stolen iPhone token can't spend or change an account. It's recorded as a `desktop` session, which is cosmetic wording on the website.

**Servers today (from the public `/api/members/vpn/stats`):** id 2 Germany (Falkenstein, DE), id 1 Helsinki 1 (Finland, FI). The app reads them from the API, and nothing about them is hard-coded.

## Differences from Android, on purpose
- **Device names have a random per-install part:** `iPhone abcd1234 (2)`. The cleanup of a device whose key is gone matches by name, so two phones with the same name on one account could delete each other's devices. **Android's names, based on the phone model, have this bug:** two identical phones could hit it. It's flagged, not changed, as you asked.
- **The private key comes from CryptoKit,** not WireGuardKit, because only the extension links WireGuard's Go library.
- **The extension never sends wireguard-go's text to the app,** since it holds the private key. It sends three figures.

## Needed from the backend
Nothing, for a working VPN. Two optional changes are on this branch, not deployed:
1. `backend/app/vpn/unified.py`: the capability table gains `ios`. It needs the Phase 1 deploy, which is still awaiting your approval.
2. `frontend/src/website/vpnDesktop.tsx`: `client=ios` sends the code to `kiwivpn://callback`, and the page says "iPhone". See `SIGN-IN.md`.
