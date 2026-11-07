#!/usr/bin/env bash
# The browser proxy for the Kiwi VPN extension, added to a node that already runs the agent (install-shared.sh or install.sh).
# Adds its own user, service, certificate and firewall rules. Never touches wg0, wg1, their peers, SSH or ufw's policy; it stops if wg0 changes.
# Works on shared nodes (ufw + iptables owner rules) and on nodes made by install.sh (nftables: run that with WITH_PROXY=1 first).
#
#   PROXY_HOST=hel1.vpn.example.com bash install-proxy.sh    install or re-apply (the name must already point at this server)
#   bash install-proxy.sh --uninstall                        remove all of it
#
# Run as root, with proxy.py and agent.py next to this script. Env: PROXY_HOST, CERT_EMAIL (optional), PUBLIC_IP, WAN_IF (eth0), OTHER_IF (wg0).
set -euo pipefail

WAN_IF="${WAN_IF:-eth0}"
OTHER_IF="${OTHER_IF:-wg0}"
PROXY_PORT=443
USER_NAME=kiwi-proxy
AGENT_USER=kiwi-vpn
ETC=/etc/kiwi-vpn-proxy
LIB=/var/lib/kiwi-vpn-proxy
OPT=/opt/kiwi-vpn
AGENT_ENV=/etc/kiwi-vpn/agent.env
UNIT=/etc/systemd/system/kiwi-vpn-proxy.service
AGENT_DROPIN=/etc/systemd/system/kiwi-vpn-agent.service.d/proxy.conf
HOOK=/etc/letsencrypt/renewal-hooks/deploy/kiwi-vpn-proxy.sh
RULES="$ETC/rules.sh"
NFT_FILE=/etc/kiwi-vpn/nft.d/kiwi-proxy.nft  # nftables nodes (install.sh): reloaded with the node's ruleset
NFT=0
grep -q "Managed by kiwi-vpn install.sh" /etc/nftables.conf 2>/dev/null && NFT=1
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() { echo "error: $*" >&2; exit 1; }
say() { echo "==> $*"; }
[ "$(id -u)" = 0 ] || die "run as root"

other_state() { { sha256sum "/etc/wireguard/$OTHER_IF.conf" 2>/dev/null; wg show "$OTHER_IF" peers 2>/dev/null; wg show "$OTHER_IF" listen-port 2>/dev/null; } | sha256sum | cut -d' ' -f1; }
BEFORE="$(other_state)"
check_other() { [ "$(other_state)" = "$BEFORE" ] || die "$OTHER_IF changed during this run: stop and investigate"; }

agent_env_clear() { [ -f "$AGENT_ENV" ] && sed -i '/^PROXY_/d' "$AGENT_ENV"; return 0; }

if [ "${1:-}" = "--uninstall" ]; then
  say "Removing the browser proxy; $OTHER_IF and the agent's WireGuard work stay as they are"
  systemctl disable --now kiwi-vpn-proxy 2>/dev/null || true
  [ -x "$RULES" ] && "$RULES" down
  rm -f "$UNIT" "$AGENT_DROPIN" "$HOOK" "$OPT/proxy.py"
  agent_env_clear
  systemctl daemon-reload
  systemctl try-restart kiwi-vpn-agent
  if [ "$NFT" = 1 ]; then
    rm -f "$NFT_FILE"
    nft delete table inet kiwi_proxy 2>/dev/null || true
    echo "note: 80 and 443 stay open in this node's firewall until install.sh is re-run with WITH_PROXY=0" >&2
  else
    ufw delete allow "$PROXY_PORT/tcp" >/dev/null 2>&1 || true
    ufw delete allow 80/tcp >/dev/null 2>&1 || true
  fi
  if [ -f "$ETC/host" ] && command -v certbot >/dev/null; then certbot delete --non-interactive --cert-name "$(cat "$ETC/host")" >/dev/null 2>&1 || true; fi
  rm -rf "$ETC" "$LIB"
  gpasswd -d "$AGENT_USER" "$USER_NAME" >/dev/null 2>&1 || true
  userdel "$USER_NAME" 2>/dev/null || true
  groupdel "$USER_NAME" 2>/dev/null || true
  check_other
  say "Removed. Close 80/tcp and $PROXY_PORT/tcp in the provider's firewall too, and delete the DNS record."
  exit 0
fi

[ -n "${PROXY_HOST:-}" ] || die "set PROXY_HOST, e.g. hel1.vpn.example.com"
printf '%s' "$PROXY_HOST" | grep -Eq '^[a-z0-9.-]+$' || die "PROXY_HOST doesn't look like a host name"
[ -f "$HERE/proxy.py" ] && [ -f "$HERE/agent.py" ] || die "proxy.py and agent.py must be next to this script"
[ -f "$AGENT_ENV" ] || die "no agent here: run install-shared.sh or install.sh first"
PUBLIC_IP="${PUBLIC_IP:-$(ip -4 -o addr show "$WAN_IF" scope global | awk '{print $4}' | cut -d/ -f1 | head -n1)}"
[ -n "$PUBLIC_IP" ] || die "couldn't find the public IP: set PUBLIC_IP"
# This server's own IPv6 networks (Hetzner gives each server a /64): the proxy's users must not reach them either.
OWN_NETS="${OWN_NETS-$(ip -6 -o addr show "$WAN_IF" scope global | awk '{print $4}' | python3 -c 'import ipaddress,sys; print(",".join(sorted({str(ipaddress.ip_interface(l.strip()).network) for l in sys.stdin if l.strip()})))')}"
if [ "$NFT" = 1 ]; then
  # a node made by install.sh: nftables owns the firewall and opens 80/443 itself (WITH_PROXY=1)
  grep -q 'tcp dport { 80, 443 } accept' /etc/nftables.conf || die "this node's firewall doesn't open 80 and 443: re-run install.sh with WITH_PROXY=1 first"
  grep -qF 'include "/etc/kiwi-vpn/nft.d/*.nft"' /etc/nftables.conf || die "this node's install.sh is too old for the proxy: re-run the current install.sh (WITH_PROXY=1)"
else
  command -v ufw >/dev/null && ufw status | grep -q "^Status: active" || die "ufw isn't active on this server: its firewall is set up another way, so opening 80 and 443 needs a different step (stop and check)"
fi
RESOLVED="$(getent ahostsv4 "$PROXY_HOST" | awk '{print $1; exit}' || true)"
[ "$RESOLVED" = "$PUBLIC_IP" ] || die "$PROXY_HOST points at '${RESOLVED:-nothing}', not $PUBLIC_IP: fix DNS first"
if ss -ltn "sport = :$PROXY_PORT" | grep -q LISTEN && ! systemctl is-active --quiet kiwi-vpn-proxy; then die "something else is using port $PROXY_PORT"; fi
if ss -ltn "sport = :80" | grep -q LISTEN; then die "something is using port 80, which the certificate needs"; fi

say "Service user and directories"
id "$USER_NAME" >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$USER_NAME"
usermod -aG "$USER_NAME" "$AGENT_USER"  # the agent writes logins into the shared folder; the proxy gets nothing of the agent's (not its token)
install -d -m 0750 -o root -g "$USER_NAME" "$ETC" "$ETC/tls"
install -d -m 2770 -o "$USER_NAME" -g "$USER_NAME" "$LIB"
install -m 0644 "$HERE/proxy.py" "$OPT/proxy.py"
install -m 0644 "$HERE/agent.py" "$OPT/agent.py"
printf '%s' "$PROXY_HOST" > "$ETC/host"

say "Certificate for $PROXY_HOST (browsers only accept a publicly trusted one for a proxy)"
command -v certbot >/dev/null || { DEBIAN_FRONTEND=noninteractive apt-get update -qq; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq certbot >/dev/null; }
if [ "$NFT" = 0 ]; then
  ufw allow 80/tcp >/dev/null
  ufw allow "$PROXY_PORT/tcp" >/dev/null
fi
EMAIL_ARGS=(--register-unsafely-without-email)
[ -n "${CERT_EMAIL:-}" ] && EMAIL_ARGS=(-m "$CERT_EMAIL")
install -d -m 0755 "$(dirname "$HOOK")"
cat > "$HOOK" <<EOF
#!/bin/sh
# Copies the renewed certificate to where the proxy (not root) can read it, and restarts only the proxy.
set -e
install -m 0640 -o root -g $USER_NAME /etc/letsencrypt/live/$PROXY_HOST/fullchain.pem $ETC/tls/fullchain.pem
install -m 0640 -o root -g $USER_NAME /etc/letsencrypt/live/$PROXY_HOST/privkey.pem $ETC/tls/privkey.pem
# on the first install the service doesn't exist yet
if systemctl cat kiwi-vpn-proxy >/dev/null 2>&1; then systemctl try-restart kiwi-vpn-proxy; fi
EOF
chmod 0755 "$HOOK"
certbot certonly --standalone --preferred-challenges http --non-interactive --agree-tos --keep-until-expiring "${EMAIL_ARGS[@]}" -d "$PROXY_HOST" >/dev/null
"$HOOK"

say "Firewall rules for the proxy's user only: the public internet and nothing else"
if [ "$NFT" = 1 ]; then
V6_DENY="fc00::/7, fe80::/10, ff00::/8"
[ -n "$OWN_NETS" ] && V6_DENY="$(echo "$OWN_NETS" | sed 's/,/, /g'), $V6_DENY"
install -d -m 0750 "$(dirname "$NFT_FILE")"
cat > "$NFT_FILE" <<EOF
# Managed by kiwi-vpn install-proxy.sh. Matches only the $USER_NAME user. No log rules on purpose.
# Loaded with the node's ruleset (install.sh's nftables.conf includes this folder), so a reload never drops it.
table inet kiwi_proxy
delete table inet kiwi_proxy
table inet kiwi_proxy {
  chain output {
    type filter hook output priority 0; policy accept;
    meta skuid "$USER_NAME" jump guard
  }
  chain guard {
    ct state established,related return
    ip daddr 127.0.0.53 meta l4proto { udp, tcp } th dport 53 return
    oif lo reject
    ip daddr $PUBLIC_IP reject
    ip daddr { 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16, 224.0.0.0/4 } reject
    ip6 daddr { $V6_DENY } reject
    tcp dport { 25, 465, 587 } reject
  }
}
EOF
nft -c -f "$NFT_FILE" || die "the proxy's nftables rules don't load"
cat > "$RULES" <<EOF
#!/bin/sh
# nftables node: the rules live in $NFT_FILE (also loaded with the node's ruleset).
case "\$1" in
  up) nft -f $NFT_FILE ;;
  down) nft delete table inet kiwi_proxy 2>/dev/null; exit 0 ;;
esac
EOF
else
cat > "$RULES" <<EOF
#!/bin/sh
# Every rule matches only the $USER_NAME user. No LOG rules on purpose.
remove() {
  iptables -D OUTPUT -m owner --uid-owner $USER_NAME -j KIWI-PROXY 2>/dev/null
  iptables -F KIWI-PROXY 2>/dev/null; iptables -X KIWI-PROXY 2>/dev/null
  ip6tables -D OUTPUT -m owner --uid-owner $USER_NAME -j KIWI-PROXY 2>/dev/null
  ip6tables -F KIWI-PROXY 2>/dev/null; ip6tables -X KIWI-PROXY 2>/dev/null
  return 0
}
case "\$1" in
  up)
    remove
    set -e
    iptables -N KIWI-PROXY
    iptables -A KIWI-PROXY -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    iptables -A KIWI-PROXY -d 127.0.0.53 -p udp --dport 53 -j RETURN
    iptables -A KIWI-PROXY -d 127.0.0.53 -p tcp --dport 53 -j RETURN
    iptables -A KIWI-PROXY -o lo -j REJECT
    iptables -A KIWI-PROXY -d $PUBLIC_IP -j REJECT
    for net in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.168.0.0/16 224.0.0.0/4; do
      iptables -A KIWI-PROXY -d "\$net" -j REJECT
    done
    iptables -A KIWI-PROXY -p tcp -m multiport --dports 25,465,587 -j REJECT
    iptables -I OUTPUT 1 -m owner --uid-owner $USER_NAME -j KIWI-PROXY
    ip6tables -N KIWI-PROXY
    ip6tables -A KIWI-PROXY -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    ip6tables -A KIWI-PROXY -o lo -j REJECT
    for net in $(echo "$OWN_NETS" | tr ',' ' '); do
      ip6tables -A KIWI-PROXY -d "\$net" -j REJECT
    done
    for net in fc00::/7 fe80::/10 ff00::/8; do
      ip6tables -A KIWI-PROXY -d "\$net" -j REJECT
    done
    ip6tables -A KIWI-PROXY -p tcp -m multiport --dports 25,465,587 -j REJECT
    ip6tables -I OUTPUT 1 -m owner --uid-owner $USER_NAME -j KIWI-PROXY
    ;;
  down) remove ;;
esac
EOF
fi
chmod 0750 "$RULES"

say "Proxy service"
(umask 027; cat > "$ETC/proxy.env" <<EOF
PROXY_BIND=0.0.0.0
PROXY_PORT=$PROXY_PORT
TLS_CERT=$ETC/tls/fullchain.pem
TLS_KEY=$ETC/tls/privkey.pem
USERS_FILE=$LIB/users.json
USAGE_FILE=$LIB/usage.json
PUBLIC_IPS=$PUBLIC_IP
OWN_NETS=$OWN_NETS
EOF
)
chown root:"$USER_NAME" "$ETC/proxy.env"
cat > "$UNIT" <<EOF
[Unit]
Description=Kiwi VPN browser proxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
Group=$USER_NAME
Environment=KIWI_PROXY_CONFIG=$ETC/proxy.env
Environment=PYTHONDONTWRITEBYTECODE=1
# the firewall rules come first: if they can't be applied the proxy doesn't start
ExecStartPre=+$RULES up
ExecStart=/usr/bin/python3 -I $OPT/proxy.py
ExecStopPost=+$RULES down
Restart=on-failure
RestartSec=3
LimitNOFILE=8192
MemoryMax=600M
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
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
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
SystemCallArchitectures=native
SystemCallFilter=@system-service
UMask=0027

[Install]
WantedBy=multi-user.target
EOF

say "Telling the agent about it, and letting it write logins"
install -d -m 0755 "$(dirname "$AGENT_DROPIN")"
cat > "$AGENT_DROPIN" <<EOF
[Service]
ReadWritePaths=$LIB
EOF
agent_env_clear
cat >> "$AGENT_ENV" <<EOF
PROXY_HOST=$PROXY_HOST
PROXY_PORT=$PROXY_PORT
PROXY_USERS_FILE=$LIB/users.json
PROXY_USAGE_FILE=$LIB/usage.json
EOF
systemctl daemon-reload
systemctl enable --now kiwi-vpn-proxy
systemctl restart kiwi-vpn-proxy
systemctl restart kiwi-vpn-agent
sleep 2
systemctl is-active --quiet kiwi-vpn-proxy || die "the proxy didn't start: journalctl -u kiwi-vpn-proxy"
systemctl is-active --quiet kiwi-vpn-agent || die "the agent didn't restart: journalctl -u kiwi-vpn-agent"
check_other

echo
echo "Done. https://$PROXY_HOST is the browser proxy. Provider firewall: allow 80/tcp (renewals) and $PROXY_PORT/tcp."
echo "The backend picks it up at the next sync (Admin > VPN > Servers > Test does it now)."
