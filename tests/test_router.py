import httpx

from conductor import router


def _transport(text: str, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json={"content": [{"type": "text", "text": text}]})

    return httpx.MockTransport(handler)


async def test_route_parses_fenced_json_and_filters_unknown_keys() -> None:
    cfg = {"anthropic_api_key": "sk"}
    catalog = [("ui", "the frontend"), ("api", "the backend")]
    async with httpx.AsyncClient(
        transport=_transport('```json\n{"repos": ["ui", "ghost"]}\n```')
    ) as client:
        keys = await router.route_repos(
            cfg, ticket="restyle the header", catalog=catalog, client=client
        )
    assert keys == ["ui"]  # hallucinated "ghost" dropped


async def test_route_single_repo_short_circuits_without_a_call() -> None:
    # One repo → nothing to decide, no API call (cfg has no creds, which would fail a real call).
    keys = await router.route_repos({}, ticket="anything", catalog=[("only", "")])
    assert keys == ["only"]


async def test_route_falls_back_to_first_on_error() -> None:
    cfg = {"anthropic_api_key": "sk"}
    catalog = [("ui", ""), ("api", "")]
    async with httpx.AsyncClient(transport=_transport("", status=500)) as client:
        keys = await router.route_repos(cfg, ticket="x", catalog=catalog, client=client)
    assert keys == ["ui"]  # first repo, never blocks a build


async def test_route_falls_back_when_nothing_valid_chosen() -> None:
    cfg = {"anthropic_api_key": "sk"}
    catalog = [("ui", ""), ("api", "")]
    async with httpx.AsyncClient(transport=_transport('{"repos": ["nope"]}')) as client:
        keys = await router.route_repos(cfg, ticket="x", catalog=catalog, client=client)
    assert keys == ["ui"]
