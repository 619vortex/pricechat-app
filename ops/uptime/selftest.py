"""Local end-to-end run of .github/workflows/uptime.yml's own shell steps, with no GitHub and no real Telegram.

Takes the "Check", "Open, update or close the uptime issue" and "Alert on Telegram" steps' scripts straight out of the workflow file
and runs them several times against the real public endpoints. `gh` is a local fake that keeps the repository's issues in a JSON file
and answers `--json ... --jq ...` by running the workflow's own jq expressions through real jq, so the issue logic (open once, comment
on change / every REMIND_HOURS, close on recovery) is exercised end to end. The Telegram send goes to a fake Bot API on 127.0.0.1.
Failures are simulated by adding unreachable proxy hosts.

Needs bash, curl, openssl and jq on PATH (Git Bash on Windows; put a jq binary's folder first with --path).
    python ops/uptime/selftest.py [--path DIR_WITH_JQ]
Exit code 0 = every expectation held. Read-only GETs/CONNECTs to public addresses only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKFLOW = HERE.parent.parent / ".github" / "workflows" / "uptime.yml"

sent: list[dict[str, str]] = []


class FakeBotApi(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        form = {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}
        sent.append({"path": self.path.split("/bot", 1)[1].split("/", 1)[1] if "/bot" in self.path else self.path, **form})
        data = json.dumps({"ok": True, "result": {"message_id": len(sent)}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a: object) -> None:
        pass


# --- the fake `gh` (python selftest.py --fake-gh <args>) -------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _opt(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args else None


def fake_gh(args: list[str]) -> int:
    state_file = Path(os.environ["FAKE_GH_STATE"])
    st = json.loads(state_file.read_text()) if state_file.exists() else {"issues": [], "labels": [], "calls": []}
    st["calls"].append(args[:2])

    def out(data: object) -> int:
        expr = _opt(args, "--jq")
        if expr is None:
            print(json.dumps(data))
            return 0
        r = subprocess.run([shutil.which("jq") or "jq", "-r", expr], input=json.dumps(data), capture_output=True, text=True)
        sys.stdout.write(r.stdout.replace("\r\n", "\n"))
        sys.stderr.write(r.stderr)
        return r.returncode

    code = 0
    if args[:2] == ["label", "create"]:
        if args[2] in st["labels"]:
            code = 1  # already exists (the workflow ignores it)
        else:
            st["labels"].append(args[2])
    elif args[:2] == ["issue", "list"]:
        label, state = _opt(args, "--label"), _opt(args, "--state")
        code = out([{"number": i["number"]} for i in reversed(st["issues"]) if label in i["labels"] and i["state"] == state][:1])
    elif args[:2] == ["issue", "view"]:
        i = st["issues"][int(args[2]) - 1]
        code = out({"body": i["body"], "createdAt": i["createdAt"], "comments": i["comments"]})
    elif args[:2] == ["issue", "create"]:
        n = len(st["issues"]) + 1
        st["issues"].append({"number": n, "title": _opt(args, "--title"), "labels": [_opt(args, "--label")], "body": _opt(args, "--body"),
                             "createdAt": _now(), "state": "open", "comments": []})
        print(f"https://github.example/owner/repo/issues/{n}")
    elif args[:2] == ["issue", "comment"]:
        st["issues"][int(args[2]) - 1]["comments"].append({"body": _opt(args, "--body"), "createdAt": _now()})
    elif args[:2] == ["issue", "close"]:
        st["issues"][int(args[2]) - 1]["state"] = "closed"
    else:
        print(f"fake gh: unsupported {args}", file=sys.stderr)
        code = 2
    state_file.write_text(json.dumps(st))
    return code


# --- the run ------------------------------------------------------------------------------------------------------------

def main() -> int:
    import yaml  # PyYAML

    if "--path" in sys.argv:  # e.g. a folder holding a jq binary, put first on PATH for the workflow's scripts
        os.environ["PATH"] = sys.argv[sys.argv.index("--path") + 1] + os.pathsep + os.environ["PATH"]
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = wf["jobs"]["probe"]
    steps = {s.get("id") or s["name"]: s for s in job["steps"]}
    check, issue_step = steps["check"]["run"], steps["issue"]["run"]
    tg = next(s for s in job["steps"] if s["name"].startswith("Alert on Telegram"))
    env_defaults = {k: v for k, v in job["env"].items() if isinstance(v, str) and "${{" not in v}

    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeBotApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    work = Path(tempfile.mkdtemp(prefix="uptime-selftest-"))
    fake_bin = work / "bin"
    fake_bin.mkdir()
    py = Path(sys.executable).as_posix()
    (fake_bin / "gh").write_text(f'#!/usr/bin/env bash\nexec "{py}" "{Path(__file__).resolve().as_posix()}" --fake-gh "$@"\n', newline="\n")
    (fake_bin / "gh").chmod(0o755)
    state_file = work / "gh-state.json"
    bash = shutil.which("bash") or "bash"
    proxies = "hel1.vpn.pricechat.live:443 ash1.vpn.pricechat.live:443"

    def outputs() -> dict[str, str]:
        res, lines, i = {}, (work / "out.txt").read_text().splitlines(), 0
        while i < len(lines):
            line = lines[i]
            if "<<" in line and "=" not in line.split("<<")[0]:
                k, delim = line.split("<<", 1)
                body = []
                i += 1
                while lines[i] != delim:
                    body.append(lines[i])
                    i += 1
                res[k] = "\n".join(body)
            elif "=" in line:
                k, v = line.split("=", 1)
                res[k] = v
            i += 1
        return res

    def run(extra_proxy: str = "", telegram: bool = True) -> tuple[dict[str, str], str]:
        env = {**os.environ, **env_defaults, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
               "PROXIES": f"{proxies} {extra_proxy}".strip(), "TG_TOKEN": "000:selftest" if telegram else "", "TG_CHAT": "424242" if telegram else "",
               "TG_API": f"http://127.0.0.1:{server.server_address[1]}", "GITHUB_OUTPUT": str(work / "out.txt"), "GH_TOKEN": "fake",
               "GH_REPO": "owner/repo", "OWNER": "repo-owner", "RUN_URL": "https://github.example/owner/repo/actions/runs/1",
               "FAKE_GH_STATE": str(state_file)}
        (work / "out.txt").write_text("")
        log = ""
        for script in (check, issue_step):
            r = subprocess.run([bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", script], cwd=work, env=env, capture_output=True,
                               text=True, timeout=300)
            log += r.stdout + r.stderr + f"[exit {r.returncode}]\n"
        o = outputs()
        if o.get("event") and env["TG_TOKEN"] and env["TG_CHAT"]:  # the step's `if:`
            r = subprocess.run([bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", tg["run"]], cwd=work,
                               env={**env, "MESSAGE": o.get("message", "")}, capture_output=True, text=True, timeout=60)
            log += r.stdout + r.stderr
        return o, log

    def gh_state() -> dict:  # type: ignore[type-arg]
        return json.loads(state_file.read_text()) if state_file.exists() else {"issues": [], "labels": []}

    def backdate(hours: float) -> None:
        st = gh_state()
        for i in st["issues"]:
            for c in [i, *i["comments"]]:
                t = datetime.strptime(c["createdAt"], "%Y-%m-%dT%H:%M:%SZ") - timedelta(hours=hours)
                c["createdAt"] = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        state_file.write_text(json.dumps(st))

    ok = True

    def expect(label: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= cond
        print(f"{'PASS' if cond else 'FAIL'}  {label}{(': ' + detail) if detail and not cond else ''}")

    bad1, bad2 = "unreachable.invalid:443", "unreachable2.invalid:443"
    plan = [  # label, extra proxies, telegram secrets set, backdate state first (h), failing checks, event, open issues, comments on #1
        ("run 1: all real checks healthy", "", True, 0, 0, "", 0, 0),
        ("run 2: an unreachable proxy appears", bad1, True, 0, 1, "PROBLEM", 1, 0),
        ("run 3: same failure, nothing changed", bad1, True, 0, 1, "", 1, 0),
        ("run 4: same failure 7 h later", bad1, True, 7, 1, "STILL FAILING", 1, 1),
        ("run 5: a second proxy fails too", f"{bad1} {bad2}", True, 0, 2, "CHANGED", 1, 2),
        ("run 6: every check passes again", "", True, 0, 0, "RECOVERED", 0, 3),
        ("run 7: nothing changed", "", True, 0, 0, "", 0, 3),
        ("run 8: a failure without the Telegram secrets", bad1, False, 0, 1, "PROBLEM", 1, 3),
    ]
    for label, extra, telegram, back, want_fails, want_event, want_open, want_comments in plan:
        if back:
            backdate(back)
        before = len(sent)
        o, log = run(extra, telegram)
        st = gh_state()
        new = sent[before:]
        expect(f"{label}: {want_fails} failing check(s)", o.get("count") == str(want_fails), log[-1500:])
        expect(f"{label}: event '{want_event}'", o.get("event", "") == want_event, log[-1500:])
        expect(f"{label}: {want_open} open uptime issue(s)", sum(i["state"] == "open" for i in st["issues"]) == want_open, json.dumps(st)[-800:])
        expect(f"{label}: {want_comments} comment(s) on issue #1", len(st["issues"][0]["comments"]) == want_comments if st["issues"] else want_comments == 0)
        want_tg = 1 if want_event and telegram else 0
        expect(f"{label}: {want_tg} Telegram message(s)", len(new) == want_tg, json.dumps(new))
        for m in new:
            print("      telegram:", " | ".join(m["text"].splitlines()[:2]))
    issues = gh_state()["issues"]
    expect("issue #1 mentions the owner and names the failing proxy", "@repo-owner" in issues[0]["body"] and bad1 in issues[0]["body"])
    expect("issue #1 was closed with a 'Recovered' comment", issues[0]["state"] == "closed" and "Recovered" in issues[0]["comments"][-1]["body"])
    expect("issue #1's CHANGED comment names both proxies", bad2 in issues[0]["comments"][1]["body"] and bad1 in issues[0]["comments"][1]["body"])
    expect("the label was created once", gh_state()["labels"] == ["uptime"])
    server.shutdown()
    shutil.rmtree(work, ignore_errors=True)
    print("ALL PASS" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--fake-gh":
        sys.exit(fake_gh(sys.argv[2:]))
    sys.exit(main())
