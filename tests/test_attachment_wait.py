"""The default attachment wait: a comment waits 10 s after its ingestion for its card, then expires."""

from tests.test_attachments_runtime import world  # noqa: F401  (a session bound with the default wait)


async def test_default_binding_holds_a_comment_ten_seconds_for_its_card(world):  # noqa: F811
    assert (await world.store.attachment_binding(world.sid))["wait_ms"] == 10_000
    await world.card(10, "GIAY-01")
    await world.comment(ts=15)
    start = world.now
    world.now = start + 9_999
    await world.service.expire_due(world.sid)
    assert not await world.store.attachments(world.sid), "expired before 10 s"
    world.now = start + 10_000
    await world.service.expire_due(world.sid)
    [event] = await world.store.attachments(world.sid)
    assert event["outcome"] == "expired"
    assert event["deadline_ms"] == event["comment_ingested_ms"] + 10_000 == event["ts_ms"]
