# Kiwi VPN infrastructure manager (Hetzner Cloud)

`kiwi-infra.mjs` builds a new Kiwi VPN country from `catalog.json`: firewall, server, DNS, WireGuard, browser proxy, TLS,
checks and Admin registration. It needs Node 20+, `ssh`, `tar`, and the Vercel CLI (for DNS).

| Command | Changes anything? |
|---|---|
| `audit` | no: servers, firewalls, IPs, list prices, traffic, warnings |
| `plan <node>` | no: every step and the monthly cost at 0/1/2/5 TB |
| `health [node]` | no: DNS, TLS, the proxy's 407, certificate days left, Hetzner status and traffic |
| `create <node>` | **paid**: a firewall (free) and the server, with delete/rebuild protection |
| `dns <node>` | Vercel DNS: adds the node's A record (refuses to change an existing one) |
| `install <node>` | the new server: copies the committed node files over SSH, checks their sha256 there, runs `install.sh` then `install-proxy.sh`, then `kiwi-vpn-doctor` |
| `register <node>` | no: prints the Admin > VPN fields; the agent token goes to the clipboard only |
| `reboot <node>` | Hetzner reboot of a managed node |

Every command that changes something needs a person at a real terminal typing the phrase it shows, e.g.
`create ash1 21.09 1a2b3c4d`: the node, the monthly cost and a hash of the exact request. Without a terminal (scripts,
agents) it refuses. In Claude Code, run them with `!`, e.g. `! node vpn-node/infra/kiwi-infra.mjs create ash1`. It never
deletes anything, and it won't touch `hel1` (shared with the owner's own tunnel) or `fsn1`.

## A new country

1. Add it to `catalog.json` (`location` and `serverType` from `audit`/`plan`; the plan refuses types that can't be ordered there).
2. `plan` → `create` → `dns` → `install` → `register`, then Admin > VPN > Add server. Every app and the extension list it once it's online.

## What a node gets

- **cloud-init (first boot)**: SSH keys only (no passwords), root by key only, packages upgraded. No secrets in user data
  (Hetzner keeps it, and it's readable on the server's metadata address, which customers can't reach).
- **install.sh**: its own wg0 (51820/udp, IPv4 pool + IPv6 ULA, client IPv6 refused at once so nothing leaks or hangs),
  nftables (default drop; customers can't reach the node, each other, private ranges, metadata or port 25), the no-logs
  unbound resolver, unattended security upgrades, short journald retention, the agent on 8443 **from the backend only**,
  80/443 for the proxy. `QUIET_SECRETS=1`: the agent token is written to `/root/kiwi-node.txt` (root only), never to a log.
- **install-proxy.sh**: the browser proxy on 443 with a Let's Encrypt certificate (auto-renewed), its own user, limited by
  its own nftables table (loaded with the node's ruleset, so a reload never drops it): public internet only, never the
  node's own IPv4 or IPv6 networks, never mail ports.
- **Hetzner firewall** `kiwi-<node>`: 22/tcp, 51820/udp, 80+443/tcp, ICMP; 8443 only from Railway's static IPs.

Recovery: systemd restarts the agent and proxy on failure; the backend marks a node offline after two failed syncs and
alerts the owner (once a Telegram chat is set); `health` exits non-zero on any failure, so it can run on a schedule;
`reboot` is the manual last resort. Nodes hold nothing the backend can't re-send, so they're rebuilt instead of backed up.

## Not tested on a real server yet

The nftables files are checked by `nft -c` on the server before anything is loaded (this PC has no Linux). The first approved
node is the end-to-end test: `install` stops at the first failing step.
