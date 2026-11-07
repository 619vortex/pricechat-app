#!/usr/bin/env bash
# Real WireGuard client test on a Kiwi node, run as root ON the node by `kiwi-infra.mjs selftest`.
# A network namespace gets only a veth to the host and a WireGuard client to the node's public address, so every packet it
# sends either goes through the tunnel or nowhere. Checks: handshake, IPv4 exit, the node's DNS, IPv6 refused quickly
# (no leak, no hang), no access to the node itself / metadata / private ranges / port 25. Removes everything it adds.
# Prints PASS/FAIL lines and never the agent token.
set -uo pipefail
NS=kiwi-selftest
PUB_IP="$(ip -4 -o addr show "$(ip -4 route show default | awk '{print $5; exit}')" scope global | awk '{split($4,a,"/"); print a[1]; exit}')"
TOKEN="$(sed -n 's/^TOKEN=//p' /etc/kiwi-vpn/agent.env)"
PORT="$(sed -n 's/^AGENT_PORT=//p' /etc/kiwi-vpn/agent.env)"; PORT="${PORT:-8443}"
SRV_KEY="$(wg show wg0 public-key)"; WG_PORT="$(wg show wg0 listen-port)"
CK="$(wg genkey)"; CP="$(printf '%s' "$CK" | wg pubkey)"
TEST_IP=10.66.255.250
fails=0
pass() { echo "PASS $*"; }
fail() { echo "FAIL $*"; fails=$((fails+1)); }
agent() { curl -sk -o /dev/null -w '%{http_code}' -X "$1" -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' ${3:+--data "$3"} "https://127.0.0.1:$PORT$2"; }
nsx() { ip netns exec "$NS" "$@"; }
cleanup() {
  ip netns del "$NS" 2>/dev/null; ip link del kst0 2>/dev/null; rm -rf "/etc/netns/$NS"
  [ "$(agent DELETE "/v1/peers/$(printf '%s' "$CP" | tr '+/' '-_')")" = 200 ] && echo "cleanup: test peer removed" || echo "cleanup: test peer NOT removed"
}
trap cleanup EXIT

PEERS_BEFORE="$(wg show wg0 peers | wc -l)"
[ "$(agent PUT "/v1/peers/$(printf '%s' "$CP" | tr '+/' '-_')" "{\"allowed_ip\":\"$TEST_IP/32\"}")" = 200 ] && pass "agent added a test peer" || { fail "agent refused the test peer"; exit 1; }
ALLOWED="$(wg show wg0 allowed-ips | awk -v k="$CP" '$1==k {print $2, $3}')"
echo "info: server-side allowed IPs for the test peer: $ALLOWED"
V6="$(echo "$ALLOWED" | tr ' ' '\n' | grep : | cut -d/ -f1)"

command -v dig >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq bind9-dnsutils >/dev/null
ip netns add "$NS"
mkdir -p "/etc/netns/$NS" && echo "nameserver 10.66.0.1" > "/etc/netns/$NS/resolv.conf"  # what clients are given
ip link add kst0 type veth peer name kst1
ip link set kst1 netns "$NS"
ip addr add 192.0.2.1/30 dev kst0; ip link set kst0 up
nsx ip addr add 192.0.2.2/30 dev kst1; nsx ip link set kst1 up; nsx ip link set lo up
nsx ip route add "$PUB_IP/32" via 192.0.2.1
nsx ip link add wgc type wireguard
printf '%s' "$CK" > /run/kst.key; chmod 600 /run/kst.key
nsx wg set wgc private-key /run/kst.key peer "$SRV_KEY" endpoint "$PUB_IP:$WG_PORT" allowed-ips 0.0.0.0/0,::/0 persistent-keepalive 5
rm -f /run/kst.key
nsx ip addr add "$TEST_IP/32" dev wgc
[ -n "$V6" ] && nsx ip -6 addr add "$V6/128" dev wgc nodad
nsx ip link set wgc up
nsx ip route add default dev wgc
nsx ip -6 route add default dev wgc 2>/dev/null

EXIT4="$(nsx curl -4 -s -m 10 https://api.ipify.org)"
[ "$EXIT4" = "$PUB_IP" ] && pass "IPv4 through the tunnel exits as $EXIT4" || fail "IPv4 exit was '$EXIT4', expected $PUB_IP"
HS="$(nsx wg show wgc latest-handshakes | awk '{print $2}')"
[ "${HS:-0}" -gt 0 ] && pass "WireGuard handshake done" || fail "no WireGuard handshake"
A="$(nsx dig +short +time=3 +tries=1 @10.66.0.1 example.com A | head -1)"
[ -n "$A" ] && pass "the node's resolver answers through the tunnel ($A)" || fail "no answer from 10.66.0.1"
W="$(nsx dig +short +time=3 +tries=1 @10.66.0.1 whoami.cloudflare TXT CH 2>/dev/null; nsx dig +short +time=3 @10.66.0.1 o-o.myaddr.l.google.com TXT | tr -d '"' | head -1)"
case "$W" in *"$PUB_IP"*|*2a01*|*:*) pass "public resolvers see the node, not the client ($(echo $W | tr '\n' ' '))";; "") echo "info: resolver-identity lookup gave nothing";; *) echo "info: resolver identity: $W";; esac
T0=$(date +%s%N); nsx curl -6 -s -m 8 -o /dev/null https://api64.ipify.org; RC=$?; MS=$(( ($(date +%s%N)-T0)/1000000 ))
[ $RC -ne 0 ] && [ $MS -lt 3000 ] && pass "IPv6 is refused, quickly (${MS} ms, curl exit $RC): no IPv6 leaves, apps fall back to IPv4" || fail "IPv6: curl exit $RC after ${MS} ms"
for t in "10.66.0.1:22 the node's SSH over the tunnel" "10.66.0.1:$PORT the agent over the tunnel" "169.254.169.254:80 the metadata service" "10.0.0.1:80 a private range" "192.168.1.1:80 a private range"; do
  set -- $t; hp="$1"; shift
  nsx timeout 5 bash -c "</dev/tcp/${hp%:*}/${hp#*:}" 2>/dev/null && fail "reached $hp ($*)" || pass "refused $hp ($*)"
done
nsx timeout 6 bash -c "</dev/tcp/aspmx.l.google.com/25" 2>/dev/null && fail "port 25 is reachable" || pass "port 25 (mail) blocked"
nsx ping -c1 -W2 "${TEST_IP%.*}.249" >/dev/null 2>&1 && fail "reached another client address" || pass "no client-to-client traffic"
echo "info: peers before $PEERS_BEFORE"
echo "summary: $fails failed"
exit $fails
