# Helsinki agent port 8443: verified closed to the internet; optional inner layer (owner runs it)

## Finding (2026-11-17)

An earlier report said Helsinki's 8443 was "public (answers 401)". **That was wrong.** The test ran from the owner's PC, whose
traffic goes through the personal WireGuard tunnel (client1) into the Helsinki server itself, so it never passed Hetzner's cloud
firewall. Probed from two independent GitHub-hosted runners instead (KIWIVPN run 44106009166; Linux 51.57.83.103, Windows
20.57.38.48), TCP 8443 **times out** (dropped by the firewall) on 65.109.255.206, 2.29.149.29 and hel1.vpn.pricechat.live, and
on Ashburn 178.156.252.184. Ports 22 and 443 answer; no VPN hostname has an AAAA record.

This matches the note that Helsinki's Hetzner firewall `wg` was narrowed on 2026-11-07 to Railway's outbound IPs
`152.55.180.240`, `162.220.234.242`, `152.55.180.242`. **No action is required.** What follows only adds the same allowlist
in ufw on the server (defence in depth, e.g. against tunnel clients reaching 8443 from inside), if you want it.

## Why it can't interrupt VPN connections

| Port | What uses it | Touched? |
|---|---|---|
| UDP 51820 | the owner's own WireGuard (wg0, client1) | no |
| UDP 51821 | Kiwi VPN customers (wg1) | no |
| TCP 443 | the browser-extension proxy | no |
| TCP 22 | SSH | no |
| TCP 8443 | the Kiwi agent: only the backend on Railway calls it (vpn.sync every 5 minutes, adding/removing devices) | **yes** |

Tunnels and the proxy never use 8443. Worst case if an address is missing: Railway can't reach the agent, so new devices can't be
added on Helsinki and expiry isn't applied there until the rollback. Existing tunnels keep working throughout.

## 0. Before you start (read-only)

1. Railway → service `api` → Settings → Networking → Static Outbound IPs: confirm the three addresses above are still the list.
2. Hetzner Console → Firewalls → `wg`: confirm the TCP 8443 rule's sources are exactly those addresses.
3. `ufw status numbered > /root/ufw-before-8443.txt` (for the rollback).

## 1. ufw (allow first, then remove the open rule: no gap)

```
for ip in 152.55.180.240 162.220.234.242 152.55.180.242; do ufw allow proto tcp from $ip to any port 8443 comment 'kiwi agent: railway'; done
ufw status numbered | grep 8443
# delete "8443/tcp ALLOW IN Anywhere" and "8443/tcp (v6) ALLOW IN Anywhere (v6)", highest number first:
ufw delete <n>
ufw status numbered | grep 8443   # only the three Railway rules are left
```

Save it for later installer re-runs: `AGENT_ALLOW=152.55.180.240,162.220.234.242,152.55.180.242` (install-shared.sh keeps it).

## 2. Check (within 10 minutes)

- Admin → Kiwi VPN → Servers: Helsinki's last sync is newer than the change, no error; no `vpn.sync` error in Railway logs.
- `wg show wg1 latest-handshakes` and `wg show wg0`: handshakes still recent, client1 still there.

## Rollback (seconds)

`ufw allow 8443/tcp`. The backend reconnects at the next vpn.sync.
