#!/usr/bin/env bash
# Kiwi VPN on a server that already runs another WireGuard tunnel (wg0). Adds a separate interface for customers and leaves
# wg0, its config and peers, ufw's policy and SSH alone. Use this instead of install.sh on such a host: install.sh turns ufw off,
# flushes the firewall and rewrites wg0. Never point the agent at wg0: the backend removes peers it doesn't own.
#
#   bash install-shared.sh               install or re-apply (keeps the key, token and certificate)
#   bash install-shared.sh --uninstall   remove all of it; wg0 is untouched either way
#
# Customers can reach the internet and nothing else: not wg0, not each other, not this host, no private ranges, no port 25.
# Run as root, with agent.py next to this script. Env: PUBLIC_IP, WAN_IF (eth0), WG_IF (wg1), WG_PORT (51821), AGENT_PORT (8443),
# POOL (10.66.0.1/16), DNS (1.1.1.1), OTHER_IF (wg0).
set -euo pipefail

WAN_IF="${WAN_IF:-eth0}"
WG_IF="${WG_IF:-wg1}"
OTHER_IF="${OTHER_IF:-wg0}"
WG_PORT="${WG_PORT:-51821}"
AGENT_PORT="${AGENT_PORT:-8443}"
POOL="${POOL:-10.66.0.1/16}"
DNS="${DNS:-1.1.1.1}"
ETC=/etc/kiwi-vpn
LIB=/var/lib/kiwi-vpn
OPT=/opt/kiwi-vpn
SVC_USER=kiwi-vpn
UNIT=/etc/systemd/system/kiwi-vpn-agent.service
RULES="$ETC/$WG_IF-rules.sh"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() { echo "error: $*" >&2; exit 1; }
say() { echo "==> $*"; }
[ "$(id -u)" = 0 ] || die "run as root"
[ "$WG_IF" != "$OTHER_IF" ] || die "WG_IF must not be $OTHER_IF"

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
  rm -rf "$ETC" "$LIB" "$OPT"
  userdel "$SVC_USER" 2>/dev/null || true
}

if [ "${1:-}" = "--uninstall" ]; then
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

say "Service user and directories"
id "$SVC_USER" >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$SVC_USER"
install -d -m 0750 -o root -g "$SVC_USER" "$ETC"
install -d -m 0750 -o "$SVC_USER" -g "$SVC_USER" "$LIB"
install -d -m 0755 "$OPT"
install -m 0644 "$HERE/agent.py" "$OPT/agent.py"

say "$WG_IF: its own key, address and firewall rules"
[ -s "/etc/wireguard/$WG_IF.key" ] || (umask 077; wg genkey > "/etc/wireguard/$WG_IF.key")
cat > "$RULES" <<EOF
#!/bin/sh
# Kiwi VPN customers ($WG_IF). Every rule matches only this interface or its pool. No LOG rules on purpose.
remove() {
  iptables -D INPUT -i $WG_IF -j KIWI-IN 2>/dev/null
  iptables -D FORWARD -i $WG_IF -j KIWI-FWD 2>/dev/null
  iptables -D FORWARD -i $OTHER_IF -o $WG_IF -j REJECT 2>/dev/null
  iptables -t nat -D POSTROUTING -s $POOL_NET -o $WAN_IF -j MASQUERADE 2>/dev/null
  iptables -F KIWI-IN 2>/dev/null; iptables -X KIWI-IN 2>/dev/null
  iptables -F KIWI-FWD 2>/dev/null; iptables -X KIWI-FWD 2>/dev/null
  return 0
}
case "\$1" in
  up)
    remove
    set -e
    iptables -N KIWI-IN
    iptables -A KIWI-IN -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
    iptables -A KIWI-IN -d ${POOL%/*} -p icmp -j ACCEPT
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
    iptables -A KIWI-FWD -p tcp --dport 25 -j REJECT --reject-with tcp-reset
    iptables -A KIWI-FWD -o $WAN_IF -j ACCEPT
    iptables -A KIWI-FWD -j DROP
    iptables -I INPUT 1 -i $WG_IF -j KIWI-IN
    iptables -I FORWARD 1 -i $WG_IF -j KIWI-FWD
    iptables -I FORWARD 1 -i $OTHER_IF -o $WG_IF -j REJECT
    iptables -t nat -A POSTROUTING -s $POOL_NET -o $WAN_IF -j MASQUERADE
    ;;
  down) remove ;;
esac
EOF
chmod 0750 "$RULES"
(umask 077; cat > "/etc/wireguard/$WG_IF.conf" <<EOF
# Kiwi VPN customers. Peers are added at runtime by the agent; $OTHER_IF is separate and never touched from here.
[Interface]
Address = $POOL
ListenPort = $WG_PORT
PostUp = wg set %i private-key /etc/wireguard/$WG_IF.key
PostUp = $RULES up
PostDown = $RULES down
SaveConfig = false
EOF
)

say "Agent certificate, token and config"
if [ ! -s "$ETC/tls.crt" ] || [ ! -s "$ETC/tls.key" ]; then
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 \
    -subj "/CN=kiwi-vpn-node" -addext "subjectAltName=IP:$PUBLIC_IP" -keyout "$ETC/tls.key" -out "$ETC/tls.crt" 2>/dev/null
fi
chown root:"$SVC_USER" "$ETC/tls.key" "$ETC/tls.crt"
chmod 0640 "$ETC/tls.key"; chmod 0644 "$ETC/tls.crt"
TOKEN="$(sed -n 's/^TOKEN=//p' "$ETC/agent.env" 2>/dev/null | head -n1 || true)"
[ "${#TOKEN}" -ge 32 ] || TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
(umask 027; cat > "$ETC/agent.env" <<EOF
# Kiwi VPN node agent config (TOKEN is preserved on re-run)
TOKEN=$TOKEN
POOL=$POOL
POOL6=
ENDPOINT_HOST=$PUBLIC_IP
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
)
chown root:"$SVC_USER" "$ETC/agent.env"; chmod 0640 "$ETC/agent.env"
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

say "Starting $WG_IF and the agent; opening $WG_PORT/udp and $AGENT_PORT/tcp"
ufw allow "$WG_PORT/udp" >/dev/null
ufw allow "$AGENT_PORT/tcp" >/dev/null
systemctl daemon-reload
systemctl enable --now "wg-quick@$WG_IF"
systemctl enable --now kiwi-vpn-agent
check_other

echo
echo "Done. For Admin > VPN > Add server (read the token privately, on the server: grep ^TOKEN= $ETC/agent.env):"
echo "  agent_url:   https://$PUBLIC_IP:$AGENT_PORT"
echo "  cert_sha256: $(openssl x509 -in "$ETC/tls.crt" -outform DER | sha256sum | cut -d' ' -f1)"
echo "Provider firewall: allow $WG_PORT/udp and $AGENT_PORT/tcp too."
