"""Cross-system end-to-end: the REAL Vortex worker (src/pricechat_assist from the Vortex repo: WorkerClient, Service, Assessor and
its validator) driving the REAL Price Chat backend (/api/vortex/* via FastAPI's TestClient) for "/help <trade>" checks.

Only the edges are faked: Hyperliquid (FakeMD), Telegram (TG) and the LLM (a function that writes from the FACTS in its prompt).
The Vortex repo is another checkout: these tests SKIP when it isn't there. Point VORTEX_REPO at it to override the default path.
"""

import json
import os
import re
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.db.session import SessionLocal
from app.tradingos import assist

from tests.test_trading_os import FakeMD
from tests.test_vortex_assist import TG, TOKEN, ask, jobs, md, run, tgfake, token  # noqa: F401 - fixtures

VORTEX_REPO = Path(os.environ.get("VORTEX_REPO") or r"C:\AI\pc-wt\vortex-assist")
if not (VORTEX_REPO / "src" / "pricechat_assist" / "__init__.py").is_file():
    pytest.skip(f"Vortex repo not found at {VORTEX_REPO} (set VORTEX_REPO)", allow_module_level=True)
sys.path.append(str(VORTEX_REPO / "src"))  # appended: the backend's own modules always win a name clash

from pricechat_assist import validator  # noqa: E402
from pricechat_assist.assessor import Assessor  # noqa: E402
from pricechat_assist.client import WorkerClient  # noqa: E402
from pricechat_assist.config import Config  # noqa: E402
from pricechat_assist.service import AUTH_BACKOFF_S, Service  # noqa: E402

BASE = "https://pricechat.test"


class ApiSession:
    """TEST ADAPTER: FastAPI's TestClient behind the requests.Session surface the worker uses (.get/.post with json=, headers=,
    timeout=(connect, read), allow_redirects=). Records every call, and any exception the app raised (the worker would
    otherwise fold it into a TransientError and hide a backend bug)."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.calls: list[tuple[str, str, int]] = []
        self.errors: list[BaseException] = []

    def _path(self, url: str) -> str:
        assert url.startswith(BASE + "/"), url  # the worker only ever talks to its configured base URL
        return url[len(BASE):]

    def _go(self, method: str, url: str, timeout: Any, allow_redirects: bool, **kw: Any) -> Any:
        assert isinstance(timeout, tuple) and len(timeout) == 2 and all(t > 0 for t in timeout), timeout
        assert allow_redirects is False  # the bearer must never follow a redirect
        try:
            r = self.client.request(method, self._path(url), follow_redirects=allow_redirects, **kw)
        except BaseException as e:
            self.errors.append(e)
            raise
        self.calls.append((method, self._path(url), r.status_code))
        return r

    def get(self, url: str, headers: dict[str, str] | None = None, timeout: Any = None, allow_redirects: bool = True) -> Any:
        return self._go("GET", url, timeout, allow_redirects, headers=headers)

    def post(self, url: str, json: Any = None, headers: dict[str, str] | None = None, timeout: Any = None,
             allow_redirects: bool = True) -> Any:
        return self._go("POST", url, timeout, allow_redirects, json=json, headers=headers)


def facts_of(prompt: str) -> dict[str, Any]:
    m = re.search(r"BEGIN_FACTS\n(.*?)\nEND_FACTS", prompt, re.S)
    assert m, prompt
    return json.loads(m.group(1))


def grounded_answer(f: dict[str, Any]) -> str:
    """What a well-behaved model writes: every number copied from FACTS."""
    lines = [f"Risk {f['risk_score']}/10 - {f['trend']} trend, {f['leverage']:g}x leverage on a {f['atr_pct']}% daily range.", "Key risks:"]
    if f.get("liq_price"):
        lines.append(f"- liquidation near {f['liq_price']}, only {f['liq_distance_pct']}% from the price {f['price']}")
    lines += [f"- {flag}" for flag in f["risk_flags"][:3]]
    lines.append(f"Safer: stop at {f['suggested_stop']} ({f['suggested_stop_reason']}), smaller size or lower leverage.")
    lines.append(f"Invalidation: a daily close < {f['support'][0] if f['support'] else f['suggested_stop']} breaks it; > {f['price']} is fine.")
    return "\n".join(lines)


class LLM:
    """TEST FIXTURE: the fake model. `write(facts, prompt)` returns the draft; every call is recorded."""

    def __init__(self, write: Any) -> None:
        self.write = write
        self.prompts: list[str] = []

    def __call__(self, system: str, prompt: str, timeout: float) -> str:
        assert "ONLY the FACTS" in system and timeout > 0
        self.prompts.append(prompt)
        return self.write(facts_of(prompt), prompt)


def worker(client: TestClient, llm: LLM, tok: str = TOKEN) -> tuple[Service, ApiSession]:
    sess = ApiSession(client)
    cfg = Config(enabled=True, api_base=BASE, token=tok, model="fake", ollama_url="http://ollama.invalid", max_jobs=1, wait=0,
                 status_file="")
    return Service(cfg, WorkerClient(BASE, tok, session=sess), Assessor(llm), sleep=lambda s: None), sess


def check_in(svc: Service, db: Session) -> None:
    """The worker's empty long-poll is its heartbeat: afterwards the bot counts Vortex as online."""
    assert svc.step() == 0.0 and svc.stats["claims"] == 1 and svc.stats["jobs"] == 0
    db.expire_all()
    assert assist.online(db)


def test_worker_answers_a_check_and_a_follow_up(client: TestClient, db: Session, md: FakeMD, token: str, tgfake: TG) -> None:
    llm = LLM(lambda f, p: grounded_answer(f))
    svc, sess = worker(client, llm)
    check_in(svc, db)

    # 1. a member asks through the bot: the placeholder goes out and a job waits
    tg = TG()
    ask(db, tg, "/help i long btc at 81k 10x", user=70, chat=-5, msg=11)
    assert tg.sent[0]["text"] == assist.PLACEHOLDER and tg.sent[0]["reply_to"] == 11
    (j,) = jobs(db)
    assert j.status == "pending" and j.kind == "assess"

    # 2. the real worker claims it, the fake model writes from FACTS, the real validator passes it, the backend edits the placeholder
    assert svc.step() == 0.0
    assert svc.stats["answered"] == 1 and svc.stats["invalid"] == 0 and len(llm.prompts) == 1 and not sess.errors
    f = facts_of(llm.prompts[0])
    assert f["coin"] == "BTC" and f["side"] == "long" and f["entry"] == 81000 and f["leverage"] == 10
    assert "BEGIN_QUESTION\ni long btc at 81k 10x\nEND_QUESTION" in llm.prompts[0]
    want = grounded_answer(f)
    ok, text, why = validator.validate(want, f, ["i long btc at 81k 10x"])
    assert ok, why
    (e,) = tgfake.edits
    assert e["chat"] == -5 and e["id"] == j.message_id
    assert e["text"] == assist.answer_html(assist.sanitize(text), f) and e["text"].endswith(f"<i>{assist.FOOTER}</i>")
    assert f"Risk {f['risk_score']}/10" in e["text"] and "Invalidation: a daily close &lt; " in e["text"] and "&gt; " in e["text"]
    (j,) = jobs(db)
    assert j.status == "answered" and j.answer == assist.sanitize(text)

    # 4. a reply to the answer is a follow-up with the conversation so far; the worker answers that too
    seen: list[str] = []

    def follow(f: dict[str, Any], prompt: str) -> str:
        seen.append(prompt)
        return f"At 5x the liquidation moves further away; keep the stop at {f['suggested_stop']}. Risk stays {f['risk_score']}/10."
    llm.write = follow
    assert run(assist.followup(db, tg, -5, 70, "what if I use 5x?", 13, j.message_id)) == "queued"
    assert tg.sent[-1]["text"] == assist.THINKING
    assert svc.step() == 0.0 and svc.stats["answered"] == 2 and not sess.errors
    assert "BEGIN_HISTORY\nUser: i long btc at 81k 10x\nVortex: Risk" in seen[0] and "what if I use 5x?" in seen[0]
    assert "FOLLOW-UP" in seen[0]
    fu = jobs(db)[-1]
    assert fu.kind == "followup" and fu.status == "answered" and fu.answer.startswith("At 5x")
    assert tgfake.edits[-1]["id"] == fu.message_id and tgfake.edits[-1]["text"].endswith(f"<i>{assist.FOOTER}</i>")


def test_an_invented_price_is_refused_twice_and_the_fallback_goes_out(client: TestClient, db: Session, md: FakeMD, token: str,
                                                                     tgfake: TG) -> None:
    llm = LLM(lambda f, p: f"Risk {f['risk_score']}/10. BTC is heading to 123,456 by Friday, liquidation at 66,666.")
    svc, sess = worker(client, llm)
    check_in(svc, db)
    ask(db, TG(), "/help i long btc at 81k 10x")
    assert svc.step() == 0.0 and not sess.errors
    assert len(llm.prompts) == 2 and "was rejected (ungrounded_numbers" in llm.prompts[1]  # the strict retry, then give up
    assert svc.stats["invalid"] == 1 and svc.stats["failed"] == 1 and svc.stats["answered"] == 0
    assert ("POST", f"/api/vortex/jobs/{jobs(db)[0].public_id}/fail", 200) in sess.calls
    (j,) = jobs(db)
    assert j.status == "failed" and tgfake.edits[0]["text"] == j.fallback and "Trade check · $BTC long" in j.fallback
    assert "123" not in tgfake.edits[0]["text"]


def test_a_wrong_token_is_an_auth_error_and_takes_no_job(client: TestClient, db: Session, md: FakeMD, token: str, tgfake: TG) -> None:
    good, _ = worker(client, LLM(lambda f, p: grounded_answer(f)))
    check_in(good, db)
    ask(db, TG(), "/help i long btc at 81k 10x")
    llm = LLM(lambda f, p: grounded_answer(f))
    bad, sess = worker(client, llm, tok="vx_" + "wrong000" * 5)
    assert bad.step() == AUTH_BACKOFF_S
    assert bad.auth_failed and bad.stats["auth_errors"] == 1 and bad.stats["claims"] == 0 and llm.prompts == []
    assert sess.calls == [("POST", "/api/vortex/claim", 401)] and not sess.errors
    (j,) = jobs(db)
    assert j.status == "pending" and j.claims == 0 and tgfake.edits == []
    assert good.step() == 0.0 and jobs(db)[0].status == "answered"  # the right token still gets it


def test_expiry_sends_the_fallback_and_a_late_answer_is_a_409_the_worker_shrugs_off(client: TestClient, db: Session, md: FakeMD,
                                                                                   token: str, tgfake: TG) -> None:
    llm = LLM(lambda f, p: grounded_answer(f))
    svc, sess = worker(client, llm)
    check_in(svc, db)

    # unclaimed past 60 s: never handed out, the expiry job edits the placeholder into the fallback
    ask(db, TG(), "/help i long btc at 81k 10x", user=1)
    (j,) = jobs(db)
    j.created_at = utcnow() - timedelta(seconds=61)
    db.commit()
    assert svc.step() == 0.0 and svc.stats["jobs"] == 0 and llm.prompts == []
    tg = TG()
    assert run(assist.expire(db, tg, utcnow())) == 1
    (j,) = jobs(db)
    assert j.status == "expired" and tg.edits[0]["text"] == j.fallback

    # claimed in time, but the model is slow: the backend expires it meanwhile, the answer comes back 409 → "late", no crash
    ask(db, TG(), "/help i long btc at 81k 10x", user=2)
    late = jobs(db)[-1]
    expiry_tg = TG()

    def slow(f: dict[str, Any], prompt: str) -> str:
        with SessionLocal() as s:  # 61 s pass while the model thinks; the backend's expiry job runs
            job = assist.find(s, late.public_id)
            job.created_at = utcnow() - timedelta(seconds=61)
            s.commit()
            assert run(assist.expire(s, expiry_tg, utcnow())) == 1
        return grounded_answer(f)
    llm.write = slow
    assert svc.step() == 0.0
    assert svc.stats["late"] == 1 and svc.stats["answered"] == 0 and svc.stats["errors"] == 0 and not sess.errors
    assert ("POST", f"/api/vortex/jobs/{late.public_id}/answer", 409) in sess.calls
    late = jobs(db)[-1]
    assert late.status == "expired" and expiry_tg.edits[0]["text"] == late.fallback and tgfake.edits == []
