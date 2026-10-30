"""Read-only Facebook connector: the comments of one live video on a Page, polled from the Graph API.

Only GET requests leave this module; `ReadOnlyTransport` refuses any other method before it reaches the
network. The Page token travels in the Authorization header, never in a URL, and is scrubbed from every error
message. Commenter ids are hashed into `user_ref`; display names are never requested (`from{id}` only).

Endpoint: https://developers.facebook.com/docs/graph-api/reference/live-video/comments/
Permissions on the Page token (https://developers.facebook.com/docs/permissions/): pages_show_list,
pages_read_engagement, pages_read_user_content. Rate limits and error codes:
https://developers.facebook.com/docs/graph-api/overview/rate-limiting/ and
https://developers.facebook.com/docs/graph-api/guides/error-handling/
"""

import asyncio
import hashlib
import logging
import re
from collections import deque
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx

from app.contracts import now_ms
from connectors.facebook.interface import CommentSource

log = logging.getLogger("connectors.facebook")

GRAPH_HOST = "https://graph.facebook.com"
DEFAULT_GRAPH_VERSION = "v26.0"
FIELDS = "id,from{id},message,created_time,parent{id}"  # never `name`
PAGE_SIZE = 100
MAX_PAGES_PER_POLL = 20
REMEMBERED_IDS = 10_000
MAX_BACKOFF_SECONDS = 60
ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")
REDACTED = "[redacted]"
# Throttling (4 app, 17 user, 32 Page, 613 custom) and transient (1, 2) errors: back off, then retry.
TRANSIENT_CODES = {1, 2, 4, 17, 32, 613}


def redact(text: str, secret: str) -> str:
    """Remove the token from any text that could reach a log, an exception or the console."""
    return text.replace(secret, REDACTED) if secret else text


def user_ref(commenter_id: str) -> str:
    """Pseudonymous reference: a truncated sha256 of the commenter id. Never the id, never a name."""
    return "fb_" + hashlib.sha256(f"fb-user:{commenter_id}".encode()).hexdigest()[:16]


def safe_id(raw: str) -> str:
    """Graph ids as contract ids; anything outside the contract pattern is replaced by its hash."""
    raw = str(raw)
    return raw if ID_PATTERN.match(raw) else "fb.h" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def parse_time(value: str | None) -> int:
    """Graph `created_time` (ISO 8601 such as 2026-10-30T09:00:00+0000) -> epoch ms; missing -> now."""
    if not value:
        return now_ms()
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


class WriteRefused(RuntimeError):
    """A request other than GET was about to leave the read-only connector."""


class ReadOnlyTransport(httpx.AsyncBaseTransport):
    """Wraps a transport and refuses every request that is not a GET before it reaches the network."""

    def __init__(self, inner: httpx.AsyncBaseTransport):
        self.inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method.upper() != "GET":
            raise WriteRefused(f"read-only connector: {request.method} {request.url.path} refused")
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


class GraphError(RuntimeError):
    """A Graph API error: code, subcode, type and fbtrace_id for triage; the message has the token scrubbed."""

    def __init__(self, status: int, code, subcode, etype, trace, message: str):
        self.status, self.code, self.subcode, self.etype, self.trace = status, code, subcode, etype, trace
        super().__init__(
            f"graph api {status}: code={code} subcode={subcode} type={etype} fbtrace_id={trace}: {message}"
        )

    @classmethod
    def of(cls, response: httpx.Response, secret: str) -> "GraphError":
        try:
            body = response.json()
        except ValueError:
            body = {}
        err = body.get("error") if isinstance(body, dict) else None
        err = err if isinstance(err, dict) else {}
        message = str(err.get("message") or response.text[:200])
        return cls(
            response.status_code,
            err.get("code"),
            err.get("error_subcode"),
            err.get("type"),
            err.get("fbtrace_id"),
            redact(message, secret),
        )

    @property
    def transient(self) -> bool:
        return self.code in TRANSIENT_CODES


class FacebookCommentSource(CommentSource):
    """Polls GET /{live-video-id}/comments and yields contract rows; one instance per live video."""

    def __init__(
        self,
        live_video_id: str,
        token: str,
        *,
        graph_version: str = DEFAULT_GRAPH_VERSION,
        poll_seconds: float = 2.0,
        live_filter: str = "no_filter",
        transport: httpx.AsyncBaseTransport | None = None,
        sleep=asyncio.sleep,
    ):
        if not token or not live_video_id:
            raise ValueError("a Page token and a live video id are required")
        self._token = token
        self.path = f"/{graph_version}/{live_video_id}/comments"
        self.poll_seconds = poll_seconds
        self.live_filter = live_filter
        self._sleep = sleep
        self._since: int | None = None
        self._seen: set[str] = set()
        self._order: deque[str] = deque()
        self.http = httpx.AsyncClient(
            base_url=GRAPH_HOST,
            headers={"Authorization": f"Bearer {token}"},
            transport=ReadOnlyTransport(transport or httpx.AsyncHTTPTransport()),
            timeout=15,
        )

    def __repr__(self) -> str:
        return "FacebookCommentSource(read-only)"

    async def aclose(self) -> None:
        await self.http.aclose()

    async def __aenter__(self) -> "FacebookCommentSource":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    @staticmethod
    def row(raw: dict) -> dict | None:
        """One Graph comment -> contract fields. Without `from` (not visible to the app) the pseudonym is
        per comment, so the comment is kept but cannot be linked to the same person's other comments."""
        cid = raw.get("id")
        if not cid:
            return None
        sender = (raw.get("from") or {}).get("id")
        ref = (
            user_ref(str(sender))
            if sender
            else "fb-anon_" + hashlib.sha256(str(cid).encode()).hexdigest()[:16]
        )
        parent = (raw.get("parent") or {}).get("id")
        return {
            "comment_id": safe_id(cid),
            "user_ref": ref,
            "text": raw.get("message") or "",
            "reply_to": safe_id(parent) if parent else None,
            "ts_ms": parse_time(raw.get("created_time")),
        }

    def _remember(self, comment_id: str) -> bool:
        if comment_id in self._seen:
            return False
        self._seen.add(comment_id)
        self._order.append(comment_id)
        if len(self._order) > REMEMBERED_IDS:
            self._seen.discard(self._order.popleft())
        return True

    async def poll(self) -> list[dict]:
        """New comments since the previous poll, following cursors up to MAX_PAGES_PER_POLL pages."""
        rows: list[dict] = []
        after = None
        for _ in range(MAX_PAGES_PER_POLL):
            params: dict = {
                "fields": FIELDS,
                "filter": "stream",
                "order": "chronological",
                "live_filter": self.live_filter,
                "limit": PAGE_SIZE,
            }
            if self._since is not None:
                params["since"] = self._since
            if after:
                params["after"] = after
            r = await self.http.get(self.path, params=params)
            if r.status_code != 200:
                raise GraphError.of(r, self._token)
            body = r.json()
            for raw in body.get("data") or []:
                row = self.row(raw)
                if row and self._remember(row["comment_id"]):
                    rows.append(row)
            paging = body.get("paging") or {}
            after = (paging.get("cursors") or {}).get("after") if paging.get("next") else None
            if not after:
                break
        if rows:
            self._since = max(self._since or 0, max(r["ts_ms"] for r in rows) // 1000)
        return rows

    async def comments(self, max_polls: int | None = None) -> AsyncIterator[dict]:
        """Yield new comments, polling every `poll_seconds`; transient Graph errors back off, others raise."""
        polls, backoff = 0, self.poll_seconds
        while max_polls is None or polls < max_polls:
            polls += 1
            try:
                rows = await self.poll()
            except GraphError as exc:
                if not exc.transient:
                    raise
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                log.warning(
                    "graph api error code=%s subcode=%s; next poll in %.0fs", exc.code, exc.subcode, backoff
                )
                await self._sleep(backoff)
                continue
            backoff = self.poll_seconds
            for row in rows:
                yield row
            if max_polls is None or polls < max_polls:
                await self._sleep(self.poll_seconds)
