"""A fake shared node (Helsinki's layout) for the apply-*.sh tests: a folder for its files, a stateful fake iptables
(fake_iptables.py), and stand-ins for wg, systemctl, ss, apt-get, unbound-checkconf, netplan and ip that log every call.
Placeholders only (192.0.2.x, 2001:db8::); never a real address. Tests only.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASH = shutil.which("bash") or r"C:\Program Files\Git\bin\bash.exe"
if "system32" in BASH.lower():
    BASH = r"C:\Program Files\Git\bin\bash.exe"
PRIMARY, FLOAT, NET6 = "192.0.2.10", "192.0.2.50", "2001:db8:4b9c::/64"


def git_show(rev_path: str) -> bytes:
    return subprocess.run(["git", "show", rev_path], cwd=HERE, capture_output=True, check=True).stdout


# wg1-rules.sh as install-shared.sh wrote it on 2026-11-03, then hand-edited on 2026-11-12 for the floating IP
# (SNAT first in nat POSTROUTING instead of the appended MASQUERADE that wg0's generic MASQUERADE shadowed).
WG1_RULES_LIVE = f"""#!/bin/sh
# Kiwi VPN customers (wg1). Every rule matches only this interface or its pool. No LOG rules on purpose.
remove() {{
  iptables -D INPUT -i wg1 -j KIWI-IN 2>/dev/null
  iptables -D FORWARD -i wg1 -j KIWI-FWD 2>/dev/null
  iptables -D FORWARD -i wg0 -o wg1 -j REJECT 2>/dev/null
  iptables -t nat -D POSTROUTING -s 10.66.0.0/16 -o eth0 -j SNAT --to-source {FLOAT} 2>/dev/null
  iptables -F KIWI-IN 2>/dev/null; iptables -X KIWI-IN 2>/dev/null
  iptables -F KIWI-FWD 2>/dev/null; iptables -X KIWI-FWD 2>/dev/null
  return 0
}}
case "$1" in
  up)
    remove
    set -e
    iptables -N KIWI-IN
    iptables -A KIWI-IN -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
    iptables -A KIWI-IN -d 10.66.0.1 -p icmp -j ACCEPT
    iptables -A KIWI-IN -j REJECT
    iptables -N KIWI-FWD
    iptables -A KIWI-FWD -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
    iptables -A KIWI-FWD -m conntrack --ctstate INVALID -j DROP
    iptables -A KIWI-FWD -o wg1 -j REJECT
    iptables -A KIWI-FWD -o wg0 -j REJECT
    iptables -A KIWI-FWD -d 10.0.0.0/8 -j REJECT
    iptables -A KIWI-FWD -d 100.64.0.0/10 -j REJECT
    iptables -A KIWI-FWD -d 169.254.0.0/16 -j REJECT
    iptables -A KIWI-FWD -d 172.16.0.0/12 -j REJECT
    iptables -A KIWI-FWD -d 192.168.0.0/16 -j REJECT
    iptables -A KIWI-FWD -p tcp --dport 25 -j REJECT --reject-with tcp-reset
    iptables -A KIWI-FWD -o eth0 -j ACCEPT
    iptables -A KIWI-FWD -j DROP
    iptables -I INPUT 1 -i wg1 -j KIWI-IN
    iptables -I FORWARD 1 -i wg1 -j KIWI-FWD
    iptables -I FORWARD 1 -i wg0 -o wg1 -j REJECT
    iptables -t nat -I POSTROUTING 1 -s 10.66.0.0/16 -o eth0 -j SNAT --to-source {FLOAT}
    ;;
  down) remove ;;
esac
"""

# the same file before the floating IP (as install-shared.sh wrote it)
WG1_RULES_ORIG = WG1_RULES_LIVE.replace(
    f"  iptables -t nat -D POSTROUTING -s 10.66.0.0/16 -o eth0 -j SNAT --to-source {FLOAT} 2>/dev/null",
    "  iptables -t nat -D POSTROUTING -s 10.66.0.0/16 -o eth0 -j MASQUERADE 2>/dev/null").replace(
    f"    iptables -t nat -I POSTROUTING 1 -s 10.66.0.0/16 -o eth0 -j SNAT --to-source {FLOAT}",
    "    iptables -t nat -A POSTROUTING -s 10.66.0.0/16 -o eth0 -j MASQUERADE")

# the proxy's rules.sh: the 2026-11-04 installer + the own-IPv6 fix + the hand-added floating-IP SNAT
PROXY_RULES_LIVE = f"""#!/bin/sh
# Every rule matches only the kiwi-proxy user. No LOG rules on purpose.
remove() {{
  iptables -D OUTPUT -m owner --uid-owner kiwi-proxy -j KIWI-PROXY 2>/dev/null
  iptables -F KIWI-PROXY 2>/dev/null; iptables -X KIWI-PROXY 2>/dev/null
  ip6tables -D OUTPUT -m owner --uid-owner kiwi-proxy -j KIWI-PROXY 2>/dev/null
  ip6tables -F KIWI-PROXY 2>/dev/null; ip6tables -X KIWI-PROXY 2>/dev/null
  iptables -t nat -D POSTROUTING -m owner --uid-owner kiwi-proxy -o eth0 -j SNAT --to-source {FLOAT} 2>/dev/null
  return 0
}}
case "$1" in
  up)
    remove
    set -e
    iptables -N KIWI-PROXY
    iptables -A KIWI-PROXY -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    iptables -A KIWI-PROXY -d 127.0.0.53 -p udp --dport 53 -j RETURN
    iptables -A KIWI-PROXY -d 127.0.0.53 -p tcp --dport 53 -j RETURN
    iptables -A KIWI-PROXY -o lo -j REJECT
    iptables -A KIWI-PROXY -d {PRIMARY} -j REJECT
    for net in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.168.0.0/16 224.0.0.0/4; do
      iptables -A KIWI-PROXY -d "$net" -j REJECT
    done
    iptables -A KIWI-PROXY -p tcp -m multiport --dports 25,465,587 -j REJECT
    iptables -I OUTPUT 1 -m owner --uid-owner kiwi-proxy -j KIWI-PROXY
    ip6tables -N KIWI-PROXY
    ip6tables -A KIWI-PROXY -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    ip6tables -A KIWI-PROXY -o lo -j REJECT
    ip6tables -A KIWI-PROXY -d {NET6} -j REJECT  # kiwi: own IPv6
    for net in fc00::/7 fe80::/10 ff00::/8; do
      ip6tables -A KIWI-PROXY -d "$net" -j REJECT
    done
    ip6tables -I OUTPUT 1 -m owner --uid-owner kiwi-proxy -j KIWI-PROXY
    iptables -t nat -I POSTROUTING 1 -m owner --uid-owner kiwi-proxy -o eth0 -j SNAT --to-source {FLOAT}
    ;;
  down) remove ;;
esac
"""
PROXY_RULES_ORIG = "\n".join(l for l in PROXY_RULES_LIVE.split("\n") if "SNAT" not in l)

AGENT_ENV = f"""# Kiwi VPN node agent config (TOKEN is preserved on re-run)
TOKEN={"t" * 43}
POOL=10.66.0.1/16
POOL6=
ENDPOINT_HOST={FLOAT}
LISTEN_PORT=51821
DNS=1.1.1.1
ADBLOCK=0
AGENT_BIND=0.0.0.0
AGENT_PORT=8443
TLS_CERT=/etc/kiwi-vpn/tls.crt
TLS_KEY=/etc/kiwi-vpn/tls.key
WG_INTERFACE=wg1
STATE_FILE=/var/lib/kiwi-vpn/peers.json
PROXY_HOST=hel1.vpn.example.invalid
PROXY_PORT=443
PROXY_USERS_FILE=/var/lib/kiwi-vpn-proxy/users.json
PROXY_USAGE_FILE=/var/lib/kiwi-vpn-proxy/usage.json
"""
PROXY_ENV = f"PROXY_BIND=0.0.0.0\nPROXY_PORT=443\nUSERS_FILE=/var/lib/kiwi-vpn-proxy/users.json\nPUBLIC_IPS={PRIMARY}\nOWN_NETS={NET6}\n"

STUBS = {
    "id": 'echo 0',
    "sleep": ':',
    "wg": r'''echo "wg $*" >> "$LOG"
case "$*" in
  "show interfaces") echo "wg0 wg1" ;;
  "show wg0 listen-port") echo 51820 ;;
  "show wg1 listen-port") echo 51821 ;;
  "show wg0 allowed-ips") echo "client1key= 10.8.0.2/32" ;;
  "show wg1 allowed-ips") cat "$ROOT/wg1-peers" ;;
  *) echo "wg: unexpected $*" >&2; exit 9 ;;
esac''',
    "iptables": r'''echo "iptables $*" >> "$LOG"; "$PY" "$FAKE_IPT" "$ROOT/iptables.json" "$@"''',
    "ip6tables": r'''echo "ip6tables $*" >> "$LOG"''',
    "ip": r'''echo "ip $*" >> "$LOG"
case "$*" in
  "-4 -o addr show eth0"*) echo "2: eth0    inet 192.0.2.10/32 scope global dynamic eth0"; [ -f "$ROOT/float-on" ] && echo "2: eth0    inet 192.0.2.50/32 scope global eth0"; true ;;
  "addr add 192.0.2.50/32 dev eth0") touch "$ROOT/float-on" ;;
  "addr del 192.0.2.50/32 dev eth0") rm -f "$ROOT/float-on" ;;
  *) echo "ip: unexpected $*" >&2; exit 9 ;;
esac''',
    "netplan": r'''echo "netplan $*" >> "$LOG"; [ "$1" = generate ] || { echo "netplan: only generate is allowed" >&2; exit 9; }; [ -z "$NETPLAN_FAILS" ]''',
    "ss": r'''echo "ss $*" >> "$LOG"
case "$*" in
  *":53"*) [ -n "$OTHER_DNS" ] && echo 'UNCONN 0 0 0.0.0.0:53 0.0.0.0:* users:(("dnsmasq",pid=9,fd=4))'; true ;;
  *) echo "LISTEN 0 128 0.0.0.0:443 0.0.0.0:*" ;;
esac''',
    "apt-get": r'''echo "apt-get $*" >> "$LOG"; [ "$1" = update ] || { printf '#!/bin/sh\n' > "$ROOT/bin/unbound"; chmod +x "$ROOT/bin/unbound"; }''',
    "unbound-checkconf": r'''echo "unbound-checkconf $*" >> "$LOG"; [ -z "$UNBOUND_BAD" ]''',
    "systemctl": r'''echo "systemctl $*" >> "$LOG"
case "$*" in
  "cat kiwi-vpn-proxy") echo unit ;;
  "restart kiwi-vpn-proxy")
    [ -n "$WG_CHANGES" ] && echo "newpeer= 10.66.0.9/32" >> "$ROOT/wg1-peers"
    sh "$ROOT/etc/kiwi-vpn-proxy/rules.sh" down; sh "$ROOT/etc/kiwi-vpn-proxy/rules.sh" up || exit 1
    "$PY" -I -c "import sys; sys.path.insert(0, sys.argv[1]); import proxy; proxy.Proxy(proxy.load_config(sys.argv[2]))" \
      "$ROOT/opt/kiwi-vpn" "$ROOT/etc/kiwi-vpn-proxy/proxy.env" 2> "$ROOT/proxy-start.err" && echo up > "$ROOT/proxy-up" || { rm -f "$ROOT/proxy-up"; exit 1; }
    [ -n "$PROXY_FAILS" ] && [ ! -f "$ROOT/failed-once" ] && { touch "$ROOT/failed-once"; rm -f "$ROOT/proxy-up"; exit 1; }
    exit 0 ;;
  "restart kiwi-vpn-agent")
    "$PY" -I -c "import sys; sys.path.insert(0, sys.argv[1]); import agent; agent.Agent(agent.load_config(sys.argv[2]), runner=lambda a: '')" \
      "$ROOT/opt/kiwi-vpn" "$ROOT/etc/kiwi-vpn/agent.env" 2> "$ROOT/agent-start.err" && echo up > "$ROOT/agent-up" || { rm -f "$ROOT/agent-up"; exit 1; } ;;
  "restart unbound") [ -z "$UNBOUND_DOWN" ] && touch "$ROOT/unbound-up"; true ;;
  "disable --now unbound") rm -f "$ROOT/unbound-up" ;;
  "is-active --quiet kiwi-vpn-proxy") [ -f "$ROOT/proxy-up" ] ;;
  "is-active --quiet kiwi-vpn-agent") [ -f "$ROOT/agent-up" ] ;;
  "is-active --quiet unbound") [ -f "$ROOT/unbound-up" ] ;;
  "enable -q unbound"|"mask -q unbound-resolvconf.service") ;;
  *) echo "systemctl: unexpected $*" >&2; exit 9 ;;
esac''',
    # does unbound answer on $1? (only when it runs and a config of ours listens there)
    "dnsprobe": r'''[ -f "$ROOT/unbound-up" ] && grep -qx "  interface: $1" "$ROOT"/etc/unbound/unbound.conf.d/*.conf''',
}


def make_server(tmp_path, scripts, *, agent_rev="b840198", proxy_rev="407896e", float_live=True):
    root = tmp_path / "srv"
    for d in ("etc/kiwi-vpn", "etc/kiwi-vpn-proxy", "opt/kiwi-vpn", "etc/wireguard", "etc/netplan", "etc/unbound/unbound.conf.d", "root", "bin"):
        (root / d).mkdir(parents=True)
    (root / "opt/kiwi-vpn/agent.py").write_bytes(git_show(f"{agent_rev}:vpn-node/agent.py"))
    (root / "opt/kiwi-vpn/proxy.py").write_bytes(git_show(f"{proxy_rev}:vpn-node/proxy.py"))
    (root / "etc/kiwi-vpn/agent.env").write_text(AGENT_ENV if float_live else AGENT_ENV.replace(f"ENDPOINT_HOST={FLOAT}", f"ENDPOINT_HOST={PRIMARY}"), newline="\n")
    (root / "etc/kiwi-vpn/wg1-rules.sh").write_text(WG1_RULES_LIVE if float_live else WG1_RULES_ORIG, newline="\n")
    (root / "etc/kiwi-vpn-proxy/proxy.env").write_text(PROXY_ENV, newline="\n")
    (root / "etc/kiwi-vpn-proxy/rules.sh").write_text(PROXY_RULES_LIVE if float_live else PROXY_RULES_ORIG, newline="\n")
    (root / "etc/wireguard/wg0.conf").write_text("[Interface]\n# client1's, never touched\n")
    (root / "etc/wireguard/wg1.conf").write_text("[Interface]\n# Kiwi customers\n")
    (root / "wg1-peers").write_text("custkey= 10.66.0.2/32\n", newline="\n")
    if float_live:
        (root / "etc/netplan/60-kiwi-floating.yaml").write_text(f"network:\n  version: 2\n  ethernets:\n    eth0:\n      addresses: [{FLOAT}/32]\n", newline="\n")
        (root / "float-on").write_text("")
    (root / "agent-up").write_text("up")
    (root / "proxy-up").write_text("up")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    for f in scripts + ["agent.py", "proxy.py", "install-shared.sh", "install-proxy.sh"]:
        shutil.copy(os.path.join(HERE, f), pkg)
    for name, body in {**STUBS, "python3": '"$PY" "$@"'}.items():
        p = root / "bin" / name
        p.write_text("#!/usr/bin/env bash\n" + body + "\n", newline="\n")
    # the live nat table: wg0's generic MASQUERADE (appended by its PostUp), then Kiwi's rules as the live files make them
    (root / "iptables.json").write_text(json.dumps({"nat": {"PREROUTING": [], "INPUT": [], "OUTPUT": [], "POSTROUTING": ["-o eth0 -j MASQUERADE"]}}))
    env = env_for(root)
    for rules in ("etc/kiwi-vpn/wg1-rules.sh", "etc/kiwi-vpn-proxy/rules.sh"):
        r = subprocess.run([BASH, "-c", f'export PATH="{unix(root / "bin")}:/usr/bin:/bin"; sh "{unix(root / rules)}" up'], env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    (root / "calls.log").unlink(missing_ok=True)
    return root, pkg


def env_for(root, **extra):
    return {**os.environ, "KIWI_TEST_ROOT": unix(root), "ROOT": unix(root), "LOG": unix(root / "calls.log"), "PY": unix(sys.executable),
            "FAKE_IPT": unix(os.path.join(HERE, "fake_iptables.py")), "KIWI_TEST_DNS_PROBE": unix(root / "bin" / "dnsprobe"), **extra}


def run(server, script, *args, **env):
    root, pkg = server
    cmd = f'export PATH="{unix(root / "bin")}:/usr/bin:/bin"; bash "{unix(pkg / script)}" ' + " ".join(args)
    return subprocess.run([BASH, "-c", cmd], env=env_for(root, **env), capture_output=True, text=True, timeout=120)


def unix(p):
    p = str(p)
    if os.name == "nt" and len(p) > 1 and p[1] == ":":
        return "/" + p[0].lower() + p[2:].replace("\\", "/")
    return p


def calls(root):
    return (root / "calls.log").read_text().splitlines() if (root / "calls.log").exists() else []


def nat(root):
    return json.loads((root / "iptables.json").read_text())["nat"]["POSTROUTING"]


def chain(root, name, table="filter"):
    return json.loads((root / "iptables.json").read_text()).get(table, {}).get(name, [])


def never_touches_wg0(root):
    for c in calls(root):
        if c.startswith("wg "):
            assert c.split()[1] == "show", c  # never set / syncconf / addconf / setconf
        if c.startswith("systemctl "):
            assert "wg-quick" not in c, c
        if c.startswith("iptables "):
            assert "wg0" not in c or "-i wg0 -o wg1" in c or "-o wg0 -j REJECT" in c, c  # only Kiwi's own rules that name wg0
        assert "MASQUERADE" not in c or "10.66.0.0/16" in c, c  # wg0's generic MASQUERADE is never touched
    assert (root / "etc/wireguard/wg0.conf").read_text() == "[Interface]\n# client1's, never touched\n"
