#!/usr/bin/env bash
# Closes the browser proxy's IPv6 gap on a node that already runs it (Helsinki today): the proxy's users could open
# connections to this server's own IPv6 addresses (its SSH, the agent), which the provider's firewall doesn't see.
#
#   bash apply-proxy-ipv6-fix.sh            read-only: shows exactly what would change, changes nothing (the default)
#   bash apply-proxy-ipv6-fix.sh --apply    makes the change
#   bash apply-proxy-ipv6-fix.sh --rollback /root/kiwi-pre-ipv6fix-<time>
#
# Touches only: /opt/kiwi-vpn/proxy.py, /etc/kiwi-vpn-proxy/proxy.env (adds OWN_NETS), /etc/kiwi-vpn-proxy/rules.sh
# (adds ip6tables rejects for the proxy's own user), then restarts kiwi-vpn-proxy alone. It doesn't touch WireGuard
# (any interface, its config or peers), the agent, ufw, SSH or the certificate. It records every WireGuard interface's
# config, peers and port first and stops (and rolls back) if any of them changes.
# Run as root with the new proxy.py next to this script. Env: WAN_IF (eth0), OWN_NETS (override the detected networks).
set -euo pipefail

WAN_IF="${WAN_IF:-eth0}"
R="${KIWI_TEST_ROOT:-}"  # tests only: a fake root folder (test_apply_ipv6_fix.py); empty on a server
ETC="$R/etc/kiwi-vpn-proxy"
OPT="$R/opt/kiwi-vpn"
ENVF="$ETC/proxy.env"
RULES="$ETC/rules.sh"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# proxy.py as installed on 2026-11-04 (main b840198..5107c81): anything else means it was changed by hand, so stop.
KNOWN_OLD=be1bf8be1542c668445356d18e833bff10f45a9e19fe1a11a8047ec6051d7e96
MARK="# kiwi: own IPv6"

OWNER="-o root -g kiwi-proxy"; [ -n "$R" ] && OWNER=""
die() { echo "error: $*" >&2; exit 1; }
say() { echo "==> $*"; }
[ "$(id -u)" = 0 ] || die "run as root"

# Every WireGuard interface: its config file, peers (keys + allowed IPs + endpoint-free), port. Handshakes/bytes excluded.
wg_state() {
  for i in $(wg show interfaces 2>/dev/null); do
    echo "== $i"
    sha256sum "$R/etc/wireguard/$i.conf" 2>/dev/null || echo "no conf"
    wg show "$i" listen-port
    wg show "$i" allowed-ips | sort
  done
}
wg_digest() { wg_state | sha256sum | cut -d' ' -f1; }

restore() {
  local dir="$1"
  [ -f "$dir/proxy.py" ] && [ -f "$dir/proxy.env" ] && [ -f "$dir/rules.sh" ] || die "$dir isn't a backup from this script"
  install -m 0644 "$dir/proxy.py" "$OPT/proxy.py"
  install -m 0640 $OWNER "$dir/proxy.env" "$ENVF"
  install -m 0750 "$dir/rules.sh" "$RULES"
  systemctl restart kiwi-vpn-proxy
  sleep 2
  systemctl is-active --quiet kiwi-vpn-proxy && say "Restored from $dir; the proxy is running" || die "restored, but the proxy didn't start: journalctl -u kiwi-vpn-proxy"
}

if [ "${1:-}" = "--rollback" ]; then
  [ -n "${2:-}" ] || die "say which backup: --rollback /root/kiwi-pre-ipv6fix-<time>"
  BEFORE="$(wg_digest)"
  restore "$2"
  [ "$(wg_digest)" = "$BEFORE" ] || die "WireGuard changed during the rollback: investigate"
  exit 0
fi

APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1
[ -z "${1:-}" ] || [ "$APPLY" = 1 ] || die "unknown option $1"

# ---- checks (all read-only) ----
[ -f "$HERE/proxy.py" ] || die "the new proxy.py must be next to this script"
grep -q '"OWN_NETS"' "$HERE/proxy.py" || die "the proxy.py next to this script doesn't have the fix"
python3 -I -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$HERE/proxy.py" || die "the new proxy.py doesn't parse"
systemctl cat kiwi-vpn-proxy >/dev/null 2>&1 || die "no browser proxy on this server: use install-proxy.sh instead"
[ -f "$ENVF" ] && [ -f "$RULES" ] && [ -f "$OPT/proxy.py" ] || die "the proxy's files aren't where install-proxy.sh puts them"
LIVE="$(sha256sum "$OPT/proxy.py" | cut -d' ' -f1)"
NEW="$(sha256sum "$HERE/proxy.py" | cut -d' ' -f1)"
if [ "$LIVE" != "$KNOWN_OLD" ] && [ "$LIVE" != "$NEW" ] && [ "${FORCE:-0}" != 1 ]; then
  die "the installed proxy.py ($LIVE) isn't the version this fix replaces: stop and compare it first (FORCE=1 to go on anyway)"
fi
grep -q 'ip6tables -N KIWI-PROXY' "$RULES" || die "rules.sh has no ip6tables chain: it isn't the installer's"
grep -q 'ip6tables -A KIWI-PROXY -o lo -j REJECT' "$RULES" || die "rules.sh doesn't have the line the new rules go after"
OWN_NETS="${OWN_NETS-$(ip -6 -o addr show "$WAN_IF" scope global | awk '{print $4}' | python3 -I -c 'import ipaddress,sys; print(",".join(sorted({str(ipaddress.ip_interface(l.strip()).network) for l in sys.stdin if l.strip()})))')}"
[ -n "$OWN_NETS" ] || die "no global IPv6 on $WAN_IF: nothing to protect (set WAN_IF if it's another interface)"
python3 -I -c "
import ipaddress,sys
for n in sys.argv[1].split(','):
    net = ipaddress.ip_network(n, strict=False)
    assert net.version == 6 and net.prefixlen >= 48, n
" "$OWN_NETS" || die "OWN_NETS ($OWN_NETS) isn't a list of IPv6 networks of /48 or smaller"

# ---- the new files, built in a temporary folder ----
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
grep -v '^OWN_NETS=' "$ENVF" > "$WORK/proxy.env"
echo "OWN_NETS=$OWN_NETS" >> "$WORK/proxy.env"
python3 -I - "$RULES" "$WORK/rules.sh" "$OWN_NETS" "$MARK" <<'PY'
import sys
src, dst, nets, mark = sys.argv[1:]
lines = [l for l in open(src).read().split("\n") if mark not in l]
anchor = "    ip6tables -A KIWI-PROXY -o lo -j REJECT"
i = lines.index(anchor)
lines[i + 1:i + 1] = [f"    ip6tables -A KIWI-PROXY -d {n} -j REJECT  {mark}" for n in nets.split(",")]
open(dst, "w").write("\n".join(lines))
PY
sh -n "$WORK/rules.sh" || die "the new rules.sh doesn't parse"

echo "WireGuard now (kept exactly as it is):"
wg_state | sed 's/^/    /'
echo
echo "proxy.py: $LIVE -> $NEW"
echo "proxy.env:"; diff -u "$ENVF" "$WORK/proxy.env" | sed -n '3,$p' | sed 's/^/    /' || true
echo "rules.sh:";  diff -u "$RULES" "$WORK/rules.sh" | sed -n '3,$p' | sed 's/^/    /' || true
echo
echo "Then: systemctl restart kiwi-vpn-proxy (only). Browser users reconnect by themselves within seconds."

if [ "$APPLY" != 1 ]; then
  echo
  echo "Nothing changed. Run again with --apply to make this change."
  exit 0
fi

# ---- apply ----
BEFORE="$(wg_digest)"
BACKUP="$R/root/kiwi-pre-ipv6fix-$(date -u +%Y%m%dT%H%M%SZ)"
(umask 077; mkdir -p "$BACKUP")
cp -p "$OPT/proxy.py" "$ENVF" "$RULES" "$BACKUP/"
say "Backup in $BACKUP"
fail() { echo "error: $*: rolling back" >&2; restore "$BACKUP"; exit 1; }

install -m 0644 "$HERE/proxy.py" "$OPT/proxy.py"
install -m 0640 $OWNER "$WORK/proxy.env" "$ENVF"
install -m 0750 "$WORK/rules.sh" "$RULES"
say "Restarting the proxy (only)"
systemctl restart kiwi-vpn-proxy || fail "the proxy didn't restart"
sleep 2
systemctl is-active --quiet kiwi-vpn-proxy || fail "the proxy isn't running"
for n in $(echo "$OWN_NETS" | tr ',' ' '); do
  ip6tables -S KIWI-PROXY | grep -q -- "-d $n -j REJECT" || fail "the reject for $n isn't in the firewall"
done
ss -ltn "sport = :443" | grep -q LISTEN || fail "nothing listens on 443"
[ "$(wg_digest)" = "$BEFORE" ] || { echo "error: WireGuard changed during this run (not by this script): investigate. The proxy fix stays applied; roll back with --rollback $BACKUP" >&2; exit 1; }
say "Done. WireGuard unchanged. Undo: bash $0 --rollback $BACKUP"
