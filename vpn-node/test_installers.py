"""install-shared.sh and install-proxy.sh: the files they generate (--print-config, no root), and the generated rules.sh run
against a stateful fake iptables (fake_iptables.py) on top of a wg0-style generic MASQUERADE.

    python -m pytest vpn-node/test_installers.py -q -p no:cacheprovider     (needs bash: Git Bash on Windows)
"""
import json
import os
import re
import subprocess
import sys

import pytest

from node_fakes import BASH, FLOAT, HERE, PRIMARY, unix

STRIP = ("POOL", "POOL6", "DNS", "PUBLIC_IP", "WAN_IF", "AGENT_ALLOW", "WITH_PROXY", "EXIT_IP", "LOCAL_DNS", "OWN_NETS", "NFT_MODE")


def render(script, **env):
    e = {k: v for k, v in os.environ.items() if k not in STRIP}
    e.update(env)
    r = subprocess.run([BASH, script, "--print-config"], cwd=HERE, capture_output=True, text=True, timeout=60, env=e)
    assert r.returncode == 0, r.stderr
    files, cur = {}, None
    for line in r.stdout.splitlines():
        m = re.fullmatch(r"##### (\S+) #####", line)
        if m:
            cur = m.group(1)
            files[cur] = ""
        elif cur:
            files[cur] += line + "\n"
    return files


def run_rules(tmp_path, rules_text, *, times=1, start=None):
    """Runs a generated rules.sh `up` against the fake firewall; returns the iptables state."""
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    state = tmp_path / "ipt.json"
    state.write_text(json.dumps(start or {"nat": {"PREROUTING": [], "INPUT": [], "OUTPUT": [], "POSTROUTING": ["-o eth0 -j MASQUERADE"]}}))
    (bin_ / "iptables").write_text(f'#!/usr/bin/env bash\n"{unix(sys.executable)}" "{unix(os.path.join(HERE, "fake_iptables.py"))}" "{unix(state)}" "$@"\n', newline="\n")
    (bin_ / "ip6tables").write_text("#!/usr/bin/env bash\ntrue\n", newline="\n")
    rules = tmp_path / "rules.sh"
    rules.write_text(rules_text, newline="\n")
    for _ in range(times):
        r = subprocess.run([BASH, "-c", f'export PATH="{unix(bin_)}:/usr/bin:/bin"; sh "{unix(rules)}" up'], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    return json.loads(state.read_text())


# ---------- install-shared.sh ----------
def test_shared_syntax_and_lf_endings():
    for name in ("install-shared.sh", "install-proxy.sh", "apply-dns-privacy.sh", "apply-floating-ip.sh"):
        assert subprocess.run([BASH, "-n", name], cwd=HERE).returncode == 0, name
        assert b"\r\n" not in open(os.path.join(HERE, name), "rb").read(), name


def test_shared_customers_get_the_nodes_own_resolver_by_default():
    f = render("install-shared.sh")
    env = f["/etc/kiwi-vpn/agent.env"]
    assert "DNS=10.66.0.1\n" in env and "1.1.1.1" not in env
    ub = f["/etc/unbound/unbound.conf.d/kiwi-vpn.conf"]
    for must in ("interface: 10.66.0.1", "ip-freebind: yes", "access-control: 0.0.0.0/0 refuse", "access-control: 10.66.0.0/16 allow",
                 "verbosity: 0", "log-queries: no", "log-replies: no"):
        assert must in ub, must
    rules = f["/etc/kiwi-vpn/wg1-rules.sh"]
    assert rules.index("--dport 53 -j ACCEPT") < rules.index("iptables -A KIWI-IN -j REJECT")
    assert "-s 10.66.0.0/16 -d 10.66.0.1 -p udp --dport 53 -j ACCEPT" in rules


def test_shared_without_local_dns_keeps_the_old_behaviour():
    f = render("install-shared.sh", LOCAL_DNS="0")
    assert "DNS=1.1.1.1\n" in f["/etc/kiwi-vpn/agent.env"]
    assert "/etc/unbound/unbound.conf.d/kiwi-vpn.conf" not in f
    assert "--dport 53" not in f["/etc/kiwi-vpn/wg1-rules.sh"]


def test_shared_blocks_every_mail_port():
    rules = render("install-shared.sh")["/etc/kiwi-vpn/wg1-rules.sh"]
    assert "-p tcp -m multiport --dports 25,465,587 -j REJECT --reject-with tcp-reset" in rules
    assert "--dport 25 -j" not in rules


def test_shared_agent_allowlist_replaces_the_open_port():
    ufw = render("install-shared.sh", AGENT_ALLOW="198.51.100.1,198.51.100.2/32")["ufw"].splitlines()
    assert "ufw allow proto tcp from 198.51.100.1 to any port 8443" in ufw
    assert "ufw allow proto tcp from 198.51.100.2/32 to any port 8443" in ufw
    assert "ufw delete allow 8443/tcp" in ufw and "ufw allow 8443/tcp" not in ufw
    assert "ufw logging off" in ufw
    assert "ufw allow 8443/tcp" in render("install-shared.sh")["ufw"].splitlines()  # empty = as before (warned at install)


def test_shared_bad_allowlist_is_refused():
    e = {k: v for k, v in os.environ.items() if k not in STRIP}
    e["AGENT_ALLOW"] = "198.51.100.1; rm -rf /"
    r = subprocess.run([BASH, "install-shared.sh", "--print-config"], cwd=HERE, capture_output=True, text=True, env=e)
    assert r.returncode != 0 and "AGENT_ALLOW" in r.stderr


def test_shared_options_are_kept_for_re_runs_and_logs_are_short():
    f = render("install-shared.sh", AGENT_ALLOW="198.51.100.1", EXIT_IP=FLOAT)
    assert f["/etc/kiwi-vpn/install.env"] == f"AGENT_ALLOW=198.51.100.1\nEXIT_IP={FLOAT}\nLOCAL_DNS=1\n"
    assert "MaxRetentionSec=7day" in f["/etc/systemd/journald.conf.d/kiwi-vpn.conf"]
    src = open(os.path.join(HERE, "install-shared.sh"), encoding="utf-8").read()
    assert 'PROXY_LINES="$(grep \'^PROXY_\'' in src  # install-proxy.sh's agent.env lines survive a re-run


def test_shared_rules_on_a_host_with_wg0s_masquerade(tmp_path):
    rules = render("install-shared.sh", PUBLIC_IP=PRIMARY)["/etc/kiwi-vpn/wg1-rules.sh"]
    st = run_rules(tmp_path, rules, times=2)  # twice: nothing doubles
    post = st["nat"]["POSTROUTING"]
    assert post == ["-s 10.66.0.0/16 -o eth0 -j MASQUERADE", "-o eth0 -j MASQUERADE"]
    kin = st["filter"]["KIWI-IN"]
    assert kin == ["-m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT", "-d 10.66.0.1 -p icmp -j ACCEPT",
                   "-s 10.66.0.0/16 -d 10.66.0.1 -p udp --dport 53 -j ACCEPT", "-s 10.66.0.0/16 -d 10.66.0.1 -p tcp --dport 53 -j ACCEPT", "-j REJECT"]
    assert st["filter"]["INPUT"] == ["-i wg1 -j KIWI-IN"]
    assert st["filter"]["FORWARD"] == ["-i wg0 -o wg1 -j REJECT", "-i wg1 -j KIWI-FWD"]


def test_shared_exit_ip_snat_goes_first_and_is_the_endpoint(tmp_path):
    f = render("install-shared.sh", PUBLIC_IP=PRIMARY, EXIT_IP=FLOAT)
    assert f"ENDPOINT_HOST={FLOAT}\n" in f["/etc/kiwi-vpn/agent.env"]
    st = run_rules(tmp_path, f["/etc/kiwi-vpn/wg1-rules.sh"], times=2)
    assert st["nat"]["POSTROUTING"] == [f"-s 10.66.0.0/16 -o eth0 -j SNAT --to-source {FLOAT}", "-o eth0 -j MASQUERADE"]


def test_shared_down_removes_only_kiwis_rules(tmp_path):
    f = render("install-shared.sh", PUBLIC_IP=PRIMARY, EXIT_IP=FLOAT)
    run_rules(tmp_path, f["/etc/kiwi-vpn/wg1-rules.sh"])
    r = subprocess.run([BASH, "-c", f'export PATH="{unix(tmp_path / "bin")}:/usr/bin:/bin"; sh "{unix(tmp_path / "rules.sh")}" down'], capture_output=True, text=True)
    assert r.returncode == 0
    st = json.loads((tmp_path / "ipt.json").read_text())
    assert st["nat"]["POSTROUTING"] == ["-o eth0 -j MASQUERADE"]
    assert "KIWI-IN" not in st["filter"] and st["filter"]["INPUT"] == [] and st["filter"]["FORWARD"] == []


# ---------- install-proxy.sh ----------
def test_proxy_resolves_with_its_own_unbound_and_may_reach_only_that():
    f = render("install-proxy.sh")
    env = f["/etc/kiwi-vpn-proxy/proxy.env"]
    assert "RESOLVER=127.0.0.1\n" in env
    ub = f["/etc/unbound/unbound.conf.d/kiwi-proxy.conf"]
    for must in ("interface: 127.0.0.1", "access-control: 127.0.0.0/8 allow", "log-queries: no", "log-replies: no", "verbosity: 0"):
        assert must in ub, must
    rules = f["/etc/kiwi-vpn-proxy/rules.sh"]
    assert "127.0.0.53" not in rules
    assert rules.index("-d 127.0.0.1 -p udp --dport 53 -j RETURN") < rules.index("-o lo -j REJECT")


def test_proxy_without_local_dns_keeps_the_old_behaviour():
    f = render("install-proxy.sh", LOCAL_DNS="0")
    assert "RESOLVER=\n" in f["/etc/kiwi-vpn-proxy/proxy.env"]
    assert "-d 127.0.0.53 -p udp --dport 53 -j RETURN" in f["/etc/kiwi-vpn-proxy/rules.sh"]
    assert "/etc/unbound/unbound.conf.d/kiwi-proxy.conf" not in f


def test_proxy_on_an_nftables_node_has_the_same_limits_as_on_a_shared_node():
    nft = render("install-proxy.sh", NFT_MODE="1")["/etc/kiwi-vpn/nft.d/kiwi-proxy.nft"]
    assert 'meta skuid "kiwi-proxy" jump guard' in nft
    for must in ("169.254.0.0/16", "10.0.0.0/8", "127.0.0.0/8", f"ip daddr {PRIMARY} reject", "{ 25, 465, 587 }", "oif lo reject",
                 "ip6 daddr { 2001:db8:1:2::/64, fc00::/7, fe80::/10, ff00::/8 } reject", "ip daddr 127.0.0.1 meta l4proto { udp, tcp } th dport 53 return"):
        assert must in nft, must
    assert "127.0.0.53" not in nft
    assert "delete table inet kiwi_proxy" in nft  # loading it twice never doubles the rules
    assert "kiwi_proxy_nat" not in nft


def test_proxy_exit_ip_on_both_firewall_kinds(tmp_path):
    f = render("install-proxy.sh", EXIT_IP=FLOAT)
    st = run_rules(tmp_path, f["/etc/kiwi-vpn-proxy/rules.sh"], times=2)
    assert st["nat"]["POSTROUTING"] == [f"-m owner --uid-owner kiwi-proxy -o eth0 -j SNAT --to-source {FLOAT}", "-o eth0 -j MASQUERADE"]
    assert f"-d {FLOAT} -j REJECT" in st["filter"]["KIWI-PROXY"]
    assert f"PUBLIC_IPS={FLOAT},{PRIMARY}\n" in f["/etc/kiwi-vpn-proxy/proxy.env"]
    nft = render("install-proxy.sh", EXIT_IP=FLOAT, NFT_MODE="1")["/etc/kiwi-vpn/nft.d/kiwi-proxy.nft"]
    assert "type nat hook postrouting priority 99;" in nft and f'meta skuid "kiwi-proxy" oifname "eth0" snat to {FLOAT}' in nft
    assert f"ip daddr {{ {PRIMARY}, {FLOAT} }} reject" in nft
    rules_nft = render("install-proxy.sh", EXIT_IP=FLOAT, NFT_MODE="1")["/etc/kiwi-vpn-proxy/rules.sh"]
    assert "nft delete table ip kiwi_proxy_nat" in rules_nft


def test_proxy_journal_and_ufw_logging():
    src = open(os.path.join(HERE, "install-proxy.sh"), encoding="utf-8").read()
    assert "ufw logging off" in src and 'render_journald > "$JOURNALD_CONF"' in src
    assert "MaxRetentionSec=7day" in render("install-proxy.sh")["/etc/systemd/journald.conf.d/kiwi-vpn.conf"]
    assert 'EMAIL_ARGS=(-m "$CERT_EMAIL")' in src and "certbot update_account" in src


def test_generated_rules_parse():
    for script, key, env in (("install-shared.sh", "/etc/kiwi-vpn/wg1-rules.sh", {"EXIT_IP": FLOAT}), ("install-proxy.sh", "/etc/kiwi-vpn-proxy/rules.sh", {"EXIT_IP": FLOAT})):
        r = subprocess.run([BASH, "-c", "sh -n"], input=render(script, **env)[key], capture_output=True, text=True)
        assert r.returncode == 0, (script, r.stderr)


def test_install_sh_blocks_every_mail_port_and_warns_without_an_allowlist():
    src = open(os.path.join(HERE, "install.sh"), encoding="utf-8").read()
    assert 'tcp dport { 25, 465, 587 } reject with tcp reset comment "kiwi: smtp 25 blocked, 465 587 too"' in src
    assert '"kiwi: smtp 25 blocked"' in src  # the doctor's check still matches nodes with the older rule
    assert "warning: AGENT_ALLOW is empty" in src


def test_no_pipeline_that_fails_a_fresh_install():
    for name in ("install-shared.sh", "apply-dns-privacy.sh", "apply-floating-ip.sh"):
        for n, line in enumerate(open(os.path.join(HERE, name), encoding="utf-8"), 1):
            if re.search(r"\$\([^()]*2>/dev/null\s*\|(?!\|)", line):
                assert "|| true" in line, f"{name}:{n}: {line.strip()}"
