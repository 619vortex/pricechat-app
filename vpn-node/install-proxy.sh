#!/usr/bin/env bash
# The browser proxy for the Kiwi VPN extension, added to a node that already runs the agent (install-shared.sh or install.sh).
# Adds its own user, service, certificate, firewall rules and a private resolver. Never touches wg0, wg1, their peers, SSH or
# ufw's policy; it stops if wg0 changes.
# Works on shared nodes (ufw + iptables owner rules) and on nodes made by install.sh (nftables: run that with WITH_PROXY=1 first).
#
#   PROXY_HOST=hel1.vpn.example.com bash install-proxy.sh    install or re-apply (the name must already point at this server)
#   bash install-proxy.sh --uninstall                        remove all of it
#   bash install-proxy.sh --print-config                     dry run: print every generated file (no root, no changes; tests)
#
# Run as root, with proxy.py and agent.py next to this script. Env: PROXY_HOST, CERT_EMAIL (ops address for Let's Encrypt
# expiry mail; recommended), PUBLIC_IP, WAN_IF (eth0), OTHER_IF (wg0),
#   EXIT_IP     a second IPv4 on WAN_IF (e.g. a floating IP) the proxy's traffic leaves from: SNAT for the proxy's user only,
#               placed first in nat POSTROUTING so another tunnel's MASQUERADE can't shadow it. Kept on re-runs.
#   LOCAL_DNS   1 (default): the proxy resolves names with its own unbound on 127.0.0.1 (no query logs), and its firewall
#               allows DNS to that resolver only. 0: the system resolver (systemd-resolved, the provider's servers) as before.
set -euo pipefail

MODE=install
case "${1:-}" in
  --uninstall) MODE=uninstall ;;
  --print-config) MODE=print ;;
  "") ;;
  *) echo "error: unknown option $1" >&2; exit 1 ;;
esac

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
UNBOUND_CONF=/etc/unbound/unbound.conf.d/kiwi-proxy.conf
JOURNALD_CONF=/etc/systemd/journald.conf.d/kiwi-vpn.conf
RESOLVER=127.0.0.1
LOCAL_DNS="${LOCAL_DNS:-1}"
NFT=0
grep -q "Managed by kiwi-vpn install.sh" /etc/nftables.conf 2>/dev/null && NFT=1
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() { echo "error: $*" >&2; exit 1; }
say() { echo "==> $*"; }
# a routable IPv4: not private, shared, loopback, link-local, multicast or unspecified (documentation ranges pass, for tests)
valid_ipv4() { python3 -I -c "import ipaddress as i,sys; a=i.ip_address(sys.argv[1]); assert a.version==4 and not (a.is_loopback or a.is_link_local or a.is_multicast or a.is_unspecified or any(a in i.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','100.64.0.0/10','0.0.0.0/8')))" "$1" 2>/dev/null; }

# ---------- generated files (also printed by --print-config) ----------
render_unbound() {
  cat <<EOF
# Managed by kiwi-vpn install-proxy.sh. The browser proxy's private resolver: loopback only, no query or reply logs.
# (install.sh / install-shared.sh keep their own kiwi-vpn.conf on the tunnel address; unbound merges both.)
server:
  interface: $RESOLVER
  port: 53
  access-control: 127.0.0.0/8 allow
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
  # the proxy refuses private answers anyway; this keeps a public name from pointing inside at the resolver too
  private-address: 10.0.0.0/8
  private-address: 100.64.0.0/10
  private-address: 169.254.0.0/16
  private-address: 172.16.0.0/12
  private-address: 192.168.0.0/16
  private-address: fc00::/7
  private-address: fe80::/10
EOF
}

render_journald() {
  cat <<EOF
[Journal]
MaxRetentionSec=7day
SystemMaxUse=200M
EOF
}

dns_allow_ipt() {  # the proxy's user may reach its resolver on loopback, and nothing else there
  local ip=127.0.0.53
  if [ "$LOCAL_DNS" = 1 ]; then ip="$RESOLVER"; fi
  echo "    iptables -A KIWI-PROXY -d $ip -p udp --dport 53 -j RETURN"
  echo "    iptables -A KIWI-PROXY -d $ip -p tcp --dport 53 -j RETURN"
}

snat_line() {  # $1 = -I POSTROUTING 1 / -D POSTROUTING
  echo "iptables -t nat $1 -m owner --uid-owner $USER_NAME -o $WAN_IF -j SNAT --to-source $EXIT_IP"
}

render_rules_ipt() {
  echo '#!/bin/sh'
  echo "# Every rule matches only the $USER_NAME user. No LOG rules on purpose."
  echo 'remove() {'
  echo "  iptables -D OUTPUT -m owner --uid-owner $USER_NAME -j KIWI-PROXY 2>/dev/null"
  echo '  iptables -F KIWI-PROXY 2>/dev/null; iptables -X KIWI-PROXY 2>/dev/null'
  echo "  ip6tables -D OUTPUT -m owner --uid-owner $USER_NAME -j KIWI-PROXY 2>/dev/null"
  echo '  ip6tables -F KIWI-PROXY 2>/dev/null; ip6tables -X KIWI-PROXY 2>/dev/null'
  if [ -n "$EXIT_IP" ]; then echo "  $(snat_line "-D POSTROUTING") 2>/dev/null  # kiwi: exit ip"; fi
  cat <<EOF
  return 0
}
case "\$1" in
  up)
    remove
    set -e
    iptables -N KIWI-PROXY
    iptables -A KIWI-PROXY -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
$(dns_allow_ipt)
    iptables -A KIWI-PROXY -o lo -j REJECT
    iptables -A KIWI-PROXY -d $PUBLIC_IP -j REJECT
EOF
  if [ -n "$EXIT_IP" ]; then echo "    iptables -A KIWI-PROXY -d $EXIT_IP -j REJECT"; fi
  cat <<EOF
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
EOF
  # first in nat POSTROUTING: another tunnel's generic MASQUERADE further down must not catch the proxy's traffic first
  if [ -n "$EXIT_IP" ]; then echo "    $(snat_line "-I POSTROUTING 1")  # kiwi: exit ip"; fi
  echo '    ;;'
  echo '  down) remove ;;'
  echo 'esac'
}

render_nft() {
  local v6_deny="fc00::/7, fe80::/10, ff00::/8" dns_ip=127.0.0.53 own4="$PUBLIC_IP"
  if [ -n "$OWN_NETS" ]; then v6_deny="$(echo "$OWN_NETS" | sed 's/,/, /g'), $v6_deny"; fi
  if [ "$LOCAL_DNS" = 1 ]; then dns_ip="$RESOLVER"; fi
  if [ -n "$EXIT_IP" ]; then own4="{ $PUBLIC_IP, $EXIT_IP }"; fi
  cat <<EOF
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
    ip daddr $dns_ip meta l4proto { udp, tcp } th dport 53 return
    oif lo reject
    ip daddr $own4 reject
    ip daddr { 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16, 224.0.0.0/4 } reject
    ip6 daddr { $v6_deny } reject
    tcp dport { 25, 465, 587 } reject
  }
}
EOF
  if [ -n "$EXIT_IP" ]; then
    cat <<EOF
# The proxy's traffic leaves from $EXIT_IP. Priority 99 runs before the node's masquerade (100); the first NAT decision wins.
table ip kiwi_proxy_nat
delete table ip kiwi_proxy_nat
table ip kiwi_proxy_nat {
  chain postrouting {
    type nat hook postrouting priority 99;
    meta skuid "$USER_NAME" oifname "$WAN_IF" snat to $EXIT_IP
  }
}
EOF
  fi
}

render_rules_nft() {
  cat <<EOF
#!/bin/sh
# nftables node: the rules live in $NFT_FILE (also loaded with the node's ruleset).
case "\$1" in
  up) nft -f $NFT_FILE ;;
  down) nft delete table inet kiwi_proxy 2>/dev/null; nft delete table ip kiwi_proxy_nat 2>/dev/null; exit 0 ;;
esac
EOF
}

render_proxy_env() {
  local r=""
  if [ "$LOCAL_DNS" = 1 ]; then r="$RESOLVER"; fi
  cat <<EOF
PROXY_BIND=0.0.0.0
PROXY_PORT=$PROXY_PORT
TLS_CERT=$ETC/tls/fullchain.pem
TLS_KEY=$ETC/tls/privkey.pem
USERS_FILE=$LIB/users.json
USAGE_FILE=$LIB/usage.json
PUBLIC_IPS=${EXIT_IP:+$EXIT_IP,}$PUBLIC_IP
OWN_NETS=$OWN_NETS
EXIT_IP=$EXIT_IP
RESOLVER=$r
EOF
}

if [ "$MODE" = print ]; then
  PUBLIC_IP="${PUBLIC_IP:-192.0.2.10}"; OWN_NETS="${OWN_NETS-2001:db8:1:2::/64}"; EXIT_IP="${EXIT_IP:-}"; NFT="${NFT_MODE:-0}"
  if [ "$LOCAL_DNS" = 1 ]; then echo "##### $UNBOUND_CONF #####"; render_unbound; fi
  echo "##### $JOURNALD_CONF #####"; render_journald
  if [ "$NFT" = 1 ]; then echo "##### $NFT_FILE #####"; render_nft; echo "##### $RULES #####"; render_rules_nft
  else echo "##### $RULES #####"; render_rules_ipt; fi
  echo "##### $ETC/proxy.env #####"; render_proxy_env
  exit 0
fi

[ "$(id -u)" = 0 ] || die "run as root"

other_state() { { sha256sum "/etc/wireguard/$OTHER_IF.conf" 2>/dev/null; wg show "$OTHER_IF" peers 2>/dev/null; wg show "$OTHER_IF" listen-port 2>/dev/null; } | sha256sum | cut -d' ' -f1; }
BEFORE="$(other_state)"
check_other() { [ "$(other_state)" = "$BEFORE" ] || die "$OTHER_IF changed during this run: stop and investigate"; }

agent_env_clear() { [ -f "$AGENT_ENV" ] && sed -i '/^PROXY_/d' "$AGENT_ENV"; return 0; }

if [ "$MODE" = uninstall ]; then
  say "Removing the browser proxy; $OTHER_IF and the agent's WireGuard work stay as they are"
  systemctl disable --now kiwi-vpn-proxy 2>/dev/null || true
  [ -x "$RULES" ] && "$RULES" down
  rm -f "$UNIT" "$AGENT_DROPIN" "$HOOK" "$OPT/proxy.py"
  agent_env_clear
  systemctl daemon-reload
  systemctl try-restart kiwi-vpn-agent
  if [ -f "$UNBOUND_CONF" ]; then
    rm -f "$UNBOUND_CONF"
    # unbound stays for the tunnel's resolver when install.sh / install-shared.sh configured one
    if ls /etc/unbound/unbound.conf.d/kiwi-vpn*.conf >/dev/null 2>&1; then systemctl restart unbound || true; else systemctl disable --now unbound 2>/dev/null || true; fi
  fi
  if [ "$NFT" = 1 ]; then
    rm -f "$NFT_FILE"
    nft delete table inet kiwi_proxy 2>/dev/null || true
    nft delete table ip kiwi_proxy_nat 2>/dev/null || true
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
[ "$LOCAL_DNS" = 0 ] || [ "$LOCAL_DNS" = 1 ] || die "LOCAL_DNS is 0 or 1"
[ -z "${CERT_EMAIL:-}" ] || printf '%s' "$CERT_EMAIL" | grep -Eq '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$' || die "CERT_EMAIL doesn't look like an email address"
PUBLIC_IP="${PUBLIC_IP:-$(ip -4 -o addr show "$WAN_IF" scope global | awk '{print $4}' | cut -d/ -f1 | head -n1)}"
[ -n "$PUBLIC_IP" ] || die "couldn't find the public IP: set PUBLIC_IP"
# a re-run keeps the exit IP chosen at install (env still overrides; EXIT_IP= with nothing turns it off)
if [ -z "${EXIT_IP+x}" ] && [ -r "$ETC/proxy.env" ]; then EXIT_IP="$(sed -n 's/^EXIT_IP=//p' "$ETC/proxy.env" | head -n1)"; fi
EXIT_IP="${EXIT_IP:-}"
if [ -n "$EXIT_IP" ]; then
  valid_ipv4 "$EXIT_IP" || die "EXIT_IP ($EXIT_IP) isn't a public IPv4 address"
  [ "$EXIT_IP" != "$PUBLIC_IP" ] || die "EXIT_IP is the server's main address: leave it empty"
  ip -4 -o addr show "$WAN_IF" | grep -qF " $EXIT_IP/" || die "$EXIT_IP isn't on $WAN_IF yet: add it first (apply-floating-ip.sh does it for a running node)"
fi
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
[ "$RESOLVED" = "$PUBLIC_IP" ] || { [ -n "$EXIT_IP" ] && [ "$RESOLVED" = "$EXIT_IP" ]; } || die "$PROXY_HOST points at '${RESOLVED:-nothing}', not $PUBLIC_IP${EXIT_IP:+ or $EXIT_IP}: fix DNS first"
if ss -ltn "sport = :$PROXY_PORT" | grep -q LISTEN && ! systemctl is-active --quiet kiwi-vpn-proxy; then die "something else is using port $PROXY_PORT"; fi
if ss -ltn "sport = :80" | grep -q LISTEN; then die "something is using port 80, which the certificate needs"; fi
if [ "$LOCAL_DNS" = 1 ]; then
  # something other than unbound answering DNS on loopback or everywhere (dnsmasq, bind...) would take the proxy's lookups
  OTHER53="$( (ss -Hlnup 'sport = :53' || true) | grep -E "(127\.0\.0\.1|0\.0\.0\.0|\*):53 " | grep -v '"unbound"' || true)"
  [ -z "$OTHER53" ] || die "another DNS server listens on $RESOLVER:53 or on every address: $OTHER53"
  OTHERCONF="$( (grep -rlE '^\s*interface:\s*127\.0\.0\.1\s*$' /etc/unbound/ || true) | grep -vF "$UNBOUND_CONF" || true)"
  [ -z "$OTHERCONF" ] || die "unbound is already set to listen on 127.0.0.1 in $OTHERCONF: stop and check"
fi

say "Service user and directories"
id "$USER_NAME" >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$USER_NAME"
usermod -aG "$USER_NAME" "$AGENT_USER"  # the agent writes logins into the shared folder; the proxy gets nothing of the agent's (not its token)
install -d -m 0750 -o root -g "$USER_NAME" "$ETC" "$ETC/tls"
install -d -m 2770 -o "$USER_NAME" -g "$USER_NAME" "$LIB"
install -m 0644 "$HERE/proxy.py" "$OPT/proxy.py"
install -m 0644 "$HERE/agent.py" "$OPT/agent.py"
printf '%s' "$PROXY_HOST" > "$ETC/host"

say "Logs: the journal keeps 7 days; ufw logs no packets"
install -d /etc/systemd/journald.conf.d
render_journald > "$JOURNALD_CONF"
systemctl restart systemd-journald
if [ "$NFT" = 0 ]; then ufw logging off >/dev/null; fi

if [ "$LOCAL_DNS" = 1 ]; then
  say "Private resolver for the proxy: unbound on $RESOLVER:53, no query logs"
  command -v unbound >/dev/null || { DEBIAN_FRONTEND=noninteractive apt-get update -qq; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq unbound >/dev/null; }
  systemctl mask -q unbound-resolvconf.service 2>/dev/null || true  # never let the package take over this host's own resolver
  render_unbound > "$UNBOUND_CONF"
  unbound-checkconf >/dev/null || { rm -f "$UNBOUND_CONF"; die "unbound's config doesn't check"; }
  systemctl enable -q unbound
  systemctl restart unbound
  ok=0
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if python3 -I -c "import asyncio,sys; sys.path.insert(0, sys.argv[1]); import proxy; sys.exit(0 if asyncio.run(proxy.dns_lookup(sys.argv[2], 'one.one.one.one')) else 1)" "$HERE" "$RESOLVER" 2>/dev/null; then ok=1; break; fi
    sleep 1
  done
  [ "$ok" = 1 ] || die "unbound doesn't answer on $RESOLVER:53 (journalctl -u unbound): the proxy was not switched to it"
fi

say "Certificate for $PROXY_HOST (browsers only accept a publicly trusted one for a proxy)"
command -v certbot >/dev/null || { DEBIAN_FRONTEND=noninteractive apt-get update -qq; DEBIAN_FRONTEND=noninteractive apt-get install -y -qq certbot >/dev/null; }
if [ "$NFT" = 0 ]; then
  ufw allow 80/tcp >/dev/null
  ufw allow "$PROXY_PORT/tcp" >/dev/null
fi
EMAIL_ARGS=(--register-unsafely-without-email)
if [ -n "${CERT_EMAIL:-}" ]; then EMAIL_ARGS=(-m "$CERT_EMAIL"); else echo "warning: no CERT_EMAIL: Let's Encrypt can't warn anyone before the certificate expires (kiwi-infra.mjs health checks it)" >&2; fi
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
# an account registered earlier without an email gets one now (certbot keeps the account across renewals)
if [ -n "${CERT_EMAIL:-}" ]; then certbot update_account --non-interactive -m "$CERT_EMAIL" >/dev/null 2>&1 || echo "warning: couldn't set the certificate email on the existing account" >&2; fi
"$HOOK"

say "Firewall rules for the proxy's user only: the public internet and nothing else${EXIT_IP:+; it leaves from $EXIT_IP}"
if [ "$NFT" = 1 ]; then
  install -d -m 0750 "$(dirname "$NFT_FILE")"
  render_nft > "$NFT_FILE"
  nft -c -f "$NFT_FILE" || die "the proxy's nftables rules don't load"
  render_rules_nft > "$RULES"
else
  render_rules_ipt > "$RULES"
fi
chmod 0750 "$RULES"

say "Proxy service"
(umask 027; render_proxy_env > "$ETC/proxy.env")
chown root:"$USER_NAME" "$ETC/proxy.env"
cat > "$UNIT" <<EOF
[Unit]
Description=Kiwi VPN browser proxy
After=network-online.target unbound.service
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
