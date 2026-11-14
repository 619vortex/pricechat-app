"""X refuses (403) an API post with more than one cashtag (owner, 2026-11-14): the X text keeps only its first cashtag, says the same
thing, and still goes through every factual check."""
from app.newsdesk import checks


def test_second_tag_after_its_name_is_dropped():
    t = "Deutsche Bank's Bitcoin $BTC and Ether $ETH custody is open to clients, bank says."
    assert checks.one_cashtag(t) == "Deutsche Bank's Bitcoin $BTC and Ether custody is open to clients, bank says."


def test_tag_standing_for_the_name_becomes_the_symbol():
    t = "Market check: $BTC -2.1% · $ETH -3.4% · $SOL -5.0%"
    out = checks.one_cashtag(t)
    assert out == "Market check: $BTC -2.1% · ETH -3.4% · SOL -5.0%"
    assert checks.cashtag_count(out) == 1


def test_repeat_of_the_same_tag_keeps_only_the_first():
    out = checks.one_cashtag("Bitcoin $BTC breaks below $70,000. $BTC is down 5% today.")
    assert checks.cashtag_count(out) == 1
    assert out.startswith("Bitcoin $BTC breaks below $70,000.")
    assert "BTC is down 5% today." in out


def test_dollar_amounts_and_single_tags_untouched():
    for t in ("SpaceX-Tesla talks put 30,000 bitcoin $BTC back in focus, worth $2.1 billion.", "Oil rises 3% to $82 a barrel.", ""):
        assert checks.one_cashtag(t) == t


def test_one_cashtag_never_adds_a_claim():
    src = "Deutsche Bank said its bitcoin and ether custody is open to clients."
    t = "Deutsche Bank's Bitcoin $BTC and Ether $ETH custody is open to clients, bank says."
    out = checks.one_cashtag(t)
    assert set(out.split()) - set(t.replace("$ETH", "").split()) == set()
    assert checks.final_gate(out, src) == []


def test_final_gate_still_holds_after_the_fix():
    # an unsupported ticker kept as the first tag is still refused; an invented figure still is
    src = "Ether custody is open to clients, Deutsche Bank says."
    assert checks.final_gate(checks.one_cashtag("Dogecoin $DOGE and Ether $ETH custody opens"), src)
    assert checks.final_gate(checks.one_cashtag("Ether $ETH custody opens to 4,000 clients, Deutsche Bank says."), src)


def test_x_words_applies_the_limit(monkeypatch):
    from types import SimpleNamespace

    from app.newsdesk import engine, network

    pkg = {"x_text": "Bitcoin $BTC and Ether $ETH fall as liquidations hit $1 billion. https://example.com/a",
           "links": [{"url": "https://example.com/a", "name": "The Block", "kind": "source"}]}
    monkeypatch.setattr(network, "stored", lambda ev: pkg)
    monkeypatch.setattr(network, "source_text_of", lambda ev: "Bitcoin and ether fall as liquidations hit $1 billion")
    ev = SimpleNamespace(facts={}, kind="news")
    text, links, _src, _names = engine.x_words(ev, None, [], ["The Block"])
    assert checks.cashtag_count(text) == 1
    assert text.startswith("Bitcoin $BTC and Ether fall")
    assert links == ["https://example.com/a"]
