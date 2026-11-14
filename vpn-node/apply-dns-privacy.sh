#!/usr/bin/env bash
# DNS privacy (audit M6/M7) and the proxy/agent hardening for a node that already runs install-shared.sh + install-proxy.sh
# (Helsinki today), without re-running the installers. NEVER RUN AGAINST THE LIVE NODE YET: tested only against a fake server
# (test_apply_dns_privacy.py); the owner approves and runs it.
#
#   bash apply-dns-privacy.sh                   read-only: shows exactly what would change, changes nothing (the default)
#   bash apply-dns-privacy.sh --apply           makes the change
#   bash apply-dns-privacy.sh --rollback /root/kiwi-pre-dns-<time>
#
# What --apply changes, in this order (each step is checked before the next; any failure rolls everything back):
#   1. unbound (installed if missing) with no query logs on the wg1 address (customers) and on 127.0.0.1 (the proxy);
#      it must answer on both before anything is switched to it
#   2. wg1-rules.sh: customers may reach that resolver (udp/tcp 53 on the wg1 address) and no mail ports 25/465/587;
#      applied in place with the rules script itself (no interface restart, peers stay up)
#   3. agent.env DNS=<wg1 address> (the backend hands it to devices from its next sync) and the new agent.py; agent restart
#   4. the new proxy.py (M2 limits + RESOLVER), proxy.env RESOLVER=127.0.0.1, the proxy's guard chain allows DNS only to
#      127.0.0.1 (no longer 127.0.0.53, systemd-resolved, the provider's resolvers); proxy restart
# It never touches wg0 or any other WireGuard interface, their configs or peers, ufw, SSH, the certificate or the floating-IP
# NAT rules already in the rules files (kept line for line). It records every WireGuard interface first and stops if one changes.
# Run as root with the new agent.py, proxy.py, install-shared.sh and install-proxy.sh next to this script.
# Env: FORCE=1 (installed agent.py/proxy.py aren't a version this was written against: go on anyway, after comparing).
set -euo pipefail

R="${KIWI_TEST_ROOT:-}"  # tests only: a fake root folder (test_apply_dns_privacy.py); empty on a server
ETC="$R/etc/kiwi-vpn"
PETC="$R/etc/kiwi-vpn-proxy"
OPT="$R/opt/kiwi-vpn"
UB="$R/etc/unbound/unbound.conf.d"
AGENT_ENV="$ETC/agent.env"
PROXY_ENV="$PETC/proxy.env"
PROXY_RULES="$PETC/rules.sh"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# what is on Helsinki: agent.py from b840198 (or 947152b before the proxy), proxy.py from 407896e (own-IPv6 fix) or b840198
KNOWN_AGENTS="966ac1e176b8bcd0f8d600a3fde7131d723117087bd030d9b4542a01b4dabd55 921ea0651e1d7a5b6090606a8ca733d414431d4b2a434d7db4d8dcfa071f92d6"
KNOWN_PROXIES="99478b3f419399c68cef8e3e7ddc879cf9f1b5503fa7e4d562d82d33181f8ec4 be1bf8be1542c668445356d18e833bff10f45a9e19fe1a11a8047ec6051d7e96"
MARK="# kiwi: dns"
RESOLVER=127.0.0.1

OWNER_P="-o root -g kiwi-proxy"; OWNER_A="-o root -g kiwi-vpn"; [ -n "$R" ] && OWNER_P="" && OWNER_A=""
die() { echo "error: $*" >&2; exit 1; }
say() { echo "==> $*"; }
[ "$(id -u)" = 0 ] || die "run as root"

wg_state() {
  for i in $(wg show interfaces 2>/dev/null); do
    echo "== $i"
    sha256sum "$R/etc/wireguard/$i.conf" 2>/dev/null || echo "no conf"
    wg show "$i" listen-port
    wg show "$i" allowed-ips | sort
  done
}
wg_digest() { wg_state | sha256sum | cut -d' ' -f1; }

dns_probe() {  # $1 = resolver address: does it answer a real question?
  if [ -n "${KIWI_TEST_DNS_PROBE:-}" ]; then "$KIWI_TEST_DNS_PROBE" "$1"; return; fi
  python3 -I -c "import asyncio,sys; sys.path.insert(0, sys.argv[1]); import proxy; sys.exit(0 if asyncio.run(proxy.dns_lookup(sys.argv[2], 'one.one.one.one')) else 1)" "$HERE" "$1" 2>/dev/null
}
wait_dns() { for _ in 1 2 3 4 5 6 7 8 9 10; do dns_probe "$1" && return 0; sleep 1; done; return 1; }

# Kiwi's SNAT rules (floating IP) must come before any generic MASQUERADE in nat POSTROUTING, or they never match.
nat_order_ok() {
  iptables -t nat -S POSTROUTING | python3 -I -c '
import sys
pool, lines = sys.argv[1], [l.split() for l in sys.stdin if l.startswith("-A POSTROUTING")]
kiwi = [i for i, l in enumerate(lines) if "SNAT" in l and ((pool in l) or ("kiwi-proxy" in l))]
generic = [i for i, l in enumerate(lines) if "MASQUERADE" in l and "-s" not in l]
sys.exit(0 if not kiwi or not generic or max(kiwi) < min(generic) else 1)' "$POOL_NET"
}

restore() {
  local dir="$1"
  [ -f "$dir/agent.env" ] && [ -f "$dir/wg1-rules.sh" ] && [ -f "$dir/proxy.env" ] && [ -f "$dir/rules.sh" ] || die "$dir isn't a backup from this script"
  install -m 0644 "$dir/agent.py" "$OPT/agent.py"
  install -m 0644 "$dir/proxy.py" "$OPT/proxy.py"
  install -m 0640 $OWNER_A "$dir/agent.env" "$AGENT_ENV"
  install -m 0750 "$dir/wg1-rules.sh" "$WG_RULES"
  install -m 0640 $OWNER_P "$dir/proxy.env" "$PROXY_ENV"
  install -m 0750 "$dir/rules.sh" "$PROXY_RULES"
  "$WG_RULES" up || echo "warning: the old wg1 rules didn't apply cleanly: check iptables -S KIWI-IN" >&2
  systemctl restart kiwi-vpn-agent || true
  systemctl restart kiwi-vpn-proxy || true
  for f in kiwi-vpn.conf kiwi-proxy.conf; do
    if [ -f "$dir/unbound-$f" ]; then install -m 0644 "$dir/unbound-$f" "$UB/$f"; else rm -f "$UB/$f"; fi
  done
  if [ -f "$dir/unbound-was-active" ]; then systemctl restart unbound || true; else systemctl disable --now unbound >/dev/null 2>&1 || true; fi
  # customers are back on the old DNS: a later install-shared.sh re-run must keep that until someone chooses otherwise
  if [ -f "$ETC/install.env" ]; then sed -i '/^LOCAL_DNS=/d' "$ETC/install.env"; echo "LOCAL_DNS=0" >> "$ETC/install.env"; fi
  sleep 2
  systemctl is-active --quiet kiwi-vpn-proxy && systemctl is-active --quiet kiwi-vpn-agent && say "Restored from $dir; the agent and the proxy are running" \
    || die "restored from $dir, but the agent or the proxy isn't running: journalctl -u kiwi-vpn-agent -u kiwi-vpn-proxy"
}

# ---- what this node is (read-only) ----
[ -f "$AGENT_ENV" ] || die "no agent here ($AGENT_ENV)"
WG_IF="$(sed -n 's/^WG_INTERFACE=//p' "$AGENT_ENV" | head -n1)"
POOL="$(sed -n 's/^POOL=//p' "$AGENT_ENV" | head -n1)"
[ -n "$WG_IF" ] && [ -n "$POOL" ] || die "agent.env has no WG_INTERFACE / POOL"
[ "$WG_IF" != wg0 ] || die "the agent runs on wg0: this script is for shared nodes (install-shared.sh), not install.sh nodes"
WG_ADDR="${POOL%/*}"
POOL_NET="$(python3 -I -c "import ipaddress,sys; print(ipaddress.ip_interface(sys.argv[1]).network)" "$POOL")"
WG_RULES="$ETC/$WG_IF-rules.sh"

if [ "${1:-}" = "--rollback" ]; then
  [ -n "${2:-}" ] || die "say which backup: --rollback /root/kiwi-pre-dns-<time>"
  BEFORE="$(wg_digest)"
  restore "$2"
  [ "$(wg_digest)" = "$BEFORE" ] || die "WireGuard changed during the rollback: investigate"
  exit 0
fi

APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1
[ -z "${1:-}" ] || [ "$APPLY" = 1 ] || die "unknown option $1"

# ---- checks (all read-only) ----
for f in agent.py proxy.py install-shared.sh install-proxy.sh; do [ -f "$HERE/$f" ] || die "$f must be next to this script"; done
grep -q '"RESOLVER"' "$HERE/proxy.py" || die "the proxy.py next to this script doesn't have RESOLVER"
grep -q 'PRE_LOGIN_PER_IP' "$HERE/proxy.py" || die "the proxy.py next to this script doesn't have the connection limits"
for f in agent.py proxy.py; do python3 -I -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$HERE/$f" || die "the new $f doesn't parse"; done
systemctl cat kiwi-vpn-proxy >/dev/null 2>&1 || die "no browser proxy on this server"
for f in "$WG_RULES" "$PROXY_ENV" "$PROXY_RULES" "$OPT/agent.py" "$OPT/proxy.py"; do [ -f "$f" ] || die "$f is missing: this node isn't laid out as install-shared.sh + install-proxy.sh make it"; done
LIVE_A="$(sha256sum "$OPT/agent.py" | cut -d' ' -f1)"; NEW_A="$(sha256sum "$HERE/agent.py" | cut -d' ' -f1)"
LIVE_P="$(sha256sum "$OPT/proxy.py" | cut -d' ' -f1)"; NEW_P="$(sha256sum "$HERE/proxy.py" | cut -d' ' -f1)"
known() { local h="$1" new="$2" list="$3"; [ "$h" = "$new" ] || case " $list " in *" $h "*) true ;; *) false ;; esac; }
if [ "${FORCE:-0}" != 1 ]; then
  known "$LIVE_A" "$NEW_A" "$KNOWN_AGENTS" || die "the installed agent.py ($LIVE_A) isn't a version this was written against: compare it first (FORCE=1 to go on)"
  known "$LIVE_P" "$NEW_P" "$KNOWN_PROXIES" || die "the installed proxy.py ($LIVE_P) isn't a version this was written against: compare it first (FORCE=1 to go on)"
fi
grep -q '^    iptables -A KIWI-IN -j REJECT$' "$WG_RULES" || die "$WG_RULES doesn't have the line the DNS rules go before: it isn't the installer's"
grep -q 'iptables -N KIWI-PROXY' "$PROXY_RULES" || die "$PROXY_RULES isn't the iptables rules install-proxy.sh writes (an nftables node?)"
grep -Eq -- "-d (127\.0\.0\.53|$RESOLVER) -p udp --dport 53 -j RETURN" "$PROXY_RULES" || die "$PROXY_RULES has no DNS line to change"
OTHER53="$( (ss -Hlnup 'sport = :53' || true) | grep -E "(127\.0\.0\.1|$(printf '%s' "$WG_ADDR" | sed 's/\./\\./g')|0\.0\.0\.0|\*):53 " | grep -v '"unbound"' || true)"
[ -z "$OTHER53" ] || die "another DNS server listens where unbound must: $OTHER53"
OTHERCONF="$( (grep -rlE '^\s*interface:' "$R/etc/unbound/" 2>/dev/null || true) | grep -vE '/kiwi-(vpn|proxy)\.conf$' || true)"
[ -z "$OTHERCONF" ] || die "unbound already has interfaces set in $OTHERCONF: this host's unbound is used for something else, stop and check"

# ---- the new files, built in a temporary folder (unbound configs straight from the installers) ----
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
section() { awk -v h="##### $2 #####" '$0 ~ /^##### / {on = ($0 == h); next} on' "$1"; }
POOL="$POOL" WG_IF="$WG_IF" LOCAL_DNS=1 bash "$HERE/install-shared.sh" --print-config > "$WORK/shared.out" 2>/dev/null || die "install-shared.sh --print-config failed"
LOCAL_DNS=1 bash "$HERE/install-proxy.sh" --print-config > "$WORK/proxy.out" 2>/dev/null || die "install-proxy.sh --print-config failed"
section "$WORK/shared.out" /etc/unbound/unbound.conf.d/kiwi-vpn.conf > "$WORK/kiwi-vpn.conf"
section "$WORK/proxy.out" /etc/unbound/unbound.conf.d/kiwi-proxy.conf > "$WORK/kiwi-proxy.conf"
grep -q "interface: $WG_ADDR" "$WORK/kiwi-vpn.conf" && grep -q "interface: $RESOLVER" "$WORK/kiwi-proxy.conf" || die "couldn't build the unbound configs"

python3 -I - "$WG_RULES" "$WORK/wg1-rules.sh" "$WG_ADDR" "$POOL_NET" "$MARK" <<'PY'
import sys
src, dst, addr, pool, mark = sys.argv[1:]
lines = [l for l in open(src).read().split("\n") if mark not in l]
i = lines.index("    iptables -A KIWI-IN -j REJECT")
lines[i:i] = [f"    iptables -A KIWI-IN -s {pool} -d {addr} -p {p} --dport 53 -j ACCEPT  {mark}" for p in ("udp", "tcp")]
old = "    iptables -A KIWI-FWD -p tcp --dport 25 -j REJECT --reject-with tcp-reset"
if old in lines:  # L19: no mail ports at all, not just 25
    lines[lines.index(old)] = "    iptables -A KIWI-FWD -p tcp -m multiport --dports 25,465,587 -j REJECT --reject-with tcp-reset  # kiwi: no mail"
open(dst, "w").write("\n".join(lines))
PY
sed 's/^\(    iptables -A KIWI-PROXY -d \)127\.0\.0\.53\( -p \(udp\|tcp\) --dport 53 -j RETURN\)$/\1'"$RESOLVER"'\2/' "$PROXY_RULES" > "$WORK/rules.sh"
{ grep -v '^DNS=' "$AGENT_ENV"; echo "DNS=$WG_ADDR"; } > "$WORK/agent.env"
{ grep -v '^RESOLVER=' "$PROXY_ENV"; echo "RESOLVER=$RESOLVER"; } > "$WORK/proxy.env"
for f in wg1-rules.sh rules.sh; do sh -n "$WORK/$f" || die "the new $f doesn't parse"; done
grep -q -- "-d $RESOLVER -p udp --dport 53 -j RETURN" "$WORK/rules.sh" && ! grep -q '127\.0\.0\.53' "$WORK/rules.sh" || die "couldn't switch the proxy's DNS rule"
INSTALL_ENV_NEW="$( { grep -v '^LOCAL_DNS=' "$ETC/install.env" 2>/dev/null || true; echo "LOCAL_DNS=1"; } )"

echo "WireGuard now (kept exactly as it is):"
wg_state | sed 's/^/    /'
echo
show() { echo "$1:"; if [ -f "$2" ]; then diff -u "$2" "$3" | sed -n '3,$p' | sed 's/^/    /' || true; else sed 's/^/    +/' "$3"; fi; }
show "unbound $UB/kiwi-vpn.conf (customers)" "$UB/kiwi-vpn.conf" "$WORK/kiwi-vpn.conf"
show "unbound $UB/kiwi-proxy.conf (proxy)" "$UB/kiwi-proxy.conf" "$WORK/kiwi-proxy.conf"
show "$WG_RULES" "$WG_RULES" "$WORK/wg1-rules.sh"
show "$AGENT_ENV" "$AGENT_ENV" "$WORK/agent.env"
show "$PROXY_ENV" "$PROXY_ENV" "$WORK/proxy.env"
show "$PROXY_RULES" "$PROXY_RULES" "$WORK/rules.sh"
echo "agent.py: $LIVE_A -> $NEW_A"
echo "proxy.py: $LIVE_P -> $NEW_P"
command -v unbound >/dev/null 2>&1 || echo "unbound isn't installed: apt-get install unbound (the package's resolvconf hook is masked first)"
echo
echo "Then: unbound restart, $WG_IF rules re-applied in place (no interface restart), kiwi-vpn-agent and kiwi-vpn-proxy restart"
echo "(about 2 s each; browser users reconnect by themselves). Devices get DNS $WG_ADDR from their next config download;"
echo "configs already on devices keep 1.1.1.1, which still works."

if [ "$APPLY" != 1 ]; then
  echo
  echo "Nothing changed. Run again with --apply to make this change."
  exit 0
fi

# ---- apply ----
BEFORE="$(wg_digest)"
BACKUP="$R/root/kiwi-pre-dns-$(date -u +%Y%m%dT%H%M%SZ)"
(umask 077; mkdir -p "$BACKUP")
cp -p "$OPT/agent.py" "$OPT/proxy.py" "$AGENT_ENV" "$PROXY_ENV" "$PROXY_RULES" "$BACKUP/"
cp -p "$WG_RULES" "$BACKUP/wg1-rules.sh"
[ -f "$ETC/install.env" ] && cp -p "$ETC/install.env" "$BACKUP/install.env"
for f in kiwi-vpn.conf kiwi-proxy.conf; do [ -f "$UB/$f" ] && cp -p "$UB/$f" "$BACKUP/unbound-$f"; done
systemctl is-active --quiet unbound && touch "$BACKUP/unbound-was-active"
iptables -t nat -S POSTROUTING > "$BACKUP/nat-postrouting.txt" 2>/dev/null || true
say "Backup in $BACKUP"
fail() { echo "error: $*: rolling back" >&2; restore "$BACKUP"; exit 1; }

say "1/4 unbound on $WG_ADDR and $RESOLVER (no query logs)"
if ! command -v unbound >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get update -qq || fail "apt-get update failed"
  systemctl mask -q unbound-resolvconf.service 2>/dev/null || true
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq unbound >/dev/null || fail "couldn't install unbound"
fi
systemctl mask -q unbound-resolvconf.service 2>/dev/null || true  # never let the package take over this host's own resolver
install -d "$UB"
install -m 0644 "$WORK/kiwi-vpn.conf" "$UB/kiwi-vpn.conf"
install -m 0644 "$WORK/kiwi-proxy.conf" "$UB/kiwi-proxy.conf"
unbound-checkconf >/dev/null || fail "unbound's config doesn't check"
systemctl enable -q unbound || fail "couldn't enable unbound"
systemctl restart unbound || fail "unbound didn't start"
wait_dns "$RESOLVER" || fail "unbound doesn't answer on $RESOLVER:53"
wait_dns "$WG_ADDR" || fail "unbound doesn't answer on $WG_ADDR:53"

say "2/4 $WG_IF rules: DNS to $WG_ADDR, no mail ports (in place)"
install -m 0750 "$WORK/wg1-rules.sh" "$WG_RULES"
"$WG_RULES" up || fail "the new $WG_IF rules didn't apply"
iptables -S KIWI-IN | grep -q -- "--dport 53 -j ACCEPT" || fail "the DNS rule isn't in KIWI-IN"
nat_order_ok || fail "Kiwi's NAT rules aren't first in nat POSTROUTING any more"

say "3/4 agent: DNS=$WG_ADDR, new agent.py"
install -m 0644 "$HERE/agent.py" "$OPT/agent.py"
install -m 0640 $OWNER_A "$WORK/agent.env" "$AGENT_ENV"
printf '%s\n' "$INSTALL_ENV_NEW" > "$ETC/install.env"
systemctl restart kiwi-vpn-agent || fail "the agent didn't restart"
sleep 2
systemctl is-active --quiet kiwi-vpn-agent || fail "the agent isn't running"

say "4/4 proxy: resolver $RESOLVER only, connection limits"
install -m 0644 "$HERE/proxy.py" "$OPT/proxy.py"
install -m 0640 $OWNER_P "$WORK/proxy.env" "$PROXY_ENV"
install -m 0750 "$WORK/rules.sh" "$PROXY_RULES"
systemctl restart kiwi-vpn-proxy || fail "the proxy didn't restart"
sleep 2
systemctl is-active --quiet kiwi-vpn-proxy || fail "the proxy isn't running"
iptables -S KIWI-PROXY | grep -Eq -- "-d $RESOLVER(/32)? -p udp( -m udp)? --dport 53 -j RETURN" || fail "the proxy's DNS rule isn't in the firewall"
! iptables -S KIWI-PROXY | grep -q '127\.0\.0\.53' || fail "the proxy can still reach 127.0.0.53"
ss -ltn "sport = :443" | grep -q LISTEN || fail "nothing listens on 443"
nat_order_ok || fail "Kiwi's NAT rules aren't first in nat POSTROUTING any more"
[ "$(wg_digest)" = "$BEFORE" ] || { echo "error: WireGuard changed during this run (not by this script): investigate. The change stays applied; roll back with --rollback $BACKUP" >&2; exit 1; }
say "Done. WireGuard unchanged. Undo: bash $0 --rollback $BACKUP"
