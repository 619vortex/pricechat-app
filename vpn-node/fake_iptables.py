"""A stand-in for iptables/ip6tables in the shell-script tests: keeps tables, chains and rule order in a JSON file, so a test can
check what the scripts' rules.sh files really build (order in nat POSTROUTING, no duplicates after two runs). Tests only.

    python fake_iptables.py <state.json> [iptables arguments...]

Supports -t, -N, -X, -F, -A, -I [n], -D (exact spec, fails when absent like the real one), -S [chain], -C. Unknown = exit 2.
"""
import json
import os
import sys

BUILTIN = {"filter": ["INPUT", "FORWARD", "OUTPUT"], "nat": ["PREROUTING", "INPUT", "OUTPUT", "POSTROUTING"]}


def main(argv):
    path, args = argv[0], argv[1:]
    state = json.load(open(path)) if os.path.exists(path) else {}
    table = "filter"
    if args[:1] == ["-t"]:
        table, args = args[1], args[2:]
    chains = state.setdefault(table, {c: [] for c in BUILTIN.get(table, [])})
    for c in BUILTIN.get(table, []):
        chains.setdefault(c, [])
    if args and args[0] in ("-w",):
        args = args[1:]
    op, rest = args[0], args[1:]
    code = 0
    if op == "-N":
        if rest[0] in chains:
            print(f"iptables: Chain already exists.", file=sys.stderr)
            code = 1
        else:
            chains[rest[0]] = []
    elif op == "-X":
        if rest[0] not in chains or rest[0] in BUILTIN.get(table, []):
            code = 1
        else:
            del chains[rest[0]]
    elif op == "-F":
        if rest and rest[0] not in chains:
            code = 1
        elif rest:
            chains[rest[0]] = []
    elif op == "-A":
        if rest[0] not in chains:
            code = 1
        else:
            chains[rest[0]].append(" ".join(rest[1:]))
    elif op == "-I":
        chain, spec = rest[0], rest[1:]
        pos = 1
        if spec and spec[0].isdigit():
            pos, spec = int(spec[0]), spec[1:]
        if chain not in chains:
            code = 1
        else:
            chains[chain].insert(pos - 1, " ".join(spec))
    elif op in ("-D", "-C"):
        chain, spec = rest[0], " ".join(rest[1:])
        if chain not in chains or spec not in chains[chain]:
            code = 1
        elif op == "-D":
            chains[chain].remove(spec)
    elif op == "-S":
        for c, rules in chains.items():
            if rest and c != rest[0]:
                continue
            print(f"-P {c} ACCEPT" if c in BUILTIN.get(table, []) else f"-N {c}")
            for r in rules:
                print(f"-A {c} {r}")
    else:
        print(f"fake iptables: unsupported {args}", file=sys.stderr)
        code = 2
    json.dump(state, open(path, "w"))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
