#!/usr/bin/env bash
# Read-only look at a Kiwi VPN node before adding the browser proxy. Changes nothing, prints no keys or tokens.
#
#   bash check-node.sh fsn1.vpn.pricechat.live          (run as root on the server)
#
# Answers: which installer set this node up, which WireGuard interface is Kiwi's and which isn't, how its firewall works,
# whether 80 and 443 are free, its IPv6 networks, whether the name already points here, and if the agent code matches.
set -uo pipefail
NAME="${1:-}"
WAN_IF="${WAN_IF:-eth0}"
ENV=/etc/kiwi-vpn/agent.env
ok() { echo "  ok    $*"; }
no() { echo "  STOP  $*"; STOP=1; }
note() { echo "  note  $*"; }
STOP=0
[ "$(id -u)" = 0 ] || { echo "run as root (it only reads)"; exit 1; }

echo "== server"
echo "  $(hostname) · $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown OS}") · up $(uptime -p 2>/dev/null)"
PUBLIC_IP="$(ip -4 -o addr show "$WAN_IF" scope global | awk '{print $4}' | cut -d/ -f1 | head -n1)"
echo "  IPv4 $PUBLIC_IP on $WAN_IF"
echo "  IPv6 $(ip -6 -o addr show "$WAN_IF" scope global | awk '{print $4}' | paste -sd' ' -)"

echo "== Kiwi agent"
if [ -f "$ENV" ]; then
  KIWI_IF="$(sed -n 's/^WG_INTERFACE=//p' "$ENV")"; KIWI_IF="${KIWI_IF:-wg0}"
  ok "agent config present; Kiwi's interface is $KIWI_IF"
  grep -E '^(AGENT_BIND|AGENT_PORT|WG_INTERFACE|POOL6?|PROXY_HOST|PROXY_PORT)=' "$ENV" | sed 's/^/        /'
  grep -q '^PROXY_HOST=.' "$ENV" && note "a browser proxy is already configured here"
else
  no "no $ENV: no Kiwi agent on this server"
  KIWI_IF=""
fi
for u in kiwi-vpn-agent kiwi-vpn-proxy; do echo "  $u: $(systemctl is-active "$u" 2>/dev/null)"; done
[ -f /opt/kiwi-vpn/agent.py ] && echo "  agent.py sha256 $(sha256sum /opt/kiwi-vpn/agent.py | cut -c1-16)  (installer would replace it with the repo's)"

echo "== WireGuard (interfaces, ports, peer counts; no keys)"
for i in $(wg show interfaces 2>/dev/null); do
  role="NOT Kiwi's: must stay untouched"; [ "$i" = "$KIWI_IF" ] && role="Kiwi's"
  echo "  $i  port $(wg show "$i" listen-port)  peers $(wg show "$i" peers | wc -l)  ($role)"
  [ "$i" != "$KIWI_IF" ] && OTHER="$i"
done
[ -n "${OTHER:-}" ] && note "run the installer with OTHER_IF=$OTHER so it stops if $OTHER changes" || note "no other WireGuard interface: OTHER_IF can stay at its default"
[ "$KIWI_IF" = wg0 ] && note "Kiwi uses wg0 here (install.sh style): the installer's OTHER_IF must NOT be wg0, or a customer joining mid-run stops it"

echo "== firewall"
if command -v ufw >/dev/null && ufw status | grep -q "^Status: active"; then
  ok "ufw is active (the installer's 'ufw allow' works)"; ufw status | sed -n '/^To/,$p' | sed 's/^/        /'
else
  no "ufw is not active: the installer would stop; this node needs its own step for 80/443"
fi
nft list ruleset >/dev/null 2>&1 && echo "  nftables: $(nft list ruleset 2>/dev/null | grep -c . ) lines of rules" || echo "  nftables: none"
echo "  provider firewall (Hetzner) can't be seen from here: check 80/tcp and 443/tcp there"

echo "== ports the proxy needs"
for p in 80 443; do
  if ss -ltnH "sport = :$p" | grep -q .; then
    who="$(ss -ltnpH "sport = :$p" | sed -n 's/.*users:((\"\([^\"]*\)\".*/\1/p' | head -n1)"
    [ "$who" = python3 ] && systemctl is-active --quiet kiwi-vpn-proxy && note "$p in use by the Kiwi proxy already" || no "$p is in use by ${who:-something}"
  else ok "$p is free"; fi
done
echo "  listening: $(ss -ltnH | awk '{print $4}' | sed 's/.*://' | sort -un | paste -sd' ' -)"

echo "== tools"
command -v python3 >/dev/null && ok "$(python3 --version)" || no "no python3"
command -v certbot >/dev/null && ok "certbot present" || note "certbot will be installed (apt)"
command -v iptables >/dev/null && command -v ip6tables >/dev/null && ok "iptables and ip6tables present" || no "iptables/ip6tables missing"

echo "== name"
if [ -n "$NAME" ]; then
  got="$(getent ahostsv4 "$NAME" | awk '{print $1; exit}')"
  [ "$got" = "$PUBLIC_IP" ] && ok "$NAME points here" || no "$NAME points at '${got:-nothing}', not $PUBLIC_IP: add the DNS record first"
else note "pass the planned name to check DNS"; fi

echo
[ "$STOP" = 0 ] && echo "READY: nothing blocks the proxy install." || echo "NOT READY: fix the STOP lines first."
