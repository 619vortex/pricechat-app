# Kiwi VPN node

One small VPS = one Kiwi VPN region. It runs WireGuard plus `agent.py`, a stdlib-only
HTTPS API (port 8443) that the Price Chat backend uses to add and remove member devices.

## Add a server

1. **Make a VPS:** Ubuntu 24.04, smallest plan (1 vCPU / 1 GB is plenty), from Hetzner,
   DigitalOcean or Vultr, in the region you want. Set up SSH key login.
2. **Run the installer** (as root). Add `--adblock` to turn on the ad & tracker blocklist:
   ```bash
   scp vpn-node/install.sh vpn-node/agent.py root@<server-ip>:/root/
   ssh root@<server-ip> bash /root/install.sh            # or: bash /root/install.sh --adblock
   ```
   Or without copying files, from a URL you host them at:
   ```bash
   curl -fsSL <raw-url>/install.sh | KIWI_AGENT_URL=<raw-url>/agent.py bash -s -- --adblock
   ```
3. **Paste the printed values** into Price Chat → **Admin → VPN → Add server**:
   `agent_url` (`https://<ip>:8443`), `agent_token`, `cert_sha256`. Pick a name, region and
   country code. The backend reads the WireGuard public key, endpoint, port and DNS from the agent.
4. If the provider has a cloud firewall, allow `22/tcp`, `51820/udp`, `8443/tcp`.
5. Run `kiwi-vpn-doctor` once; everything should be `[ OK ]`.

The installer sets up `wg0` (10.66.0.1/16 plus fd66:66::1/64, UDP 51820), IPv4 forwarding, NAT,
unattended security upgrades and an nftables firewall that allows only SSH, WireGuard and the
agent (it replaces any existing ruleset and turns off ufw). The agent runs as the unprivileged
`kiwi-vpn` user with only `CAP_NET_ADMIN`.
Options via env: `PUBLIC_IP`, `SSH_PORT`, `WG_PORT`, `AGENT_PORT`, `POOL`, `POOL6` (`""` = off),
`BLOCKLIST_URL`. `bash install.sh --print-config [--adblock]` prints every file it would
write without changing anything (no root needed).

## DNS and ad & tracker blocking (`--adblock`)

- Every node runs `unbound` as a private recursive resolver listening **only on
  10.66.0.1:53** (the tunnel address; the firewall accepts port 53 only from the VPN pool),
  with no logging. Clients always get `DNS = 10.66.0.1` (`/v1/status` `"dns": ["10.66.0.1"]`),
  so their DNS goes through the tunnel to a resolver we control rather than a third party.
- `bash install.sh --adblock` adds the blocklist and its daily timer and sets `"adblock": true`
  in `/v1/status`. Re-running **without** `--adblock` (the default) removes the timer and
  empties the list; the resolver keeps running. Because the DNS address never changes,
  toggling takes effect for every existing device at once and configs never need re-creating.
- Blocklist: the [StevenBlack unified hosts](https://github.com/StevenBlack/hosts) list
  (ads + malware/trackers), fetched over HTTPS and turned into
  `local-zone: "<domain>." always_nxdomain` (subdomains are blocked too).
- `kiwi-vpn-blocklist.timer` refreshes it daily. Each download must be a sane size
  (100 KB to 50 MB), hold 1,000 to 2,000,000 domains and pass `unbound-checkconf` before it is
  swapped in. On any failure the previous list stays (or is restored), and unbound keeps running.
  Run by hand: `kiwi-vpn-blocklist-update`; logs: `journalctl -u kiwi-vpn-blocklist`.
- False positive? Put the domain (one per line) in `/etc/kiwi-vpn/adblock-allow.txt` and run
  `kiwi-vpn-blocklist-update`. Another list: set `BLOCKLIST_URL=` in `/etc/kiwi-vpn/adblock.env`
  (must be an `https://` hosts-format list).

## Leak and abuse protection

- **IPv6:** clients route `::/0` into the tunnel so IPv6 can never leak around it. The node
  never forwards IPv6 (`net.ipv6.conf.all.forwarding = 0`) and the firewall answers it right
  away with an ICMPv6 "no route", so apps fall back to IPv4 instead of hanging. For that
  answer to reach a client, its config needs the matching ULA address too:
  `Address = <v4>/32, <v6>/128`. `/v1/status` reports wg0's prefix as `"address6"`
  (`"fd66:66::1/64"`, or `null` when `POOL6` is off) and the rule is `ipv6_for()` in `agent.py`:
  `offset = int(peer IPv4) - int(POOL network address)`, `v6 = POOL6 network address + offset`
  (10.66.0.5 → fd66:66::5, 10.66.1.2 → fd66:66::102, 10.66.255.254 → fd66:66::fffe).
  The agent already adds that /128 to each peer. Without it the client's IPv6 is still
  dropped (black-holed) by WireGuard, just not answered.
- **Client isolation:** peers cannot reach each other, nor SSH / the agent / anything else on
  the node through the tunnel (only DNS and ping).
- **No outbound SMTP:** TCP 25 from the pool is rejected (spam abuse protection).
- Private, CGNAT and link-local ranges (incl. the cloud metadata service 169.254.169.254) are
  unreachable from the pool. TCP MSS is clamped to the path MTU.
- Unattended upgrades install security updates daily (no automatic reboot; the doctor says
  when a reboot is needed).

## Update / health / uninstall

- **Update the agent:** copy the new `agent.py` and `install.sh`, then
  `bash install.sh --update`. It replaces only `agent.py`, its systemd unit and
  `kiwi-vpn-doctor`, then restarts the agent. Keys, token, TLS cert, peers, firewall and
  the ad blocking setting are untouched, so nothing changes in Admin.
  (A full `bash install.sh [--adblock]` re-run is also safe and also keeps keys, token, cert
  and peers, but it re-applies all settings: without `--adblock` it turns ad blocking off.)
- **Health check:** `sudo kiwi-vpn-doctor` prints a checklist (services, `wg0` up and port,
  forwarding, firewall rules, agent `/v1/health`, TLS cert expiry, unbound + blocklist age
  (blocklist when ad blocking is on), disk, memory, clock, security updates, peer count and handshake
  ages as counts). It prints no keys, tokens or client IPs, so it is safe to paste.
  Exit code 1 if anything failed.
- **Uninstall:** `bash install.sh --uninstall` (leaves an SSH-only firewall), then delete the
  server in Admin. Or just destroy the VPS.
- **Logs:** `journalctl -u kiwi-vpn-agent` (start/stop and errors only).
- **New token:** delete the `TOKEN=` line in `/etc/kiwi-vpn/agent.env`, re-run the installer
  (with `--adblock` if it is on), paste the new token in Admin → VPN → server → edit.

## What is and isn't logged

What the node **keeps**: `/var/lib/kiwi-vpn/peers.json`, which is each device's WireGuard
**public key and tunnel IP**. Nothing else. The kernel holds the last handshake time and
byte counters in memory (WireGuard needs them to work). The backend reads them for the
member's device list and monthly usage. The DNS resolver (unbound) holds a short-lived
in-memory DNS cache (not per client, never written to disk) and the blocklist file.

What it **never records**: client IP addresses or endpoints, DNS queries or answers,
destinations, connection logs or traffic content.
- The agent's HTTP request log is off and it never reads the endpoint column from `wg`.
- The firewall has no LOG rules and conntrack logging stays off.
- unbound runs with `verbosity: 0`, `log-queries: no`, `log-replies: no`,
  `log-local-actions: no`, `log-servfail: no` and no statistics, so blocked or resolved
  lookups are never written anywhere.
- journald keeps system logs (service start/stop and errors) for 7 days at most.
- `kiwi-vpn-doctor` output contains only counts and health states.

Client private keys never touch the server (or our database).

## Agent API (backend only)

`GET /v1/health` (no auth), and with `Authorization: Bearer <token>`: `GET /v1/status`
(includes `"dns"`, `"adblock": true|false` and `"address6"`), `PUT /v1/peers/{key}`
`{"allowed_ip": "10.66.0.5/32"}`, `DELETE /v1/peers/{key}` (key is URL-safe base64).
Config: `/etc/kiwi-vpn/agent.env`.

Tests (no `wg` needed; the installer tests need `bash` and use `--print-config`):
`python -m pytest vpn-node/test_agent.py -q`.
