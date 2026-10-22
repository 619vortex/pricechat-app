#!/usr/bin/env bash
# Kiwi VPN node installer for a fresh Ubuntu 24.04 VPS. Run as root. Idempotent:
# re-running keeps the server key, TLS cert, token and peers, and updates the agent.
#
#   bash install.sh                 install / update
#   bash install.sh --uninstall     remove everything this script installed
#
# Optional env: PUBLIC_IP (auto-detected), SSH_PORT (22), WG_PORT (51820),
#               AGENT_PORT (8443), POOL (10.66.0.1/16), DNS (1.1.1.1,1.0.0.1),
#               KIWI_AGENT_URL (where to download agent.py if it is not next to this script)
set -euo pipefail

SSH_PORT="${SSH_PORT:-$( (sshd -T 2>/dev/null || true) | awk '/^port /{print $2; exit}')}"
SSH_PORT="${SSH_PORT:-22}"
WG_PORT="${WG_PORT:-51820}"
AGENT_PORT="${AGENT_PORT:-8443}"
POOL="${POOL:-10.66.0.1/16}"
DNS="${DNS:-1.1.1.1,1.0.0.1}"
WG_IF=wg0
ETC=/etc/kiwi-vpn
LIB=/var/lib/kiwi-vpn
OPT=/opt/kiwi-vpn
SVC_USER=kiwi-vpn
UNIT=/etc/systemd/system/kiwi-vpn-agent.service
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo .)"

die() { echo "error: $*" >&2; exit 1; }
say() { echo "==> $*"; }

[ "$(id -u)" -eq 0 ] || die "run as root (sudo bash install.sh)"

if [ "${1:-}" = "--uninstall" ]; then
  say "Uninstalling Kiwi VPN node"
  systemctl disable --now kiwi-vpn-agent 2>/dev/null || true
  systemctl disable --now "wg-quick@$WG_IF" 2>/dev/null || true
  rm -f "$UNIT" /etc/sysctl.d/99-kiwi-vpn.conf /etc/systemd/journald.conf.d/kiwi-vpn.conf
  rm -rf "$OPT" "$ETC" "$LIB" "/etc/wireguard/$WG_IF.conf" "/etc/wireguard/$WG_IF.key"
  # Leave a minimal SSH-only firewall rather than an open box.
  cat > /etc/nftables.conf <<EOF
#!/usr/sbin/nft -f
flush ruleset
table inet filter {
  chain input {
    type filter hook input priority 0; policy drop;
    iif lo accept
    ct state established,related accept
    meta l4proto { icmp, ipv6-icmp } accept
    tcp dport $SSH_PORT accept
  }
}
EOF
  nft -f /etc/nftables.conf || true
  userdel "$SVC_USER" 2>/dev/null || true
  systemctl daemon-reload
  sysctl -q -w net.ipv4.ip_forward=0 || true
  say "Done. Packages (wireguard-tools, nftables) were left installed."
  exit 0
fi

grep -q 'VERSION_ID="24.04"' /etc/os-release 2>/dev/null || echo "warning: tested on Ubuntu 24.04 only" >&2

# ---------- packages ----------
say "Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends wireguard-tools nftables python3 openssl curl ca-certificates >/dev/null

# ---------- network facts ----------
WAN_IF="$(ip -4 route show default | awk '{for(i=1;i<NF;i++) if($i=="dev"){print $(i+1); exit}}')"
[ -n "$WAN_IF" ] || die "could not find the default network interface"
if [ -z "${PUBLIC_IP:-}" ]; then
  PUBLIC_IP="$(ip -4 -o addr show dev "$WAN_IF" scope global | awk '{split($4,a,"/"); print a[1]; exit}')"
  case "$PUBLIC_IP" in
    10.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*|192.168.*|"")
      PUBLIC_IP="$(curl -4 -fsS --max-time 10 https://api.ipify.org || true)" ;;
  esac
fi
[ -n "$PUBLIC_IP" ] || die "could not detect the public IP; re-run with PUBLIC_IP=x.x.x.x"
POOL_NET="$(python3 -c "import ipaddress,sys; print(ipaddress.ip_interface(sys.argv[1]).network)" "$POOL")"

# ---------- kernel: forwarding on, no conntrack logging ----------
say "Enabling IPv4 forwarding"
cat > /etc/sysctl.d/99-kiwi-vpn.conf <<EOF
net.ipv4.ip_forward = 1
EOF
sysctl -q -p /etc/sysctl.d/99-kiwi-vpn.conf

# ---------- service user + dirs ----------
id "$SVC_USER" >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$SVC_USER"
install -d -m 0750 -o root -g "$SVC_USER" "$ETC"
install -d -m 0700 -o "$SVC_USER" -g "$SVC_USER" "$LIB"
install -d -m 0755 "$OPT"
install -d -m 0700 /etc/wireguard

# ---------- WireGuard interface ----------
if [ ! -s "/etc/wireguard/$WG_IF.key" ]; then
  say "Generating WireGuard server key"
  (umask 077; wg genkey > "/etc/wireguard/$WG_IF.key")
fi
WG_CONF_NEW="$(cat <<EOF
# Managed by kiwi-vpn install.sh. Peers are added at runtime by the agent.
[Interface]
Address = $POOL
ListenPort = $WG_PORT
PostUp = wg set %i private-key /etc/wireguard/$WG_IF.key
SaveConfig = false
EOF
)"
WG_CHANGED=0
if [ "$(cat "/etc/wireguard/$WG_IF.conf" 2>/dev/null)" != "$WG_CONF_NEW" ]; then
  (umask 077; printf '%s\n' "$WG_CONF_NEW" > "/etc/wireguard/$WG_IF.conf")
  WG_CHANGED=1
fi

# ---------- firewall + NAT (nftables owns the whole ruleset) ----------
say "Writing firewall (SSH $SSH_PORT/tcp, WireGuard $WG_PORT/udp, agent $AGENT_PORT/tcp only)"
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  echo "note: disabling ufw; nftables rules below replace it" >&2
  ufw --force disable >/dev/null
fi
cat > /etc/nftables.conf <<EOF
#!/usr/sbin/nft -f
# Managed by kiwi-vpn install.sh. No log rules on purpose (no traffic logging).
flush ruleset
table inet filter {
  chain input {
    type filter hook input priority 0; policy drop;
    iif lo accept
    ct state invalid drop
    ct state established,related accept
    meta l4proto { icmp, ipv6-icmp } accept
    tcp dport $SSH_PORT accept
    udp dport $WG_PORT accept
    tcp dport $AGENT_PORT accept
  }
  chain forward {
    type filter hook forward priority 0; policy drop;
    ct state established,related accept
    iifname "$WG_IF" oifname "$WAN_IF" ip saddr $POOL_NET accept
  }
  chain output {
    type filter hook output priority 0; policy accept;
  }
}
table ip nat {
  chain postrouting {
    type nat hook postrouting priority 100;
    oifname "$WAN_IF" ip saddr $POOL_NET masquerade
  }
}
EOF
nft -c -f /etc/nftables.conf
systemctl enable -q nftables
systemctl restart nftables

# ---------- TLS cert + token + agent config ----------
if [ ! -s "$ETC/tls.crt" ] || [ ! -s "$ETC/tls.key" ]; then
  say "Generating self-signed TLS certificate (EC P-256, 10 years)"
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 \
    -subj "/CN=kiwi-vpn-node" -addext "subjectAltName=IP:$PUBLIC_IP" \
    -keyout "$ETC/tls.key" -out "$ETC/tls.crt" 2>/dev/null
fi
chown root:"$SVC_USER" "$ETC/tls.key" "$ETC/tls.crt"
chmod 0640 "$ETC/tls.key"; chmod 0644 "$ETC/tls.crt"

TOKEN="$(sed -n 's/^TOKEN=//p' "$ETC/agent.env" 2>/dev/null | head -n1)"
[ "${#TOKEN}" -ge 32 ] || TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
(umask 027; cat > "$ETC/agent.env" <<EOF
# Kiwi VPN node agent config (managed by install.sh; TOKEN is preserved on re-run)
TOKEN=$TOKEN
POOL=$POOL
ENDPOINT_HOST=$PUBLIC_IP
LISTEN_PORT=$WG_PORT
DNS=$DNS
AGENT_BIND=0.0.0.0
AGENT_PORT=$AGENT_PORT
TLS_CERT=$ETC/tls.crt
TLS_KEY=$ETC/tls.key
WG_INTERFACE=$WG_IF
STATE_FILE=$LIB/peers.json
EOF
)
chown root:"$SVC_USER" "$ETC/agent.env"; chmod 0640 "$ETC/agent.env"

# ---------- agent ----------
say "Installing agent"
if [ -f "$SCRIPT_DIR/agent.py" ]; then
  install -m 0644 -o root -g root "$SCRIPT_DIR/agent.py" "$OPT/agent.py"
elif [ -n "${KIWI_AGENT_URL:-}" ]; then
  curl -fsSL --max-time 30 "$KIWI_AGENT_URL" -o "$OPT/agent.py.new"
  python3 -m py_compile "$OPT/agent.py.new"
  install -m 0644 -o root -g root "$OPT/agent.py.new" "$OPT/agent.py"; rm -f "$OPT/agent.py.new"
else
  die "agent.py not found next to install.sh; copy it here or set KIWI_AGENT_URL"
fi

cat > "$UNIT" <<EOF
[Unit]
Description=Kiwi VPN node agent
After=network-online.target wg-quick@$WG_IF.service
Wants=network-online.target
Requires=wg-quick@$WG_IF.service
# Restart with wg0 so peers are re-applied whenever the interface is recreated.
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
# wg needs CAP_NET_ADMIN only; no root.
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
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# journald: keep system logs short-lived (the agent itself logs only start/stop/errors).
install -d /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/kiwi-vpn.conf <<EOF
[Journal]
MaxRetentionSec=7day
SystemMaxUse=200M
EOF
systemctl restart systemd-journald

systemctl daemon-reload
systemctl enable -q "wg-quick@$WG_IF" kiwi-vpn-agent
if [ "$WG_CHANGED" = 1 ] || ! systemctl is-active -q "wg-quick@$WG_IF"; then
  systemctl restart "wg-quick@$WG_IF"
fi
systemctl restart kiwi-vpn-agent

say "Waiting for the agent"
for _ in $(seq 1 20); do
  curl -fsk --max-time 2 "https://127.0.0.1:$AGENT_PORT/v1/health" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsk --max-time 2 "https://127.0.0.1:$AGENT_PORT/v1/health" >/dev/null \
  || die "agent did not start; see: journalctl -u kiwi-vpn-agent -n 50"

CERT_SHA256="$(openssl x509 -in "$ETC/tls.crt" -outform DER | sha256sum | cut -d' ' -f1)"
cat <<EOF

================ Kiwi VPN node ready ================
Paste into Price Chat -> Admin -> VPN -> Add server:

  agent_url:    https://$PUBLIC_IP:$AGENT_PORT
  agent_token:  $TOKEN
  cert_sha256:  $CERT_SHA256

(Filled in automatically from the agent: endpoint $PUBLIC_IP, listen port $WG_PORT/udp,
 address $POOL, server public key $(wg show "$WG_IF" public-key))

If your provider has a cloud firewall, allow: $SSH_PORT/tcp, $WG_PORT/udp, $AGENT_PORT/tcp.
=====================================================
EOF
