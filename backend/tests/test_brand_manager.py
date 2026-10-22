"""Brand Manager. TEST FIXTURES only: a fake TikTok API (httpx.MockTransport), made-up topics and a tiny fake MP4.
No real TikTok account, no real network, no real video is published anywhere."""

import io
import json
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brand_manager import brands, discovery, engine, queue, safety
from app.brand_manager.discovery import Topic
from app.db.base import utcnow
from app.models.brand_manager import BmAccount, BmJob, BmVideo
from app.security.credentials import secret_store

P = "/api/brand-manager"


_CURRENT: list["FakeTikTok"] = []


class FakeTikTok:
    """TikTok's OAuth + Content Posting + Display endpoints, in memory."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.status: dict[str, dict[str, Any]] = {}
        self.videos: list[dict[str, Any]] = []
        self.privacy = ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"]
        self.refresh_fails = False
        self.n = 0
        self.challenge: str | None = None  # from the authorize URL, as TikTok remembers it

    def transport(self) -> httpx.MockTransport:
        def handle(r: httpx.Request) -> httpx.Response:
            path = r.url.path
            body: dict[str, Any] = {}
            if r.content:
                body = json.loads(r.content) if r.headers.get("content-type", "").startswith("application/json") else {k: v[0] for k, v in parse_qs(r.content.decode()).items()}
            self.calls.append((path, body))
            if path == "/v2/oauth/token/":
                if body.get("grant_type") == "refresh_token" and self.refresh_fails:
                    return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Refresh token is invalid or expired."})
                assert body["client_secret"] == "tt-secret"
                if body.get("grant_type") == "authorization_code":
                    import hashlib

                    verifier = body.get("code_verifier") or ""
                    if not self.challenge or hashlib.sha256(verifier.encode()).hexdigest() != self.challenge:
                        return httpx.Response(400, json={"error": "invalid_request", "error_description": "code_verifier mismatch"})
                    assert body["redirect_uri"] == "http://127.0.0.1:5173/api/brand-manager/tiktok/callback"
                return httpx.Response(200, json={"access_token": f"act.{self.n}", "expires_in": 86400, "open_id": "open-1", "refresh_token": "rft.1",
                                                 "refresh_expires_in": 31536000, "scope": "user.info.basic,user.info.stats,video.list,video.upload,video.publish",
                                                 "token_type": "Bearer"})
            if path == "/v2/oauth/revoke/":
                return httpx.Response(200, json={})
            assert r.headers["Authorization"].startswith("Bearer act.")
            ok = {"code": "ok", "message": "", "log_id": "x"}
            if path == "/v2/user/info/":
                return httpx.Response(200, json={"data": {"user": {"open_id": "open-1", "username": "pricechat", "display_name": "Price Chat",
                                                                   "avatar_url": "https://p16.tiktok.test/a.jpg", "follower_count": 1200,
                                                                   "is_verified": False, "video_count": 3}}, "error": ok})
            if path == "/v2/post/publish/creator_info/query/":
                return httpx.Response(200, json={"data": {"creator_nickname": "Price Chat", "creator_username": "pricechat", "privacy_level_options": self.privacy,
                                                          "comment_disabled": False, "duet_disabled": True, "stitch_disabled": False,
                                                          "max_video_post_duration_sec": 600}, "error": ok})
            if path in ("/v2/post/publish/inbox/video/init/", "/v2/post/publish/video/init/"):
                self.n += 1
                pid = f"v_pub_{self.n}"
                if path == "/v2/post/publish/video/init/" and body["post_info"]["privacy_level"] != "SELF_ONLY":
                    return httpx.Response(403, json={"error": {"code": "unaudited_client_can_only_post_to_private_accounts", "message": "unaudited"}})
                self.status[pid] = {"status": "PROCESSING_DOWNLOAD"}
                return httpx.Response(200, json={"data": {"publish_id": pid}, "error": ok})
            if path == "/v2/post/publish/status/fetch/":
                return httpx.Response(200, json={"data": self.status[body["publish_id"]], "error": ok})
            if path == "/v2/video/list/":
                return httpx.Response(200, json={"data": {"videos": self.videos, "has_more": False, "cursor": 0}, "error": ok})
            return httpx.Response(404, json={"error": {"code": "not_found", "message": path}})
        return httpx.MockTransport(handle)


@pytest.fixture
def tt(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> FakeTikTok:
    runtime = client.app.state.runtime  # type: ignore[attr-defined]
    fake = FakeTikTok()
    monkeypatch.setattr(runtime, "bm_transport", fake.transport(), raising=False)
    store = secret_store(runtime.settings)
    store.set("tiktok_client_key", "tt-key")
    store.set("tiktok_client_secret", "tt-secret")
    _CURRENT[:] = [fake]
    yield fake
    for k in ("tiktok_client_key", "tiktok_client_secret", "bm_tiktok_1_access", "bm_tiktok_1_refresh"):
        store.delete(k)


def _connect(client: TestClient, tt: "FakeTikTok | None" = None) -> None:
    url = client.post(f"{P}/tiktok/connect").json()["url"]
    q = parse_qs(urlparse(url).query)
    assert q["code_challenge_method"] == ["S256"] and len(q["code_challenge"][0]) == 64
    fake = tt or _CURRENT[0]
    fake.challenge = q["code_challenge"][0]
    assert q["client_key"] == ["tt-key"] and "video.upload" in q["scope"][0] and q["redirect_uri"][0].endswith("/api/brand-manager/tiktok/callback")
    r = client.get(f"{P}/tiktok/callback", params={"code": "auth-code", "state": q["state"][0]}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/brand-manager/tiktok?connected=1")


TOPIC = Topic("crypto_market", "Bitcoin is up 3.2% in 24 hours", "k-btc-1", 1.5, {
    "headline": "Bitcoin is up 3.2% in 24 hours", "coin": {"id": "bitcoin", "name": "Bitcoin", "symbol": "BTC"},
    "facts": [{"text": "Bitcoin (BTC) price: $67,412 (CoinGecko).", "source": "CoinGecko"}, {"text": "24-hour change: +3.20%.", "source": "CoinGecko"}],
    "numbers": {"price_usd": 67412.33, "change_24h_pct": 3.2}, "images": [], "sources": [{"name": "CoinGecko", "url": "https://www.coingecko.com/en/coins/bitcoin"}]})

GOOD_SCRIPT = {
    "hook": "Bitcoin just jumped.",
    "voiceover": ("Bitcoin just jumped. It's up 3.2 percent in the last 24 hours, trading around $67,400 according to CoinGecko. "
                  "That's a clean move after a quiet week, and traders noticed. Here's the thing: big round numbers get attention, but "
                  "the move itself is what matters today. Follow Price Chat for daily market news."),
    "title": "Bitcoin up 3.2% today", "description": "Bitcoin is up 3.2% in 24 hours (CoinGecko).", "hashtags": ["#bitcoin", "crypto"],
    "storyboard": [{"scene": "hook", "text": "Bitcoin +3.2%"}], "sources": [{"name": "CoinGecko"}], "engine": "test",
    "segments": [{"scene": "hook", "text": "Bitcoin just jumped."}]}


def _mp4(seconds: float = 30.0) -> tuple[bytes, dict[str, Any]]:
    import hashlib

    data = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4000  # TEST FIXTURE: an MP4 header, not a real video
    return data, {"duration": seconds, "width": 1080, "height": 1920, "fps": 30.0, "has_audio": True, "subtitles": True,
                  "sha256": hashlib.sha256(data).hexdigest(), "tts": "test", "llm": "test"}


def _worker(client: TestClient, name: str = "spark") -> dict[str, str]:
    r = client.post(f"{P}/workers", json={"name": name}).json()
    assert r["token"].startswith("pcw_") and "--token" in r["command"]
    return {"Authorization": f"Bearer {r['token']}"}


# --- safety ----------------------------------------------------------------------------------------------------------------


def test_script_checks_catch_invented_numbers_and_financial_claims() -> None:
    st = brands.defaults.merged({})
    assert safety.check(GOOD_SCRIPT, TOPIC.brief, st) == []  # "$67,400" rounds the brief's 67,412.33; "3.2" is in it
    bad = {**GOOD_SCRIPT, "voiceover": GOOD_SCRIPT["voiceover"] + " Analysts say it will hit $100,000 next month, guaranteed."}
    problems = safety.check(bad, TOPIC.brief, st)
    assert any("100000" in p for p in problems) and any("guaranteed" in p and "will hit" in p for p in problems)
    assert any("Banned" in p for p in safety.check({**GOOD_SCRIPT, "voiceover": GOOD_SCRIPT["voiceover"] + " Join the presale."}, TOPIC.brief, st))
    assert any("Too short" in p for p in safety.check({**GOOD_SCRIPT, "voiceover": "Bitcoin is up. " * 4}, TOPIC.brief, st))
    assert safety.numbers("$67K, 3.2%, 1.5 million") == [67000.0, 3.2, 1500000.0]


def test_claims_the_research_does_not_make_are_sent_back() -> None:
    st = brands.defaults.merged({})
    rare = {**GOOD_SCRIPT, "voiceover": GOOD_SCRIPT["voiceover"] + " It's one of only a few in this condition, a record."}
    problems = safety.check(rare, TOPIC.brief, st)
    assert any("one of only" in p and "record" in p for p in problems)
    said = {**TOPIC.brief, "facts": [*TOPIC.brief["facts"], {"text": "A record sale for the set.", "source": "x"}]}
    assert not any("claims aren't" in p for p in safety.check({**GOOD_SCRIPT, "voiceover": GOOD_SCRIPT["voiceover"] + " A record."}, said, st))


def test_hashtags_mix_brand_story_and_niche_tags() -> None:
    from app.brand_manager import hashtags

    st = brands.defaults.merged({})
    card = {"headline": "Charizard ex PSA 10 sold for $1,200", "card": {"name": "Charizard ex"}, "facts": [{"text": "Charizard ex PSA 10 sold."}]}
    tags = hashtags.build(["#Charizard EX!!", "#fyp", "#tothemoon", "#pokemonInvesting"], st, "pokemon_rare", card)
    assert tags == ["#pricechat", "#charizard", "#psa10", "#charizardex", "#pokemoncards"]
    # a brand that still has the first defaults saved gets the new ones (no #crypto on Pokémon videos)
    old = brands.defaults.merged({"hashtags": {"always": ["#crypto", "#pricechat"], "max": 5}})
    assert old["hashtags"]["always"] == ["#pricechat"]
    assert hashtags.build([], {**st, "hashtags": {"always": [], "per_category": {}, "max": 2}}, "crypto_market", TOPIC.brief) == ["#bitcoin", "#btc"]


def test_rss_parsing_refuses_entity_tricks() -> None:
    feed = ("<?xml version='1.0'?><rss><channel><item><title>SEC approves something</title><link>https://news.test/a</link>"
            "<pubDate>Sat, 11 Oct 2026 10:00:00 GMT</pubDate><description>&lt;p&gt;Summary here.&lt;/p&gt;</description></item></channel></rss>")
    items = discovery.parse_feed(feed, "News")
    assert items[0]["title"] == "SEC approves something" and items[0]["summary"] == "Summary here."
    assert discovery.parse_feed("<!DOCTYPE x [<!ENTITY a 'aaaa'>]><rss/>", "News") == []


# --- TikTok connection + START AUTO ---------------------------------------------------------------------------------------


def test_start_auto_explains_exactly_what_blocks_it(client: TestClient) -> None:
    r = client.post(f"{P}/auto/start", json={}).json()
    assert r["started"] is False and r["running"] is False
    assert r["blocking"][0].startswith("The TikTok app isn't set up")
    assert any("Confirm that Brand Manager may upload" in b for b in r["blocking"])
    o = client.get(f"{P}/overview").json()
    assert o["brand"]["name"] and o["automation"]["status"] == "stopped" and [s["key"] for s in o["pipeline"]][:3] == ["discover", "research", "script"]


def test_oauth_tokens_are_encrypted_refreshed_and_revoked(client: TestClient, db: Session, tt: FakeTikTok) -> None:
    _connect(client)
    acc = client.get(f"{P}/tiktok").json()
    assert acc["status"] == "connected" and acc["username"] == "pricechat" and acc["can_upload_drafts"] and acc["can_direct_post"]
    assert acc["profile"]["follower_count"] == 1200
    row = db.scalar(select(BmAccount))
    assert "act." not in json.dumps({c.name: str(getattr(row, c.name)) for c in BmAccount.__table__.columns})  # tokens never in the DB
    assert secret_store(client.app.state.runtime.settings).get("bm_tiktok_1_access") is not None  # type: ignore[attr-defined]
    # expiry: the next call refreshes with the refresh token
    db.refresh(row)
    row.access_expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert client.post(f"{P}/tiktok/refresh").status_code == 200
    assert any(b.get("grant_type") == "refresh_token" for p, b in tt.calls if p == "/v2/oauth/token/")
    # revoked refresh token: marked expired, and START AUTO says so
    db.refresh(row)
    row.access_expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    tt.refresh_fails = True
    assert client.post(f"{P}/tiktok/refresh").status_code == 400
    blocking = client.post(f"{P}/auto/start", json={"consent": True}).json()["blocking"]
    assert blocking[0].startswith("TikTok isn't connected") and "Reconnect TikTok" in blocking[0]
    tt.refresh_fails = False
    assert client.post(f"{P}/tiktok/disconnect").json()["status"] == "disconnected"
    assert ("/v2/oauth/revoke/", {"client_key": "tt-key", "client_secret": "tt-secret", "token": tt.calls[-1][1].get("token")}) in tt.calls or \
        any(p == "/v2/oauth/revoke/" for p, _ in tt.calls)


# --- the whole pipeline -----------------------------------------------------------------------------------------------------


def _start(client: TestClient, monkeypatch: pytest.MonkeyPatch, topics: list[Topic]) -> None:
    async def fake_discover(db: Session, brand_id: int, strategy: dict, perf: Any = None, transport: Any = None, now: Any = None, **kw: Any) -> list[Topic]:
        recent = discovery.recent_videos(db, brand_id, 14, utcnow())
        return discovery.rank([Topic(t.category, t.topic, t.key, t.score, dict(t.brief)) for t in topics], strategy, recent, perf)

    monkeypatch.setattr(discovery, "discover", fake_discover)
    r = client.post(f"{P}/auto/start", json={"consent": True}).json()
    assert r["started"] is True and r["status"] == "running", r


def test_start_auto_plans_renders_checks_and_sends_to_drafts(client: TestClient, db: Session, tt: FakeTikTok, monkeypatch: pytest.MonkeyPatch) -> None:
    _connect(client)
    topics = [TOPIC, Topic("facts", "5 facts", "k-facts", 0.8, {"headline": "5 facts", "facts": [{"text": "Bitcoin's supply is capped at 21 million.", "source": "x"}],
                                                                "numbers": {}, "images": [], "sources": [{"name": "bitcoin.org", "url": "https://bitcoin.org"}]})]
    _start(client, monkeypatch, topics)
    client.post(f"{P}/auto/tick")
    vids = list(db.scalars(select(BmVideo).order_by(BmVideo.id)))
    assert len(vids) == 2 and {v.topic_key for v in vids} == {"k-btc-1", "k-facts"}  # 3 slots a day, 2 fresh topics: no duplicates
    assert all(v.stages["discover"]["status"] == "done" and v.stages["research"]["status"] == "done" for v in vids)
    assert db.scalar(select(BmJob).where(BmJob.video_id == vids[0].id)).status == "queued"
    client.post(f"{P}/auto/tick")
    assert len(list(db.scalars(select(BmVideo)))) == 2  # a second tick doesn't duplicate

    # a worker claims, gets its script rejected for an invented number, fixes it, uploads
    h = _worker(client)
    assert client.post(f"{P}/worker/claim", json={}).status_code == 401
    job = client.post(f"{P}/worker/claim", json={"info": {"tts": ["system"]}}, headers=h).json()
    jid = job["job_id"]
    assert job["brief"]["numbers"]["price_usd"] == 67412.33 and job["brand"]["name"] and job["strategy"]["min_seconds"] == 20
    assert job["strategy"]["voice_name"] == "am_michael" and job["suggested_hashtags"][:3] == ["#pricechat", "#bitcoin", "#btc"]
    assert client.post(f"{P}/worker/jobs/{jid}/progress", json={"stage": "script", "status": "running"}, headers=h).json() == {"ok": True, "cancel": False}
    bad = client.post(f"{P}/worker/jobs/{jid}/script", json={"script": {**GOOD_SCRIPT, "voiceover": GOOD_SCRIPT["voiceover"] + " It could reach $90,000."}}, headers=h).json()
    assert bad["ok"] is False and "90000" in bad["problems"][0]
    ok = client.post(f"{P}/worker/jobs/{jid}/script", json={"script": GOOD_SCRIPT}, headers=h).json()
    assert ok["ok"] and ok["script"]["hashtags"] == ["#pricechat", "#bitcoin", "#btc", "#crypto", "#cryptotrading"]
    assert "Not financial advice." in ok["script"]["description"]
    data, meta = _mp4()
    r = client.post(f"{P}/worker/jobs/{jid}/result", headers=h, files={"video": ("v.mp4", io.BytesIO(data), "video/mp4"),
                                                                       "thumb": ("t.jpg", io.BytesIO(b"\xff\xd8thumb"), "image/jpeg")},
                    data={"meta": json.dumps(meta)})
    assert r.json() == {"ok": True, "status": "ready"}
    v = db.get(BmVideo, job["video_id"])
    db.refresh(v)
    assert v.video_key and v.qc["problems"] == [] and v.stages["quality"]["status"] == "done"
    # the dashboard plays it
    assert client.get(f"{P}/videos/{v.id}/media/video").content == data
    part = client.get(f"{P}/videos/{v.id}/media/video", headers={"Range": "bytes=0-11"})
    assert part.status_code == 206 and part.content == data[:12]

    # its slot comes: uploaded to TikTok drafts (PULL_FROM_URL from our signed URL), never called "published"
    v.slot_at = utcnow() - timedelta(minutes=1)
    db.commit()
    client.post(f"{P}/auto/tick")
    init = next(b for p, b in tt.calls if p == "/v2/post/publish/inbox/video/init/")
    url = init["source_info"]["video_url"]
    assert init["source_info"]["source"] == "PULL_FROM_URL" and "/api/brand-manager/media/" in url
    path = urlparse(url).path
    assert client.get(path).content == data  # TikTok can fetch it without a login…
    assert client.get(path.replace("/video.mp4", "/thumb.jpg").replace(path.split("/")[-2], "0" * 40)).status_code == 404  # …but not forged
    db.refresh(v)
    assert v.status == "publishing"
    tt.status[v.publish_id] = {"status": "SEND_TO_USER_INBOX"}
    client.post(f"{P}/auto/tick")
    db.refresh(v)
    assert v.status == "sent_to_drafts" and v.published_at is None and v.post_id is None
    tt.status[v.publish_id] = {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [7300000000001]}
    client.post(f"{P}/auto/tick")
    db.refresh(v)
    assert v.status == "published" and v.post_id == "7300000000001"

    # analytics: TikTok's own counts only
    tt.videos = [{"id": "7300000000001", "title": "Bitcoin up 3.2% today", "create_time": int(utcnow().timestamp()), "view_count": 5400,
                  "like_count": 300, "comment_count": 12, "share_count": 9, "share_url": "https://tiktok.test/v/1"}]
    a = client.post(f"{P}/analytics/collect").json()
    assert a["result"]["matched"] == 1 and a["insights"]["best_video"]["views"] == 5400
    o = client.get(f"{P}/overview").json()
    assert o["stats"]["total_views"] == 5400 and o["stats"]["followers"] == 1200 and o["stats"]["published_today"] == 1


def test_stop_blocks_new_work_and_leases_survive_crashes(client: TestClient, db: Session, tt: FakeTikTok, monkeypatch: pytest.MonkeyPatch) -> None:
    _connect(client)
    _start(client, monkeypatch, [TOPIC])
    client.post(f"{P}/auto/tick")
    h = _worker(client)
    job = client.post(f"{P}/worker/claim", json={}, headers=h).json()
    # the worker dies: its lease runs out, the job goes back to the queue
    j = db.get(BmJob, job["job_id"])
    j.lease_until = utcnow() - timedelta(seconds=1)
    db.commit()
    again = client.post(f"{P}/worker/claim", json={}, headers=h).json()
    assert again["job_id"] == job["job_id"]
    db.refresh(j)
    assert j.attempts == 2
    # the first (dead) claim can't report anymore once taken over? it's the same worker here; a different worker can't touch it
    h2 = _worker(client, "cloud")
    assert client.post(f"{P}/worker/jobs/{job['job_id']}/progress", json={"stage": "voice", "status": "running"}, headers=h2).status_code == 409
    # STOP AUTO: nothing new is claimed; the job in progress may finish
    client.post(f"{P}/auto/stop")
    db.refresh(j)
    j.status, j.worker_id = "queued", None
    db.commit()
    assert client.post(f"{P}/worker/claim", json={}, headers=h2).status_code == 204
    # EMERGENCY STOP cancels queued work and START AUTO says why it can't run
    r = client.post(f"{P}/auto/emergency", json={"on": True}).json()
    db.refresh(j)
    assert j.status == "cancelled" and r["status"] == "stopped"
    assert client.post(f"{P}/auto/start", json={}).json()["blocking"][0].startswith("The emergency stop is on")
    client.post(f"{P}/auto/emergency", json={"on": False})


def test_failed_quality_check_retries_then_fails(client: TestClient, db: Session, tt: FakeTikTok, monkeypatch: pytest.MonkeyPatch) -> None:
    _connect(client)
    _start(client, monkeypatch, [TOPIC])
    client.post(f"{P}/auto/tick")
    h = _worker(client)
    for attempt in range(3):
        job = client.post(f"{P}/worker/claim", json={}, headers=h)
        if job.status_code == 204:  # backoff: let it run now
            j = db.scalar(select(BmJob))
            db.refresh(j)
            j.run_after = utcnow()
            db.commit()
            job = client.post(f"{P}/worker/claim", json={}, headers=h)
        jid = job.json()["job_id"]
        client.post(f"{P}/worker/jobs/{jid}/script", json={"script": GOOD_SCRIPT}, headers=h)
        data, meta = _mp4()
        meta["width"] = 1920  # landscape: wrong
        client.post(f"{P}/worker/jobs/{jid}/result", headers=h, files={"video": ("v.mp4", io.BytesIO(data), "video/mp4")}, data={"meta": json.dumps(meta)})
    v = db.scalar(select(BmVideo))
    db.refresh(v)
    assert v.status == "failed" and "1920×1920" in (v.error or "")
    assert any("failed" in e["message"] for e in client.get(f"{P}/events").json()["events"])


def test_direct_posting_needs_tiktoks_posting_screen_and_stays_private_until_audited(client: TestClient, db: Session, tt: FakeTikTok,
                                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    _connect(client)
    client.patch(f"{P}/brand", json={"strategy": {"publish_mode": "direct"}})
    _start(client, monkeypatch, [TOPIC])
    client.post(f"{P}/auto/tick")
    h = _worker(client)
    jid = client.post(f"{P}/worker/claim", json={}, headers=h).json()["job_id"]
    client.post(f"{P}/worker/jobs/{jid}/script", json={"script": GOOD_SCRIPT}, headers=h)
    data, meta = _mp4()
    assert client.post(f"{P}/worker/jobs/{jid}/result", headers=h, files={"video": ("v.mp4", io.BytesIO(data), "video/mp4")},
                       data={"meta": json.dumps(meta)}).json()["status"] == "needs_review"
    v = db.scalar(select(BmVideo))
    v.slot_at = utcnow() - timedelta(minutes=1)
    db.commit()
    client.post(f"{P}/auto/tick")
    db.refresh(v)
    assert v.status == "needs_review" and v.publish_id is None  # not approved: nothing sent
    client.post(f"{P}/tiktok/refresh")  # fresh creator info (privacy options)
    no_choice = client.post(f"{P}/videos/{v.id}/approve", json={"consent": True})
    assert no_choice.status_code == 400 and "Choose who can see" in no_choice.json()["detail"]
    public = client.post(f"{P}/videos/{v.id}/approve", json={"consent": True, "privacy_level": "PUBLIC_TO_EVERYONE"})
    assert public.status_code == 400 and "private" in public.json()["detail"]
    assert client.post(f"{P}/videos/{v.id}/approve", json={"privacy_level": "SELF_ONLY"}).status_code == 400  # music usage consent
    r = client.post(f"{P}/videos/{v.id}/approve", json={"consent": True, "privacy_level": "SELF_ONLY", "allow_duet": True}).json()
    assert r["status"] == "approved" and r["post_settings"]["allow_duet"] is False  # duet is off in the creator's settings
    client.post(f"{P}/auto/tick")
    post = next(b for p, b in tt.calls if p == "/v2/post/publish/video/init/")["post_info"]
    assert post["privacy_level"] == "SELF_ONLY" and post["disable_comment"] is True and post["is_aigc"] is True
    db.refresh(v)
    tt.status[v.publish_id] = {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": []}
    client.post(f"{P}/auto/tick")
    out = client.get(f"{P}/videos/{v.id}").json()
    assert out["status"] == "published" and out["private"] is True and "private" in out["stages"]["publish"]["note"]


def test_settings_validate_and_merge(client: TestClient) -> None:
    assert client.patch(f"{P}/brand", json={"strategy": {"videos_per_day": 50}}).status_code == 400
    assert client.patch(f"{P}/brand", json={"strategy": {"post_times": ["25:00"]}}).status_code == 400
    assert client.patch(f"{P}/brand", json={"strategy": {"timezone": "Mars/Base"}}).status_code == 400
    r = client.patch(f"{P}/brand", json={"name": "Price Chat", "avoid": ["politics"], "strategy": {"videos_per_day": 2, "unknown": 1}}).json()
    assert r["strategy"]["videos_per_day"] == 2 and r["strategy"]["min_seconds"] == 20 and "unknown" not in r["strategy"]
    assert r["strategy"]["watermark"] is False  # TikTok forbids apps adding watermarks: off unless the owner turns it on
    st = client.get(f"{P}/brand").json()
    assert set(st["templates"]) == {"breaking_news", "price_alert", "pokemon_spotlight", "top_facts", "youtube_highlight"}


def _brand_fields_in_frontend() -> list[str]:
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "brand" / "api.ts").read_text(encoding="utf-8")
    block = re.search(r"export const BRAND_FIELDS = \[(.*?)\] as const", src, re.S)
    assert block, "BRAND_FIELDS not found in the frontend api.ts"
    return re.findall(r'"(\w+)"', block.group(1))


def test_settings_page_saves_every_tab_with_the_backend_schema(client: TestClient) -> None:
    """The Settings page sends only BRAND_FIELDS (never the GET's read-only catalogs like `voices`), for every tab."""
    from app.api.routes.brand_manager import BrandIn

    fields = _brand_fields_in_frontend()
    assert set(fields) == set(BrandIn.model_fields)  # the frontend and backend share one settings schema
    got = client.get(f"{P}/brand").json()
    assert got["voices"]["bm_george"] and "voices" not in fields  # a catalog of narrators, not a setting
    assert client.patch(f"{P}/brand", json={"voices": got["voices"]}).status_code == 422  # still refused if ever sent

    def save(change: dict[str, Any], strategy: dict[str, Any] | None = None) -> dict[str, Any]:
        cur = client.get(f"{P}/brand").json()
        body = {k: cur[k] for k in fields}
        body.update(change)
        body["strategy"] = {**cur["strategy"], **(strategy or {})}
        r = client.patch(f"{P}/brand", json=body)
        assert r.status_code == 200, r.text
        return r.json()

    save({}, {"voice_name": "bm_george", "voice_speed": 1.05, "creative": {**got["strategy"]["creative"], "narrator": "documentary"}})  # voice first
    general = save({"name": "Price Chat", "cta": "Follow for daily market stories.", "colors": ["#00e58f", "#050706", "#e6c46e"]})
    content = save({}, {"min_seconds": 25, "max_seconds": 45, "categories": ["pokemon_rare", "crypto_news"]})
    publishing = save({}, {"post_times": ["09:00", "18:30"], "timezone": "Europe/London", "approval_required": True})
    ai = save({}, {"llm": "ollama", "quality": "high"})
    safety_ = save({"avoid": ["politics"]}, {"banned_topics": ["price prediction"], "require_sources": True})
    final = client.get(f"{P}/brand").json()
    assert general["cta"] == final["cta"] == "Follow for daily market stories."
    assert content["strategy"]["max_seconds"] == final["strategy"]["max_seconds"] == 45
    assert publishing["strategy"]["post_times"] == final["strategy"]["post_times"] == ["09:00", "18:30"]
    assert ai["strategy"]["quality"] == final["strategy"]["quality"] == "high"
    assert safety_["avoid"] == final["avoid"] == ["politics"]
    # the voice choices made before survive every later save
    assert final["strategy"]["voice_name"] == "bm_george" and final["strategy"]["voice_speed"] == 1.05
    assert final["strategy"]["creative"]["narrator"] == "documentary"


def test_slots_follow_the_brand_time_zone() -> None:
    from datetime import datetime, timezone

    st = brands.defaults.merged({"timezone": "Asia/Dubai", "post_times": ["10:00", "20:00"], "videos_per_day": 2})
    now = datetime(2026, 10, 11, 5, 0, tzinfo=timezone.utc)  # 09:00 in Dubai
    assert [s.strftime("%H:%M") for s in engine.slots(st, now)] == ["06:00", "16:00"]


def test_spelled_out_numbers_are_checked_too() -> None:
    assert safety.numbers("over fourteen billion dollars, one hundred twenty cards, twenty-five percent; someone done") == [14e9, 120.0, 25.0]
    brief = {"facts": [{"text": "24-hour trading volume: $14,370,000,000."}], "numbers": {}}
    st = brands.defaults.merged({})
    base = {**GOOD_SCRIPT, "voiceover": GOOD_SCRIPT["voiceover"].replace("3.2 percent", "a lot").replace("$67,400 according to CoinGecko", "steady")}
    ok = {**base, "title": "Volume today", "description": "", "storyboard": [], "voiceover": base["voiceover"] + " Over fourteen billion dollars traded."}
    assert not [p for p in safety.check(ok, brief, st) if "invented" in p]  # an honest rounding of a real number
    bad = {**ok, "voiceover": base["voiceover"] + " Sixteen billion dollars traded."}
    assert any("16000000000" in p for p in safety.check(bad, brief, st))


def test_spoken_decimals() -> None:
    assert safety.numbers("three hundred and eighty six point three million dollars; the point is") == [386.3e6]


def test_glitchy_pokemon_moves_are_never_topics() -> None:
    ok = {"change_30d": 24.0, "market_price": 98_500, "sales_30d": 4}
    assert discovery.plausible_move(ok)
    assert not discovery.plausible_move({**ok, "change_30d": 36915.0})  # the "Caterpie +36,915%" glitch
    assert not discovery.plausible_move({**ok, "sales_30d": 1})          # one sale is not a trend
    assert not discovery.plausible_move({**ok, "market_price": 1_200})   # $12 card: noise


def test_bootstrap_worker_registers_on_first_contact_from_its_token_hash(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib

    token = "pcw_" + "x" * 40
    monkeypatch.setattr(client.app.state.runtime.settings, "bm_worker_bootstrap_sha256", hashlib.sha256(token.encode()).hexdigest())  # type: ignore[attr-defined]
    from app.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "bm_worker_bootstrap_sha256", hashlib.sha256(token.encode()).hexdigest())
    assert client.post(f"{P}/worker/claim", json={}, headers={"Authorization": "Bearer pcw_wrong"}).status_code == 401
    assert client.post(f"{P}/worker/claim", json={}, headers={"Authorization": f"Bearer {token}"}).status_code == 204
    workers = client.get(f"{P}/workers").json()["workers"]
    assert [w["name"] for w in workers] == ["This PC (until the DGX Spark)"]
    wid = workers[0]["id"]
    client.delete(f"{P}/workers/{wid}")  # removed by the owner: the hash alone doesn't bring it back
    assert client.post(f"{P}/worker/claim", json={}, headers={"Authorization": f"Bearer {token}"}).status_code == 401



# --- PKCE (TikTok Login Kit: code_challenge = HEX(SHA256(code_verifier)), method S256) -----------------------------------


def test_pkce_pair_follows_tiktoks_rules() -> None:
    import hashlib
    import re

    from app.brand_manager import tiktok

    seen = set()
    for _ in range(50):
        verifier, challenge = tiktok.pkce_pair()
        assert 43 <= len(verifier) <= 128 and re.fullmatch(r"[A-Za-z0-9\-._~]+", verifier)
        assert challenge == hashlib.sha256(verifier.encode()).hexdigest() and re.fullmatch(r"[0-9a-f]{64}", challenge)  # hex, not base64url
        seen.add(verifier)
    assert len(seen) == 50  # a fresh verifier every time


def test_authorize_url_carries_pkce_and_the_registered_redirect(client: TestClient, tt: FakeTikTok) -> None:
    from app.brand_manager import tiktok

    url = client.post(f"{P}/tiktok/connect").json()["url"]
    assert url.startswith("https://www.tiktok.com/v2/auth/authorize/?")
    q = parse_qs(urlparse(url).query)
    assert q["client_key"] == ["tt-key"] and q["response_type"] == ["code"] and q["code_challenge_method"] == ["S256"]
    assert q["scope"] == ["user.info.basic,user.info.profile,user.info.stats,video.list,video.upload"]  # no video.publish by default
    assert q["redirect_uri"] == ["http://127.0.0.1:5173/api/brand-manager/tiktok/callback"]  # {MEMBERS_PUBLIC_URL}/api/brand-manager/tiktok/callback
    assert "tt-secret" not in url  # the secret and the verifier never reach the browser
    pending = tiktok._pending[q["state"][0]]
    assert pending.verifier not in url and q["code_challenge"] == [__import__("hashlib").sha256(pending.verifier.encode()).hexdigest()]


def test_callback_validates_state_session_and_sends_the_verifier(client: TestClient, db: Session, tt: FakeTikTok) -> None:
    client.cookies.set("pc_admin", "session-A")
    url = client.post(f"{P}/tiktok/connect").json()["url"]
    q = parse_qs(urlparse(url).query)
    tt.challenge = q["code_challenge"][0]
    # unknown state
    r = client.get(f"{P}/tiktok/callback", params={"code": "c", "state": "forged"}, follow_redirects=False)
    assert "error=" in r.headers["location"] and "expired" in r.headers["location"]
    # another dashboard session can't finish this sign-in (and the attempt burns the state)
    client.cookies.set("pc_admin", "session-B")
    r = client.get(f"{P}/tiktok/callback", params={"code": "c", "state": q["state"][0]}, follow_redirects=False)
    assert "another dashboard session" in parse_qs(urlparse(r.headers["location"]).query)["error"][0]
    # the right session: the token exchange carries the original verifier, TikTok checks it against the challenge
    client.cookies.set("pc_admin", "session-A")
    url = client.post(f"{P}/tiktok/connect").json()["url"]
    q = parse_qs(urlparse(url).query)
    tt.challenge = q["code_challenge"][0]
    r = client.get(f"{P}/tiktok/callback", params={"code": "c", "state": q["state"][0]}, follow_redirects=False)
    assert r.headers["location"].endswith("connected=1")
    exchange = next(b for p, b in tt.calls if p == "/v2/oauth/token/" and b.get("grant_type") == "authorization_code")
    assert len(exchange["code_verifier"]) == 64 and exchange["redirect_uri"].endswith("/api/brand-manager/tiktok/callback")
    # replaying the same callback fails: state is single-use
    r = client.get(f"{P}/tiktok/callback", params={"code": "c", "state": q["state"][0]}, follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_scopes_are_comma_separated_deduplicated_and_always_include_basic() -> None:
    from app.brand_manager import tiktok
    from app.config.settings import Settings

    assert tiktok.scopes(Settings(tiktok_scopes="user.info.basic")) == ["user.info.basic"]
    assert tiktok.scopes(Settings(tiktok_scopes=" video.upload, video.list ,video.upload")) == ["user.info.basic", "video.upload", "video.list"]
    assert tiktok.scopes(Settings(tiktok_scopes="user.info.basic video.publish")) == ["user.info.basic", "video.publish"]


def test_sandbox_target_error_explains_the_manual_step(client: TestClient, tt: FakeTikTok, caplog: Any) -> None:
    caplog.set_level("INFO", logger="pricechat")
    client.post(f"{P}/tiktok/connect")
    r = client.get(f"{P}/tiktok/callback", params={"error": "non_sandbox_target", "error_description": "Not a sandbox target user & co"},
                   follow_redirects=False)
    msg = parse_qs(urlparse(r.headers["location"]).query)["error"][0]
    assert "Target users" in msg and "Not a sandbox target user & co" in msg  # the & survives: the message is URL-encoded
    logged = caplog.text
    assert "client_key=tt-key" in logged and "redirect_uri=" in logged and "non_sandbox_target" in logged
    assert "state=" not in logged and "code_challenge" not in logged


def test_wrong_verifier_is_refused_by_tiktok(client: TestClient, tt: FakeTikTok) -> None:
    url = client.post(f"{P}/tiktok/connect").json()["url"]
    q = parse_qs(urlparse(url).query)
    tt.challenge = "0" * 64  # TikTok remembers a different challenge
    r = client.get(f"{P}/tiktok/callback", params={"code": "c", "state": q["state"][0]}, follow_redirects=False)
    assert "code_verifier" in r.headers["location"] and client.get(f"{P}/tiktok").json()["status"] != "connected"


def test_tiktok_diagnostics_report_safely(client: TestClient, tt: FakeTikTok) -> None:
    d = client.get(f"{P}/tiktok/diagnostics").json()
    assert d["client_key"] == {"configured": True, "source": "dashboard", "value": "tt-key", "length": 6, "kind_hint": "unknown",
                               "env_variable_set": False, "dashboard_value_saved": True}
    assert d["client_secret"] == {"configured": True, "source": "dashboard", "length": 9} and "tt-secret" not in str(d)  # never the secret
    assert d["pair"]["valid"] is True and d["authorize_endpoint"] == "https://www.tiktok.com/v2/auth/authorize/"
    assert d["redirect_uri"].endswith("/api/brand-manager/tiktok/callback") and "video.upload" in d["scopes"]
    store = secret_store(client.app.state.runtime.settings)  # type: ignore[attr-defined]
    store.set("tiktok_client_key", "tt-secret")  # the secret pasted into the key field
    problems = client.get(f"{P}/tiktok/diagnostics").json()["problems"]
    assert any("same value" in p for p in problems)
    store.set("tiktok_client_key", "123-abc.apps.googleusercontent.com")
    assert any("Google" in p for p in client.get(f"{P}/tiktok/diagnostics").json()["problems"])


# --- Content Studio v2: creative settings, scene edits, re-render with the approved script -----------------------------------


def test_content_studio_v2_rerender_keeps_the_script_and_checks_scene_edits(client: TestClient, db: Session) -> None:
    opts = client.get(f"{P}/creative/options").json()
    assert "documentary" in opts["options"]["narrator"] and opts["defaults"]["engine"] == "editorial"
    b = brands.ensure_default(db)
    v = BmVideo(brand_id=b.id, category=TOPIC.category, template=TOPIC.template, topic=TOPIC.topic, topic_key=TOPIC.key, score=1.0,
                brief=TOPIC.brief, status="ready", stages={}, generation=1, script=dict(GOOD_SCRIPT))
    db.add(v)
    db.commit()
    # settings are validated; an on-screen number must come from the research
    assert client.put(f"{P}/videos/{v.id}/creative", json={"narrator": "robot"}).status_code == 400
    bad = client.put(f"{P}/videos/{v.id}/creative", json={"scenes": {"0": {"screen": "BTC to $90,000"}}})
    assert bad.status_code == 400 and "90000" in bad.json()["detail"]
    assert client.put(f"{P}/videos/{v.id}/creative", json={"scenes": {"5": {"screen": "x"}}}).status_code == 400
    ok = client.put(f"{P}/videos/{v.id}/creative", json={"narrator": "documentary", "scenes": {"0": {"screen": "Bitcoin +3.2%", "visual": "stat"}}})
    assert ok.status_code == 200 and ok.json()["creative"]["scenes"]["0"]["visual"] == "stat"
    # re-render: no new research, no new script; the worker gets the approved script and the creative choices
    r = client.post(f"{P}/videos/{v.id}/rerender", json={"scope": "captions", "creative": {"captions": "boxed"}}).json()
    assert r["generation"] == 2 and r["status"] == "queued" and r["script"]["voiceover"] == GOOD_SCRIPT["voiceover"]
    h = _worker(client)
    job = client.post(f"{P}/worker/claim", json={"info": {"tts": ["kokoro"], "motion": True}}, headers=h).json()
    assert job["mode"] == "rerender" and job["script"]["voiceover"] == GOOD_SCRIPT["voiceover"]
    assert job["creative"]["narrator"] == "documentary" and job["creative"]["captions"] == "boxed" and job["creative"]["engine"] == "editorial"
    assert job["creative"]["scenes"]["0"]["screen"] == "Bitcoin +3.2%"
    data, meta = _mp4()
    meta.update({"renderer": "remotion", "storyboard": [{"kind": "stat", "start": 0, "end": 3, "transition": "cut", "screen": "Bitcoin +3.2%"}],
                 "checks": [{"id": "facts", "label": "No unsupported numbers on screen", "status": "pass", "detail": "ok"}], "timings": {"render": 30.0}})
    res = client.post(f"{P}/worker/jobs/{job['job_id']}/result", headers=h, files={"video": ("v.mp4", io.BytesIO(data), "video/mp4")},
                      data={"meta": json.dumps(meta)})
    assert res.json()["status"] == "ready"
    d = client.get(f"{P}/videos/{v.id}").json()
    assert d["render_info"]["renderer"] == "remotion" and d["render_info"]["storyboard"][0]["kind"] == "stat"
    assert d["render_info"]["checks"][0]["status"] == "pass"
    # a full regenerate still writes a new script (the v1 path is unchanged)
    client.post(f"{P}/videos/{v.id}/regenerate")
    job2 = client.post(f"{P}/worker/claim", json={"info": {}}, headers=h).json()
    assert "mode" not in job2 and "script" not in job2


# --- Content Studio v4: media library with rights records, editable timeline ----------------------------------------------


def test_content_studio_v4_media_library_and_timeline(client: TestClient, db: Session) -> None:
    opts = client.get(f"{P}/creative/options").json()["options"]
    assert "collectibles" in opts["production"] and "none" in opts["narration"] and "editorial" in opts["engine"]
    b = brands.ensure_default(db)
    v = BmVideo(brand_id=b.id, category=TOPIC.category, template=TOPIC.template, topic=TOPIC.topic, topic_key=TOPIC.key, score=1.0,
                brief=TOPIC.brief, status="queued", stages={}, generation=1, script=dict(GOOD_SCRIPT))
    db.add(v)
    db.commit()
    queue.enqueue_render(db, v, manual=True)
    db.commit()
    h = _worker(client)
    job = client.post(f"{P}/worker/claim", json={"info": {"editorial": True}}, headers=h).json()
    assert job["creative"]["engine"] == "editorial" and job["media_library"] == []
    found = [{"id": "a1", "kind": "video", "provider": "commons", "url": "https://upload.wikimedia.org/x.webm", "license": "CC BY 4.0", "page": "https://commons.wikimedia.org/wiki/File:X.webm",
              "attribution": "Video: A · CC BY 4.0", "match": "exact", "status": "ok", "duration": 12.0, "width": 1920, "height": 1080},
             {"id": "a2", "kind": "image", "provider": "openverse", "url": "https://x.org/b.jpg", "license": "CC0", "page": "https://x.org/b", "match": "generic",
              "status": "failed", "note": "doesn't fit the story"}]
    assert client.post(f"{P}/worker/jobs/{job['job_id']}/media", headers=h, json={"assets": found}).json()["assets"] == 2
    lib = client.get(f"{P}/videos/{v.id}/media").json()["assets"]
    assert {a["id"]: a["origin"] for a in lib} == {"a1": "research", "a2": "research"}
    # the owner adds footage: the licence must be usable and they must confirm their rights
    new = {"url": "https://cdn.example.com/clip.mp4", "license": "CC BY-NC 4.0", "creator": "Me", "attest": True}
    assert client.post(f"{P}/videos/{v.id}/media", json=new).status_code == 400
    assert client.post(f"{P}/videos/{v.id}/media", json={**new, "license": "Owned", "attest": False}).status_code == 400
    mine = client.post(f"{P}/videos/{v.id}/media", json={**new, "license": "Owned", "shows": "our own b-roll"}).json()["asset"]
    assert mine["origin"] == "owner" and mine["kind"] == "video" and mine["permitted"]["attribution"] is False
    # the worker's finished video brings the timeline, the authenticity score and the library (the owner's asset survives)
    tl = {"version": 1, "style": "real_news", "duration": 9.4, "end_card": 1.4, "overlays": [],
          "video": [{"id": "c1", "asset": "a1", "start": 0, "dur": 4, "in": 1, "fit": "frame", "focus": [0.5, 0.5], "zoom": 1, "move": "none", "gain": 0.1},
                    {"id": "c2", "asset": mine["id"], "start": 4, "dur": 4, "fit": "fill", "focus": [0.5, 0.5], "zoom": 1, "move": "push", "gain": 0}],
          "captions": {"style": "clean", "words": [{"t": "Bitcoin", "s": 0.2, "e": 0.6, "p": 0}]}, "narration": {"mode": "ai", "gain": 1}, "music": {"track": None, "gain": 0}}
    data, meta = _mp4()
    meta.update({"renderer": "editorial", "timeline": tl, "media_library": found, "authenticity": {"score": 88, "grade": "A", "flags": []}})
    res = client.post(f"{P}/worker/jobs/{job['job_id']}/result", headers=h, files={"video": ("v.mp4", io.BytesIO(data), "video/mp4")},
                      data={"meta": json.dumps(meta)})
    assert res.json()["status"] in ("ready", "needs_review")
    d = client.get(f"{P}/videos/{v.id}").json()
    assert d["timeline"]["video"][1]["asset"] == mine["id"] and d["render_info"]["authenticity"]["score"] == 88
    assert {a["id"] for a in d["media"]} == {"a1", "a2", mine["id"]}
    # edits are validated: known assets only, clips can't run past their end, graphics only show researched numbers
    edit = d["timeline"]
    assert client.put(f"{P}/videos/{v.id}/timeline", json={"timeline": {**edit, "video": [{**edit["video"][0], "asset": "nope"}]}}).status_code == 400
    assert client.put(f"{P}/videos/{v.id}/timeline", json={"timeline": {**edit, "video": [{**edit["video"][0], "in": 10, "dur": 5}]}}).status_code == 400
    bad = client.put(f"{P}/videos/{v.id}/timeline", json={"timeline": {**edit, "overlays": [{"type": "text", "start": 1, "dur": 2, "data": {"text": "BTC to $90,000"}}]}})
    assert bad.status_code == 400 and "90000" in bad.json()["detail"]
    swapped = {**edit, "video": [{**edit["video"][1], "dur": 2.5}, edit["video"][0]],
               "overlays": [{"type": "lower_third", "start": 0.5, "dur": 2, "data": {"name": "Bitcoin", "detail": "Crypto"}}]}
    saved = client.put(f"{P}/videos/{v.id}/timeline", json={"timeline": swapped}).json()["timeline"]
    assert [c["asset"] for c in saved["video"]] == [mine["id"], "a1"] and saved["video"][1]["start"] == 2.5 and saved["duration"] == 7.9
    assert saved["captions"]["words"][0]["t"] == "Bitcoin"
    # an asset in the edit can't be excluded; others can
    assert client.patch(f"{P}/videos/{v.id}/media/a1", json={"status": "excluded"}).status_code == 409
    assert client.patch(f"{P}/videos/{v.id}/media/a2", json={"status": "excluded"}).json()["asset"]["status"] == "excluded"
    # render the edit: the worker gets the saved timeline, the approved script and the library
    r = client.post(f"{P}/videos/{v.id}/rerender", json={"scope": "timeline"}).json()
    assert r["status"] == "queued"
    job2 = client.post(f"{P}/worker/claim", json={"info": {}}, headers=h).json()
    assert job2["mode"] == "timeline" and job2["timeline"]["video"][0]["asset"] == mine["id"] and job2["script"]["voiceover"] == GOOD_SCRIPT["voiceover"]
    assert {a["id"] for a in job2["media_library"]} == {"a1", "a2", mine["id"]}
    # editorial review: not enough real media is a final failure with the reason, not a retry
    client.post(f"{P}/worker/jobs/{job2['job_id']}/fail", headers=h, json={"error": "Needs editorial review: no picture of the exact subject.", "retryable": False})
    assert client.get(f"{P}/videos/{v.id}").json()["status"] == "failed"


def test_safety_reads_currency_codes_glued_to_numbers() -> None:
    assert safety.numbers("worth more than Dh100 billion") == [100e9]
    assert safety.numbers("AED917bn and US$1.7 million") == [917e9, 1.7e6]
    assert safety.numbers("$17,225 in H1 2026") == [17225.0, 2026.0]
    brief = {"facts": [{"text": "Around 104 projects worth more than Dh100 billion were completed in H1 2026.", "source": "DLD"}]}
    assert safety._ok_number(safety.numbers("AED 100 billion")[0], safety._allowed(brief))


def test_fact_card_numbers_pass_the_fact_check_invented_ones_dont() -> None:
    brief = {"headline": "Rocket's Mewtwo PSA 10 sold for $17,225", "numbers": {"sale_usd": 17225.0},
             "facts": [{"text": "Rocket's Mewtwo PSA 10 sold for $17,225 on October 12, 2026.", "source": "eBay"}],
             "chart": {"kind": "sales", "points": [[1776643200, 6750.0], [1778000000, 7600.0], [1780000000, 9000.0], [1786000000, 12000.0],
                                                   [1791763200, 17225.41]]}}
    allowed = safety._allowed(brief)
    for said in ("2.6x", "+160%", "$10K more"):  # computed by the fact card from the brief, recomputed here
        assert all(safety._ok_number(n, allowed) for n in safety.numbers(said)), said
    assert not any(safety._ok_number(n, allowed) for n in safety.numbers("3.4x the April price"))
    assert "highest" in safety._research_text(brief)  # "the highest of 5 recorded sales" is in the card, so it may be said


def test_app_review_flow_choose_destination_fresh_creator_info_post_now_and_check_status(client: TestClient, db: Session, tt: FakeTikTok,
                                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """The flow shown to TikTok's reviewers: pick a finished video, choose drafts or a direct post, confirm on the posting
    screen (creator info read fresh), submit, then ask TikTok for the status."""
    _connect(client)
    _start(client, monkeypatch, [TOPIC])
    client.post(f"{P}/auto/tick")
    h = _worker(client)
    jid = client.post(f"{P}/worker/claim", json={}, headers=h).json()["job_id"]
    client.post(f"{P}/worker/jobs/{jid}/script", json={"script": GOOD_SCRIPT}, headers=h)
    data, meta = _mp4()
    client.post(f"{P}/worker/jobs/{jid}/result", headers=h, files={"video": ("v.mp4", io.BytesIO(data), "video/mp4")}, data={"meta": json.dumps(meta)})
    v = db.scalar(select(BmVideo))
    client.post(f"{P}/videos/{v.id}/approve", json={})  # approved for drafts ...
    r = client.put(f"{P}/videos/{v.id}/destination", json={"mode": "direct"}).json()
    assert r["publish_mode"] == "direct" and r["status"] == "needs_review" and r["approved"] is False  # ... switching takes the approval back
    assert client.post(f"{P}/videos/{v.id}/publish-now").status_code == 409  # a direct post needs the posting screen first
    before = sum(1 for p, _ in tt.calls if p == "/v2/post/publish/creator_info/query/")
    info = client.post(f"{P}/tiktok/creator-info").json()["creator_info"]
    assert info["privacy_level_options"] and sum(1 for p, _ in tt.calls if p == "/v2/post/publish/creator_info/query/") == before + 1
    tt.privacy = ["SELF_ONLY"]  # the account's options changed since: the approval checks what TikTok said last
    client.post(f"{P}/tiktok/creator-info")
    assert client.post(f"{P}/videos/{v.id}/approve", json={"consent": True, "privacy_level": "PUBLIC_TO_EVERYONE"}).status_code == 400
    assert client.post(f"{P}/videos/{v.id}/approve", json={"consent": True, "privacy_level": "SELF_ONLY", "title": "Bitcoin up 3.2%"}).json()["status"] == "approved"
    sent = client.post(f"{P}/videos/{v.id}/publish-now").json()
    assert sent["status"] == "publishing" and next(b for p, b in tt.calls if p == "/v2/post/publish/video/init/")["post_info"]["title"] == "Bitcoin up 3.2%"
    db.refresh(v)
    assert client.post(f"{P}/videos/{v.id}/publish-status").json()["publish_status"] == "PROCESSING_DOWNLOAD"
    tt.status[v.publish_id] = {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": []}
    assert client.post(f"{P}/videos/{v.id}/publish-status").json()["status"] == "published"
    assert client.put(f"{P}/videos/{v.id}/destination", json={"mode": "drafts"}).status_code == 409  # already on TikTok


def test_latest_tiktok_videos_are_read_live_and_fixable_errors_say_what_to_do(client: TestClient, tt: FakeTikTok) -> None:
    assert client.get(f"{P}/tiktok/videos").status_code == 400  # not connected
    _connect(client)
    tt.videos = [{"id": "735", "title": "Bitcoin up", "view_count": 120, "like_count": 9, "comment_count": 1, "share_count": 0, "create_time": 1792490000}]
    r = client.get(f"{P}/tiktok/videos").json()
    assert r["videos"][0]["id"] == "735" and r["videos"][0]["view_count"] == 120
    from app.brand_manager.tiktok import HINTS

    assert "private" in HINTS["unaudited_client_can_only_post_to_private_accounts"]


def test_daily_cap_counts_sends_not_videos_analytics_touched(db: Session) -> None:
    from app.brand_manager import analytics

    b = brands.ensure_default(db)
    now = utcnow()
    for i in range(10):  # posted 20 days ago; analytics rewrite them (updated_at moves) but they weren't sent today
        db.add(BmVideo(brand_id=b.id, category="facts", template="facts", topic=f"t{i}", topic_key=f"k{i}", status="published",
                       publish_id=f"p{i}", metrics={"views": 5}, metrics_at=now - timedelta(days=20), stages={}))
    db.commit()
    analytics.analyze(db, b, now)
    db.commit()
    assert engine._published_today(db, b.id, now) == 0
    brands.log(db, b.id, "Sent “t0” to TikTok (your drafts).", stage="publish")
    db.commit()
    assert engine._published_today(db, b.id, now) == 1


def test_a_trending_coin_also_in_the_headlines_is_stored_once(db: Session) -> None:
    import asyncio
    from email.utils import format_datetime

    from app.brand_manager import trends
    from app.models.brand_manager import BmTrendDay

    now = utcnow()

    def handle(r: httpx.Request) -> httpx.Response:
        if "coingecko" in r.url.host:
            return httpx.Response(200, json={"coins": [{"item": {"id": "hyperliquid", "name": "Hyperliquid", "symbol": "HYPE"}}]})
        items = "".join(f"<item><title>{t}</title><link>https://x.com/{r.url.host}/{i}</link><pubDate>{format_datetime(now)}</pubDate></item>"
                        for i, t in enumerate([f"Hyperliquid volume explodes at {r.url.host}", f"Traders flock to Hyperliquid perps {r.url.host}"]))
        return httpx.Response(200, text=f"<rss><channel>{items}</channel></rss>")

    asyncio.run(trends.refresh(db, now, transport=httpx.MockTransport(handle)))
    assert len(db.scalars(select(BmTrendDay).where(BmTrendDay.term == "hyperliquid")).all()) == 1
