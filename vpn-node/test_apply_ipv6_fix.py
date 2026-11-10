"""apply-proxy-ipv6-fix.sh against a fake server: a folder for its files, and stand-ins for wg, systemctl, ip, ip6tables...
that log every call. Proves what it changes, and that it never touches WireGuard or the agent.

    python -m pytest vpn-node/test_apply_ipv6_fix.py -q -p no:cacheprovider     (needs bash: Git Bash on Windows)
"""
import hashlib
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "apply-proxy-ipv6-fix.sh")
BASH = shutil.which("bash") or r"C:\Program Files\Git\bin\bash.exe"
NET = "2a01:4f9:c015:4b9c::/64"
OLD_PROXY = subprocess.run(["git", "show", "main:vpn-node/proxy.py"], cwd=HERE, capture_output=True, check=True).stdout

# rules.sh as the 2026-11-04 installer wrote it on Helsinki (no OWN_NETS loop)
OLD_RULES = """#!/bin/sh
# Every rule matches only the kiwi-proxy user. No LOG rules on purpose.
remove() {
  ip6tables -D OUTPUT -m owner --uid-owner kiwi-proxy -j KIWI-PROXY 2>/dev/null
  ip6tables -F KIWI-PROXY 2>/dev/null; ip6tables -X KIWI-PROXY 2>/dev/null
  return 0
}
case "$1" in
  up)
    remove
    set -e
    ip6tables -N KIWI-PROXY
    ip6tables -A KIWI-PROXY -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    ip6tables -A KIWI-PROXY -o lo -j REJECT
    for net in fc00::/7 fe80::/10 ff00::/8; do
      ip6tables -A KIWI-PROXY -d "$net" -j REJECT
    done
    ip6tables -I OUTPUT 1 -m owner --uid-owner kiwi-proxy -j KIWI-PROXY
    ;;
  down) remove ;;
esac
"""
OLD_ENV = "PROXY_BIND=0.0.0.0\nPROXY_PORT=443\nUSERS_FILE=/var/lib/kiwi-vpn-proxy/users.json\nPUBLIC_IPS=2.29.149.29\n"

STUBS = {
    "id": 'echo 0',
    "wg": r'''echo "wg $*" >> "$LOG"
case "$*" in
  "show interfaces") echo "wg0 wg1" ;;
  "show wg0 listen-port") echo 51820 ;;
  "show wg1 listen-port") echo 51821 ;;
  "show wg0 allowed-ips") echo "client1key= 10.8.0.2/32" ;;
  "show wg1 allowed-ips") cat "$ROOT/wg1-peers" ;;
  *) echo "wg: unexpected $*" >&2; exit 9 ;;
esac''',
    "ip": r'''echo "ip $*" >> "$LOG"
echo "2: eth0    inet6 2a01:4f9:c015:4b9c::1/64 scope global \\       valid_lft forever preferred_lft forever"''',
    "ip6tables": r'''echo "ip6tables $*" >> "$LOG"
S="$ROOT/ip6tables-state"
case "$1" in
  -N) : > "$S" ;;
  -A) shift; echo "-A $*" >> "$S" ;;
  -F|-X|-D) rm -f "$S" ;;
  -S) cat "$S" 2>/dev/null ;;
  -I) ;;
esac''',
    "iptables": 'echo "iptables $*" >> "$LOG"',
    "ss": 'echo "ss $*" >> "$LOG"; echo "LISTEN 0 128 0.0.0.0:443 0.0.0.0:*"',
    "systemctl": r'''echo "systemctl $*" >> "$LOG"
case "$*" in
  "cat kiwi-vpn-proxy") echo unit ;;
  "restart kiwi-vpn-proxy")
    [ -n "$WG_CHANGES" ] && echo "newpeer= 10.66.0.9/32" >> "$ROOT/wg1-peers"
    sh "$ROOT/etc/kiwi-vpn-proxy/rules.sh" up || exit 1
    # the proxy starts only if its config loads (a bad OWN_NETS raises)
    "$PY" -I -c "import sys; sys.path.insert(0, sys.argv[1]); import proxy; proxy.Proxy(proxy.load_config(sys.argv[2]))" \
      "$ROOT/opt/kiwi-vpn" "$ROOT/etc/kiwi-vpn-proxy/proxy.env" 2> "$ROOT/proxy-start.err" && echo up > "$ROOT/proxy-up" || { rm -f "$ROOT/proxy-up"; exit 1; }
    [ -n "$PROXY_FAILS" ] && [ ! -f "$ROOT/failed-once" ] && { touch "$ROOT/failed-once"; rm -f "$ROOT/proxy-up"; exit 1; }
    exit 0 ;;
  "is-active --quiet kiwi-vpn-proxy") [ -f "$ROOT/proxy-up" ] ;;
  *) echo "systemctl: unexpected $*" >&2; exit 9 ;;
esac''',
}


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "srv"
    for d in ("etc/kiwi-vpn-proxy", "opt/kiwi-vpn", "etc/wireguard", "root", "bin"):
        (root / d).mkdir(parents=True)
    (root / "opt/kiwi-vpn/proxy.py").write_bytes(OLD_PROXY)
    (root / "etc/kiwi-vpn-proxy/proxy.env").write_text(OLD_ENV, newline="\n")
    (root / "etc/kiwi-vpn-proxy/rules.sh").write_text(OLD_RULES, newline="\n")
    (root / "etc/wireguard/wg0.conf").write_text("[Interface]\n# client1's, never touched\n")
    (root / "etc/wireguard/wg1.conf").write_text("[Interface]\n# Kiwi customers\n")
    (root / "wg1-peers").write_text("custkey= 10.66.0.2/32\n", newline="\n")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    shutil.copy(SCRIPT, pkg)
    shutil.copy(os.path.join(HERE, "proxy.py"), pkg)
    for name, body in {**STUBS, "python3": '"$PY" "$@"'}.items():
        (root / "bin" / name).write_text("#!/usr/bin/env bash\n" + body + "\n", newline="\n")
    return root, pkg


def run(server, *args, **env):
    root, pkg = server
    # tools the script needs that aren't stubbed come from Git Bash's /usr/bin
    e = {**os.environ, "KIWI_TEST_ROOT": unix(root), "ROOT": unix(root), "LOG": unix(root / "calls.log"),
         "PY": unix(sys.executable), **env}
    cmd = f'export PATH="{unix(root / "bin")}:/usr/bin:/bin"; bash "{unix(pkg / "apply-proxy-ipv6-fix.sh")}" ' + " ".join(args)
    return subprocess.run([BASH, "-c", cmd], env=e, capture_output=True, text=True)


def unix(p):
    p = str(p)
    if os.name == "nt" and len(p) > 1 and p[1] == ":":
        return "/" + p[0].lower() + p[2:].replace("\\", "/")
    return p


def files(root):
    return {p: (root / p).read_bytes() for p in ("opt/kiwi-vpn/proxy.py", "etc/kiwi-vpn-proxy/proxy.env", "etc/kiwi-vpn-proxy/rules.sh",
                                               "etc/wireguard/wg0.conf", "etc/wireguard/wg1.conf", "wg1-peers")}


def calls(root):
    return (root / "calls.log").read_text().splitlines() if (root / "calls.log").exists() else []


def never_touches_wireguard_or_the_agent(root):
    for c in calls(root):
        if c.startswith("wg "):
            assert c.split()[1] == "show", c  # never set / syncconf / addconf / setconf
        if c.startswith("systemctl "):
            assert "kiwi-vpn-proxy" in c and "agent" not in c and "wg-quick" not in c, c
        assert not c.startswith("iptables "), c  # IPv4 rules aren't part of this fix


def test_by_default_it_only_shows_what_would_change(server):
    root, _ = server
    before = files(root)
    r = run(server)
    assert r.returncode == 0, r.stderr
    assert files(root) == before
    assert f"+OWN_NETS={NET}" in r.stdout
    assert f"+    ip6tables -A KIWI-PROXY -d {NET} -j REJECT  # kiwi: own IPv6" in r.stdout
    assert "Nothing changed" in r.stdout
    assert not any(c.startswith("systemctl restart") for c in calls(root))
    assert not list((root / "root").iterdir())  # no backup either
    never_touches_wireguard_or_the_agent(root)


def test_apply_refuses_the_servers_own_ipv6_and_leaves_wireguard_alone(server):
    root, pkg = server
    before = files(root)
    r = run(server, "--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    after = files(root)
    for wg in ("etc/wireguard/wg0.conf", "etc/wireguard/wg1.conf", "wg1-peers"):
        assert after[wg] == before[wg]
    assert after["opt/kiwi-vpn/proxy.py"] == (pkg / "proxy.py").read_bytes()
    assert after["etc/kiwi-vpn-proxy/proxy.env"].decode().splitlines() == OLD_ENV.splitlines() + [f"OWN_NETS={NET}"]
    assert f"-A KIWI-PROXY -d {NET} -j REJECT" in (root / "ip6tables-state").read_text()
    # the new rule sits after loopback and before the private ranges, inside the proxy user's chain only
    state = (root / "ip6tables-state").read_text().splitlines()
    assert state.index("-A KIWI-PROXY -o lo -j REJECT") < state.index(f"-A KIWI-PROXY -d {NET} -j REJECT") < state.index('-A KIWI-PROXY -d fc00::/7 -j REJECT')
    assert [c for c in calls(root) if c.startswith("systemctl restart")] == ["systemctl restart kiwi-vpn-proxy"]
    never_touches_wireguard_or_the_agent(root)
    (backup,) = (root / "root").iterdir()
    assert (backup / "proxy.py").read_bytes() == OLD_PROXY and "WireGuard unchanged" in r.stdout


def test_running_it_twice_adds_nothing_twice(server):
    root, _ = server
    assert run(server, "--apply").returncode == 0
    r = run(server, "--apply")
    assert r.returncode == 0, r.stderr
    env = (root / "etc/kiwi-vpn-proxy/proxy.env").read_text()
    assert env.count("OWN_NETS=") == 1
    assert (root / "etc/kiwi-vpn-proxy/rules.sh").read_text().count("# kiwi: own IPv6") == 1


def test_a_proxy_that_wont_start_is_rolled_back(server):
    root, _ = server
    before = files(root)
    r = run(server, "--apply", PROXY_FAILS="1")
    assert r.returncode != 0 and "rolling back" in r.stderr
    assert files(root) == before
    assert (root / "proxy-up").exists()  # the old version is running again
    never_touches_wireguard_or_the_agent(root)


def test_wireguard_changing_during_the_run_stops_it_loudly(server):
    r = run(server, "--apply", WG_CHANGES="1")
    assert r.returncode != 0 and "WireGuard changed" in r.stderr


def test_an_unexpected_installed_proxy_is_left_alone(server):
    root, _ = server
    (root / "opt/kiwi-vpn/proxy.py").write_bytes(OLD_PROXY + b"\n# edited by hand\n")
    before = files(root)
    r = run(server, "--apply")
    assert r.returncode != 0 and "isn't the version this fix replaces" in r.stderr
    assert files(root) == before


def test_a_bad_network_is_refused_before_anything_changes(server):
    root, _ = server
    before = files(root)
    for bad in ("10.0.0.0/8", "2a01::/16", "nonsense"):
        r = run(server, "--apply", OWN_NETS=bad)
        assert r.returncode != 0, bad
        assert files(root) == before


def test_rollback_puts_the_old_files_back(server):
    root, _ = server
    before = files(root)
    assert run(server, "--apply").returncode == 0
    (backup,) = (root / "root").iterdir()
    r = run(server, "--rollback", unix(backup))
    assert r.returncode == 0, r.stderr
    assert files(root) == before
    never_touches_wireguard_or_the_agent(root)


def test_the_known_old_hash_is_mains_proxy():
    text = open(SCRIPT, encoding="utf-8").read()
    assert f"KNOWN_OLD={hashlib.sha256(OLD_PROXY).hexdigest()}" in text
