# Kiwi VPN node

One small VPS = one Kiwi VPN region. It runs WireGuard plus `agent.py`, a stdlib-only
HTTPS API (port 8443) that the Price Chat backend uses to add and remove member devices.

## Add a server

1. **Make a VPS:** Ubuntu 24.04, smallest plan (1 vCPU / 1 GB is plenty), from Hetzner,
   DigitalOcean or Vultr, in the region you want. Set up SSH key login.
2. **Run the installer** (as root):
   ```bash
   scp vpn-node/install.sh vpn-node/agent.py root@<server-ip>:/root/
   ssh root@<server-ip> bash /root/install.sh
   ```
   Or without copying files, from a URL you host them at:
   ```bash
   curl -fsSL <raw-url>/install.sh | KIWI_AGENT_URL=<raw-url>/agent.py bash
   ```
3. **Paste the printed values** into Price Chat → **Admin → VPN → Add server**:
   `agent_url` (`https://<ip>:8443`), `agent_token`, `cert_sha256`. Pick a name, region and
   country code. The backend reads the WireGuard public key, endpoint and port from the agent.
4. If the provider has a cloud firewall, allow `22/tcp`, `51820/udp`, `8443/tcp`.

The installer sets up `wg0` (10.66.0.1/16, UDP 51820), IPv4 forwarding, NAT, and an nftables
firewall that allows only SSH, WireGuard and the agent (it replaces any existing ruleset and
turns off ufw). The agent runs as the unprivileged `kiwi-vpn` user with only `CAP_NET_ADMIN`.
Options via env: `PUBLIC_IP`, `SSH_PORT`, `WG_PORT`, `AGENT_PORT`, `POOL`, `DNS`.

## Update / uninstall

- **Update:** copy the new `agent.py` (and `install.sh`) and re-run `bash install.sh`.
  It is idempotent: the server key, TLS cert, token and peers are kept, so nothing changes in Admin.
- **Uninstall:** `bash install.sh --uninstall` (leaves an SSH-only firewall), then delete the
  server in Admin. Or just destroy the VPS.
- **Logs:** `journalctl -u kiwi-vpn-agent` (start/stop and errors only).
- **New token:** delete the `TOKEN=` line in `/etc/kiwi-vpn/agent.env`, re-run the installer,
  paste the new token in Admin → VPN → server → edit.

## No-logs stance

What the node **keeps**: `/var/lib/kiwi-vpn/peers.json`, which is each device's WireGuard
**public key and tunnel IP**. Nothing else. The kernel holds the last handshake time and
byte counters in memory (WireGuard needs them to work). The backend reads them for the
member's device list and monthly usage.

What it **never records**: client IP addresses or endpoints, DNS queries, destinations,
connection logs or traffic content. The agent's HTTP request log is turned off and it never
reads the endpoint column from `wg`. The firewall has no LOG rules, conntrack logging stays
off, and journald keeps system logs for 7 days at most. Client private keys never touch the
server (or our database).

## Agent API (backend only)

`GET /v1/health` (no auth), and with `Authorization: Bearer <token>`: `GET /v1/status`,
`PUT /v1/peers/{key}` `{"allowed_ip": "10.66.0.5/32"}`, `DELETE /v1/peers/{key}`
(key is URL-safe base64). Config: `/etc/kiwi-vpn/agent.env`.

Tests (no `wg` needed): `python -m pytest vpn-node/test_agent.py -q`.
