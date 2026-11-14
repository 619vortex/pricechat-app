"""apply-dns-privacy.sh against a fake Helsinki (node_fakes.py): what it changes, the order, and that wg0 is never touched.

    python -m pytest vpn-node/test_apply_dns_privacy.py -q -p no:cacheprovider     (needs bash: Git Bash on Windows)
"""
import pytest

from node_fakes import FLOAT, calls, chain, make_server, nat, never_touches_wg0, run, unix

S = "apply-dns-privacy.sh"
FILES = ("opt/kiwi-vpn/agent.py", "opt/kiwi-vpn/proxy.py", "etc/kiwi-vpn/agent.env", "etc/kiwi-vpn/wg1-rules.sh",
         "etc/kiwi-vpn-proxy/proxy.env", "etc/kiwi-vpn-proxy/rules.sh", "etc/wireguard/wg0.conf", "etc/wireguard/wg1.conf", "wg1-peers")


@pytest.fixture
def server(tmp_path):
    return make_server(tmp_path, [S])


def files(root):
    return {p: (root / p).read_bytes() for p in FILES}


def test_by_default_it_only_shows_what_would_change(server):
    root, _ = server
    before, nat_before = files(root), nat(root)
    r = run(server, S)
    assert r.returncode == 0, r.stdout + r.stderr
    assert files(root) == before and nat(root) == nat_before
    assert "+DNS=10.66.0.1" in r.stdout and "-DNS=1.1.1.1" in r.stdout
    assert "+RESOLVER=127.0.0.1" in r.stdout
    assert "+    iptables -A KIWI-IN -s 10.66.0.0/16 -d 10.66.0.1 -p udp --dport 53 -j ACCEPT  # kiwi: dns" in r.stdout
    assert "+    iptables -A KIWI-PROXY -d 127.0.0.1 -p udp --dport 53 -j RETURN" in r.stdout
    assert "+  interface: 10.66.0.1" in r.stdout and "+  interface: 127.0.0.1" in r.stdout and "+  log-queries: no" in r.stdout
    assert "Nothing changed" in r.stdout
    assert not any(c.startswith(("systemctl restart", "apt-get", "iptables")) for c in calls(root))
    assert not list((root / "root").iterdir())  # no backup either
    never_touches_wg0(root)


def test_apply_moves_customers_and_the_proxy_to_the_nodes_own_resolver(server):
    root, pkg = server
    before = files(root)
    r = run(server, S, "--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    after = files(root)
    for wg in ("etc/wireguard/wg0.conf", "etc/wireguard/wg1.conf", "wg1-peers"):
        assert after[wg] == before[wg]
    # unbound: both addresses, no logs, installed because it wasn't there
    ub = root / "etc/unbound/unbound.conf.d"
    assert "interface: 10.66.0.1" in (ub / "kiwi-vpn.conf").read_text() and "access-control: 10.66.0.0/16 allow" in (ub / "kiwi-vpn.conf").read_text()
    assert "interface: 127.0.0.1" in (ub / "kiwi-proxy.conf").read_text()
    for f in ("kiwi-vpn.conf", "kiwi-proxy.conf"):
        assert "log-queries: no" in (ub / f).read_text() and "verbosity: 0" in (ub / f).read_text()
    assert any(c.startswith("apt-get install") and "unbound" in c for c in calls(root))
    assert "systemctl mask -q unbound-resolvconf.service" in calls(root)
    # customers: DNS to the wg1 address is allowed before the REJECT, and the agent reports it
    kin = chain(root, "KIWI-IN")
    assert kin.index("-s 10.66.0.0/16 -d 10.66.0.1 -p udp --dport 53 -j ACCEPT") < kin.index("-j REJECT")
    assert "-s 10.66.0.0/16 -d 10.66.0.1 -p tcp --dport 53 -j ACCEPT" in kin
    assert "-p tcp -m multiport --dports 25,465,587 -j REJECT --reject-with tcp-reset" in chain(root, "KIWI-FWD")
    assert "DNS=10.66.0.1" in after["etc/kiwi-vpn/agent.env"].decode().splitlines()
    assert "LOCAL_DNS=1" in (root / "etc/kiwi-vpn/install.env").read_text()
    # proxy: only 127.0.0.1 for DNS, new code with RESOLVER
    kp = chain(root, "KIWI-PROXY")
    assert "-d 127.0.0.1 -p udp --dport 53 -j RETURN" in kp and not any("127.0.0.53" in x for x in kp)
    assert kp.index("-d 127.0.0.1 -p udp --dport 53 -j RETURN") < kp.index("-o lo -j REJECT")
    assert "RESOLVER=127.0.0.1" in after["etc/kiwi-vpn-proxy/proxy.env"].decode().splitlines()
    assert after["opt/kiwi-vpn/proxy.py"] == (pkg / "proxy.py").read_bytes() and after["opt/kiwi-vpn/agent.py"] == (pkg / "agent.py").read_bytes()
    # the hand-made floating-IP NAT is kept, still ahead of wg0's generic MASQUERADE
    post = nat(root)
    assert post[-1] == "-o eth0 -j MASQUERADE"
    assert f"-s 10.66.0.0/16 -o eth0 -j SNAT --to-source {FLOAT}" in post
    assert f"-m owner --uid-owner kiwi-proxy -o eth0 -j SNAT --to-source {FLOAT}" in post
    assert len(post) == 3
    assert f"--to-source {FLOAT}" in after["etc/kiwi-vpn/wg1-rules.sh"].decode()
    # order: unbound answers before anything is switched to it; agent before proxy
    c = calls(root)
    first = lambda s: next(i for i, x in enumerate(c) if x.startswith(s))
    assert first("systemctl restart unbound") < first("iptables -N KIWI-IN") < first("systemctl restart kiwi-vpn-agent") < first("systemctl restart kiwi-vpn-proxy")
    never_touches_wg0(root)
    (backup,) = (root / "root").iterdir()
    assert (backup / "wg1-rules.sh").read_bytes() == before["etc/kiwi-vpn/wg1-rules.sh"] and "WireGuard unchanged" in r.stdout


def test_running_it_twice_adds_nothing_twice(server):
    root, _ = server
    assert run(server, S, "--apply").returncode == 0
    r = run(server, S, "--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    rules = (root / "etc/kiwi-vpn/wg1-rules.sh").read_text()
    assert rules.count("# kiwi: dns") == 2 and rules.count("25,465,587") == 1
    assert (root / "etc/kiwi-vpn/agent.env").read_text().count("DNS=") == 1
    assert (root / "etc/kiwi-vpn-proxy/proxy.env").read_text().count("RESOLVER=") == 1
    assert len(nat(root)) == 3 and len(chain(root, "KIWI-IN")) == 5


def test_unbound_that_doesnt_answer_changes_nothing_for_customers(server):
    root, _ = server
    before = files(root)
    r = run(server, S, "--apply", UNBOUND_DOWN="1")
    assert r.returncode != 0 and "rolling back" in r.stderr and "doesn't answer" in r.stderr
    assert files(root) == before
    assert not (root / "etc/unbound/unbound.conf.d/kiwi-vpn.conf").exists()
    never_touches_wg0(root)


def test_a_proxy_that_wont_start_is_rolled_back(server):
    root, _ = server
    before, nat_before = files(root), nat(root)
    r = run(server, S, "--apply", PROXY_FAILS="1")
    assert r.returncode != 0 and "rolling back" in r.stderr
    assert files(root) == before
    assert sorted(nat(root)) == sorted(nat_before)
    assert any("127.0.0.53" in x for x in chain(root, "KIWI-PROXY"))  # the old guard is back
    assert (root / "proxy-up").exists() and (root / "agent-up").exists()
    assert not (root / "unbound-up").exists()  # unbound wasn't running before, so it's stopped again
    assert "LOCAL_DNS=0" in (root / "etc/kiwi-vpn/install.env").read_text()
    never_touches_wg0(root)


def test_wireguard_changing_during_the_run_stops_it_loudly(server):
    r = run(server, S, "--apply", WG_CHANGES="1")
    assert r.returncode != 0 and "WireGuard changed" in r.stderr


def test_an_unexpected_installed_proxy_or_agent_is_left_alone(server):
    root, _ = server
    for f in ("opt/kiwi-vpn/proxy.py", "opt/kiwi-vpn/agent.py"):
        good = (root / f).read_bytes()
        (root / f).write_bytes(good + b"\n# edited by hand\n")
        before = files(root)
        r = run(server, S, "--apply")
        assert r.returncode != 0 and "isn't a version this was written against" in r.stderr
        assert files(root) == before
        (root / f).write_bytes(good)


def test_another_dns_server_on_the_host_stops_it(server):
    root, _ = server
    before = files(root)
    r = run(server, S, "--apply", OTHER_DNS="1")
    assert r.returncode != 0 and "another DNS server" in r.stderr
    assert files(root) == before


def test_rollback_puts_everything_back(server):
    root, _ = server
    before, nat_before = files(root), nat(root)
    assert run(server, S, "--apply").returncode == 0
    (backup,) = (root / "root").iterdir()
    r = run(server, S, "--rollback", unix(backup))
    assert r.returncode == 0, r.stdout + r.stderr
    assert files(root) == before
    assert sorted(nat(root)) == sorted(nat_before)
    assert not (root / "etc/unbound/unbound.conf.d/kiwi-vpn.conf").exists()
    never_touches_wg0(root)


def test_it_works_on_a_node_without_the_floating_ip_too(tmp_path):
    server = make_server(tmp_path, [S], float_live=False)
    root, _ = server
    r = run(server, S, "--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DNS=10.66.0.1" in (root / "etc/kiwi-vpn/agent.env").read_text()


def test_the_known_hashes_are_what_helsinki_runs():
    import hashlib
    from node_fakes import git_show
    text = open(__file__.replace("test_apply_dns_privacy.py", S), encoding="utf-8").read()
    for rev in ("b840198:vpn-node/agent.py", "947152b:vpn-node/agent.py", "407896e:vpn-node/proxy.py", "b840198:vpn-node/proxy.py"):
        assert hashlib.sha256(git_show(rev)).hexdigest() in text, rev
