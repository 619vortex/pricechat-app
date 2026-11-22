# Kiwi VPN on Linode (Akamai Cloud): Singapore, London, Amsterdam, Tokyo

Plan A (owner, 2026-11-22): keep Germany (fsn1), Finland (hel1) and the US on Hetzner Ashburn (ash1); add four Linode Nanode 1 GB
nodes. Hetzner's idle `hil1` and `sin1` are deleted only after `sin2` passes every test, and only with the owner's go-ahead.

| Node | Region (Linode id) | Country | Host | Plan | $/month |
|---|---|---|---|---|---|
| sin2 | Singapore (`sg-sin-2`) | SG | sin2.vpn.pricechat.live | g6-nanode-1 (1 vCPU, 1 GB, 1 TB pooled) | 5.00 |
| lon1 | London (`gb-lon`) | GB | lon1.vpn.pricechat.live | g6-nanode-1 | 5.00 |
| ams1 | Amsterdam (`nl-ams`) | NL | ams1.vpn.pricechat.live | g6-nanode-1 | 5.00 |
| tyo1 | Tokyo (`jp-tyo-3`) | JP | tyo1.vpn.pricechat.live | g6-nanode-1 | 5.00 |

Transfer is pooled across the Linode account (1 TB per Nanode), then $0.005/GB (about $5/TB). Region ids: confirm with
`linode-cli regions list` on the day; if `gb-lon`/`sg-sin-2`/`jp-tyo-3` aren't offered, `eu-west`/`ap-south`/`ap-northeast` are the
older sites in the same cities.

Everything that costs money or changes production is run by the owner at a real terminal. Nothing here prints secrets.

## 0. Before buying (once)
1. Linode account in the owner's name; enable 2FA. Read the current Acceptable Use Policy and confirm a commercial VPN is allowed
   (see the policy check in the PR). Keep the abuse contact reachable: Linode expects abuse reports to be answered quickly.
2. Add the owner's SSH public key (the same `id_ed25519` Hetzner uses) under Profile > SSH Keys. No password logins.
3. Create one **Cloud Firewall** `kiwi-node` (free) and attach it to every Kiwi Linode:
   - inbound ACCEPT: TCP 22 (SSH), UDP 51820 (WireGuard), TCP 80 + 443 (Let's Encrypt and the browser proxy), ICMP;
   - inbound ACCEPT TCP 8443 **only** from Railway's three egress IPs (`catalog.json` `agentAllow`);
   - inbound default DROP; outbound default ACCEPT.
   The node's own nftables (`install.sh`, `AGENT_ALLOW`) enforces the same 8443 rule a second time.

## 1. Create one node (repeat per region; start with ONE, e.g. lon1)
In Cloud Manager (or `linode-cli linodes create --type g6-nanode-1 --region gb-lon --image linode/ubuntu24.04
--label kiwi-lon1 --authorized_keys "$(cat ~/.ssh/id_ed25519.pub)" --root_pass <random, not saved> --firewall_id <kiwi-node id>`):
Ubuntu 24.04, Nanode 1 GB, the SSH key, the `kiwi-node` firewall, no backups (stateless node), private IP off.
Note the public IPv4 (and the SSH host key fingerprint from the Linode's LISH console: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`).

## 2. DNS, install, register (approval phrase at each step)
```
! node vpn-node/infra/kiwi-infra.mjs dns lon1 --host <ipv4>
! node vpn-node/infra/kiwi-infra.mjs install lon1 --host <ipv4> --host-key SHA256:<fingerprint>
! node vpn-node/infra/kiwi-infra.mjs register lon1 --host <ipv4>
```
`install` first runs read-only checks (Ubuntu 24.04, root, no WireGuard/Docker yet), uploads only the committed files (sha-checked),
runs `install.sh` (own wg0 on 51820, nftables default-drop, client isolation, no private/metadata ranges, SMTP blocked, IPv6
rejected not routed, unbound resolver on the tunnel address, agent on 8443 for Railway only) and `install-proxy.sh` (browser proxy
on 443, Let's Encrypt), then `kiwi-vpn-doctor`. `register` prints the Admin fields; the agent token is read on the server, never
pasted in chat.

## 3. Add in Admin: TESTERS ONLY
Admin > System > Kiwi VPN > Add server: name (e.g. "United Kingdom (London)"), country GB, agent URL `https://<ipv4>:8443`, the
pinned cert sha256 and token from `register`, capacity 150, `traffic_included_gb` 1000. **Leave "Testers only" on** (the default):
the region is invisible to users until step 5.

## 4. Gate: every test must pass (per region)
- `node vpn-node/infra/kiwi-infra.mjs health` : DNS, TLS (> 21 days), proxy answers 407 without credentials, 8443 filtered from outside.
- From outside (not through any Kiwi tunnel): only 22, 80, 443 TCP and 51820 UDP answer; 8443 does not.
- KIWIVPN > Actions > "Kiwi VPN Windows" > Run workflow on `kiwi-22`, `run_e2e` ticked, with a tester session in `KIWI_CI_SESSION`:
  for the new server it must PASS connect, public IP = the node, DNS only through the node, no IPv6 leak, kill switch, reconnect
  three times, switch country while connected, disconnect (IP back to the runner's).
- Backend: Admin shows the server online for 24 h with no flaps (vpn.sync every 5 min; 2 failed syncs = offline).
- Existing Germany, Finland and Ashburn devices unaffected (their servers untouched; e2e baseline still passes).

## 5. Show to everyone (owner)
Only when step 4 is all green: Admin > the server > "Show to everyone". Then add the host to the uptime monitor (`UPTIME_PROXIES`
variable and TLS host list, see PR #5).

## 6. After sin2 is public and stable
Owner decides: delete Hetzner `kiwi-sin1` and `kiwi-hil1` (turn off delete protection first; deletion is final). Saves $30.68/month.

## Monthly cost, Plan A (all 7 regions)
Germany 8.09 + Finland 15.09 (server + customer IP) + US Ashburn 21.09 + Singapore/London/Amsterdam/Tokyo 4 x 5.00 =
**$64.27/month**, against $74.95 today (which includes the idle sin1 and hil1).
