# Kiwi VPN for Windows: architecture and audit

Status: **the service made a real VPN connection in the VM on 2026-11-04 (run by the owner). Sign-in and the tester exception are tested but not deployed. The app is being built: see PLAN.md. There is no installer yet.** Rust builds of the app only run in the VM, because Smart App Control blocks them on the host.

## Engine

| Option | Verdict |
|---|---|
| `tunnel.dll` + `wireguard.dll` (WireGuard's embeddable service) | **Chosen.** WireGuard's own project points applications here. It's designed to handle adapter setup, routes and DNS itself. Leak behaviour still has to be tested. Nothing is written by us at the cryptography or routing layer. |
| `wireguard.dll` directly | We would have to write routing, DNS and firewall handling, the parts most likely to leak or break a network. |
| Installing the official WireGuard app silently | Puts a separate WireGuard app on the machine, which the brief rules out. |
| A browser proxy or anything simulated | Ruled out by the brief. |

`wireguard.dll` is signed by WireGuard LLC (SHA-256 of wireguard-nt-1.1.zip starts `DCEB30A9`, matching the value pinned in WireGuard's own build script). Its licence allows distributing it alongside software that uses it only through the published `wireguard.h` API. It must ship unmodified, and WireGuard's name can't be used to promote Kiwi VPN. `tunnel.dll` is built from wireguard-windows (MIT).

## Pieces

```
KiwiVPN.exe (Tauri + React, runs as the user)
   │  login via the system browser; session kept with Windows DPAPI
   │  named pipe \\.\pipe\KiwiVPN, restricted to the signed-in user: Connect(server), Disconnect, Status. Nothing else.
   ▼
KiwiVPN Service (Rust, LocalSystem, installed once)
   │  writes the config where only SYSTEM can read it; creates and starts the tunnel service
   ▼
Tunnel service  WireGuardTunnel$KiwiVPN  (kiwivpn-service.exe /tunnel <config>, loads tunnel.dll)
```

WireGuard's README calls `SERVICE_SID_TYPE_UNRESTRICTED` on the tunnel service essential. The service must validate every config it's given, and only ever launches its own executable.

## Backend contract (audited against the code)

| Need | What exists | Consequence |
|---|---|---|
| Sign-in | Browser cookie `pc_session`, no desktop flow | **Added here, not deployed:** `/vpn/desktop`, `POST /api/members/desktop/code`, `POST /api/members/desktop/token`, migration 0102. One-time code + PKCE, loopback only. The app sends the result as a `pc_session` cookie, so existing routes need no change. |
| Sign out | `POST /api/members/auth/logout` | Reused. |
| Entitlement | `GET /api/members/vpn`: `eligible`, `reason`, `plan`, `trial`, `royal`, `servers`, `devices`, `usage` | Reused. The server decides; nothing local is trusted. |
| Trial | `POST /api/members/vpn/trial` | Reused. |
| Device + config | `POST /api/members/vpn/devices {name, server_id}` returns the only copy of the config, with the private key generated on the server | Reused. Store it encrypted with DPAPI, in SYSTEM-only files. Lost config: delete the device and make a new one. |
| Revoke | `DELETE /api/members/vpn/devices/{id}` | Reused. |
| Servers | `/members/vpn` lists them with `online`, name, region, country. Helsinki 1 and Germany are live. | Dynamic; nothing hardcoded. |

### Things the contract forces

1. **A device belongs to one server.** Finland and Germany each use a slot of the 3-device limit. V1 should create one per country on first use and keep it. A person with 3 devices elsewhere gets a clear "remove a device" message.
2. **New devices are rate limited** (5, then one per 10 minutes, per member). Reusing devices when switching countries avoids this.
3. **Testing while closed:** an owner-named tester (Admin > VPN > People; `POST /api/vpn/admin/testers`) may use the VPN until a date, at most 10 testers and 90 days. Nobody else gains access.
4. **The app's session has the same reach as a browser's** across member routes. A VPN-only scope would be better before a wide release.
5. **Session expiry is a re-login**, after 90 days of no renewal. There is no refresh token.

## Security rules for the service

- The pipe's rule gives the signed-in person read, write and read-attributes only. Generic write would also grant creating a pipe instance, which would let a program of theirs join the service's own pipe under its owner.
- Before writing anything, including a config with its private key, the client requires a pipe owned by SYSTEM or Administrators whose server is in session 0, and connects anonymously so the other end can't act as the person. `fake-service-test.ps1` tries both attacks.
- Any program the person runs can still ask the real service to connect or disconnect. It accepts only a validated config, so this can't run a command, but it could point the tunnel at another server. Worth reviewing before release.
- Pipe open only to the interactive user, with a fixed command set and strict parsing. No paths, commands or arbitrary config from the UI: the service fetches nothing and accepts only a validated WireGuard config.
- Never logs private keys, session tokens or configs.
- Never ships server keys or agent tokens: the installer contains none.
- Kill switch stays off and unadvertised until real leak tests pass.
- Signing: unsigned builds trigger SmartScreen warnings. Azure Artifact Signing is about $9.99 a month, but Microsoft's docs limit individuals to the US and Canada and organizations to listed countries. Otherwise an OV certificate.

## Not done yet

See `RELEASE-CHECKLIST.md`.
