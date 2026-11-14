#!/usr/bin/env bash
# Kiwi VPN on a server that already runs another WireGuard tunnel (wg0). Adds a separate interface for customers and leaves
# wg0, its config and peers, ufw's policy and SSH alone. Use this instead of install.sh on such a host: install.sh turns ufw off,
# flushes the firewall and rewrites wg0. Never point the agent at wg0: the backend removes peers it doesn't own.
#
#   bash install-shared.sh                 install or re-apply (keeps the key, token, certificate and the options below)
#   bash install-shared.sh --uninstall     remove all of it; wg0 is untouched either way
#   bash install-shared.sh --print-config  dry run: print every generated file and ufw change (no root, no changes; tests)
#
# Customers can reach the internet and nothing else: not wg0, not each other, not this host (except its DNS resolver), no
# private ranges, no mail ports 25/465/587. Their DNS goes to unbound on the wg1 address, with no query logs.
# Run as root, with agent.py next to this script. Env: PUBLIC_IP, WAN_IF (eth0), WG_IF (wg1), WG_PORT (51821), AGENT_PORT (8443),
# POOL (10.66.0.1/16), OTHER_IF (wg0),
#   AGENT_ALLOW  comma-separated IPv4/CIDR that may reach the agent port (the backend's addresses); empty = anyone (warned).
#   EXIT_IP      a second IPv4 on WAN_IF (e.g. a floating IP) customers leave from and connect to: SNAT first in nat
#                POSTROUTING, so wg0's generic MASQUERADE can't shadow it; the agent reports it as the endpoint.
#   LOCAL_DNS    1 (default): unbound on the wg1 address is the customers' DNS. 0: DNS (default 1.1.1.1) as before.
# AGENT_ALLOW, EXIT_IP and LOCAL_DNS are kept on re-runs (env still overrides).
set -euo pipefail

MODE=install
case "${1:-}" in
  --uninstall) MODE=uninstall ;;
  --print-config) MODE=print ;;
  "") ;;
  *) echo "error: unknown option $1" >&2; exit 1 ;;
esac

WAN_IF="${WAN_IF:-eth0}"
WG_IF="${WG_IF:-wg1}"
OTHER_IF="${OTHER_IF:-wg0}"
WG_PORT="${WG_PORT:-51821}"
AGENT_PORT="${AGENT_PORT:-8443}"
POOL="${POOL:-10.66.0.1/16}"
ETC=/etc/kiwi-vpn
LIB=/var/lib/kiwi-vpn
OPT=/opt/kiwi-vpn
SVC_USER=kiwi-vpn
UNIT=/etc/systemd/system/kiwi-vpn-agent.service
RULES="$ETC/$WG_IF-rules.sh"
UNBOUND_CONF=/etc/unbound/unbound.conf.d/kiwi-vpn.conf
JOURNALD_CONF=/etc/systemd/journald.conf.d/kiwi-vpn.conf
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WG_ADDR="${POOL%/*}"

die() { echo "error: $*" >&2; exit 1; }
say() { echo "==> $*"; }
valid_ipv4() { python3 -I -c "import ipaddress as i,sys; a=i.ip_address(sys.argv[1]); assert a.version==4 and not (a.is_loopback or a.is_link_local or a.is_multicast or a.is_unspecified or any(a in i.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','100.64.0.0/10','0.0.0.0/8')))" "$1" 2>/dev/null; }

[ "$WG_IF" != "$OTHER_IF" ] || die "WG_IF must not be $OTHER_IF"

# a re-run keeps the options chosen at install (env still overrides; an empty value given in env turns it off)
if [ "$MODE" != print ] && [ -r "$ETC/install.env" ]; then
  while IFS='=' read -r k v; do
    case "$k" in AGENT_ALLOW|EXIT_IP|LOCAL_DNS) [ -n "${!k+x}" ] || export "$k=$v" ;; esac
  done < "$ETC/install.env"
fi
AGENT_ALLOW="${AGENT_ALLOW:-}"
EXIT_IP="${EXIT_IP:-}"
LOCAL_DNS="${LOCAL_DNS:-1}"
[ "$LOCAL_DNS" = 0 ] || [ "$LOCAL_DNS" = 1 ] || die "LOCAL_DNS is 0 or 1"
[ -z "$AGENT_ALLOW" ] || printf '%s' "$AGENT_ALLOW" | grep -Eq '^[0-9./,]+$' || die "AGENT_ALLOW must be IPv4 addresses or CIDRs, comma-separated"
if [ "$LOCAL_DNS" = 1 ]; then DNS="${DNS:-$WG_ADDR}"; else DNS="${DNS:-1.1.1.1}"; fi

# ---------- generated files (also printed by --print-config) ----------
render_rules() {
  local nat_del nat_add
  if [ -n "$EXIT_IP" ]; then
    nat_add="iptables -t nat -I POSTROUTING 1 -s $POOL_NET -o $WAN_IF -j SNAT --to-source $EXIT_IP"
    nat_del="iptables -t nat -D POSTROUTING -s $POOL_NET -o $WAN_IF -j SNAT --to-source $EXIT_IP"
  else
    nat_add="iptables -t nat -I POSTROUTING 1 -s $POOL_NET -o $WAN_IF -j MASQUERADE"
    nat_del="iptables -t nat -D POSTROUTING -s $POOL_NET -o $WAN_IF -j MASQUERADE"
  fi
  echo '#!/bin/sh'
  echo "# Kiwi VPN customers ($WG_IF). Every rule matches only this interface or its pool. No LOG rules on purpose."
  echo "# NAT goes first in nat POSTROUTING: a generic MASQUERADE for $OTHER_IF further down must not catch customers first."
  echo 'remove() {'
  echo "  iptables -D INPUT -i $WG_IF -j KIWI-IN 2>/dev/null"
  echo "  iptables -D FORWARD -i $WG_IF -j KIWI-FWD 2>/dev/null"
  echo "  iptables -D FORWARD -i $OTHER_IF -o $WG_IF -j REJECT 2>/dev/null"
  echo "  $nat_del 2>/dev/null  # kiwi: nat"
  echo "  iptables -t nat -D POSTROUTING -s $POOL_NET -o $WAN_IF -j MASQUERADE 2>/dev/null  # an older install's rule"
  echo '  iptables -F KIWI-IN 2>/dev/null; iptables -X KIWI-IN 2>/dev/null'
  echo '  iptables -F KIWI-FWD 2>/dev/null; iptables -X KIWI-FWD 2>/dev/null'
  echo '  return 0'
  echo '}'
  echo 'case "$1" in'
  echo '  up)'
  echo '    remove'
  echo '    set -e'
  echo '    iptables -N KIWI-IN'
  echo '    iptables -A KIWI-IN -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT'
  echo "    iptables -A KIWI-IN -d $WG_ADDR -p icmp -j ACCEPT"
  if [ "$LOCAL_DNS" = 1 ]; then
    echo "    iptables -A KIWI-IN -s $POOL_NET -d $WG_ADDR -p udp --dport 53 -j ACCEPT  # kiwi: dns"
    echo "    iptables -A KIWI-IN -s $POOL_NET -d $WG_ADDR -p tcp --dport 53 -j ACCEPT  # kiwi: dns"
  fi
  cat <<EOF
    iptables -A KIWI-IN -j REJECT
    iptables -N KIWI-FWD
    iptables -A KIWI-FWD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
    iptables -A KIWI-FWD -m conntrack --ctstate INVALID -j DROP
    iptables -A KIWI-FWD -o $WG_IF -j REJECT
    iptables -A KIWI-FWD -o $OTHER_IF -j REJECT
    iptables -A KIWI-FWD -d 10.0.0.0/8 -j REJECT
    iptables -A KIWI-FWD -d 100.64.0.0/10 -j REJECT
    iptables -A KIWI-FWD -d 169.254.0.0/16 -j REJECT
    iptables -A KIWI-FWD -d 172.16.0.0/12 -j REJECT
    iptables -A KIWI-FWD -d 192.168.0.0/16 -j REJECT
    iptables -A KIWI-FWD -p tcp -m multiport --dports 25,465,587 -j REJECT --reject-with tcp-reset  # kiwi: no mail
    iptables -A KIWI-FWD -o $WAN_IF -j ACCEPT
    iptables -A KIWI-FWD -j DROP
    iptables -I INPUT 1 -i $WG_IF -j KIWI-IN
    iptables -I FORWARD 1 -i $WG_IF -j KIWI-FWD
    iptables -I FORWARD 1 -i $OTHER_IF -o $WG_IF -j REJECT
    $nat_add  # kiwi: nat
    ;;
  down) remove ;;
esac
EOF
}

render_wg_conf() {
  cat <<EOF
# Kiwi VPN customers. Peers are added at runtime by the agent; $OTHER_IF is separate and never touched from here.
[Interface]
Address = $POOL
ListenPort = $WG_PORT
PostUp = wg set %i private-key /etc/wireguard/$WG_IF.key
PostUp = $RULES up
PostDown = $RULES down
SaveConfig = false
EOF
}

render_unbound() {
  cat <<EOF
# Managed by kiwi-vpn install-shared.sh. Private resolver for $WG_IF customers: only on the tunnel address, only for its pool;
# no query or reply logging of any kind. (The browser proxy's resolver on 127.0.0.1 is install-proxy.sh's kiwi-proxy.conf.)
server:
  interface: $WG_ADDR
  port: 53
  ip-freebind: yes
  access-control: 0.0.0.0/0 refuse
  access-control: $POOL_NET allow
  verbosity: 0
  log-queries: no
  log-replies: no
  log-tag-queryreply: no
  log-local-actions: no
  log-servfail: no
  statistics-interval: 0
  extended-statistics: no
  hide-identity: yes
  hide-version: yes
  qname-minimisation: yes
  aggressive-nsec: yes
  prefetch: yes
  num-threads: 1
  msg-cache-size: 16m
  rrset-cache-size: 32m
EOF
}

render_journald() {
  cat <<EOF
[Journal]
MaxRetentionSec=7day
SystemMaxUse=200M
EOF
}

render_agent_env() {
  cat <<EOF
# Kiwi VPN node agent config (TOKEN is preserved on re-run)
TOKEN=$TOKEN
POOL=$POOL
POOL6=
ENDPOINT_HOST=${EXIT_IP:-$PUBLIC_IP}
LISTEN_PORT=$WG_PORT
DNS=$DNS
ADBLOCK=0
AGENT_BIND=0.0.0.0
AGENT_PORT=$AGENT_PORT
TLS_CERT=$ETC/tls.crt
TLS_KEY=$ETC/tls.key
WG_INTERFACE=$WG_IF
STATE_FILE=$LIB/peers.json
EOF
}

render_ufw() {  # the ufw changes, one per line (run by the installer, printed by --print-config)
  echo "ufw allow $WG_PORT/udp"
  if [ -n "$AGENT_ALLOW" ]; then
    for ip in $(echo "$AGENT_ALLOW" | tr ',' ' '); do echo "ufw allow proto tcp from $ip to any port $AGENT_PORT"; done
    echo "ufw delete allow $AGENT_PORT/tcp"
  else
    echo "ufw allow $AGENT_PORT/tcp"
  fi
  echo "ufw logging off"
}

render_install_env() {
  printf 'AGENT_ALLOW=%s\nEXIT_IP=%s\nLOCAL_DNS=%s\n' "$AGENT_ALLOW" "$EXIT_IP" "$LOCAL_DNS"
}

if [ "$MODE" = print ]; then
  PUBLIC_IP="${PUBLIC_IP:-192.0.2.10}"; TOKEN="(the agent token)"
  POOL_NET="$(python3 -c "import ipaddress,sys; print(ipaddress.ip_interface(sys.argv[1]).network)" "$POOL")"
  echo "##### $RULES #####"; render_rules
  echo "##### /etc/wireguard/$WG_IF.conf #####"; render_wg_conf
  if [ "$LOCAL_DNS" = 1 ]; then echo "##### $UNBOUND_CONF #####"; render_unbound; fi
  echo "##### $JOURNALD_CONF #####"; render_journald
  echo "##### $ETC/agent.env #####"; render_agent_env
  echo "##### $ETC/install.env #####"; render_install_env
  echo "##### ufw #####"; render_ufw
  exit 0
fi

[ "$(id -u)" = 0 ] || die "run as root"

other_state() { { sha256sum "/etc/wireguard/$OTHER_IF.conf" 2>/dev/null; wg show "$OTHER_IF" peers 2>/dev/null; wg show "$OTHER_IF" listen-port 2>/dev/null; } | sha256sum | cut -d' ' -f1; }
BEFORE="$(other_state)"
check_other() { [ "$(other_state)" = "$BEFORE" ] || die "$OTHER_IF changed during this run: stop and investigate"; }

remove_all() {
  systemctl disable --now kiwi-vpn-agent 2>/dev/null || true
  systemctl disable --now "wg-quick@$WG_IF" 2>/dev/null || true
  rm -f "$UNIT" "/etc/wireguard/$WG_IF.conf" "/etc/wireguard/$WG_IF.key"
  systemctl daemon-reload
  ufw delete allow "$WG_PORT/udp" >/dev/null 2>&1 || true
  ufw delete allow "$AGENT_PORT/tcp" >/dev/null 2>&1 || true
  for ip in $(echo "$AGENT_ALLOW" | tr ',' ' '); do ufw delete allow proto tcp from "$ip" to any port "$AGENT_PORT" >/dev/null 2>&1 || true; done
  if [ -f "$UNBOUND_CONF" ]; then
    rm -f "$UNBOUND_CONF"
    # the browser proxy's resolver (install-proxy.sh) keeps unbound running
    if [ -f /etc/unbound/unbound.conf.d/kiwi-proxy.conf ]; then systemctl restart unbound || true; else systemctl disable --now unbound 2>/dev/null || true; fi
  fi
  rm -rf "$ETC" "$LIB" "$OPT"
  userdel "$SVC_USER" 2>/dev/null || true
}

if [ "$MODE" = uninstall ]; then
  say "Removing Kiwi VPN ($WG_IF); $OTHER_IF stays as it is"
  remove_all
  check_other
  say "Removed. Remove the provider firewall's $WG_PORT/udp and $AGENT_PORT/tcp rules too."
  exit 0
fi

[ -f "$HERE/agent.py" ] || die "agent.py must be next to this script"
command -v wg-quick >/dev/null || die "wireguard-tools isn't installed"
ip link show "$WAN_IF" >/dev/null 2>&1 || die "no interface $WAN_IF"
PUBLIC_IP="${PUBLIC_IP:-$(ip -4 -o addr show "$WAN_IF" scope global | awk '{print $4}' | cut -d/ -f1 | head -n1)}"
[ -n "$PUBLIC_IP" ] || die "couldn't find the public IP: set PUBLIC_IP"
POOL_NET="$(python3 -c "import ipaddress,sys; print(ipaddress.ip_interface(sys.argv[1]).network)" "$POOL")"
if ip route show | grep -v "dev $WG_IF" | grep -q "^$POOL_NET"; then die "$POOL_NET is already routed here"; fi
if [ -n "$EXIT_IP" ]; then
  valid_ipv4 "$EXIT_IP" || die "EXIT_IP ($EXIT_IP) isn't a public IPv4 address"
  [ "$EXIT_IP" != "$PUBLIC_IP" ] || die "EXIT_IP is the server's main address: leave it empty"
  ip -4 -o addr show "$WAN_IF" | grep -qF " $EXIT_IP/" || die "$EXIT_IP isn't on $WAN_IF yet: add it first (apply-floating-ip.sh does it for a running node)"
fi
if [ "$LOCAL_DNS" = 1 ]; then
  OTHER53="$( (ss -Hlnup 'sport = :53' || true) | grep -E "($(printf '%s' "$WG_ADDR" | sed 's/\./\\./g')|0\.0\.0\.0|\*):53 " | grep -v '"unbound"' || true)"
  [ -z "$OTHER53" ] || die "another DNS server listens on $WG_ADDR:53 or on every address: $OTHER53"
fi
if [ -z "$AGENT_ALLOW" ]; then echo "warning: AGENT_ALLOW is empty: the agent port $AGENT_PORT is open to everyone in ufw (set it to the backend's addresses)" >&2; fi
WG_ACTIVE=0
systemctl is-active --quiet "wg-quick@$WG_IF" && WG_ACTIVE=1

say "Service user and directories"
id "$SVC_USER" >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$SVC_USER"
install -d -m 0750 -o root -g "$SVC_USER" "$ETC"
install -d -m 0750 -o "$SVC_USER" -g "$SVC_USER" "$LIB"
install -d -m 0755 "$OPT"
install -m 0644 "$HERE/agent.py" "$OPT/agent.py"

say "Logs: the journal keeps 7 days; ufw logs no packets"
install -d /etc/systemd/journald.conf.d
render_journald > "$JOURNALD_CONF"
systemctl restart systemd-journald

if [ "$LOCAL_DNS" = 1 ]; then
  say "Customers' DNS: unbound on $WG_ADDR:53, no query logs"
  command -v unbound >/dev/null || { DEBIAN_FRONTEND=noninteractive apt-get update -qq; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq unbound >/dev/null; }
  systemctl mask -q unbound-resolvconf.service 2>/dev/null || true  # never let the package take over this host's own resolver
  render_unbound > "$UNBOUND_CONF"
  unbound-checkconf >/dev/null || { rm -f "$UNBOUND_CONF"; die "unbound's config doesn't check"; }
  systemctl enable -q unbound
  systemctl restart unbound
fi

say "$WG_IF: its own key, address and firewall rules"
[ -s "/etc/wireguard/$WG_IF.key" ] || (umask 077; wg genkey > "/etc/wireguard/$WG_IF.key")
render_rules > "$RULES"
chmod 0750 "$RULES"
sh -n "$RULES" || die "the generated $RULES doesn't parse"
(umask 077; render_wg_conf > "/etc/wireguard/$WG_IF.conf")

say "Agent certificate, token and config"
if [ ! -s "$ETC/tls.crt" ] || [ ! -s "$ETC/tls.key" ]; then
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 \
    -subj "/CN=kiwi-vpn-node" -addext "subjectAltName=IP:$PUBLIC_IP" -keyout "$ETC/tls.key" -out "$ETC/tls.crt" 2>/dev/null
fi
chown root:"$SVC_USER" "$ETC/tls.key" "$ETC/tls.crt"
chmod 0640 "$ETC/tls.key"; chmod 0644 "$ETC/tls.crt"
TOKEN="$(sed -n 's/^TOKEN=//p' "$ETC/agent.env" 2>/dev/null | head -n1 || true)"
[ "${#TOKEN}" -ge 32 ] || TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
PROXY_LINES="$(grep '^PROXY_' "$ETC/agent.env" 2>/dev/null || true)"  # install-proxy.sh's lines survive a re-run
(umask 027; { render_agent_env; [ -z "$PROXY_LINES" ] || echo "$PROXY_LINES"; } > "$ETC/agent.env")
chown root:"$SVC_USER" "$ETC/agent.env"; chmod 0640 "$ETC/agent.env"
render_install_env > "$ETC/install.env"
cat > "$UNIT" <<EOF
[Unit]
Description=Kiwi VPN node agent ($WG_IF only)
After=network-online.target wg-quick@$WG_IF.service
Wants=network-online.target
Requires=wg-quick@$WG_IF.service
PartOf=wg-quick@$WG_IF.service

[Service]
Type=simple
User=$SVC_USER
Group=$SVC_USER
Environment=KIWI_VPN_CONFIG=$ETC/agent.env
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 -I $OPT/agent.py
Restart=on-failure
RestartSec=3
AmbientCapabilities=CAP_NET_ADMIN
CapabilityBoundingSet=CAP_NET_ADMIN
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=$LIB
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_NETLINK AF_UNIX
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
SystemCallArchitectures=native
SystemCallFilter=@system-service
UMask=0077

[Install]
WantedBy=multi-user.target
EOF

say "Starting $WG_IF and the agent; ufw: $WG_PORT/udp, $AGENT_PORT/tcp${AGENT_ALLOW:+ from $AGENT_ALLOW only}"
render_ufw | while read -r line; do $line >/dev/null 2>&1 || [ "${line#ufw delete}" != "$line" ] || die "failed: $line"; done
systemctl daemon-reload
systemctl enable --now "wg-quick@$WG_IF"
# a re-run on a running node applies the new rules in place (no interface restart: customers' peers stay up)
if [ "$WG_ACTIVE" = 1 ]; then "$RULES" up; fi
systemctl enable -q kiwi-vpn-agent
systemctl restart kiwi-vpn-agent
if [ "$LOCAL_DNS" = 1 ]; then
  ok=0
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    # one A question for one.one.one.one to the customers' resolver; any well-formed answer means it serves them
    if python3 -I - "$WG_ADDR" <<'PY' 2>/dev/null; then ok=1; break; fi
import os, socket, sys
q = os.urandom(2) + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x03one\x03one\x03one\x03one\x00\x00\x01\x00\x01"
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
s.sendto(q, (sys.argv[1], 53)); a = s.recv(1500)
sys.exit(0 if a[:2] == q[:2] and a[2] & 0x80 and a[3] & 0x0F == 0 and int.from_bytes(a[6:8], "big") > 0 else 1)
PY
    sleep 1
  done
  [ "$ok" = 1 ] || die "unbound doesn't answer on $WG_ADDR:53 (journalctl -u unbound): customers' DNS would fail; set LOCAL_DNS=0 and re-run to go back"
fi
check_other

echo
echo "Done. For Admin > VPN > Add server (read the token privately, on the server: grep ^TOKEN= $ETC/agent.env):"
echo "  agent_url:   https://$PUBLIC_IP:$AGENT_PORT"
echo "  cert_sha256: $(openssl x509 -in "$ETC/tls.crt" -outform DER | sha256sum | cut -d' ' -f1)"
echo "Customers: endpoint ${EXIT_IP:-$PUBLIC_IP}:$WG_PORT, DNS $DNS. The backend picks both up at its next sync."
echo "Provider firewall: allow $WG_PORT/udp, and $AGENT_PORT/tcp${AGENT_ALLOW:+ from $AGENT_ALLOW only}."
