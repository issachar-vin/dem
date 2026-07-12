"""Repo routing: given a ticket and the project's repos, decide which repo(s) the ticket needs.

A text-only reasoning step — the conductor gathers each repo's README (via the GitHub API, so the
model never touches GitHub) and asks Claude which repo *keys* are involved. Runs as a direct
Messages API call, not an agent container: no filesystem, no tools, nothing to clone. Best-effort —
any failure falls back to the first repo, so a routing hiccup never blocks a build."""

import logging

import httpx

from conductor.agents.contracts import MalformedAgentOutput, parse_route
from conductor.verify import ANTHROPIC_MESSAGES_URL, _claude_headers

logger = logging.getLogger("conductor")

_ROUTER_MODEL = "claude-haiku-4-5"
_MAX_README_CHARS = 4_000

_SYSTEM = (
    "You route a software ticket to the repositories it needs changes in. You are given the ticket "
    "and a catalog of the project's repositories, each with a short key and its README. Decide "
    "which repositories the ticket needs work in — usually one, sometimes several. Reply with ONLY "
    'JSON object: {"repos": ["<key>", ...]} using the exact keys from the catalog. No prose.'
)


async def route_repos(
    resolved: dict[str, str],
    *,
    ticket: str,
    catalog: list[tuple[str, str]],
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Return the repo keys the ticket needs. `catalog` is `[(key, readme), …]`. Falls back to the
    first key on any failure (empty catalog → empty list)."""
    keys = [key for key, _ in catalog]
    if len(keys) <= 1:
        return keys  # nothing to decide
    prompt = _build_prompt(ticket, catalog)
    try:
        text = await _ask(resolved, prompt, client=client)
        chosen = parse_route(text).repos
    except (httpx.HTTPError, MalformedAgentOutput, KeyError, ValueError) as exc:
        logger.warning("Repo routing failed (%s); falling back to first repo", exc)
        return keys[:1]
    picked = [k for k in chosen if k in keys]  # ignore hallucinated keys
    return picked or keys[:1]


def _build_prompt(ticket: str, catalog: list[tuple[str, str]]) -> str:
    blocks = [
        f"## Repository `{key}`\n{(readme or '(no README)')[:_MAX_README_CHARS]}"
        for key, readme in catalog
    ]
    catalog_text = "\n\n".join(blocks)
    return f"# Ticket\n\n{ticket}\n\n# Repositories\n\n{catalog_text}"


async def _ask(resolved: dict[str, str], prompt: str, *, client: httpx.AsyncClient | None) -> str:
    headers = {
        **_claude_headers(
            resolved.get("claude_code_oauth_token") or None,
            resolved.get("anthropic_api_key") or None,
        ),
        "content-type": "application/json",
    }
    body = {
        "model": resolved.get("claude_model_router") or _ROUTER_MODEL,
        "max_tokens": 200,
        "system": _SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
    }

    async def send(c: httpx.AsyncClient) -> str:
        response = await c.post(ANTHROPIC_MESSAGES_URL, headers=headers, json=body)
        response.raise_for_status()
        blocks = response.json().get("content", [])
        return "".join(b.get("text", "") for b in blocks if isinstance(b, dict))

    if client is not None:
        return await send(client)
    async with httpx.AsyncClient(timeout=30) as owned:
        return await send(owned)
