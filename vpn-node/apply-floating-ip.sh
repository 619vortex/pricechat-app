#!/usr/bin/env bash
# Puts the hand-made Helsinki floating-IP setup (2026-11-12) into code: customers (wg1) and the browser proxy leave from, and
# connect to, a second IPv4 (a Hetzner floating IP) while the server itself and wg0 keep the main address.
# NEVER RUN AGAINST THE LIVE NODE YET: tested only against a fake server (test_apply_floating_ip.py); the owner approves and runs it.
#
#   FLOAT_IP=192.0.2.50 bash apply-floating-ip.sh            read-only: what would change (the default); also reports whether
#                                                            Kiwi's NAT is first in nat POSTROUTING right now
#   FLOAT_IP=192.0.2.50 bash apply-floating-ip.sh --apply    makes the change
#   bash apply-floating-ip.sh --rollback /root/kiwi-pre-fip-<time>
#
# What --apply changes (idempotent: on a node that already has it by hand, only the differences):
#   - /etc/netplan/60-kiwi-floating.yaml: FLOAT_IP/32 on WAN_IF at boot (checked with `netplan generate`, never `netplan apply`),
#     and `ip addr add` now if it isn't there yet
#   - wg1-rules.sh: customers' NAT = SNAT to FLOAT_IP, inserted FIRST in nat POSTROUTING (wg0's generic MASQUERADE shadowed an
#     appended rule); any older Kiwi NAT line for the pool is replaced; applied in place with the rules script (peers stay up)
#   - the proxy's rules.sh: SNAT to FLOAT_IP for the kiwi-proxy user only, also first; proxy.env PUBLIC_IPS=FLOAT_IP,<main>
#     and EXIT_IP (the check page shows the floating IP; both stay refused as destinations)
#   - agent.env ENDPOINT_HOST=FLOAT_IP (new device configs connect to it); install.env EXIT_IP so installer re-runs keep it
#   - removes stray Kiwi NAT rules in nat POSTROUTING (source = the wg1 pool, or the kiwi-proxy user) before re-applying
# It never touches wg0, its config, peers or its MASQUERADE, ufw, SSH or the main address. Restarts: agent and proxy (~2 s each).
# Run as root with the current install-shared.sh/install-proxy.sh next to this script. Env: FLOAT_IP, WAN_IF (eth0).
set -euo pipefail

R="${KIWI_TEST_ROOT:-}"  # tests only: a fake root folder; empty on a server
WAN_IF="${WAN_IF:-eth0}"
ETC="$R/etc/kiwi-vpn"
PETC="$R/etc/kiwi-vpn-proxy"
AGENT_ENV="$ETC/agent.env"
PROXY_ENV="$PETC/proxy.env"
PROXY_RULES="$PETC/rules.sh"
NETPLAN="$R/etc/netplan/60-kiwi-floating.yaml"
MARK="# kiwi: floating"

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

[ -f "$AGENT_ENV" ] || die "no agent here ($AGENT_ENV)"
WG_IF="$(sed -n 's/^WG_INTERFACE=//p' "$AGENT_ENV" | head -n1)"
POOL="$(sed -n 's/^POOL=//p' "$AGENT_ENV" | head -n1)"
[ -n "$WG_IF" ] && [ -n "$POOL" ] || die "agent.env has no WG_INTERFACE / POOL"
[ "$WG_IF" != wg0 ] || die "the agent runs on wg0: this script is for shared nodes (install-shared.sh)"
POOL_NET="$(python3 -I -c "import ipaddress,sys; print(ipaddress.ip_interface(sys.argv[1]).network)" "$POOL")"
WG_RULES="$ETC/$WG_IF-rules.sh"
HAVE_PROXY=0 PX=""
if [ -f "$PROXY_RULES" ]; then HAVE_PROXY=1; PX=" and the proxy"; fi

# nat POSTROUTING as a report: Kiwi's SNAT rules must all come before any generic MASQUERADE (the one wg0 uses)
nat_report() {
  iptables -t nat -S POSTROUTING | python3 -I -c '
import sys
pool, lines = sys.argv[1], [l.split() for l in sys.stdin if l.startswith("-A POSTROUTING")]
kiwi = [i for i, l in enumerate(lines) if "SNAT" in l and (pool in l or "kiwi-proxy" in l)]
generic = [i for i, l in enumerate(lines) if "MASQUERADE" in l and "-s" not in l]
for i, l in enumerate(lines):
    print(f"    {i + 1}. {chr(32).join(l[2:])}")
ok = not kiwi or not generic or max(kiwi) < min(generic)
print("    order: OK (Kiwi SNAT first)" if ok else "    order: WRONG: a generic MASQUERADE comes before Kiwi SNAT, customers leave from the main IP")
sys.exit(0 if ok else 1)' "$POOL_NET"
}

# every nat POSTROUTING rule that belongs to Kiwi (source = the pool, or the proxy's user): never wg0's
kiwi_nat_rules() { iptables -t nat -S POSTROUTING | grep -E -- "^-A POSTROUTING .*(-s $POOL_NET |--uid-owner kiwi-proxy )" || true; }
drop_kiwi_nat() { kiwi_nat_rules | while read -r line; do iptables -t nat ${line/#-A/-D} || true; done; }

restore() {
  local dir="$1"
  [ -f "$dir/agent.env" ] && [ -f "$dir/wg1-rules.sh" ] || die "$dir isn't a backup from this script"
  install -m 0640 $OWNER_A "$dir/agent.env" "$AGENT_ENV"
  install -m 0750 "$dir/wg1-rules.sh" "$WG_RULES"
  if [ -f "$dir/install.env" ]; then cp -p "$dir/install.env" "$ETC/install.env"; else rm -f "$ETC/install.env"; fi
  if [ -f "$dir/rules.sh" ]; then install -m 0750 "$dir/rules.sh" "$PROXY_RULES"; install -m 0640 $OWNER_P "$dir/proxy.env" "$PROXY_ENV"; fi
  if [ -f "$dir/60-kiwi-floating.yaml" ]; then install -m 0600 "$dir/60-kiwi-floating.yaml" "$NETPLAN"; else rm -f "$NETPLAN"; fi
  if [ -f "$dir/address-added" ]; then ip addr del "$(cat "$dir/address-added")/32" dev "$WAN_IF" || true; fi
  drop_kiwi_nat
  "$WG_RULES" up || echo "warning: the old $WG_IF rules didn't apply cleanly: check iptables -t nat -S POSTROUTING" >&2
  systemctl restart kiwi-vpn-agent || true
  if [ -f "$dir/rules.sh" ]; then systemctl restart kiwi-vpn-proxy || true; fi
  sleep 2
  systemctl is-active --quiet kiwi-vpn-agent || die "restored from $dir, but the agent isn't running: journalctl -u kiwi-vpn-agent"
  if [ -f "$dir/rules.sh" ]; then systemctl is-active --quiet kiwi-vpn-proxy || die "restored from $dir, but the proxy isn't running"; fi
  say "Restored from $dir"
  nat_report || true
}

if [ "${1:-}" = "--rollback" ]; then
  [ -n "${2:-}" ] || die "say which backup: --rollback /root/kiwi-pre-fip-<time>"
  BEFORE="$(wg_digest)"
  restore "$2"
  [ "$(wg_digest)" = "$BEFORE" ] || die "WireGuard changed during the rollback: investigate"
  exit 0
fi

APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1
[ -z "${1:-}" ] || [ "$APPLY" = 1 ] || die "unknown option $1"

# ---- checks (all read-only) ----
[ -n "${FLOAT_IP:-}" ] || die "set FLOAT_IP (the floating IPv4 assigned to this server in the Hetzner console)"
python3 -I -c "import ipaddress as i,sys; a=i.ip_address(sys.argv[1]); assert a.version==4 and not (a.is_loopback or a.is_link_local or a.is_multicast or a.is_unspecified or any(a in i.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','100.64.0.0/10','0.0.0.0/8')))" "$FLOAT_IP" 2>/dev/null \
  || die "FLOAT_IP ($FLOAT_IP) isn't a public IPv4 address"
MAIN_IP="$(ip -4 -o addr show "$WAN_IF" scope global | awk '{print $4}' | cut -d/ -f1 | grep -vxF "$FLOAT_IP" | head -n1 || true)"
[ -n "$MAIN_IP" ] || die "no main IPv4 on $WAN_IF"
[ "$FLOAT_IP" != "$MAIN_IP" ] || die "FLOAT_IP is the server's main address"
[ -f "$WG_RULES" ] || die "$WG_RULES is missing"
grep -q '^  return 0$' "$WG_RULES" && grep -q '^  up)$' "$WG_RULES" && grep -q '^  down) remove ;;$' "$WG_RULES" || die "$WG_RULES isn't laid out as install-shared.sh writes it"
if [ "$HAVE_PROXY" = 1 ]; then
  grep -q 'iptables -N KIWI-PROXY' "$PROXY_RULES" || die "$PROXY_RULES isn't install-proxy.sh's iptables rules (an nftables node? use install-proxy.sh EXIT_IP=)"
  grep -q '^  return 0$' "$PROXY_RULES" && grep -q '^  down) remove ;;$' "$PROXY_RULES" || die "$PROXY_RULES isn't laid out as install-proxy.sh writes it"
  [ -f "$PROXY_ENV" ] || die "$PROXY_ENV is missing"
fi

# ---- the new files, built in a temporary folder ----
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
# drop every line that does NAT for this pool / the proxy's user (MASQUERADE or SNAT, by the installer or by hand), then put
# ours back: the delete in remove(), the insert-first at the end of up)
python3 -I - "$WG_RULES" "$WORK/wg1-rules.sh" "-s $POOL_NET -o $WAN_IF" "$FLOAT_IP" "$MARK" "$POOL_NET" <<'PY'
import sys
src, dst, match, ip, mark, pool = sys.argv[1:]
lines = [l for l in open(src).read().split("\n") if not ("POSTROUTING" in l and pool in l)]
lines.insert(lines.index("  return 0"), f"  iptables -t nat -D POSTROUTING {match} -j SNAT --to-source {ip} 2>/dev/null  {mark}")
lines.insert(lines.index("  return 0"), f"  iptables -t nat -D POSTROUTING {match} -j MASQUERADE 2>/dev/null  {mark}")
end = lines.index("  down) remove ;;") - 1  # the up) branch's ";;"
assert lines[end].strip() == ";;", lines[end]
lines.insert(end, f"    iptables -t nat -I POSTROUTING 1 {match} -j SNAT --to-source {ip}  {mark}")
open(dst, "w").write("\n".join(lines))
PY
if [ "$HAVE_PROXY" = 1 ]; then
  python3 -I - "$PROXY_RULES" "$WORK/rules.sh" "-m owner --uid-owner kiwi-proxy -o $WAN_IF" "$FLOAT_IP" "$MARK" <<'PY'
import sys
src, dst, match, ip, mark = sys.argv[1:]
lines = [l for l in open(src).read().split("\n") if not ("POSTROUTING" in l and "kiwi-proxy" in l)]
lines.insert(lines.index("  return 0"), f"  iptables -t nat -D POSTROUTING {match} -j SNAT --to-source {ip} 2>/dev/null  {mark}")
end = lines.index("  down) remove ;;") - 1
assert lines[end].strip() == ";;", lines[end]
lines.insert(end, f"    iptables -t nat -I POSTROUTING 1 {match} -j SNAT --to-source {ip}  {mark}")
open(dst, "w").write("\n".join(lines))
PY
  OTHERS="$(sed -n 's/^PUBLIC_IPS=//p' "$PROXY_ENV" | head -n1 | tr ',' '\n' | sed 's/ //g' | grep -vxF "$FLOAT_IP" | grep -v '^$' | paste -sd, - || true)"
  { grep -vE '^(PUBLIC_IPS|EXIT_IP)=' "$PROXY_ENV"; echo "PUBLIC_IPS=$FLOAT_IP${OTHERS:+,$OTHERS}"; echo "EXIT_IP=$FLOAT_IP"; } > "$WORK/proxy.env"
  sh -n "$WORK/rules.sh" || die "the new proxy rules.sh doesn't parse"
fi
sh -n "$WORK/wg1-rules.sh" || die "the new $WG_IF rules don't parse"
{ grep -v '^ENDPOINT_HOST=' "$AGENT_ENV"; echo "ENDPOINT_HOST=$FLOAT_IP"; } > "$WORK/agent.env"
{ grep -v '^EXIT_IP=' "$ETC/install.env" 2>/dev/null || true; echo "EXIT_IP=$FLOAT_IP"; } > "$WORK/install.env"
cat > "$WORK/60-kiwi-floating.yaml" <<EOF
# Managed by kiwi-vpn apply-floating-ip.sh: the Kiwi customers' floating IPv4 (wg1 and the browser proxy leave from it).
# Merged with the provider's $WAN_IF config by netplan; the main address stays the server's own.
network:
  version: 2
  ethernets:
    $WAN_IF:
      addresses:
        - $FLOAT_IP/32
EOF

echo "WireGuard now (kept exactly as it is):"
wg_state | sed 's/^/    /'
echo
echo "nat POSTROUTING now:"
nat_report || true
echo
show() { echo "$1:"; if [ -f "$2" ]; then diff -u "$2" "$3" | sed -n '3,$p' | sed 's/^/    /' || true; else sed 's/^/    +/' "$3"; fi; }
show "$NETPLAN" "$NETPLAN" "$WORK/60-kiwi-floating.yaml"
show "$WG_RULES" "$WG_RULES" "$WORK/wg1-rules.sh"
show "$AGENT_ENV" "$AGENT_ENV" "$WORK/agent.env"
show "$ETC/install.env" "$ETC/install.env" "$WORK/install.env"
if [ "$HAVE_PROXY" = 1 ]; then show "$PROXY_RULES" "$PROXY_RULES" "$WORK/rules.sh"; show "$PROXY_ENV" "$PROXY_ENV" "$WORK/proxy.env"; fi
if ip -4 -o addr show "$WAN_IF" | grep -qF " $FLOAT_IP/"; then echo "$FLOAT_IP is on $WAN_IF already"; else echo "ip addr add $FLOAT_IP/32 dev $WAN_IF (now; netplan keeps it after a reboot)"; fi
echo
echo "Then: stray Kiwi NAT rules removed, $WG_IF rules re-applied in place (peers stay up), agent and proxy restart (~2 s each)."
echo "Main address $MAIN_IP, wg0 and its MASQUERADE are not touched."

if [ "$APPLY" != 1 ]; then
  echo
  echo "Nothing changed. Run again with --apply to make this change."
  exit 0
fi

# ---- apply ----
BEFORE="$(wg_digest)"
BACKUP="$R/root/kiwi-pre-fip-$(date -u +%Y%m%dT%H%M%SZ)"
(umask 077; mkdir -p "$BACKUP")
cp -p "$AGENT_ENV" "$BACKUP/agent.env"
cp -p "$WG_RULES" "$BACKUP/wg1-rules.sh"
[ -f "$ETC/install.env" ] && cp -p "$ETC/install.env" "$BACKUP/install.env"
[ -f "$NETPLAN" ] && cp -p "$NETPLAN" "$BACKUP/60-kiwi-floating.yaml"
if [ "$HAVE_PROXY" = 1 ]; then cp -p "$PROXY_RULES" "$BACKUP/rules.sh"; cp -p "$PROXY_ENV" "$BACKUP/proxy.env"; fi
iptables -t nat -S POSTROUTING > "$BACKUP/nat-postrouting.txt"
say "Backup in $BACKUP"
fail() { echo "error: $*: rolling back" >&2; restore "$BACKUP"; exit 1; }

say "Floating IP $FLOAT_IP on $WAN_IF"
install -d "$(dirname "$NETPLAN")"
install -m 0600 "$WORK/60-kiwi-floating.yaml" "$NETPLAN"
netplan generate || fail "netplan doesn't accept the new file"
if ! ip -4 -o addr show "$WAN_IF" | grep -qF " $FLOAT_IP/"; then
  ip addr add "$FLOAT_IP/32" dev "$WAN_IF" || fail "couldn't add $FLOAT_IP to $WAN_IF"
  echo "$FLOAT_IP" > "$BACKUP/address-added"
fi

say "NAT: $WG_IF customers$PX leave from $FLOAT_IP (first in nat POSTROUTING)"
install -m 0750 "$WORK/wg1-rules.sh" "$WG_RULES"
[ "$HAVE_PROXY" = 1 ] && install -m 0750 "$WORK/rules.sh" "$PROXY_RULES"
drop_kiwi_nat
"$WG_RULES" up || fail "the new $WG_IF rules didn't apply"
[ "$HAVE_PROXY" = 1 ] && { "$PROXY_RULES" up || fail "the new proxy rules didn't apply"; }
nat_report || fail "Kiwi's SNAT isn't first in nat POSTROUTING"
[ "$(kiwi_nat_rules | wc -l)" -eq $((1 + HAVE_PROXY)) ] || fail "expected exactly $((1 + HAVE_PROXY)) Kiwi NAT rules"

say "Agent endpoint $FLOAT_IP$PX"
install -m 0640 $OWNER_A "$WORK/agent.env" "$AGENT_ENV"
install -m 0640 "$WORK/install.env" "$ETC/install.env"
systemctl restart kiwi-vpn-agent || fail "the agent didn't restart"
if [ "$HAVE_PROXY" = 1 ]; then
  install -m 0640 $OWNER_P "$WORK/proxy.env" "$PROXY_ENV"
  systemctl restart kiwi-vpn-proxy || fail "the proxy didn't restart"
fi
sleep 2
systemctl is-active --quiet kiwi-vpn-agent || fail "the agent isn't running"
if [ "$HAVE_PROXY" = 1 ]; then systemctl is-active --quiet kiwi-vpn-proxy || fail "the proxy isn't running"; fi
nat_report >/dev/null || fail "Kiwi's SNAT isn't first in nat POSTROUTING after the restarts"
[ "$(wg_digest)" = "$BEFORE" ] || { echo "error: WireGuard changed during this run (not by this script): investigate. The change stays applied; roll back with --rollback $BACKUP" >&2; exit 1; }
say "Done. WireGuard unchanged. Undo: bash $0 --rollback $BACKUP"
echo "Check from OUTSIDE the tunnel: a wg1 device and the browser proxy should show $FLOAT_IP; the server itself $MAIN_IP."
