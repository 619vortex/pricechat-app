"""Feed licences (owner, 2026-11-24: only commercially permitted feeds). Every feed the desk reads has a class from the terms audit; the
"compliant" policy reads only permitted ones; the default is unchanged until the owner chooses."""
from app.newsdesk import sources as s

ALL = [*s.DEFAULT_FEEDS, *s.EXTRA_FEEDS, *s.WORLDMONITOR_FEEDS]


def test_every_feed_the_desk_reads_has_a_licence_class() -> None:
    assert not {f["name"] for f in ALL} - set(s.FEED_LICENCE)
    assert {f["name"] for f in s.COMPLIANT_FEEDS} <= {n for n, c in s.FEED_LICENCE.items() if c == s.PERMITTED}


def test_the_default_policy_is_unchanged() -> None:
    assert s.feeds_for(list(s.DEFAULT_FEEDS), {}) == s.feeds_for(list(s.DEFAULT_FEEDS), {"feeds_policy": "all"})
    assert not {f["url"] for f in s.COMPLIANT_FEEDS} & {f["url"] for f in s.feeds_for(list(s.DEFAULT_FEEDS), {})}


def test_the_compliant_policy_reads_only_permitted_feeds() -> None:
    stored = [*s.DEFAULT_FEEDS, {"name": "Some Blog", "url": "https://blog.example/rss"}]
    got = s.feeds_for(stored, {"feeds_policy": "compliant"})
    assert got and all(s.licence_of(f) == s.PERMITTED for f in got)
    names = {f["name"] for f in got}
    assert {"Federal Reserve", "SEC", "UN News", "HM Treasury", "US Justice Dept"} <= names
    assert not names & {"CNBC", "Bloomberg", "The Guardian", "CoinDesk", "BBC", "Some Blog"}  # an unchecked stored feed waits for its check
    assert len({f["url"] for f in got}) == len(got)


def test_an_unknown_outlet_is_not_assumed_permitted() -> None:
    assert s.licence_of({"name": "Brand New Outlet"}) == s.UNCLEAR
