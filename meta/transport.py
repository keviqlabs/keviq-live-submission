"""HTTP transport to the Model API, and a scripted fake for tests (no network, no key)."""

from dataclasses import dataclass, field

from app.settings import env


@dataclass
class Reply:
    status: int
    body: dict
    headers: dict = field(default_factory=dict)


class HttpTransport:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 60):
        import httpx

        key = api_key or env("MODEL_API_KEY")
        if not key or key == "replace-me":
            raise RuntimeError("MODEL_API_KEY is not set in the environment (.env); see deploy/.env.example")
        self._client = httpx.AsyncClient(
            base_url=base_url or env("MODEL_API_BASE_URL", "https://api.meta.ai/v1"),
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )

    async def post(self, path: str, body: dict) -> Reply:
        r = await self._client.post(path.lstrip("/"), json=body)
        try:
            data = r.json()
        except ValueError:
            data = {"raw": r.text[:500]}
        return Reply(r.status_code, data, dict(r.headers))

    async def aclose(self) -> None:
        await self._client.aclose()


class FakeTransport:
    """Returns scripted replies in order and records every request."""

    def __init__(self, replies: list[Reply]):
        self.replies = list(replies)
        self.requests: list[tuple[str, dict]] = []

    async def post(self, path: str, body: dict) -> Reply:
        self.requests.append((path, body))
        if not self.replies:
            raise AssertionError("FakeTransport ran out of scripted replies")
        return self.replies.pop(0)

    async def aclose(self) -> None:
        pass


def responses_reply(text: str, tokens_in: int = 100, tokens_out: int = 20) -> Reply:
    """A Responses API success body in the shape the client parses."""
    return Reply(
        200,
        {
            "id": "resp_fake",
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
        },
    )
