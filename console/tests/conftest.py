from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from console.api_client import ConductorClient

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def make_client() -> Callable[[Handler], ConductorClient]:
    def _make(handler: Handler) -> ConductorClient:
        transport = httpx.MockTransport(handler)
        http = httpx.Client(transport=transport, base_url="http://conductor.test")
        return ConductorClient("http://conductor.test", client=http)

    return _make
