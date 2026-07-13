import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conductor import prompts
from conductor.prompts import PromptStore


def test_default_template_interpolates_ticket() -> None:
    rendered = prompts.default_template("engineer").format(
        ticket_id="T-1",
        title="Add login",
        body="It must accept OAuth.",
        repos="- `/work/backend` — octo/backend (branch `main`)",
    )
    assert "T-1" in rendered
    assert "Add login" in rendered
    assert "{ticket_id}" not in rendered  # every placeholder was filled


def test_validate_rejects_unknown_placeholder() -> None:
    error = prompts.validate("engineer", "Build {ticket_id} for {mystery}")
    assert error is not None
    assert "mystery" in error


def test_validate_rejects_stray_brace() -> None:
    assert prompts.validate("engineer", "return {not doubled}") is not None


def test_validate_accepts_known_fields_and_escaped_braces() -> None:
    assert (
        prompts.validate("engineer", 'Ticket {ticket_id}: emit {{"ok": true}}') is None
    )


async def test_store_seed_get_and_edit(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    store = PromptStore(sessionmaker)

    # Falls back to the bundled default before anything is seeded.
    assert (await store.get("engineer")) == prompts.default_template("engineer")

    seeded = await store.seed_defaults()
    assert seeded == len(prompts.ROLES)
    assert (await store.seed_defaults()) == 0  # seed-once: no duplicates

    await store.set("engineer", "Edited template for {ticket_id}")
    assert (await store.get("engineer")) == "Edited template for {ticket_id}"

    rendered = await store.render(
        "engineer", ticket_id="T-9", title="x", body="y", repos="z"
    )
    assert rendered == "Edited template for T-9"


async def test_store_set_validates(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    store = PromptStore(sessionmaker)
    with pytest.raises(ValueError):
        await store.set("engineer", "uses {unknown_field}")


async def test_list_returns_every_role(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    views = await PromptStore(sessionmaker).list()
    assert [v.role for v in views] == list(prompts.ROLES)
    assert all(v.content for v in views)
