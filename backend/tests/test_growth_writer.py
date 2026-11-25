"""Growth Engine writer + figures gate: placeholder substitution, refusal of any figure / sign / comparative / forecast the Finding doesn't
support, the template fallback, and the intel router's growth.write job kind (validation, PC output check, Railway fallback = rules
without a key). All findings here are built from SYNTHETIC series."""

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.growth import gate, writer
from app.growth.detectors import extremes
from app.growth.findings import Finding
from app.growth.memory import Memory
from app.intel import channel
from app.intel import router as intel_router
from tests.test_growth_detectors import AS_OF, crash_tail, walk


@pytest.fixture
def finding() -> Finding:
    mem = Memory.from_points({"px:BTC:close": crash_tail(walk(1000), 30, -0.40)})
    return extremes.rarity_move(mem, AS_OF, ("BTC",))[0]


def test_template_text_passes(finding: Finding) -> None:
    text, v = writer.template_text(finding)
    assert v == 0 and text == finding.text(0) and gate.problems(text, finding) == []
    assert len(text) <= 280


@pytest.mark.parametrize("bad, why", [
    ("Bitcoin −41.0% in 30 days.", "isn't in the finding"),  # a figure the finding doesn't have
    ("Bitcoin +40.0% in 30 days.", "plus sign"),  # sign flipped
    ("Bitcoin is up 40.0% over the past 30 days.", "calls it a rise"),  # direction flipped on the unsigned figure
    ("Bitcoin −40.0% in 30 days, the worst ever.", "comparative"),  # unsupported superlative
    ("Bitcoin −40.0% in 30 days. A bounce will follow.", "forecast"),
    ("Bitcoin −40.0% in 30 days. Buy the dip.", "advice"),
    ("Bitcoin −40.0% in 30 days, worst since January.", "month"),
    ("Bitcoin −40.0% in 30 days, twice the usual.", "number word"),
    ("Bitcoin −40.0% in 30 days https://example.com", "link"),
    ("$BTC $ETH −40.0% in 30 days", "cashtag"),
])
def test_gate_refuses(finding: Finding, bad: str, why: str) -> None:
    probs = gate.problems(bad, finding)
    assert probs, bad
    assert any(why in p for p in probs), probs


def test_gate_length(finding: Finding) -> None:
    assert any("characters" in p for p in gate.problems("Bitcoin " + "x" * 300, finding))


def test_prompt_has_placeholders_not_figures(finding: Finding) -> None:
    p = writer.build(finding)
    pm = writer.placeholder_map(finding)
    assert p["schema"]["required"] == ["text"] and p["text"] == p["user"]
    assert "{" + pm["move"] + "}" in p["user"] and "NEVER type a digit" in p["system"]
    example = p["user"].split("Example (same facts")[1]
    assert finding.numbers["move"]["text"] not in example  # the example uses placeholders


def test_substitute_uses_placeholders_verbatim(finding: Finding) -> None:
    pm = writer.placeholder_map(finding)
    raw = f"{{asset}}".replace("{asset}", "Bitcoin") + f" fell {{{pm['move_abs']}}} in {{{pm['days']}}} days. Only {{{pm['share']}}} of {{{pm['days']}}}-day windows in Hyperliquid data since {{{pm['since']}}} were this bad."
    text, probs = writer.substitute(finding, raw)
    assert probs == [] and text is not None
    assert "40.0%" in text and "{" not in text


def test_substitute_refuses_typed_digits_and_unknown_placeholders(finding: Finding) -> None:
    pm = writer.placeholder_map(finding)
    text, probs = writer.substitute(finding, f"Bitcoin {{{pm['move']}}} in 30 days.")
    assert text is None and "digits outside" in probs[0]
    text, probs = writer.substitute(finding, "Bitcoin {v99} in a month.")
    assert text is None and "unknown placeholders" in probs[0]
    text, probs = writer.substitute(finding, f"Bitcoin {{{pm['move']}}} this month, the worst ever.")
    assert text is None and any("comparative" in p for p in probs)


def test_template_fallback_skips_a_failing_variant(finding: Finding) -> None:
    finding.templates.insert(0, "Bitcoin {move}, after 99% of days were calm.")  # 99% isn't a figure of this finding
    text, v = writer.template_text(finding)
    assert v == 1 and text == finding.text(1)


def test_parse_output() -> None:
    assert writer.parse_output({"text": "x"}) == "x" and writer.parse_output('{"text": "y"}') == "y"
    assert writer.parse_output([1]) is None and writer.parse_output("nope") is None


# --- the intel router's growth.write kind ----------------------------------------------------------------------------------------------


def test_growth_write_is_an_allowed_kind_and_validated(db: Session, finding: Finding, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "growth.write" in channel.KINDS
    s = get_settings()
    monkeypatch.setattr(s, "intel_enabled", True)
    with pytest.raises(ValueError):
        intel_router.enqueue(db, "growth.write", {"text": "x"}, "bulk", settings=s)  # no prompt / schema
    job = intel_router.enqueue(db, "growth.write", writer.build(finding), "bulk", key="growth:1:1", settings=s)
    assert job is not None and job.route == "pc" and job.kind == "growth.write"
    offered = channel.offer(job, "n" * 32, s)
    assert offered["system"] == writer.SYSTEM and offered["schema"] == writer.SCHEMA and "{v" in offered["user"]
    assert channel.validate_output("growth.write", {"text": "Bitcoin {v1}"}) == {"text": "Bitcoin {v1}"}
    for bad in ({"text": ""}, {"nope": 1}, "text", {"text": "x" * 1001}):
        with pytest.raises(channel.ChannelError):
            channel.validate_output("growth.write", bad)


def test_growth_write_off_when_intel_off(db: Session, finding: Finding) -> None:
    assert get_settings().intel_enabled is False
    assert intel_router.enqueue(db, "growth.write", writer.build(finding), "bulk") is None


async def test_growth_write_fallback_is_rules_without_a_key(db: Session, finding: Finding, monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "intel_enabled", True)
    job = intel_router.enqueue(db, "growth.write", writer.build(finding), "slot", settings=s)
    assert job is not None and job.route == "cloud"  # the PC is offline
    out = await intel_router.fallback(db, job, settings=s)
    assert out is not None and out.result_by == "rules" and out.output is None and "no key" in (out.reason or "")


async def test_growth_write_cloud_answer_is_stored(db: Session, finding: Finding, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.intel import anthropic_client

    s = get_settings()
    monkeypatch.setattr(s, "intel_enabled", True)
    calls: list[dict[str, Any]] = []

    async def fake(db_: Any, system: str, user: str, schema: dict[str, Any], **kw: Any) -> tuple[dict[str, Any], str, float]:
        calls.append({"system": system, "purpose": kw.get("purpose")})
        return {"text": "Bitcoin {v1}."}, "claude-haiku-5-5", 0.0001

    monkeypatch.setattr(anthropic_client, "write_json", fake)
    job = intel_router.enqueue(db, "growth.write", writer.build(finding), "slot", settings=s)
    assert job is not None
    out = await intel_router.fallback(db, job, settings=s)
    assert out is not None and out.result_by == "cloud" and out.output == {"text": "Bitcoin {v1}."}
    assert calls and calls[0]["purpose"] == "growth.write" and calls[0]["system"] == writer.SYSTEM


def test_anthropic_parse_json() -> None:
    from app.intel import anthropic_client

    ok = {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"text": "a"}'}]}
    assert anthropic_client.parse_json(ok) == {"text": "a"}
    for bad in ({"stop_reason": "refusal"}, {"stop_reason": "end_turn", "content": [{"type": "text", "text": "[1]"}]}):
        with pytest.raises(anthropic_client.CloudUnavailable):
            anthropic_client.parse_json(bad)


def test_finding_roundtrip_keeps_dates(finding: Finding) -> None:
    d = finding.to_dict()
    assert d["as_of"] == AS_OF.isoformat() and d["publishable"] is True and d["attribution"] == ["Data: Hyperliquid"]
    back = Finding.from_dict(d)
    assert back.as_of == AS_OF and back.numbers == finding.numbers
    assert (AS_OF - timedelta(days=1)) < back.as_of
