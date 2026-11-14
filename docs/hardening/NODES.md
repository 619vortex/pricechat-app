# Kiwi VPN nodes: hardening (branch `feat/kiwi-hardening-nodes`)

Scope: the node code in `vpn-node/`: configuration drift, DNS privacy (audit M6/M7), the proxy's load limits (M2) and the
low findings L1, L18, L19, L20. Nothing here has been run against a real server. No server, DNS record, firewall or Hetzner
setting was touched; nothing was deployed or pushed.

## What changed

| Finding | Change | Where |
|---|---|---|
| Drift | `feat/kiwi-infra` merged (includes `feat/kiwi-germany-proxy`'s own-IPv6 refusal, `apply-proxy-ipv6-fix.sh`, `AGENT_ALLOW` / `WITH_PROXY` / nft.d / `QUIET_SECRETS` in `install.sh`, nftables support in `install-proxy.sh`, `node-selftest.sh`, `kiwi-infra.mjs`). Clean merge, no conflicts. | merge `c1e6059` |
| Drift (Helsinki floating IP) | `EXIT_IP` option in `install-shared.sh` and `install-proxy.sh` (SNAT inserted first in nat POSTROUTING, for the wg1 pool and for the `kiwi-proxy` user; nftables nodes get a priority-99 nat table). `apply-floating-ip.sh` writes the hand-made Helsinki setup into code for the running node: netplan `60-kiwi-floating.yaml` (`netplan generate` only, never `apply`), `ip addr add` only if missing, SNAT first, `ENDPOINT_HOST`, `PUBLIC_IPS=<floating>,<main>` + `EXIT_IP`, stray Kiwi NAT rules removed. Dry run by default, `--apply` backs up, `--rollback <dir>`; reports whether Kiwi's SNAT is still first. | `install-shared.sh`, `install-proxy.sh`, `apply-floating-ip.sh`, `proxy.py` (check page shows the first `PUBLIC_IPS`) |
| M6 | New default: customers' DNS = unbound on the wg1 address (no query/reply logs, `access-control` = the pool only, `ip-freebind`), `KIWI-IN` allows udp/tcp 53 to it only. `LOCAL_DNS=0` keeps the old 1.1.1.1. | `install-shared.sh` |
| M7 | The proxy resolves with its own stdlib DNS client against `RESOLVER=127.0.0.1` (unbound `kiwi-proxy.conf`, loopback only, no logs); its guard chain (iptables and nft) allows DNS to 127.0.0.1 only, no longer 127.0.0.53 (systemd-resolved → the provider's resolvers). Empty `RESOLVER` = getaddrinfo as before. The installer checks unbound answers before switching. | `proxy.py`, `install-proxy.sh` |
| M6/M7 for Helsinki | `apply-dns-privacy.sh`: installs unbound if missing (resolvconf hook masked), both configs taken from the installers' `--print-config`, verifies answers on both addresses, then wg1 rules in place, agent (DNS + new agent.py), proxy (new proxy.py, `RESOLVER`, guard). Refuses unknown installed agent.py/proxy.py hashes, other DNS servers on :53, unbound used for something else, non-installer rules files. Keeps the hand-made floating-IP lines. | `apply-dns-privacy.sh` |
| Backend DNS selection | No backend change needed: `_dns()` uses the first DNS the agent reports and `_apply_status()` copies `dns` and `endpoint_host` at every sync. Added a test that proves it for a node switching to its own resolver and a floating endpoint. Devices already configured keep 1.1.1.1 (still works) until they download a new config. | `backend/tests/test_vpn.py` |
| M2 | Only logged-in connections count toward `MAX_TOTAL` (3000); before login each source address may hold 50 (`PRE_LOGIN_PER_IP`, in memory only, never logged); header timeout 20 s → 5 s; TLS handshake 15 s → 10 s. | `proxy.py` |
| L1 | `install-shared.sh` `AGENT_ALLOW` → `ufw allow proto tcp from <ip> to any port 8443` per address and the open rule deleted (empty = as before, with a warning; `install.sh` warns too). Agent: at most 32 request threads, extra connections closed at once. `/v1/health` without the token only from loopback (the doctor and installers use 127.0.0.1); from the network it needs the token. The backend never calls `/v1/health` (checked `backend/app/vpn`), so a deployed backend keeps syncing; `Agent.handle()` keeps its signature (the backend's e2e tests wrap it). | `install-shared.sh`, `install.sh`, `agent.py` |
| L18 | journald 7 days / 200 MB and `ufw logging off` in `install-shared.sh` and `install-proxy.sh` (ufw only where ufw is the firewall). | both |
| L19 | 465 and 587 rejected like 25: `install.sh` (nft), `install-shared.sh` (iptables multiport), and `apply-dns-privacy.sh` for Helsinki's live wg1 rules. The proxy already refused them. | |
| L20 | `register <node> --delete-file` deletes `/root/kiwi-node.txt` after its own typed approval. `CERT_EMAIL` validated and also set on an existing certbot account; catalog `certEmail` (optional, not filled in: the owner's choice) is passed by `kiwi-infra.mjs install`. `health` grades the proxy certificate: warn under 21 days, fail when expired or unreadable; both make the exit code 1 for a scheduled job. | `install-proxy.sh`, `infra/` |
| Task 7 | `kiwi-infra.mjs install <node> --host <ip> [--host-key SHA256:...]` for "external" catalog nodes: a read-only SSH pre-check (Ubuntu 24.04, root, no WireGuard, Docker, own nftables rules or DNS server) before the typed approval; the phrase also covers the address and the pinned host key. Hetzner nodes keep their old phrase. `runInstall()` takes every outside effect as a parameter. | `infra/kiwi-infra.mjs`, `infra/lib.mjs` |
| Tests | `test_apply_ipv6_fix.py` now pins the 2026-11-04 proxy.py (`b840198`) instead of `main`, so it keeps passing after a merge. `--print-config` for `install-shared.sh` and `install-proxy.sh`. `fake_iptables.py` (stateful: tables, chains, order, `-D` of exact specs) and `node_fakes.py` (a fake Helsinki: hand-edited rules files, wg0's generic MASQUERADE, stubs that log every call). | |

## Tests (Windows, Git Bash; exact counts)

| Suite | Command | Result |
|---|---|---|
| vpn-node (Python) | `python -m pytest vpn-node -q -p no:cacheprovider` | 214 passed (was 146 before this branch) |
| vpn-node/infra (Node 24) | `node --test vpn-node/infra/lib.test.mjs vpn-node/infra/install.test.mjs` | 22 passed (was 10) |
| backend VPN | `pytest tests/test_vpn*.py -q -p no:cacheprovider` (with the SQLAlchemy shim) | 193 passed (includes 1 new test) |

New test files: `test_installers.py`, `test_apply_dns_privacy.py`, `test_apply_floating_ip.py`, `infra/install.test.mjs`;
new tests in `test_proxy.py` and `test_agent.py`. Note: `node --test vpn-node/infra/` (a folder) fails on Node 24, so list the files.

No Docker and no shellcheck on this PC, so: `bash -n` on every script, `sh -n` on every generated rules file, the generated
rules run against the fake firewall, and the apply scripts run end to end against the fake server.

### Not proven on a real node

- Real `iptables`/`ip6tables` accepting every generated line (the fake checks order and exact specs, not iptables' parser);
  `-m owner` in nat POSTROUTING; nft's priority-99 nat table winning over the masquerade at 100 (`nft -c` runs on the node first).
- unbound on Ubuntu 24.04 with two `kiwi-*.conf` files merged (interfaces, access-control precedence), `ip-freebind` before wg1
  is up, `unbound-checkconf` with these files, the package not touching `/etc/resolv.conf` (its resolvconf unit is masked first).
- The proxy's DNS client against real unbound (tested against a fake resolver: A/AAAA, NXDOMAIN, TC → TCP, timeouts).
- `ufw allow proto tcp from … to any port …`, `ufw logging off`, `certbot update_account`, `netplan generate` with the merged file.
- Helsinki's real files: the apply scripts were tested against reconstructions (the 2026-11-03/04 installer output plus the
  2026-11-12 hand edits as described). Their dry run shows the real diff; read it before `--apply`.
- `install --host` against a real non-Hetzner server (SSH, host-key pinning, `ssh-keygen -F -l` output).

## Per-node apply plan (owner approval for every step; nothing here was run)

Before any of it: merge this branch, commit, and copy the files from that commit. Check the backend still syncs after each node.

### Helsinki (shared host: owner's wg0 + Kiwi wg1, proxy, floating IP)

1. Copy `apply-floating-ip.sh`, `apply-dns-privacy.sh`, `agent.py`, `proxy.py`, `install-shared.sh`, `install-proxy.sh` to one folder, e.g. `/root/kiwi-hardening/`.
2. `FLOAT_IP=<the floating IP> bash apply-floating-ip.sh` (read-only). Expect: `order: OK`, the address already on eth0, and
   diffs that only swap the hand-made NAT lines for marked ones (plus `PUBLIC_IPS`/`EXIT_IP` in proxy.env and
   `EXIT_IP` in install.env). If the diff shows anything else, stop and compare.
3. **Approval A**: `FLOAT_IP=… bash apply-floating-ip.sh --apply`. Downtime: none for wg1 traffic beyond the moment the
   rules are re-created (well under a second); agent and proxy restart about 2 s each (browser users reconnect by themselves).
   Rollback: `bash apply-floating-ip.sh --rollback /root/kiwi-pre-fip-<time>` (doesn't remove an address it didn't add).
4. `bash apply-dns-privacy.sh` (read-only). Expect: unbound to be installed, two new unbound files, DNS/mail lines in
   wg1-rules.sh, `DNS=10.66.0.1`, `RESOLVER=127.0.0.1`, the proxy's 53 rule moved from 127.0.0.53 to 127.0.0.1, and both
   .py hash changes. It refuses if the installed agent.py/proxy.py aren't the known versions.
5. **Approval B** (installs the `unbound` package on the owner's host; it listens only on 10.66.0.1 and 127.0.0.1):
   `bash apply-dns-privacy.sh --apply`. Downtime: as in 3. Customers keep working throughout: devices configured before
   keep 1.1.1.1 (still allowed); new configs get 10.66.0.1 after the backend's next sync.
   Rollback: `bash apply-dns-privacy.sh --rollback /root/kiwi-pre-dns-<time>`. **Rollback window**: devices whose config
   was downloaded after the switch use 10.66.0.1; after a rollback they need a new config.
6. Check from outside the tunnel: a wg1 device and the browser proxy exit from the floating IP; `dig @10.66.0.1` from a
   device works; the server itself and wg0 still exit from the main IP; `wg show wg0` unchanged.
7. Not done by these scripts on Helsinki (they change the owner's whole host, so they're a separate decision):
   journald 7 days and `ufw logging off` (L18), and narrowing ufw's 8443 to the backend (L1; the Hetzner firewall already does).
   A later `AGENT_ALLOW=<railway ips> bash install-shared.sh` re-run would do both; it keeps `EXIT_IP`/`LOCAL_DNS` from
   install.env, keeps the proxy's agent.env lines and applies the rules in place, but it rewrites wg1-rules.sh and the
   agent unit, so it needs its own approval.

### Germany (fsn1, existing, no proxy yet; see `GERMANY-PROXY.md`)

1. `bash check-node.sh fsn1.vpn.pricechat.live` (read-only) to confirm which interface is Kiwi's and the firewall type.
2. If it's a shared node (ufw + wg1): **approval** for `AGENT_ALLOW=<railway ips> bash install-shared.sh` (re-run: resolver,
   mail ports, allowlist, journald, ufw logging) and then `PROXY_HOST=fsn1.vpn.pricechat.live CERT_EMAIL=<ops address> bash install-proxy.sh`
   (the proxy with its own resolver from the start). The DNS record and Hetzner 80/443 rules from GERMANY-PROXY.md come first.
   Downtime: wg1 rules re-applied in place, agent restart ~2 s.
3. If it turns out to be an install.sh (nftables) node: `bash install.sh` re-run with `WITH_PROXY=1 AGENT_ALLOW=…`, then install-proxy.sh.
4. Rollback: `install-proxy.sh --uninstall`; for install-shared.sh, `LOCAL_DNS=0 bash install-shared.sh` restores 1.1.1.1.

### Ashburn (ash1, built by kiwi-infra: install.sh + nftables + proxy)

1. **Approval**: `node vpn-node/infra/kiwi-infra.mjs install ash1` (shows `reinstall ash1 0.00 <hash>`). It re-runs
   `install.sh` (idempotent: keeps keys, token, cert, peers; adds the 465/587 reject) and `install-proxy.sh` (adds
   `kiwi-proxy.conf` on 127.0.0.1, `RESOLVER`, the guard's 127.0.0.1 rule, the M2 proxy). Set `certEmail` in catalog.json first if wanted.
   Downtime: the nftables ruleset is reloaded (a fraction of a second), the agent and proxy restart (~2 s); wg0 is restarted
   only if its config changed (it doesn't).
2. Then `node vpn-node/infra/kiwi-infra.mjs health ash1` and `kiwi-vpn-doctor` on the node (printed by install).
3. Rollback: re-run `install` from the previous commit (the manager installs exactly the committed files).

## Needs the owner

- Every apply/install above (typed approvals; this agent may not SSH or change servers).
- Choosing an ops email for `CERT_EMAIL` / `certEmail` (left empty: it's personal data).
- Whether Helsinki's host-wide journald/ufw-logging changes are wanted (they also affect the owner's own wg0 host logs).
- No secret was read or printed in this work, so nothing new needs rotating.
