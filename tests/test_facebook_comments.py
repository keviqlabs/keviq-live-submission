"""The read-only Facebook comment source against a fake Graph API: GET only, token kept out of logs and
errors, commenter ids hashed, and a session that holds Facebook events refusing the simulated Messenger."""

import json
import logging
import re
from datetime import UTC, datetime

import httpx
import pytest

from app.contracts import now_ms, validate_event
from app.settings import ROOT
from connectors.facebook import ingest
from connectors.facebook.comments import (
    FacebookCommentSource,
    GraphError,
    ReadOnlyTransport,
    WriteRefused,
    user_ref,
)
from connectors.sim.messenger import webhook_body

TOKEN = "test-token-aaaa"  # fake, low entropy on purpose
VIDEO = "100000000000001"
SENDER = "200000000000002"
NAME = "Some Display Name"


def comment(i: int, sender: str = SENDER, parent: str | None = None, when: str = "2026-10-30T09:00:00+0000"):
    raw = {
        "id": f"{VIDEO}_{i}",
        "from": {"id": sender, "name": NAME},
        "message": f"take two {i}",
        "created_time": when,
    }
    if parent:
        raw["parent"] = {"id": parent}
    return raw


class FakeGraph:
    """Pages of comments behind a MockTransport; `errors` are served first, one per request, to the requests
    for page `errors_on_page` (the first page unless told otherwise)."""

    def __init__(
        self, pages: list[list[dict]], errors: list[tuple[int, dict]] | None = None, errors_on_page: int = 0
    ):
        self.pages, self.errors, self.requests = pages, list(errors or []), []
        self.errors_on_page = errors_on_page
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        idx = int(request.url.params.get("after") or 0)
        if self.errors and idx == self.errors_on_page:
            status, error = self.errors.pop(0)
            return httpx.Response(status, json={"error": error})
        body: dict = {"data": self.pages[idx] if idx < len(self.pages) else []}
        if idx + 1 < len(self.pages):
            body["paging"] = {
                "cursors": {"after": str(idx + 1)},
                "next": f"https://graph.facebook.com/x?after={idx + 1}",
            }
        return httpx.Response(200, json=body)


def source(graph: FakeGraph, sleeps: list | None = None, **kw) -> FacebookCommentSource:
    async def no_sleep(seconds):
        if sleeps is not None:
            sleeps.append(seconds)

    return FacebookCommentSource(
        VIDEO, TOKEN, transport=graph.transport, sleep=no_sleep, poll_seconds=2, **kw
    )


# (a) only GET requests


async def test_only_get_requests_leave_the_connector():
    graph = FakeGraph([[comment(1), comment(2)], [comment(3)]])
    async with source(graph) as src:
        rows = [r async for r in src.comments(max_polls=2)]
    assert [r["comment_id"] for r in rows] == [f"{VIDEO}_1", f"{VIDEO}_2", f"{VIDEO}_3"]
    assert graph.requests and {r.method for r in graph.requests} == {"GET"}
    for r in graph.requests:
        assert r.headers["authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in str(r.url) and "access_token" not in str(r.url)
        assert "name" not in r.url.params["fields"]


async def test_any_other_method_is_refused_before_the_wire():
    graph = FakeGraph([[]])
    async with httpx.AsyncClient(
        transport=ReadOnlyTransport(graph.transport), base_url="https://x.test"
    ) as http:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with pytest.raises(WriteRefused):
                await http.request(method, f"/v26.0/{VIDEO}/comments")
        assert graph.requests == []
        assert (await http.get(f"/v26.0/{VIDEO}/comments")).status_code == 200
    async with source(graph) as src:
        with pytest.raises(WriteRefused):
            await src.http.post(src.path, json={"message": "never"})
    assert len(graph.requests) == 1


# (b) a session with Facebook events refuses the simulated Messenger


async def test_ingested_session_refuses_the_simulated_messenger(mem_app):
    _, store, client = mem_app
    graph = FakeGraph([[comment(1), comment(2, parent=f"{VIDEO}_1")]])
    quiet = {"out": lambda *_: None}
    async with source(graph) as src:
        assert await ingest.run(src, client, "live-l2", max_polls=1, **quiet) == 2
    stored = await store.read("live-l2", 0, 100)
    assert [e["source"] for e in stored] == ["facebook", "facebook"]
    assert stored[1]["reply_to"] == f"{VIDEO}_1" and stored[0]["reply_to"] is None
    assert all(SENDER not in e["user_ref"] and NAME not in str(e) for e in stored)
    async with source(FakeGraph([[comment(1)]])) as again:  # a restart re-posts: idempotent on event_id
        assert await ingest.run(again, client, "live-l2", max_polls=1, **quiet) == 1
    assert len(await store.read("live-l2", 0, 100)) == 2
    r = await client.post(
        "/api/sessions/live-l2/sim/messenger", json=webhook_body("th-u01", now_ms(), text="ok")
    )
    assert r.status_code == 403


async def test_the_same_video_in_a_second_session_is_stored_there_too(mem_app):
    """The store deduplicates event_id across sessions, so ids are scoped to the session: the same video
    ingested into two sessions lands in both, and the same rows again in one session are stored once."""
    _, store, client = mem_app
    quiet = {"out": lambda *_: None}
    pages = [[comment(1), comment(2, parent=f"{VIDEO}_1")]]
    for session_id in ("live-a", "live-b"):
        async with source(FakeGraph(pages)) as src:
            assert await ingest.run(src, client, session_id, max_polls=1, **quiet) == 2
    a, b = await store.read("live-a", 0, 100), await store.read("live-b", 0, 100)
    assert [e["source"] for e in a] == [e["source"] for e in b] == ["facebook", "facebook"]
    assert [e["comment_id"] for e in b] == [f"{VIDEO}_1", f"{VIDEO}_2"] and b[1]["reply_to"] == f"{VIDEO}_1"
    assert {e["event_id"] for e in a}.isdisjoint(e["event_id"] for e in b)
    async with source(FakeGraph(pages)) as again:  # a restart re-posts the same rows: stored once
        assert await ingest.run(again, client, "live-a", max_polls=1, **quiet) == 2
    assert [e["event_id"] for e in await store.read("live-a", 0, 100)] == [e["event_id"] for e in a]
    for session_id in ("live-a", "live-b"):
        r = await client.post(
            f"/api/sessions/{session_id}/sim/messenger", json=webhook_body("th-u01", now_ms(), text="ok")
        )
        assert r.status_code == 403


def test_event_id_is_deterministic_per_session_and_within_the_contract():
    schema = json.loads((ROOT / "contracts/events/comment.schema.json").read_text())
    pattern = re.compile(schema["properties"]["event_id"]["pattern"])
    longest = comment(1)
    longest["id"] = "c" * 96  # the longest comment id the contract allows, in the longest session id
    row = FacebookCommentSource.row(longest)
    first = ingest.event("s" * 64, row, now_ms())
    again = ingest.event("s" * 64, row, now_ms() + 1)
    other = ingest.event("live-b", row, now_ms())
    for event in (first, again, other):
        assert pattern.match(event["event_id"]) and len(event["event_id"]) <= 96
        assert event["comment_id"] == "c" * 96 and event["reply_to"] is None
    assert first["event_id"] == again["event_id"] != other["event_id"]
    reply = FacebookCommentSource.row(comment(2, parent=f"{VIDEO}_1"))
    assert ingest.event("live-a", reply, now_ms())["reply_to"] == f"{VIDEO}_1"
    assert (
        ingest.event("live-a", reply, now_ms())["event_id"]
        != ingest.event("live-a", row, now_ms())["event_id"]
    )


# (c) the token never appears in logs, URLs or error messages


async def test_token_never_reaches_logs_urls_or_errors(caplog):
    caplog.set_level(logging.DEBUG)
    oauth = {"message": f"Invalid OAuth access token {TOKEN}", "type": "OAuthException", "code": 190,
             "error_subcode": 463, "fbtrace_id": "AbC"}  # fmt: skip
    graph = FakeGraph([[comment(1)]], errors=[(400, oauth)])
    async with source(graph) as src:
        with pytest.raises(GraphError) as info:
            [r async for r in src.comments(max_polls=1)]
    exc = info.value
    assert (exc.status, exc.code, exc.subcode, exc.etype, exc.transient) == (
        400,
        190,
        463,
        "OAuthException",
        False,
    )
    assert "[redacted]" in str(exc)
    for text in (str(exc), repr(exc), repr(src), caplog.text, *(str(r.url) for r in graph.requests)):
        assert TOKEN not in text


async def test_transient_errors_back_off_and_log_without_the_token(caplog):
    caplog.set_level(logging.DEBUG)
    throttled = {"message": f"Application request limit reached {TOKEN}", "type": "OAuthException", "code": 4}
    graph, sleeps = FakeGraph([[comment(1)]], errors=[(400, throttled)]), []
    async with source(graph, sleeps) as src:
        rows = [r async for r in src.comments(max_polls=2)]
    assert [r["comment_id"] for r in rows] == [f"{VIDEO}_1"]
    assert sleeps == [4] and "code=4" in caplog.text and TOKEN not in caplog.text


async def test_a_transient_error_on_a_later_page_keeps_the_earlier_rows_for_the_retry():
    """Page 1 answers, page 2 throttles once, then both pages answer: every comment once, no cursor jump."""
    throttled = {"message": "Application request limit reached", "type": "OAuthException", "code": 4}
    pages = [[comment(1, when="2026-10-30T09:00:00+0000")], [comment(2, when="2026-10-30T09:00:05+0000")]]
    graph, sleeps = FakeGraph(pages, errors=[(400, throttled)], errors_on_page=1), []
    async with source(graph, sleeps) as src:
        rows = [r async for r in src.comments(max_polls=3)]
    assert [r["comment_id"] for r in rows] == [f"{VIDEO}_1", f"{VIDEO}_2"]
    assert sleeps == [4, 2]  # one back-off after the failed poll, then the regular interval
    assert "since" not in graph.requests[2].url.params  # the failed poll did not move the cursor
    assert graph.requests[4].url.params["since"] == str(rows[1]["ts_ms"] // 1000)


def test_config_messages_name_variables_never_values():
    with pytest.raises(SystemExit) as info:
        ingest.Config.from_env({"FACEBOOK_PAGE_TOKEN": TOKEN})
    assert "FACEBOOK_LIVE_VIDEO_ID" in str(info.value) and TOKEN not in str(info.value)
    config = ingest.Config.from_env({"FACEBOOK_PAGE_TOKEN": TOKEN, "FACEBOOK_LIVE_VIDEO_ID": VIDEO})
    assert TOKEN not in repr(config) and config.graph_version == "v26.0" and config.poll_seconds == 2.0


# (d) user_ref is a hash, never the commenter id or a name


def test_user_ref_is_a_hash_without_the_commenter_id_or_name():
    row = FacebookCommentSource.row(comment(1))
    assert SENDER not in row["user_ref"] and NAME not in str(row)
    assert (
        row["user_ref"] == user_ref(SENDER)
        and row["user_ref"].startswith("fb_")
        and len(row["user_ref"]) == 19
    )
    assert row["user_ref"] != user_ref("300000000000003")
    event = validate_event(ingest.event("live-l2", row, now_ms()))
    assert event["source"] == "facebook" and event["comment_id"] == f"{VIDEO}_1"
    anonymous = FacebookCommentSource.row({"id": f"{VIDEO}_9", "message": "hi"})  # no from, no created_time
    assert anonymous["user_ref"].startswith("fb-anon_") and anonymous["text"] == "hi"
    assert FacebookCommentSource.row({"message": "no id"}) is None


def test_created_time_becomes_epoch_ms():
    row = FacebookCommentSource.row(comment(1, when="2026-10-30T02:00:00+0000"))
    assert row["ts_ms"] == int(datetime(2026, 10, 30, 2, tzinfo=UTC).timestamp() * 1000)


async def test_since_and_dedupe_across_polls():
    graph = FakeGraph(
        [[comment(1, when="2026-10-30T09:00:00+0000"), comment(2, when="2026-10-30T09:00:05+0000")]]
    )
    async with source(graph) as src:
        rows = [r async for r in src.comments(max_polls=3)]
    assert [r["comment_id"] for r in rows] == [f"{VIDEO}_1", f"{VIDEO}_2"]
    assert "since" not in graph.requests[0].url.params
    assert graph.requests[1].url.params["since"] == str(rows[1]["ts_ms"] // 1000)


async def test_run_refuses_an_invalid_session_id(mem_app):
    _, _, client = mem_app
    async with source(FakeGraph([[comment(1)]])) as src:
        with pytest.raises(SystemExit):
            await ingest.run(src, client, "bad id!", max_polls=1)
