#!/usr/bin/env bash
# Kiwi VPN node installer for a fresh Ubuntu 24.04 VPS. Run as root. Idempotent:
# re-running keeps the server key, TLS cert, token and peers.
#
#   bash install.sh                  install / reconfigure (ad blocking OFF)
#   bash install.sh --adblock        same, with the ad & tracker blocklist ON
#                                    (clients always use the node's own no-logs resolver,
#                                    unbound on the wg0 address, so toggling never breaks
#                                    existing device configs)
#   bash install.sh --update         refresh agent.py, its systemd unit and kiwi-vpn-doctor
#                                    only, then restart the agent (keys/token/cert/peers,
#                                    firewall and adblock setting are left alone)
#   bash install.sh --uninstall      remove everything this script installed
#   bash install.sh --print-config [--adblock]
#                                    dry run: print every generated file and exit
#                                    (no root, no changes; used by the tests)
#
# Optional env: PUBLIC_IP (auto-detected), SSH_PORT (22), WG_PORT (51820),
#               AGENT_PORT (8443), POOL (10.66.0.1/16), POOL6 (fd66:66::1/64, "" = off),
#               BLOCKLIST_URL (StevenBlack unified hosts),
#               KIWI_AGENT_URL (where to download agent.py if it is not next to this script),
#               AGENT_ALLOW (comma-separated IPv4/CIDR allowed to reach the agent port; empty = anyone),
#               WITH_PROXY=1 (open 80+443/tcp for install-proxy.sh's browser proxy and certificate),
#               QUIET_SECRETS=1 (don't print the agent token: it goes to /root/kiwi-node.txt, root only;
#                                used by unattended installs so no log ever holds it)
set -euo pipefail

die() { echo "error: $*" >&2; exit 1; }
say() { echo "==> $*"; }

MODE=install
ADBLOCK=0
for arg in "$@"; do
  case "$arg" in
    --adblock) ADBLOCK=1 ;;
    --no-adblock) ADBLOCK=0 ;;
    --update) MODE=update ;;
    --uninstall) MODE=uninstall ;;
    --print-config) MODE=print ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]:-$0}" 2>/dev/null || true; exit 0 ;;
    *) die "unknown option: $arg (see --help)" ;;
  esac
done
if [ "$MODE" = update ] && [ "$ADBLOCK" = 1 ]; then
  die "--update never changes settings; run 'bash install.sh --adblock' to turn ad blocking on"
fi

SSH_PORT="${SSH_PORT:-$( (sshd -T 2>/dev/null || true) | awk '/^port /{print $2; exit}')}"
SSH_PORT="${SSH_PORT:-22}"
WG_PORT="${WG_PORT:-51820}"
AGENT_PORT="${AGENT_PORT:-8443}"
POOL="${POOL:-10.66.0.1/16}"
POOL6="${POOL6-fd66:66::1/64}"
# a re-run keeps the agent allowlist and proxy ports chosen at install (env still overrides)
if [ -r /etc/kiwi-vpn/install.env ]; then
  while IFS='=' read -r k v; do
    case "$k" in AGENT_ALLOW|WITH_PROXY) [ -n "${!k+x}" ] || export "$k=$v" ;; esac
  done < /etc/kiwi-vpn/install.env
fi
AGENT_ALLOW="${AGENT_ALLOW:-}"
WITH_PROXY="${WITH_PROXY:-0}"
QUIET_SECRETS="${QUIET_SECRETS:-0}"
NFT_D=/etc/kiwi-vpn/nft.d  # extra tables (the browser proxy's) that survive every reload of this ruleset
[ -z "$AGENT_ALLOW" ] || printf '%s' "$AGENT_ALLOW" | grep -Eq '^[0-9./,]+$' || die "AGENT_ALLOW must be IPv4 addresses or CIDRs, comma-separated"
BLOCKLIST_URL="${BLOCKLIST_URL:-https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts}"
WG_IF=wg0
WG_ADDR="${POOL%/*}"
ETC=/etc/kiwi-vpn
LIB=/var/lib/kiwi-vpn
OPT=/opt/kiwi-vpn
SVC_USER=kiwi-vpn
UNIT=/etc/systemd/system/kiwi-vpn-agent.service
UNBOUND_CONF=/etc/unbound/unbound.conf.d/kiwi-vpn.conf
BLOCKLIST=/etc/unbound/kiwi-vpn-blocklist.conf
UPDATER=/usr/local/sbin/kiwi-vpn-blocklist-update
BL_SERVICE=/etc/systemd/system/kiwi-vpn-blocklist.service
BL_TIMER=/etc/systemd/system/kiwi-vpn-blocklist.timer
DOCTOR=/usr/local/bin/kiwi-vpn-doctor
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || echo .)"

# a.b.c.d/len -> network/len (pure bash, so --print-config needs no python)
pool_net() {
  local ip="${1%/*}" len="${1#*/}" a b c d n mask
  IFS=. read -r a b c d <<<"$ip"
  n=$(( (a << 24) | (b << 16) | (c << 8) | d ))
  mask=$(( len == 0 ? 0 : (0xFFFFFFFF << (32 - len)) & 0xFFFFFFFF ))
  n=$(( n & mask ))
  echo "$(( (n >> 24) & 255 )).$(( (n >> 16) & 255 )).$(( (n >> 8) & 255 )).$(( n & 255 ))/$len"
}
POOL_NET="$(pool_net "$POOL")"

# How clients' IPv6 is answered. They get AllowedIPs ::/0, so v6 enters the tunnel; it is
# never forwarded, and is rejected at once ("no route") so apps fall back to IPv4 quickly.
V6_VERDICT="reject with icmpx type no-route"

# ======================= templates (pure functions of the vars above) =======================

render_wg_conf() {
  cat <<EOF
# Managed by kiwi-vpn install.sh. Peers are added at runtime by the agent.
[Interface]
Address = $POOL${POOL6:+, $POOL6}
ListenPort = $WG_PORT
PostUp = wg set %i private-key /etc/wireguard/$WG_IF.key
SaveConfig = false
EOF
}

render_sysctl() {
  cat <<EOF
# Managed by kiwi-vpn install.sh
net.ipv4.ip_forward = 1
EOF
  if [ "$HAVE_V6" = 1 ]; then
    cat <<EOF
# IPv6 is never routed for VPN clients: nftables rejects it (no leak, no hang).
net.ipv6.conf.all.forwarding = 0
EOF
  fi
}

render_nftables() {
  cat <<EOF
#!/usr/sbin/nft -f
# Managed by kiwi-vpn install.sh. No log rules on purpose (no traffic logging).
flush ruleset
table inet filter {
  chain prerouting {
    type filter hook prerouting priority -150; policy accept;
    # Clients route ::/0 into the tunnel. Answer that IPv6 immediately instead of letting
    # it hang; it is never forwarded (net.ipv6.conf.all.forwarding = 0).
    iifname "$WG_IF" meta nfproto ipv6 fib daddr type != local $V6_VERDICT comment "kiwi: v6 reject"
  }
  chain input {
    type filter hook input priority 0; policy drop;
    iif lo accept
    ct state invalid drop
    ct state established,related accept
    meta l4proto { icmp, ipv6-icmp } accept
    # DNS to the node's own resolver, from the VPN pool only.
    iifname "$WG_IF" ip saddr $POOL_NET ip daddr $WG_ADDR udp dport 53 accept comment "kiwi: dns"
    iifname "$WG_IF" ip saddr $POOL_NET ip daddr $WG_ADDR tcp dport 53 accept comment "kiwi: dns"
    # VPN clients may not reach SSH, the agent or anything else on the node itself.
    iifname "$WG_IF" reject with icmpx type admin-prohibited comment "kiwi: tunnel to host"
    tcp dport $SSH_PORT accept
    udp dport $WG_PORT accept
$(agent_rule)
$(proxy_rule)
  }
  chain forward {
    type filter hook forward priority 0; policy drop;
    tcp flags & (syn | rst) == syn tcp option maxseg size set rt mtu comment "kiwi: mss clamp"
    ct state invalid drop
    ct state established,related accept
    meta nfproto ipv6 $V6_VERDICT comment "kiwi: v6 reject"
    iifname "$WG_IF" oifname "$WG_IF" reject with icmpx type admin-prohibited comment "kiwi: client isolation"
    iifname "$WG_IF" tcp dport 25 reject with tcp reset comment "kiwi: smtp 25 blocked"
    iifname "$WG_IF" ip daddr { 10.0.0.0/8, 100.64.0.0/10, 169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16 } reject with icmpx type admin-prohibited comment "kiwi: no private or metadata"
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
include "$NFT_D/*.nft"
EOF
}

agent_rule() {
  if [ -n "$AGENT_ALLOW" ]; then
    echo "    ip saddr { $(echo "$AGENT_ALLOW" | sed 's/,/, /g') } tcp dport $AGENT_PORT accept comment \"kiwi: agent, backend only\""
  else
    echo "    tcp dport $AGENT_PORT accept"
  fi
}

proxy_rule() {
  if [ "$WITH_PROXY" = 1 ]; then echo '    tcp dport { 80, 443 } accept comment "kiwi: browser proxy + its certificate"'; fi
}

render_unbound_conf() {
  cat <<EOF
# Managed by kiwi-vpn install.sh. Private resolver for VPN clients (always on; the included
# blocklist is empty unless install.sh --adblock). Listens only on the tunnel address;
# no query or reply logging of any kind.
server:
  interface: $WG_ADDR
  port: 53
  ip-freebind: yes
  do-ip6: no
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
  include: "$BLOCKLIST"
EOF
}

render_blocklist_updater() {
  cat <<'EOF'
#!/usr/bin/env bash
# kiwi-vpn-blocklist-update: refresh the unbound ad & tracker blocklist (managed by install.sh).
# Downloads a hosts list over HTTPS, converts it to unbound local-zones, validates it and
# only then swaps it in. On any failure the current list stays in place.
set -euo pipefail
URL="https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts"
if [ -r /etc/kiwi-vpn/adblock.env ]; then . /etc/kiwi-vpn/adblock.env; fi
URL="${BLOCKLIST_URL:-$URL}"
LIVE=/etc/unbound/kiwi-vpn-blocklist.conf
ALLOW=/etc/kiwi-vpn/adblock-allow.txt
MIN_BYTES=100000 MAX_BYTES=52428800 MIN_ENTRIES=1000 MAX_ENTRIES=2000000

fail() { echo "kiwi-vpn-blocklist: $*; keeping the current list" >&2; exit 1; }
case "$URL" in https://*) ;; *) fail "BLOCKLIST_URL must be https" ;; esac

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
curl -fsSL --proto '=https' --tlsv1.2 --max-time 300 --max-filesize "$MAX_BYTES" \
  -o "$tmp/hosts" "$URL" || fail "download failed"
bytes="$(stat -c %s "$tmp/hosts")"
[ "$bytes" -ge "$MIN_BYTES" ] && [ "$bytes" -le "$MAX_BYTES" ] || fail "odd download size ($bytes bytes)"

# "0.0.0.0 ads.example.com" -> ads.example.com (valid hostnames only, deduplicated)
awk '{ sub(/\r$/, "") }
     ($1 == "0.0.0.0" || $1 == "127.0.0.1") && NF >= 2 {
       d = tolower($2)
       if (d ~ /^[0-9.]+$/ || d == "localhost.localdomain" || length(d) > 253) next
       if (d ~ /^([a-z0-9_]([a-z0-9_-]*[a-z0-9_])?\.)+[a-z0-9]([a-z0-9-]*[a-z0-9])?$/) print d
     }' "$tmp/hosts" | sort -u > "$tmp/domains"
if [ -s "$ALLOW" ]; then
  tr 'A-Z' 'a-z' < "$ALLOW" | sed 's/[[:space:]#].*//; /^$/d' > "$tmp/allow"
  grep -vxF -f "$tmp/allow" "$tmp/domains" > "$tmp/domains.ok" || true
  mv "$tmp/domains.ok" "$tmp/domains"
fi
n="$(wc -l < "$tmp/domains")"
[ "$n" -ge "$MIN_ENTRIES" ] && [ "$n" -le "$MAX_ENTRIES" ] || fail "odd entry count ($n)"
{
  echo "# Kiwi VPN blocklist: $n domains from $URL ($(date -u +%F))"
  awk '{ printf "local-zone: \"%s.\" always_nxdomain\n", $1 }' "$tmp/domains"
} > "$tmp/blocklist.conf"

# Validate the candidate on its own, then as part of the full config, before going live.
printf 'server:\n  chroot: ""\n  include: "%s"\n' "$tmp/blocklist.conf" > "$tmp/check.conf"
unbound-checkconf "$tmp/check.conf" >/dev/null || fail "candidate list fails unbound-checkconf"

if [ -f "$LIVE" ]; then cp -p "$LIVE" "$LIVE.prev"; fi
install -m 0644 "$tmp/blocklist.conf" "$LIVE.new"
mv -f "$LIVE.new" "$LIVE"
restore() {
  if [ -f "$LIVE.prev" ]; then mv -f "$LIVE.prev" "$LIVE"; else echo "# empty" > "$LIVE"; fi
  systemctl restart unbound || true
  fail "$1"
}
unbound-checkconf >/dev/null || restore "full config check failed"
systemctl reload-or-restart unbound || restore "unbound reload failed"
sleep 2
systemctl is-active -q unbound || restore "unbound not running after reload"
rm -f "$LIVE.prev"
echo "kiwi-vpn-blocklist: $n domains loaded"
EOF
}

render_blocklist_service() {
  cat <<EOF
[Unit]
Description=Kiwi VPN: refresh the ad & tracker blocklist
After=network-online.target unbound.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$UPDATER
PrivateTmp=yes
ProtectHome=yes
NoNewPrivileges=yes
EOF
}

render_blocklist_timer() {
  cat <<EOF
[Unit]
Description=Kiwi VPN: daily ad & tracker blocklist refresh

[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF
}

render_agent_env() {
  cat <<EOF
# Kiwi VPN node agent config (managed by install.sh; TOKEN is preserved on re-run)
TOKEN=$TOKEN
POOL=$POOL
POOL6=$POOL6
ENDPOINT_HOST=$PUBLIC_IP
LISTEN_PORT=$WG_PORT
DNS=$WG_ADDR
ADBLOCK=$ADBLOCK
AGENT_BIND=0.0.0.0
AGENT_PORT=$AGENT_PORT
TLS_CERT=$ETC/tls.crt
TLS_KEY=$ETC/tls.key
WG_INTERFACE=$WG_IF
STATE_FILE=$LIB/peers.json
EOF
}

render_agent_unit() {
  cat <<EOF
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
}

render_journald() {
  cat <<EOF
[Journal]
MaxRetentionSec=7day
SystemMaxUse=200M
EOF
}

render_auto_upgrades() {
  cat <<EOF
// Managed by kiwi-vpn install.sh: daily package lists + unattended security upgrades
// (Ubuntu's 50unattended-upgrades allows only the -security pocket by default).
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
}

render_doctor() {
  cat <<'EOF'
#!/usr/bin/env bash
# kiwi-vpn-doctor: health checklist for a Kiwi VPN node (installed by install.sh).
# Safe to paste: prints no keys, tokens, client IPs or endpoints; handshakes are counts only.
set -u
ENV=/etc/kiwi-vpn/agent.env
fails=0 warns=0
ok()   { printf '[ OK ] %s\n' "$*"; }
bad()  { printf '[FAIL] %s\n' "$*"; fails=$((fails + 1)); }
warn() { printf '[WARN] %s\n' "$*"; warns=$((warns + 1)); }
conf() { sed -n "s/^$1=//p" "$ENV" 2>/dev/null | head -n1; }

echo "Kiwi VPN doctor  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "OS: $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-?}"), kernel $(uname -r), $(uptime -p 2>/dev/null)"
[ "$(id -u)" -eq 0 ] || warn "not root: run 'sudo kiwi-vpn-doctor' for the full checklist"

if [ -r "$ENV" ]; then ok "config $ENV"; else bad "config $ENV missing or unreadable"; fi
WG_IF="$(conf WG_INTERFACE)"; WG_IF="${WG_IF:-wg0}"
PORT="$(conf LISTEN_PORT)"; PORT="${PORT:-51820}"
AGENT_PORT="$(conf AGENT_PORT)"; AGENT_PORT="${AGENT_PORT:-8443}"
POOL="$(conf POOL)"; WG_ADDR="${POOL%/*}"
ADBLOCK="$(conf ADBLOCK)"

echo "--- services"
for s in "wg-quick@$WG_IF" kiwi-vpn-agent nftables; do
  if systemctl is-active -q "$s"; then ok "$s active"; else bad "$s not active (journalctl -u $s -n 50)"; fi
done
if [ -f /opt/kiwi-vpn/agent.py ]; then
  ok "agent.py sha256 $(sha256sum /opt/kiwi-vpn/agent.py | cut -c1-12)"
fi

echo "--- WireGuard"
if [ -n "$(ip link show dev "$WG_IF" up 2>/dev/null)" ]; then ok "$WG_IF is up"; else bad "$WG_IF is down"; fi
lp="$(wg show "$WG_IF" listen-port 2>/dev/null || true)"
if [ "$lp" = "$PORT" ]; then ok "$WG_IF listen port $lp/udp"; else bad "$WG_IF listen port '${lp:-?}' (expected $PORT)"; fi
if ss -Hlun "sport = :$PORT" 2>/dev/null | grep -q .; then ok "udp $PORT listening"; else bad "nothing listening on udp $PORT"; fi

echo "--- routing and firewall"
if [ "$(sysctl -n net.ipv4.ip_forward 2>/dev/null)" = 1 ]; then ok "IPv4 forwarding on"; else bad "IPv4 forwarding off"; fi
v6f="$(sysctl -n net.ipv6.conf.all.forwarding 2>/dev/null || echo 0)"
if [ "$v6f" = 0 ]; then ok "IPv6 forwarding off (client v6 is rejected, not routed)"; else bad "IPv6 forwarding is on"; fi
rules="$(nft list ruleset 2>/dev/null || true)"
if [ -z "$rules" ]; then
  bad "nftables ruleset empty or unreadable"
else
  need=("masquerade" "kiwi: dns" "kiwi: client isolation" "kiwi: smtp 25 blocked" "kiwi: v6 reject" "kiwi: mss clamp" "kiwi: tunnel to host")
  missing=""
  for r in "${need[@]}"; do grep -qF "$r" <<<"$rules" || missing="$missing [$r]"; done
  if [ -z "$missing" ]; then ok "nftables rules present (${#need[@]} checked)"; else bad "nftables rules missing:$missing"; fi
  if grep -qE '(^|[[:space:]])log([[:space:]]|$)' <<<"$rules"; then warn "nftables has a log rule (Kiwi adds none)"; fi
fi

echo "--- agent"
if curl -fsk --max-time 3 "https://127.0.0.1:$AGENT_PORT/v1/health" 2>/dev/null | grep -q '"ok": *true'; then
  ok "agent healthy on :$AGENT_PORT/v1/health"
else
  bad "agent not answering on :$AGENT_PORT/v1/health (journalctl -u kiwi-vpn-agent -n 50)"
fi
if [ -r /etc/kiwi-vpn/tls.crt ]; then
  if openssl x509 -checkend 2592000 -noout -in /etc/kiwi-vpn/tls.crt >/dev/null 2>&1; then
    ok "TLS cert valid for 30+ days"
  else
    warn "TLS cert expires within 30 days"
  fi
fi

echo "--- DNS resolver"
if systemctl is-active -q unbound; then ok "unbound active"; else bad "unbound not active (journalctl -u unbound -n 50)"; fi
if ss -Hlun "sport = :53" 2>/dev/null | grep -qF "$WG_ADDR:53"; then ok "unbound listening on $WG_ADDR:53"; else bad "nothing on $WG_ADDR:53"; fi
for o in verbosity log-queries log-replies; do
  v="$(unbound-checkconf -o "$o" 2>/dev/null || echo '?')"
  case "$o=$v" in verbosity=0|log-queries=no|log-replies=no) ;; *) bad "unbound $o is '$v' (should be off)" ;; esac
done
if [ "$ADBLOCK" = 1 ]; then
  if systemctl is-active -q kiwi-vpn-blocklist.timer; then ok "daily blocklist timer active"; else bad "kiwi-vpn-blocklist.timer not active"; fi
  bl=/etc/unbound/kiwi-vpn-blocklist.conf
  if [ -s "$bl" ]; then
    n="$(grep -c '^local-zone:' "$bl" || true)"
    age=$(( ($(date +%s) - $(stat -c %Y "$bl")) / 86400 ))
    if [ "${n:-0}" -gt 0 ]; then ok "blocklist: $n domains, ${age}d old"; else warn "blocklist is empty (DNS works, nothing blocked)"; fi
    if [ "$age" -gt 3 ]; then warn "blocklist not refreshed for ${age} days (journalctl -u kiwi-vpn-blocklist -n 20)"; fi
  else
    bad "blocklist file missing"
  fi
else
  ok "ad blocking off (resolver runs without a blocklist)"
fi

echo "--- host"
use="$(df -P / | awk 'NR==2 {gsub("%", "", $5); print $5}')"
if [ "${use:-0}" -ge 90 ]; then bad "disk / ${use}% used"; elif [ "${use:-0}" -ge 80 ]; then warn "disk / ${use}% used"; else ok "disk / ${use}% used"; fi
read -r total avail < <(free -m | awk '/^Mem:/ {print $2, $7}')
if [ "${avail:-0}" -lt 100 ]; then warn "memory: ${avail:-?} MB available of ${total:-?} MB"; else ok "memory: ${avail} MB available of ${total} MB"; fi
ok "load average: $(cut -d' ' -f1-3 /proc/loadavg)"
if [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = yes ]; then ok "clock synchronised"; else warn "clock not NTP-synchronised"; fi
if apt-config dump 2>/dev/null | grep -q 'APT::Periodic::Unattended-Upgrade "1"'; then ok "unattended security upgrades on"; else warn "unattended upgrades off"; fi
if [ -f /var/run/reboot-required ]; then warn "reboot required to finish security updates"; fi

echo "--- peers (counts only)"
peers="$(wg show "$WG_IF" peers 2>/dev/null | grep -c . || true)"
saved="$(grep -cE '"[A-Za-z0-9+/]{43}="' /var/lib/kiwi-vpn/peers.json 2>/dev/null || true)"
if [ "${peers:-0}" = "${saved:-0}" ]; then ok "peers: ${peers:-0} (matches the agent's peer file)"; else warn "peers: ${peers:-0} in kernel, ${saved:-0} in the agent's peer file"; fi
wg show "$WG_IF" latest-handshakes 2>/dev/null | awk -v now="$(date +%s)" '
  { t = $2; if (t == 0) never++; else if (now - t <= 180) live++; else if (now - t <= 86400) day++; else old++ }
  END { printf "[ OK ] handshakes: %d in last 3 min, %d in last 24 h, %d older, %d never\n", live, day, old, never }'

echo "--- summary: $fails failed, $warns warnings"
[ "$fails" -eq 0 ]
EOF
}

# ======================= dry run =======================
if [ "$MODE" = print ]; then
  WAN_IF="${WAN_IF:-eth0}"
  PUBLIC_IP="${PUBLIC_IP:-203.0.113.10}"
  TOKEN="<token>"
  HAVE_V6=1
  show() { printf '\n##### %s #####\n' "$1"; shift; "$@"; }
  echo "# kiwi-vpn install.sh --print-config (adblock=$ADBLOCK, WAN_IF=$WAN_IF) - nothing is changed"
  show "/etc/wireguard/$WG_IF.conf" render_wg_conf
  show /etc/sysctl.d/99-kiwi-vpn.conf render_sysctl
  show /etc/nftables.conf render_nftables
  show "$ETC/agent.env" render_agent_env
  show "$UNIT" render_agent_unit
  show /etc/systemd/journald.conf.d/kiwi-vpn.conf render_journald
  show /etc/apt/apt.conf.d/20auto-upgrades render_auto_upgrades
  show "$DOCTOR" render_doctor
  show "$UNBOUND_CONF" render_unbound_conf
  if [ "$ADBLOCK" = 1 ]; then
    show "$UPDATER" render_blocklist_updater
    show "$BL_SERVICE" render_blocklist_service
    show "$BL_TIMER" render_blocklist_timer
  fi
  exit 0
fi

[ "$(id -u)" -eq 0 ] || die "run as root (sudo bash install.sh)"

# ======================= shared steps =======================
install_agent_file() {
  if [ -f "$SCRIPT_DIR/agent.py" ]; then
    python3 -m py_compile "$SCRIPT_DIR/agent.py"
    install -m 0644 -o root -g root "$SCRIPT_DIR/agent.py" "$OPT/agent.py"
  elif [ -n "${KIWI_AGENT_URL:-}" ]; then
    curl -fsSL --proto '=https' --max-time 30 "$KIWI_AGENT_URL" -o "$OPT/agent.py.new"
    python3 -m py_compile "$OPT/agent.py.new"
    install -m 0644 -o root -g root "$OPT/agent.py.new" "$OPT/agent.py"; rm -f "$OPT/agent.py.new"
  else
    die "agent.py not found next to install.sh; copy it here or set KIWI_AGENT_URL"
  fi
  rm -rf "$OPT/__pycache__" "$SCRIPT_DIR/__pycache__" 2>/dev/null || true
}

install_doctor() {
  (umask 022; render_doctor > "$DOCTOR.new")
  chmod 0755 "$DOCTOR.new"; mv -f "$DOCTOR.new" "$DOCTOR"
}

wait_agent() {
  say "Waiting for the agent"
  for _ in $(seq 1 20); do
    curl -fsk --max-time 2 "https://127.0.0.1:$AGENT_PORT/v1/health" >/dev/null 2>&1 && return 0
    sleep 1
  done
  die "agent did not start; see: journalctl -u kiwi-vpn-agent -n 50 (or run kiwi-vpn-doctor)"
}

# Ad blocking off: drop the timer + updater; the resolver keeps running with an empty list.
remove_blocklist_updater() {
  systemctl disable --now kiwi-vpn-blocklist.timer 2>/dev/null || true
  rm -f "$BL_TIMER" "$BL_SERVICE" "$UPDATER" "$BLOCKLIST.prev" "$ETC/adblock.env"
}

# ======================= --uninstall =======================
if [ "$MODE" = uninstall ]; then
  say "Uninstalling Kiwi VPN node"
  remove_blocklist_updater
  systemctl disable --now unbound 2>/dev/null || true
  rm -f "$UNBOUND_CONF" "$BLOCKLIST"
  systemctl disable --now kiwi-vpn-agent 2>/dev/null || true
  systemctl disable --now "wg-quick@$WG_IF" 2>/dev/null || true
  rm -f "$UNIT" "$DOCTOR" /etc/sysctl.d/99-kiwi-vpn.conf /etc/systemd/journald.conf.d/kiwi-vpn.conf
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
  say "Done. Packages (wireguard-tools, nftables, unbound) and unattended security upgrades were left installed."
  exit 0
fi

# ======================= --update =======================
if [ "$MODE" = update ]; then
  [ -f "$ETC/agent.env" ] && [ -f "$OPT/agent.py" ] || die "not installed yet; run 'bash install.sh' first"
  AGENT_PORT="$(sed -n 's/^AGENT_PORT=//p' "$ETC/agent.env" | head -n1)"; AGENT_PORT="${AGENT_PORT:-8443}"
  say "Updating agent.py, its systemd unit and kiwi-vpn-doctor (keys, token, cert, peers untouched)"
  install_agent_file
  render_agent_unit > "$UNIT"
  install_doctor
  systemctl daemon-reload
  systemctl restart kiwi-vpn-agent
  wait_agent
  say "Updated. agent.py sha256 $(sha256sum "$OPT/agent.py" | cut -c1-12). Check: kiwi-vpn-doctor"
  exit 0
fi

# ======================= full install / reconfigure =======================
grep -q 'VERSION_ID="24.04"' /etc/os-release 2>/dev/null || echo "warning: tested on Ubuntu 24.04 only" >&2

say "Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
PKGS=(wireguard-tools nftables python3 openssl curl ca-certificates unattended-upgrades
      unbound unbound-anchor dns-root-data)
apt-get install -y -qq --no-install-recommends "${PKGS[@]}" >/dev/null

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
HAVE_V6=0
if [ -e /proc/sys/net/ipv6 ]; then HAVE_V6=1; fi
if [ "$HAVE_V6" = 0 ] || [ "$(cat /proc/sys/net/ipv6/conf/all/disable_ipv6 2>/dev/null || echo 1)" != 0 ]; then
  POOL6=""  # kernel IPv6 off: no ULA on wg0 (client v6 is then dropped by WireGuard itself)
fi

# ---------- kernel: IPv4 forwarding on, IPv6 forwarding off ----------
say "Enabling IPv4 forwarding (IPv6 is never forwarded)"
render_sysctl > /etc/sysctl.d/99-kiwi-vpn.conf
sysctl -q -p /etc/sysctl.d/99-kiwi-vpn.conf

# ---------- unattended security upgrades ----------
say "Enabling unattended security upgrades"
render_auto_upgrades > /etc/apt/apt.conf.d/20auto-upgrades
systemctl enable -q --now unattended-upgrades apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true

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
if [ -f "/etc/wireguard/$WG_IF.conf" ] && ! grep -q "Managed by kiwi-vpn install.sh" "/etc/wireguard/$WG_IF.conf"; then
  die "/etc/wireguard/$WG_IF.conf isn't ours. This installer takes over the firewall and $WG_IF: on a server with another tunnel, use install-shared.sh"
fi
WG_CONF_NEW="$(render_wg_conf)"
WG_CHANGED=0
if [ "$(cat "/etc/wireguard/$WG_IF.conf" 2>/dev/null)" != "$WG_CONF_NEW" ]; then
  (umask 077; printf '%s\n' "$WG_CONF_NEW" > "/etc/wireguard/$WG_IF.conf")
  WG_CHANGED=1
fi

# ---------- firewall + NAT (nftables owns the whole ruleset) ----------
say "Writing firewall (SSH $SSH_PORT/tcp, WireGuard $WG_PORT/udp, agent $AGENT_PORT/tcp, DNS 53 from the VPN pool)"
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  echo "note: disabling ufw; nftables rules below replace it" >&2
  ufw --force disable >/dev/null
fi
install -d -m 0750 "$NFT_D"
render_nftables > /etc/nftables.conf.new
if ! nft -c -f /etc/nftables.conf.new 2>/dev/null; then
  # Old kernels cannot reject in prerouting; client v6 is then dropped (still never leaked).
  echo "note: this kernel cannot reject in prerouting; client IPv6 will be dropped instead" >&2
  V6_VERDICT="drop"
  render_nftables > /etc/nftables.conf.new
  nft -c -f /etc/nftables.conf.new
fi
mv -f /etc/nftables.conf.new /etc/nftables.conf
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

TOKEN="$( (sed -n 's/^TOKEN=//p' "$ETC/agent.env" 2>/dev/null || true) | head -n1)"
[ "${#TOKEN}" -ge 32 ] || TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
(umask 027; render_agent_env > "$ETC/agent.env")
printf 'AGENT_ALLOW=%s
WITH_PROXY=%s
' "$AGENT_ALLOW" "$WITH_PROXY" > "$ETC/install.env"
chown root:"$SVC_USER" "$ETC/agent.env"; chmod 0640 "$ETC/agent.env"

# ---------- private resolver (always) + ad & tracker blocklist (optional) ----------
say "Setting up the VPN DNS resolver on $WG_ADDR:53 (no query logging; ad blocking $(if [ "$ADBLOCK" = 1 ]; then echo on; else echo off; fi))"
# Never let the unbound package take over this host's own resolver.
systemctl mask -q unbound-resolvconf.service 2>/dev/null || true
render_unbound_conf > "$UNBOUND_CONF"
if [ "$ADBLOCK" = 1 ]; then
  echo "BLOCKLIST_URL=$BLOCKLIST_URL" > "$ETC/adblock.env"
  [ -s "$BLOCKLIST" ] && grep -q '^local-zone:' "$BLOCKLIST" || echo "# empty until the first download" > "$BLOCKLIST"
  (umask 022; render_blocklist_updater > "$UPDATER.new"); chmod 0755 "$UPDATER.new"; mv -f "$UPDATER.new" "$UPDATER"
  render_blocklist_service > "$BL_SERVICE"
  render_blocklist_timer > "$BL_TIMER"
else
  remove_blocklist_updater
  echo "# ad blocking off (install.sh --adblock turns it on)" > "$BLOCKLIST"
fi
unbound-checkconf >/dev/null || die "unbound config check failed"
systemctl daemon-reload
systemctl enable -q unbound
systemctl restart unbound
if [ "$ADBLOCK" = 1 ]; then
  systemctl enable -q --now kiwi-vpn-blocklist.timer
  "$UPDATER" || echo "warning: first blocklist download failed; DNS works unfiltered and the daily timer retries" >&2
fi

# ---------- agent ----------
say "Installing agent"
install_agent_file
render_agent_unit > "$UNIT"
install_doctor

# journald: keep system logs short-lived (the agent itself logs only start/stop/errors).
install -d /etc/systemd/journald.conf.d
render_journald > /etc/systemd/journald.conf.d/kiwi-vpn.conf
systemctl restart systemd-journald

systemctl daemon-reload
systemctl enable -q "wg-quick@$WG_IF" kiwi-vpn-agent
if [ "$WG_CHANGED" = 1 ] || ! systemctl is-active -q "wg-quick@$WG_IF"; then
  systemctl restart "wg-quick@$WG_IF"
fi
systemctl restart kiwi-vpn-agent
wait_agent

CERT_SHA256="$(openssl x509 -in "$ETC/tls.crt" -outform DER | sha256sum | cut -d' ' -f1)"
SHOW_TOKEN="$TOKEN"
if [ "$QUIET_SECRETS" = 1 ]; then
  (umask 077; printf 'agent_url=https://%s:%s\nagent_token=%s\ncert_sha256=%s\n' "$PUBLIC_IP" "$AGENT_PORT" "$TOKEN" "$CERT_SHA256" > /root/kiwi-node.txt)
  SHOW_TOKEN="(in /root/kiwi-node.txt, root only)"
fi
cat <<EOF

================ Kiwi VPN node ready ================
Paste into Price Chat -> Admin -> VPN -> Add server:

  agent_url:    https://$PUBLIC_IP:$AGENT_PORT
  agent_token:  $SHOW_TOKEN
  cert_sha256:  $CERT_SHA256

(Filled in automatically from the agent: endpoint $PUBLIC_IP, listen port $WG_PORT/udp,
 address $POOL, server public key $(wg show "$WG_IF" public-key))

Client DNS: $WG_ADDR (this node, no logs). Ad blocking: $(if [ "$ADBLOCK" = 1 ]; then echo ON; else echo off; fi)
Health check any time: kiwi-vpn-doctor
If your provider has a cloud firewall, allow: $SSH_PORT/tcp, $WG_PORT/udp, $AGENT_PORT/tcp${AGENT_ALLOW:+ (from $AGENT_ALLOW only)}$(if [ "$WITH_PROXY" = 1 ]; then echo ", 80/tcp, 443/tcp"; fi).
=====================================================
EOF
