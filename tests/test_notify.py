from typing import Any

import pytest

from conductor import notify


class _FakeClient:
    """Stands in for httpx.AsyncClient, recording posts instead of sending them."""

    calls: list[tuple[str, dict[str, Any]]] = []
    raises: Exception | None = None

    def __init__(self, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def post(self, url: str, **kwargs: Any) -> None:
        if _FakeClient.raises is not None:
            raise _FakeClient.raises
        _FakeClient.calls.append((url, kwargs))


@pytest.fixture(autouse=True)
def _reset() -> None:
    _FakeClient.calls = []
    _FakeClient.raises = None


async def test_none_mode_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify.httpx, "AsyncClient", _FakeClient)
    await notify.notify({"notify_mode": "none"}, "hi")
    assert _FakeClient.calls == []


async def test_ntfy_posts_message_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify.httpx, "AsyncClient", _FakeClient)
    await notify.notify(
        {"notify_mode": "ntfy", "notify_ntfy_url": "http://ntfy/topic"}, "done"
    )
    url, kwargs = _FakeClient.calls[0]
    assert url == "http://ntfy/topic"
    assert kwargs["content"] == b"done"


async def test_slack_posts_text_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify.httpx, "AsyncClient", _FakeClient)
    await notify.notify(
        {"notify_mode": "slack", "notify_slack_webhook_url": "http://hook"}, "done"
    )
    url, kwargs = _FakeClient.calls[0]
    assert url == "http://hook"
    assert kwargs["json"] == {"text": "done"}


async def test_delivery_error_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notify.httpx, "AsyncClient", _FakeClient)
    _FakeClient.raises = notify.httpx.ConnectError("down")
    await notify.notify(
        {"notify_mode": "webhook", "notify_webhook_url": "http://x"}, "done"
    )  # must not raise
