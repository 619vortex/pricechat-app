"""Local end-to-end run of kiwi-uptime.yml's own shell steps, with no GitHub and no real Telegram.

Takes the "Check" and "Alert" steps' scripts straight out of the workflow file, runs them several times with the state
folder kept between runs (what actions/cache does on GitHub), against the real public endpoints, and points the Telegram
send at a fake Bot API on 127.0.0.1. It also proves the change-detection: it sends when the set of failures changes and
stays quiet when nothing changed. One failure is simulated by adding an unreachable proxy host.

Needs bash, curl, openssl and jq on PATH (Git Bash on Windows; jq can be any jq-compatible binary).
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml  # PyYAML

HERE = Path(__file__).resolve().parent
WORKFLOW = HERE / "kiwi-uptime.yml"

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


def main() -> int:
    if "--path" in sys.argv:  # e.g. a folder holding a jq binary, put first on PATH for the workflow's scripts
        os.environ["PATH"] = sys.argv[sys.argv.index("--path") + 1] + os.pathsep + os.environ["PATH"]
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = wf["jobs"]["check"]
    steps = {s.get("id") or s["name"]: s for s in job["steps"]}
    check = steps["check"]["run"]
    alert = next(s["run"] for s in job["steps"] if s["name"].startswith("Alert on Telegram"))
    env_defaults = {k: v for k, v in job["env"].items() if isinstance(v, str) and "${{" not in v}

    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeBotApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    work = Path(tempfile.mkdtemp(prefix="kiwi-uptime-selftest-"))
    bash = shutil.which("bash") or "bash"
    proxies = "hel1.vpn.pricechat.live:443 ash1.vpn.pricechat.live:443"

    def run(extra_proxy: str = "") -> tuple[int, str]:
        env = {**os.environ, **env_defaults, "PROXIES": f"{proxies} {extra_proxy}".strip(),
               "TG_TOKEN": "000:selftest", "TG_CHAT": "424242", "TG_API": f"http://127.0.0.1:{server.server_address[1]}",
               "GITHUB_OUTPUT": str(work / "out.txt"), "GITHUB_SERVER_URL": "https://github.example",
               "GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "1"}
        (work / "out.txt").write_text("")
        c = subprocess.run([bash, "-c", check], cwd=work, env=env, capture_output=True, text=True, timeout=300)
        a = subprocess.run([bash, "-c", alert], cwd=work, env=env, capture_output=True, text=True, timeout=60)
        count = int(dict(line.split("=", 1) for line in (work / "out.txt").read_text().splitlines() if "=" in line).get("count", "-1"))
        return count, c.stdout + a.stdout + c.stderr + a.stderr

    ok = True

    def expect(label: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= cond
        print(f"{'PASS' if cond else 'FAIL'}  {label}{(': ' + detail) if detail and not cond else ''}")

    plan = [
        ("run 1: all real checks healthy, first run (no state yet)", "", 0, 0),
        ("run 2: nothing changed", "", 0, 0),
        ("run 3: an unreachable proxy appears", "unreachable.invalid:443", 1, 1),
        ("run 4: same failure, nothing changed", "unreachable.invalid:443", 1, 0),
        ("run 5: the failure is gone", "", 0, 1),
        ("run 6: nothing changed", "", 0, 0),
    ]
    for label, extra, want_fails, want_new in plan:
        before = len(sent)
        fails, log = run(extra)
        new = sent[before:]
        expect(f"{label}: {want_fails} failing check(s)", fails == want_fails, log[-800:])
        expect(f"{label}: {want_new} Telegram message(s)", len(new) == want_new, json.dumps(new))
        for m in new:
            print("      sent:", json.dumps({k: m[k] for k in ("path", "chat_id", "disable_web_page_preview")}),
                  "|", m["text"].splitlines()[0], "|", (m["text"].splitlines()[1:2] or [""])[0])
    expect("the PROBLEM message names the failing proxy", any("PROBLEM" in m["text"] and "unreachable.invalid" in m["text"] for m in sent))
    expect("the recovery message says so", any("recovered" in m["text"] for m in sent))
    server.shutdown()
    shutil.rmtree(work, ignore_errors=True)
    print("ALL PASS" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
