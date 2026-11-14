"""apply-floating-ip.sh against a fake Helsinki (node_fakes.py): rule order in nat POSTROUTING, idempotence on the hand-made
setup, rollback, and that wg0 and its MASQUERADE are never touched.

    python -m pytest vpn-node/test_apply_floating_ip.py -q -p no:cacheprovider     (needs bash: Git Bash on Windows)
"""
import json
import os

import pytest

from node_fakes import FLOAT, PRIMARY, calls, make_server, nat, never_touches_wg0, run, unix

S = "apply-floating-ip.sh"
FILES = ("etc/kiwi-vpn/agent.env", "etc/kiwi-vpn/wg1-rules.sh", "etc/kiwi-vpn-proxy/proxy.env", "etc/kiwi-vpn-proxy/rules.sh",
         "etc/wireguard/wg0.conf", "etc/wireguard/wg1.conf", "wg1-peers")
WG1_SNAT = f"-s 10.66.0.0/16 -o eth0 -j SNAT --to-source {FLOAT}"
PROXY_SNAT = f"-m owner --uid-owner kiwi-proxy -o eth0 -j SNAT --to-source {FLOAT}"
WG0 = "-o eth0 -j MASQUERADE"


def files(root):
    out = {p: (root / p).read_bytes() for p in FILES}
    np = root / "etc/netplan/60-kiwi-floating.yaml"
    out["netplan"] = np.read_bytes() if np.exists() else None
    out["float-on"] = (root / "float-on").exists()
    return out


@pytest.fixture
def fresh(tmp_path):  # the node as install-shared.sh + install-proxy.sh left it, before any floating IP
    return make_server(tmp_path, [S], float_live=False)


@pytest.fixture
def live(tmp_path):  # the node as it is since 2026-11-12: floating IP set up by hand
    return make_server(tmp_path, [S], float_live=True)


def test_by_default_it_only_shows_what_would_change(fresh):
    root, _ = fresh
    before, nat_before = files(root), nat(root)
    r = run(fresh, S, FLOAT_IP=FLOAT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert files(root) == before and nat(root) == nat_before
    assert f"+ENDPOINT_HOST={FLOAT}" in r.stdout and f"+PUBLIC_IPS={FLOAT},{PRIMARY}" in r.stdout
    assert f"+    iptables -t nat -I POSTROUTING 1 {WG1_SNAT}  # kiwi: floating" in r.stdout
    assert f"ip addr add {FLOAT}/32 dev eth0 (now" in r.stdout
    assert "Nothing changed" in r.stdout
    assert not any(c.startswith(("systemctl", "netplan", "ip addr")) for c in calls(root))
    assert not any(c.startswith("iptables") and "-S" not in c for c in calls(root))
    assert not list((root / "root").iterdir())


def test_apply_puts_kiwi_snat_first_and_leaves_wg0s_masquerade_last(fresh):
    root, _ = fresh
    before = files(root)
    r = run(fresh, S, "--apply", FLOAT_IP=FLOAT)
    assert r.returncode == 0, r.stdout + r.stderr
    post = nat(root)
    assert sorted(post[:2]) == sorted([WG1_SNAT, PROXY_SNAT]) and post[2:] == [WG0], post
    assert not any("10.66.0.0/16" in x and "MASQUERADE" in x for x in post)  # the old appended rule is gone
    after = files(root)
    for wg in ("etc/wireguard/wg0.conf", "etc/wireguard/wg1.conf", "wg1-peers"):
        assert after[wg] == before[wg]
    assert f"ENDPOINT_HOST={FLOAT}" in after["etc/kiwi-vpn/agent.env"].decode().splitlines()
    env = after["etc/kiwi-vpn-proxy/proxy.env"].decode().splitlines()
    assert f"PUBLIC_IPS={FLOAT},{PRIMARY}" in env and f"EXIT_IP={FLOAT}" in env
    assert f"EXIT_IP={FLOAT}" in (root / "etc/kiwi-vpn/install.env").read_text()
    assert f"- {FLOAT}/32" in after["netplan"].decode() and after["float-on"]
    assert "netplan generate" in calls(root) and not any(c.startswith("netplan apply") for c in calls(root))
    assert f"ip addr add {FLOAT}/32 dev eth0" in calls(root)
    assert [c for c in calls(root) if c.startswith("systemctl restart")] == ["systemctl restart kiwi-vpn-agent", "systemctl restart kiwi-vpn-proxy"]
    never_touches_wg0(root)
    assert "order: OK" in r.stdout and "WireGuard unchanged" in r.stdout


def test_on_the_hand_made_live_setup_it_changes_only_the_differences(live):
    root, _ = live
    nat_before = nat(root)
    r = run(live, S, "--apply", FLOAT_IP=FLOAT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(nat(root)) == sorted(nat_before) and nat(root)[-1] == WG0
    assert not any(c.startswith("ip addr add") for c in calls(root))  # already on eth0
    never_touches_wg0(root)


def test_running_it_twice_adds_nothing_twice(fresh):
    root, _ = fresh
    assert run(fresh, S, "--apply", FLOAT_IP=FLOAT).returncode == 0
    first = files(root)
    r = run(fresh, S, "--apply", FLOAT_IP=FLOAT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert files(root) == first
    assert len(nat(root)) == 3
    assert (root / "etc/kiwi-vpn/wg1-rules.sh").read_text().count("-I POSTROUTING") == 1


def test_the_dry_run_reports_a_shadowed_snat(live):
    root, _ = live
    state = json.loads((root / "iptables.json").read_text())
    state["nat"]["POSTROUTING"] = [WG0, WG1_SNAT, PROXY_SNAT]  # e.g. wg0 restarted with an -I rule after Kiwi's
    (root / "iptables.json").write_text(json.dumps(state))
    r = run(live, S, FLOAT_IP=FLOAT)
    assert r.returncode == 0 and "order: WRONG" in r.stdout
    r = run(live, S, "--apply", FLOAT_IP=FLOAT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert nat(root)[-1] == WG0  # put right without touching wg0's rule


def test_netplan_refusing_the_file_rolls_everything_back(fresh):
    root, _ = fresh
    before, nat_before = files(root), nat(root)
    r = run(fresh, S, "--apply", FLOAT_IP=FLOAT, NETPLAN_FAILS="1")
    assert r.returncode != 0 and "rolling back" in r.stderr
    assert files(root) == before
    assert sorted(nat(root)) == sorted(nat_before)
    never_touches_wg0(root)


def test_a_proxy_that_wont_start_rolls_back_the_address_too(fresh):
    root, _ = fresh
    before, nat_before = files(root), nat(root)
    r = run(fresh, S, "--apply", FLOAT_IP=FLOAT, PROXY_FAILS="1")
    assert r.returncode != 0 and "rolling back" in r.stderr
    assert files(root) == before  # netplan file removed, address deleted, rules and envs back
    assert sorted(nat(root)) == sorted(nat_before)
    assert f"ip addr del {FLOAT}/32 dev eth0" in calls(root)


def test_rollback_puts_everything_back(fresh):
    root, _ = fresh
    before, nat_before = files(root), nat(root)
    assert run(fresh, S, "--apply", FLOAT_IP=FLOAT).returncode == 0
    (backup,) = (root / "root").iterdir()
    r = run(fresh, S, "--rollback", unix(backup))
    assert r.returncode == 0, r.stdout + r.stderr
    assert files(root) == before
    assert sorted(nat(root)) == sorted(nat_before)
    never_touches_wg0(root)


@pytest.mark.parametrize("bad", ["10.0.0.5", "nonsense", PRIMARY, "", "2001:db8::5"])
def test_a_bad_floating_ip_is_refused_before_anything_changes(fresh, bad):
    root, _ = fresh
    before = files(root)
    r = run(fresh, S, "--apply", FLOAT_IP=bad)
    assert r.returncode != 0
    assert files(root) == before


def test_a_node_without_the_proxy(fresh):
    root, _ = fresh
    os.remove(root / "etc/kiwi-vpn-proxy/rules.sh")
    r = run(fresh, S, "--apply", FLOAT_IP=FLOAT)
    assert r.returncode == 0, r.stdout + r.stderr
    assert [x for x in nat(root) if "10.66.0.0/16" in x] == [WG1_SNAT]
    assert not any("restart kiwi-vpn-proxy" in c for c in calls(root))


def test_new_installs_make_the_same_rules():
    # install-shared.sh EXIT_IP=... and install-proxy.sh EXIT_IP=... generate exactly the SNAT this script applies
    import subprocess
    from node_fakes import BASH, HERE
    env = {**os.environ, "EXIT_IP": FLOAT, "PUBLIC_IP": PRIMARY}
    shared = subprocess.run([BASH, "install-shared.sh", "--print-config"], cwd=HERE, env=env, capture_output=True, text=True).stdout
    proxy = subprocess.run([BASH, "install-proxy.sh", "--print-config"], cwd=HERE, env=env, capture_output=True, text=True).stdout
    assert f"iptables -t nat -I POSTROUTING 1 {WG1_SNAT}" in shared and f"ENDPOINT_HOST={FLOAT}" in shared
    assert f"iptables -t nat -I POSTROUTING 1 {PROXY_SNAT}" in proxy and f"PUBLIC_IPS={FLOAT},{PRIMARY}" in proxy
