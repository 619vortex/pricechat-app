# Germany (Falkenstein) browser proxy: diagnosis and runbook

Status: prepared, not applied. Nothing on either server has been changed.

## Why Germany is in the apps but not in Chrome

The apps and the extension read the same server list (`GET /api/members/vpn`), but they filter it differently.

| | Windows / Android | Chrome extension |
|---|---|---|
| Connects with | WireGuard (UDP 51821 on Germany) | HTTPS proxy on TCP 443 (`proxy.py`) |
| Shows a server when | it is enabled and online | `browser: true` (`lib.js` `browserServers`) |

`browser` is `bool(server.proxy_host)` (`backend/app/vpn/service.py` server lists). `proxy_host` is copied from the agent's status at each sync (`_apply_status`), and the agent reports a proxy only when `PROXY_HOST` is set in `/etc/kiwi-vpn/agent.env` (`agent.py` `status`). `install-proxy.sh` sets it. It has only ever been run on Helsinki.

Seen from outside on 2026-11-06: `/api/members/vpn/stats` lists Germany `online: true, browser: false`; TCP 443 and 80 on <Germany IPv4> don't answer; `fsn1.vpn.pricechat.live` doesn't exist. Germany's agent port 8443 doesn't answer from the internet but Railway reaches it (it reports online), so its firewall is tighter than Helsinki's.

## What the fix changes (in order)

1. **DNS** (Vercel DNS, pricechat.live): add `A fsn1.vpn.pricechat.live -> <Germany IPv4>`. No AAAA, matching Helsinki.
2. **Hetzner firewall for the Germany server**: allow inbound TCP 80 (certificate issue and renewal) and TCP 443 (the proxy). Hetzner's `set_rules` replaces every rule, so copy the existing ones (22, 51821/udp, 8443 as it is now) into the new set.
3. **Read-only pre-check on the server**: `bash check-node.sh fsn1.vpn.pricechat.live`. It must say READY. It reports which WireGuard interface is Kiwi's and which isn't, the firewall type, free ports, IPv6 networks and whether DNS points there.
4. **Install**: copy `proxy.py`, `agent.py`, `install-proxy.sh` to the server and run
   `PROXY_HOST=fsn1.vpn.pricechat.live OTHER_IF=<the non-Kiwi interface the pre-check names, if any> bash install-proxy.sh`.
   It adds a `kiwi-proxy` user, a systemd service, a Let's Encrypt certificate, `ufw allow 80,443`, owner-matched iptables/ip6tables rules, and `PROXY_*` lines in agent.env; then restarts the proxy and the agent (not WireGuard). It stops if the other WireGuard interface changes during the run.
5. **Backend**: nothing to deploy. The next `vpn.sync` (or Admin > VPN > Servers > Test) sees the proxy and Germany appears in the extension.
6. **Check**: `curl -x https://fsn1.vpn.pricechat.live:443 http://check.kiwi-vpn.invalid/` gives 407; the extension lists Germany; connecting shows <Germany IPv4>.

Undo: `bash install-proxy.sh --uninstall` on the server, then remove the two firewall rules and the DNS record.

## Security implications

- **New public surface**: TCP 443 (TLS, every request needs a per-browser login; wrong logins get 407) and TCP 80 (only certbot's standalone server, only during issue/renewal).
- **What a browser user can reach**: the public internet only. The proxy refuses private, loopback, link-local and multicast ranges, the server's own IPv4, and mail ports 25/465/587, in `proxy.py` and again in owner-matched firewall rules.
- **IPv6 gap, fixed in this branch**: until now only the server's own *IPv4* was refused. Its own IPv6 /64 (Hetzner gives one per server) wasn't, so a signed-in browser user could open connections to services listening on the server's IPv6, such as SSH, from inside the server, where Hetzner's firewall doesn't apply. Germany accepts SSH password logins (`Permission denied (publickey,password)`), so this matters. `proxy.py` gains `OWN_NETS` and `install-proxy.sh` fills it from the WAN interface and adds matching ip6tables rejects. **Helsinki has the same gap today** (see below).
- **Shared exit**: browser users exit from the server's addresses, as WireGuard customers already do.
- **Agent code replaced**: the installer copies the repo's `agent.py` over the server's. The pre-check prints the current file's hash so the change is visible.
- **Logs**: the proxy logs no destinations; usage is byte counts per login for the data cap.

## SSH access needed

Root (or sudo) on <Germany IPv4>. The `kiwi_vpn` key on the build PC is refused there; Helsinki accepts it. Options: add `~/.ssh/kiwi_vpn.pub` to `/root/.ssh/authorized_keys` on Germany (Hetzner console or rescue), or run the two scripts yourself. While you're there, consider `PasswordAuthentication no` in sshd once a key works (not part of this change).

## Helsinki: the same IPv6 fix, applied on its own

Helsinki already runs the proxy, so it doesn't need the installer again (that would also replace agent.py and restart the
agent). `apply-proxy-ipv6-fix.sh` changes only the proxy:

1. Copy `apply-proxy-ipv6-fix.sh` and the new `proxy.py` to one folder on the server, e.g. `/root/kiwi-ipv6fix/`.
2. `bash apply-proxy-ipv6-fix.sh` (read-only, the default): prints every WireGuard interface with its config hash, port and
   peers, the proxy.py hash change, and the exact diff of `proxy.env` and `rules.sh`. Nothing changes.
3. `bash apply-proxy-ipv6-fix.sh --apply`: backs up the three files to `/root/kiwi-pre-ipv6fix-<time>/`, installs them,
   restarts **only** `kiwi-vpn-proxy` (about 2 s; browser users reconnect by themselves), and checks the new ip6tables reject is
   loaded, 443 listens, and WireGuard (wg0 with client1, and wg1) is byte-for-byte as before. If the proxy doesn't come back it
   restores the backup itself.
4. Undo any time: `bash apply-proxy-ipv6-fix.sh --rollback /root/kiwi-pre-ipv6fix-<time>`.

It refuses to run if the installed proxy.py isn't the 2026-11-04 version (sha256 be1bf8be...), so a hand edit is never
overwritten blindly. Tested against a fake server in `test_apply_ipv6_fix.py` (9 tests: dry run changes nothing, only
`wg show` is ever called, only the proxy restarts, rollback on a failed start, stops on a WireGuard change, refuses bad
networks, idempotent, rollback restores).

Germany gets the fix as part of its first install (`install-proxy.sh` already fills `OWN_NETS`).
